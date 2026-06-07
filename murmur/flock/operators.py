"""The seven operators — Murmur's reusable execution primitives.

Each operator is one pattern from the design catalog, implemented once as a
Template Method: the skeleton (spawn → run → collect) is fixed; the node's ``role``
fills in the subagent instruction. They all share the signature
``async def op(ctx: NodeContext) -> list[Artifact]`` and route every model call
through :meth:`NodeContext.call`, so the budget ledger and the concurrency bulkhead
apply uniformly.

| op         | pattern                | shape in → out                         |
|------------|------------------------|----------------------------------------|
| classify   | classify-and-act       | items → one label artifact             |
| map        | fan-out                | N items → N results (parallel)         |
| reduce     | synthesize (barrier)   | many items → one merged artifact       |
| tournament | pairwise bracket       | N items → N ranked (winner first)      |
| verify     | adversarial refutation | top_k items → annotated (contested)    |
| filter     | generate-and-filter    | scored items → top_k by score          |
| loop       | loop until done        | seed → refined artifact                |
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from murmur.flock.artifact import Artifact, parse_score
from murmur.flock.gateway import ModelPort
from murmur.flock.ir import Effort, Node, Trust


@dataclass
class NodeContext:
    """Everything one operator needs to run its node.

    ``items`` are the resolved, flattened inputs (upstream outputs + sources).
    ``model`` is already metered and tagged with this node's id. ``semaphore`` is
    the shared bulkhead that caps how many subagent calls run at once.
    """

    node: Node
    items: list[Artifact]
    model: ModelPort
    semaphore: asyncio.Semaphore
    calls: int = field(default=0)

    @property
    def effort(self) -> Effort:
        return self.node.effort

    async def call(self, *, system: str, user: str) -> str:
        """One subagent call, through the bulkhead. Returns the reply text."""

        async with self.semaphore:
            reply = await self.model.complete(system=system, user=user, effort=self.effort)
        self.calls += 1
        return reply.text

    def output_trust(self) -> Trust:
        """Taint propagation: a node's output is untrusted if any input is."""

        if self.node.trust == "untrusted" or any(it.trust == "untrusted" for it in self.items):
            return "untrusted"
        return "trusted"


# --- operators ---------------------------------------------------------------------


async def op_map(ctx: NodeContext) -> list[Artifact]:
    """Fan out: spawn one isolated subagent per input item, in parallel."""

    role = ctx.node.role or "Process this item and return the result."
    trust = ctx.output_trust()

    async def one(item: Artifact) -> Artifact:
        text = await ctx.call(system=role, user=item.content)
        return Artifact(
            id=f"{ctx.node.id}:{item.id}",
            content=text,
            score=parse_score(text),
            meta={"source_item": item.id},
            trust=trust,
        )

    return list(await asyncio.gather(*(one(it) for it in ctx.items)))


async def op_reduce(ctx: NodeContext) -> list[Artifact]:
    """Barrier + synthesize: merge all inputs into one artifact via one call."""

    role = ctx.node.role or "Synthesize the inputs into one coherent result."
    merged = "\n\n".join(f"[{a.id}]\n{a.content}" for a in ctx.items)
    text = await ctx.call(system=role, user=merged or "(no inputs)")
    return [
        Artifact(
            id=ctx.node.id,
            content=text,
            meta={"merged_from": [a.id for a in ctx.items]},
            trust=ctx.output_trust(),
        )
    ]


async def op_classify(ctx: NodeContext) -> list[Artifact]:
    """Classify-and-act: label the input so downstream nodes can route on it."""

    role = ctx.node.role or "Classify the input. Reply with a single short label."
    body = ctx.items[0].content if ctx.items else ""
    text = await ctx.call(system=role, user=body)
    label = next((ln.strip() for ln in text.splitlines() if ln.strip()), "unknown")[:60]
    return [
        Artifact(
            id=ctx.node.id, content=label, meta={"label": label}, trust=ctx.output_trust()
        )
    ]


async def op_filter(ctx: NodeContext) -> list[Artifact]:
    """Generate-and-filter: keep the top_k inputs by score (cheap, no model call).

    Scoring is ``map``'s job; ``filter`` selects. Items still carrying a score in
    their text get it parsed here as a fallback. Unscored items sort as zero.
    """

    top_k = int(ctx.node.params.get("top_k", len(ctx.items)))

    def score_of(a: Artifact) -> float:
        if a.score is not None:
            return a.score
        parsed = parse_score(a.content)
        return parsed if parsed is not None else 0.0

    ranked = sorted(ctx.items, key=score_of, reverse=True)
    return ranked[: max(0, top_k)]


async def op_tournament(ctx: NodeContext) -> list[Artifact]:
    """Pairwise bracket: single-elimination comparisons; return items winner-first."""

    items = list(ctx.items)
    if len(items) <= 1:
        return [it.with_meta(rank=i + 1) for i, it in enumerate(items)]

    role = ctx.node.role or "Which candidate is stronger? Reply 'A' or 'B' with a reason."
    eliminated_round: dict[str, int] = {}
    contenders = items
    round_no = 0
    while len(contenders) > 1:
        round_no += 1
        pairs = [
            (contenders[i], contenders[i + 1]) for i in range(0, len(contenders) - 1, 2)
        ]
        results = await asyncio.gather(*(_compare(ctx, role, a, b) for a, b in pairs))
        winners = [w for w, loser in results]
        for _w, loser in results:
            eliminated_round[loser.id] = round_no
        if len(contenders) % 2 == 1:
            winners.append(contenders[-1])  # odd one out gets a bye
        contenders = winners

    champion = contenders[0]
    rest = sorted(
        (it for it in items if it.id != champion.id),
        key=lambda it: eliminated_round.get(it.id, 0),
        reverse=True,
    )
    ordered = [champion, *rest]
    return [it.with_meta(rank=i + 1) for i, it in enumerate(ordered)]


async def _compare(
    ctx: NodeContext, role: str, a: Artifact, b: Artifact
) -> tuple[Artifact, Artifact]:
    """Return ``(winner, loser)`` for one pairwise comparison."""

    prompt = (
        f"Candidate A:\n{a.content}\n\n"
        f"Candidate B:\n{b.content}\n\n"
        "Which is stronger? Reply with 'A' or 'B' first, then a one-line reason."
    )
    verdict = (await ctx.call(system=role, user=prompt)).strip().upper()
    for ch in verdict:
        if ch == "A":
            return a, b
        if ch == "B":
            return b, a
    return a, b  # no clear pick → keep A (stable)


async def op_verify(ctx: NodeContext) -> list[Artifact]:
    """Adversarial verification: a blind refuter attacks each of the top_k artifacts."""

    top_k = int(ctx.node.params.get("top_k", len(ctx.items)))
    targets, passthrough = ctx.items[:top_k], ctx.items[top_k:]
    role = ctx.node.role or (
        "Adversarially challenge this artifact. List concrete flaws, "
        "or reply 'OK' if you find none."
    )

    async def refute(item: Artifact) -> Artifact:
        critique = (await ctx.call(system=role, user=item.content)).strip()
        contested = bool(critique) and not critique.upper().startswith("OK")
        return item.with_meta(contested=contested, critique=critique[:500])

    verified = list(await asyncio.gather(*(refute(it) for it in targets)))
    return [*verified, *passthrough]


async def op_loop(ctx: NodeContext) -> list[Artifact]:
    """Loop until done: refine a draft until a stop token appears or max_iters hit."""

    max_iters = max(1, int(ctx.node.params.get("max_iters", 3)))
    stop = str(ctx.node.params.get("stop", "DONE"))
    role = ctx.node.role or "Improve the draft. Append 'DONE' on its own line when complete."
    draft = ctx.items[0].content if ctx.items else ctx.node.role
    iterations = 0
    for _ in range(max_iters):
        iterations += 1
        draft = await ctx.call(system=role, user=draft)
        if stop and stop in draft:
            break
    return [
        Artifact(
            id=ctx.node.id,
            content=draft,
            meta={"iterations": iterations},
            trust=ctx.output_trust(),
        )
    ]


Operator = Callable[[NodeContext], Awaitable[list[Artifact]]]

OPERATORS: dict[str, Operator] = {
    "classify": op_classify,
    "map": op_map,
    "reduce": op_reduce,
    "tournament": op_tournament,
    "verify": op_verify,
    "filter": op_filter,
    "loop": op_loop,
}
