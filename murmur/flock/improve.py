"""The self-improving layer — generate many workflows, keep the best, learn from it.

Because the model is cheap, for a new task you don't write *one* workflow — you
generate K candidate plans, run them all, score their outputs, and keep the winner
(:func:`best_of_k`). :func:`self_improving_plan` then closes the loop: it first checks
the :class:`~murmur.flock.library.TemplateLibrary` for a proven shape for this kind of
task and reuses it (cheap, no tournament); only on a miss does it run the tournament and
distill the winner back into the library. So the harness gets faster and better at a
task type the more it sees it — a harness that learns which harnesses work.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from murmur.flock.gateway import ModelPort
from murmur.flock.ir import WorkflowPlan
from murmur.flock.library import Template, TemplateLibrary
from murmur.flock.models import ModelResolver
from murmur.flock.planner import DEFAULT_BUDGET, plan_workflow
from murmur.flock.scheduler import RunReport, execute_plan

# A scorer ranks a finished candidate; higher is better.
PlanScorer = Callable[[WorkflowPlan, RunReport], float]

# Style hints that nudge the planner to diversify its candidates.
DEFAULT_STYLES: tuple[str, ...] = (
    "",
    "Favor heavy fan-out and adversarial verification for reliability.",
    "Favor a lean, low-cost pipeline with as few nodes as possible.",
)


def default_scorer(plan: WorkflowPlan, report: RunReport) -> float:
    """A reasonable default: success first, then output volume, fewer contested, cheaper.

    Failed runs score far below any success; among successes, more synthesized output
    and fewer verifier-flagged ("contested") picks win, with a mild penalty for cost.
    """

    if not report.ok:
        return -1000.0 + len(report.results) - len(report.errors)
    content = sum(len(a.content) for a in report.final)
    contested = sum(
        1 for r in report.results.values() for a in r.output if a.meta.get("contested")
    )
    return 1000.0 + min(content, 2000) / 100.0 - contested * 5.0 - report.spent_cost_usd


@dataclass(frozen=True, slots=True)
class Candidate:
    """One generated plan, the run it produced, and its score."""

    plan: WorkflowPlan
    report: RunReport
    score: float


@dataclass(frozen=True, slots=True)
class TournamentResult:
    """The winner plus every candidate, best-first."""

    winner: Candidate
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True, slots=True)
class PlanDecision:
    """Outcome of :func:`self_improving_plan`: a plan and how it was obtained."""

    plan: WorkflowPlan
    origin: str  # "reused" (from the library) or "mined" (won a fresh tournament)
    template: Template | None = None
    tournament: TournamentResult | None = None


def _styles_for(k: int, styles: Sequence[str] | None) -> list[str]:
    chosen = list(styles or DEFAULT_STYLES)
    while len(chosen) < k:
        chosen.append(f"Try a distinct alternative approach #{len(chosen)}.")
    return chosen[:k]


async def best_of_k(
    task: str,
    *,
    model: ModelPort,
    sources: Sequence[str] = (),
    source_values: dict[str, Any] | None = None,
    k: int = 3,
    styles: Sequence[str] | None = None,
    scorer: PlanScorer | None = None,
    resolver: ModelResolver | None = None,
    max_parallel: int = 8,
    budget_tokens: int = DEFAULT_BUDGET,
) -> TournamentResult:
    """Generate *k* candidate plans for *task*, run them all, and rank by *scorer*."""

    scorer = scorer or default_scorer
    source_values = source_values or {}
    hints = _styles_for(k, styles)

    plans: list[WorkflowPlan] = []
    for hint in hints:
        prompt = task if not hint else f"{task}\n\nPlanning preference: {hint}"
        plan = await plan_workflow(
            prompt, model=model, sources=sources, budget_tokens=budget_tokens
        )
        plans.append(replace(plan, goal=task))  # normalize goal back to the real task

    reports = await asyncio.gather(
        *(
            execute_plan(
                p, sources=source_values, resolver=resolver, max_parallel=max_parallel
            )
            for p in plans
        )
    )
    candidates = tuple(
        Candidate(plan=p, report=r, score=scorer(p, r)) for p, r in zip(plans, reports, strict=True)
    )
    ranked = tuple(sorted(candidates, key=lambda c: c.score, reverse=True))
    return TournamentResult(winner=ranked[0], candidates=ranked)


async def self_improving_plan(
    task: str,
    *,
    model: ModelPort,
    library: TemplateLibrary,
    sources: Sequence[str] = (),
    source_values: dict[str, Any] | None = None,
    k: int = 3,
    styles: Sequence[str] | None = None,
    scorer: PlanScorer | None = None,
    resolver: ModelResolver | None = None,
    reuse: bool = True,
    max_parallel: int = 8,
    budget_tokens: int = DEFAULT_BUDGET,
) -> PlanDecision:
    """Reuse a proven template for *task* if one exists; otherwise mine a new one.

    On a library hit the stored plan is returned directly (no tournament). On a miss,
    a :func:`best_of_k` tournament runs and the winner is distilled into the library for
    next time.
    """

    if reuse:
        hit = library.find(task)
        if hit is not None:
            plan = replace(hit.workflow(), goal=task)
            return PlanDecision(plan=plan, origin="reused", template=hit)

    result = await best_of_k(
        task,
        model=model,
        sources=sources,
        source_values=source_values,
        k=k,
        styles=styles,
        scorer=scorer,
        resolver=resolver,
        max_parallel=max_parallel,
        budget_tokens=budget_tokens,
    )
    template = library.add(task=task, plan=result.winner.plan, score=result.winner.score)
    return PlanDecision(
        plan=result.winner.plan, origin="mined", template=template, tournament=result
    )
