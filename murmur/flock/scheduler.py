"""The executor — interprets a :class:`WorkflowPlan` by running its DAG.

Concurrency falls out of the dependency graph: every node becomes an asyncio task
that first awaits the tasks of its upstream nodes, then runs its operator. Tasks that
share no path run at the same time; a ``reduce`` (or any node with several inputs)
naturally becomes a barrier because it awaits all of them. A semaphore caps how many
subagent calls run at once (the bulkhead), and the per-run :class:`BudgetLedger` is a
circuit breaker — once the token cap trips, further calls raise and the offending node
is recorded as failed rather than taking down the whole run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from murmur.flock.artifact import Artifact, as_artifacts
from murmur.flock.eventlog import (
    NODE_FAILED,
    RUN_FINISHED,
    RUN_STARTED,
    FlockEvent,
    FlockLog,
    completed_nodes,
)
from murmur.flock.gateway import BudgetLedger, CallLog, MeteredModel
from murmur.flock.ir import WorkflowPlan, validate_plan
from murmur.flock.models import ModelResolver, offline_resolver
from murmur.flock.operators import OPERATORS, NodeContext


class QuarantineViolation(RuntimeError):
    """A trusted node received untrusted (tainted) input at run time.

    The static taint check in :func:`~murmur.flock.ir.validate_plan` catches
    node→node laundering; this guard catches taint that enters from an untrusted
    *source* declared at execution time. Recorded as the node's error so the run
    stays inspectable rather than crashing.
    """


@dataclass(frozen=True, slots=True)
class NodeResult:
    """Outcome of one node: its output artifacts, or an error if it failed."""

    node_id: str
    op: str
    output: tuple[Artifact, ...]
    calls: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class RunReport:
    """Everything a run produced: per-node results, the final artifacts, and cost."""

    goal: str
    results: dict[str, NodeResult]
    final: tuple[Artifact, ...]
    spent_tokens: int
    spent_cost_usd: float
    model_calls: int
    log: CallLog = field(default_factory=CallLog)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results.values())

    @property
    def errors(self) -> dict[str, str]:
        return {nid: r.error for nid, r in self.results.items() if r.error is not None}


def _terminal_ids(plan: WorkflowPlan) -> list[str]:
    """Node ids that no other node consumes — the plan's outputs."""

    consumed: set[str] = set()
    ids = set(plan.node_ids)
    for n in plan.nodes:
        consumed.update(d for d in n.inputs if d in ids)
    return [nid for nid in plan.node_ids if nid not in consumed]


async def execute_plan(
    plan: WorkflowPlan,
    *,
    sources: dict[str, Any] | None = None,
    resolver: ModelResolver | None = None,
    max_parallel: int = 8,
    validate: bool = True,
    event_log: FlockLog | None = None,
    untrusted_sources: Iterable[str] | None = None,
) -> RunReport:
    """Run *plan* to completion and return a :class:`RunReport`.

    ``sources`` maps each plan-level source name to its initial value (string, list of
    strings, or list of ``{"id","content","score"}`` dicts). ``resolver`` turns a
    node's model spec into a :class:`ModelPort`; it defaults to a deterministic offline
    resolver so a hand-written plan runs end-to-end with no API keys.

    Pass ``event_log`` to make the run resumable: nodes already finished in the log are
    restored and skipped, so a re-run only does outstanding work. Name any tainted
    inputs in ``untrusted_sources`` and the quarantine guard refuses to let them reach a
    trusted node.
    """

    if validate:
        validate_plan(plan)
    resolver = resolver or offline_resolver()
    sources = sources or {}
    tainted_sources = set(untrusted_sources or ())

    ledger = BudgetLedger(budget_tokens=plan.budget_tokens)
    log = CallLog()
    semaphore = asyncio.Semaphore(max(1, max_parallel))

    source_artifacts: dict[str, list[Artifact]] = {}
    for name, value in sources.items():
        arts = as_artifacts(name, value)
        if name in tainted_sources:
            arts = [replace(a, trust="untrusted") for a in arts]
        source_artifacts[name] = arts

    outputs: dict[str, list[Artifact]] = {}
    results: dict[str, NodeResult] = {}
    tasks: dict[str, asyncio.Task[None]] = {}

    # Replay: restore nodes that already finished in the log and skip re-running them.
    plan_ids = set(plan.node_ids)
    replayed: set[str] = set()
    if event_log is not None:
        for nid, done in completed_nodes(event_log.read()).items():
            if nid in plan_ids:
                outputs[nid] = list(done.output)
                results[nid] = NodeResult(
                    node_id=nid, op=done.op, output=done.output, calls=done.calls
                )
                replayed.add(nid)
        event_log.append(FlockEvent(kind=RUN_STARTED, meta={"goal": plan.goal, "resumed": True}))

    async def run_node(node_id: str) -> None:
        if node_id in replayed:
            return  # restored from the log — its output is already known
        node = plan.node(node_id)
        # Barrier: wait for every upstream node this one depends on.
        for dep in node.inputs:
            if dep in tasks:
                await tasks[dep]

        items: list[Artifact] = []
        for name in node.inputs:
            if name in outputs:
                items.extend(outputs[name])
            elif name in source_artifacts:
                items.extend(source_artifacts[name])

        model = MeteredModel(resolver(node.model), ledger=ledger, on_call=log.record).for_node(
            node.id
        )
        ctx = NodeContext(node=node, items=items, model=model, semaphore=semaphore)
        try:
            # Quarantine: a trusted node must never read tainted input at run time.
            if node.trust == "trusted":
                tainted = [it.id for it in items if it.trust == "untrusted"]
                if tainted:
                    raise QuarantineViolation(
                        f"trusted node {node.id!r} received untrusted input(s) {tainted}"
                    )
            produced = await OPERATORS[node.op](ctx)
            outputs[node.id] = produced
            results[node.id] = NodeResult(
                node_id=node.id, op=node.op, output=tuple(produced), calls=ctx.calls
            )
            if event_log is not None:
                event_log.append(
                    FlockEvent.node_finished(node.id, node.op, tuple(produced), ctx.calls)
                )
        except Exception as exc:  # noqa: BLE001 - record any operator failure, keep the run inspectable
            outputs[node.id] = []
            error = f"{type(exc).__name__}: {exc}"
            results[node.id] = NodeResult(
                node_id=node.id, op=node.op, output=(), calls=ctx.calls, error=error
            )
            if event_log is not None:
                event_log.append(
                    FlockEvent(
                        kind=NODE_FAILED, node_id=node.id, op=node.op, calls=ctx.calls, error=error
                    )
                )

    # Create tasks in topological order so each dependency's task exists before any
    # dependent awaits it. create_task only schedules — nothing runs until we await.
    for node in plan.topological_order():
        tasks[node.id] = asyncio.create_task(run_node(node.id))
    await asyncio.gather(*tasks.values())

    final: list[Artifact] = []
    for nid in _terminal_ids(plan):
        final.extend(outputs.get(nid, []))

    report = RunReport(
        goal=plan.goal,
        results=results,
        final=tuple(final),
        spent_tokens=ledger.spent_tokens,
        spent_cost_usd=ledger.spent_cost_usd,
        model_calls=ledger.calls,
        log=log,
    )
    if event_log is not None:
        event_log.append(FlockEvent(kind=RUN_FINISHED, meta={"ok": report.ok}))
    return report
