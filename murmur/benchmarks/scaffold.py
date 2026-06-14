"""Scaffolds and the suite runner.

A ``Scaffold`` is the harness/agent strategy under test -- the only thing the
headline comparison varies while holding tasks, N, and seed policy constant.
``run_suite`` runs each task N times under a scaffold and folds the recorded
events into a ``SuiteResult`` the regression gate can compare.

The pairing is exact: baseline and candidate use the same per-task seeds, so a
trajectory draws the identical random sequence under both scaffolds and only the
scaffold's success threshold differs. The delta is therefore attributable to the
scaffold alone -- the whole point of "changing only the harness."
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from murmur.adapters.agents.stochastic import stochastic_agent_factory, stochastic_tools
from murmur.adapters.storage.memory import InMemoryEventStore
from murmur.benchmarks.loader import SUITE_VERSION
from murmur.benchmarks.swe.types import PrimableJudge, SwePrediction
from murmur.core.conductor import RunConductor
from murmur.core.events import Event
from murmur.core.ports import AgentPort, JudgePort
from murmur.core.results import result_from_events
from murmur.core.suite import SuiteResult, TaskReliability
from murmur.core.types import RunResult, TaskSpec
from murmur.gateway.tool_gateway import ToolCallable

SEED_STRIDE = 1000  # keep per-(task, lane) seeds from colliding


@dataclass(frozen=True, slots=True)
class Scaffold:
    """An agent strategy. ``success_delta`` shifts every task's base difficulty;
    ``error_rate`` is its flaky-tool rate. Two scaffolds that differ only here model
    a harness-only change."""

    name: str
    success_delta: float = 0.0
    error_rate: float = 0.08

    def success_rate_for(self, task: TaskSpec) -> float:
        base = float(task.metadata.get("difficulty", 0.7))
        return max(0.0, min(1.0, base + self.success_delta))


BASELINE_SCAFFOLD = Scaffold(name="baseline", success_delta=0.0, error_rate=0.08)


async def run_suite(
    tasks: list[TaskSpec],
    *,
    scaffold: Scaffold,
    n: int,
    seed: int,
    branch: str,
    commit: str = "",
    suite_version: str = SUITE_VERSION,
    seed_policy: str = "per-lane",
) -> SuiteResult:
    reliabilities: list[TaskReliability] = []
    for index, task in enumerate(tasks):
        factory = stochastic_agent_factory(
            success_rate=scaffold.success_rate_for(task),
            error_rate=scaffold.error_rate,
            base_seed=seed + index * 100,
        )
        conductor = RunConductor(
            agent_factory=factory,
            storage=(store := InMemoryEventStore()),
            tools=stochastic_tools(),
        )
        await conductor.run(task, n=n)
        events = list(await store.read_events())
        result = result_from_events(events, task_id=task.task_id)
        reliabilities.append(_task_reliability(task.task_id, result))

    return SuiteResult(
        suite_version=suite_version,
        branch=branch,
        n=n,
        seed=seed,
        seed_policy=seed_policy,
        scaffold=scaffold.name,
        commit=commit,
        tasks=tuple(reliabilities),
    )


async def run_judged_suite(
    tasks: list[TaskSpec],
    *,
    agent_factory: Callable[[int], AgentPort],
    judge: JudgePort | None = None,
    n: int,
    seed: int,
    branch: str,
    suite_version: str,
    scaffold: str,
    tools: dict[str, ToolCallable] | None = None,
    commit: str = "",
    concurrent: bool = False,
) -> SuiteResult:
    """Real-suite path: run an arbitrary ``AgentPort`` ×N per task, judged by a real
    ``JudgePort``, and fold the outcomes into the same ``SuiteResult`` the gate uses.

    ``agent_factory(seed)`` builds one agent for one trajectory; every lane gets a
    distinct seed so attempts are independent. Unlike :func:`run_suite` (which is
    wired to the stochastic agent) this drives the conductor, so a real SWE-bench
    run is recorded as events and inherits tracing, replay, and diagnosis. It
    defaults to ``concurrent=False`` because a real judge (the SWE-bench Docker
    harness) should not be invoked from many lanes at once.
    """

    reliabilities: list[TaskReliability] = []
    for task_index, task in enumerate(tasks):
        def lane_factory(lane: int, task_index: int = task_index) -> AgentPort:
            return agent_factory(seed + task_index * SEED_STRIDE + lane)

        conductor = RunConductor(
            agent_factory=lane_factory,
            storage=InMemoryEventStore(),
            tools=tools or {},
            judge=judge,
            concurrent=concurrent,
        )
        result = await conductor.run(task, n=n)
        reliabilities.append(_task_reliability(task.task_id, result))

    return SuiteResult(
        suite_version=suite_version,
        branch=branch,
        n=n,
        seed=seed,
        seed_policy="per-lane",
        scaffold=scaffold,
        commit=commit,
        tasks=tuple(reliabilities),
    )


@dataclass(frozen=True, slots=True)
class _TaskCtx:
    task: TaskSpec
    conductor: RunConductor
    store: InMemoryEventStore
    run_id: str


@dataclass(frozen=True, slots=True)
class JudgedRun:
    """A batched judged-suite result plus the recorded events for the trace view."""

    suite: SuiteResult
    events: dict[str, list[Event]]  # task_id -> recorded events


async def run_judged_suite_batched(
    tasks: list[TaskSpec],
    *,
    agent_factory: Callable[[int], AgentPort],
    judge: PrimableJudge,
    n: int,
    seed: int,
    branch: str,
    suite_version: str,
    scaffold: str,
    tools: dict[str, ToolCallable] | None = None,
    commit: str = "",
    concurrent_agents: bool = True,
) -> JudgedRun:
    """Traced **and** batched: run every agent through the conductor (so each
    trajectory is recorded), but evaluate each attempt's patches across all
    instances in one harness run, then finalize from the primed cache.

    This is the reconciliation of the two earlier paths: it keeps the trace/replay/
    diagnosis of the integrated path and the per-attempt batch parallelism of
    ``murmur bench``. Judging batches *across instances within an attempt* (one
    patch per instance per harness run) -- the only shape the SWE-bench harness
    accepts, since it keys predictions by ``instance_id``.
    """

    contexts: list[_TaskCtx] = []
    for task_index, task in enumerate(tasks):
        def lane_factory(lane: int, task_index: int = task_index) -> AgentPort:
            return agent_factory(seed + task_index * SEED_STRIDE + lane)

        store = InMemoryEventStore()
        conductor = RunConductor(
            agent_factory=lane_factory, storage=store, tools=tools or {}, judge=judge,
            concurrent=False,
        )
        run_id = await conductor.begin_run(task, n)
        contexts.append(_TaskCtx(task, conductor, store, run_id))

    for attempt in range(n):
        # Phase 1: run each task's agent for this attempt; the trace is recorded.
        coros = [
            ctx.conductor.run_agent_deferred(ctx.task, ctx.run_id, attempt) for ctx in contexts
        ]
        if concurrent_agents:
            pendings = list(await asyncio.gather(*coros))
        else:
            pendings = [await coro for coro in coros]
        # Phase 2: evaluate this attempt's patches across all instances in one run.
        predictions = [
            SwePrediction(ctx.task.task_id, pending.output)
            for ctx, pending in zip(contexts, pendings, strict=True)
            if not pending.error
        ]
        if predictions:
            judge.prime(predictions, run_id=f"{scaffold}-a{attempt}")
        # Phase 3: finalize each trajectory from the now-primed cache.
        for ctx, pending in zip(contexts, pendings, strict=True):
            outcome = "error" if pending.error else await judge.judge(ctx.task, pending.output)
            await ctx.conductor.finalize_trajectory(ctx.task, pending, outcome)

    reliabilities: list[TaskReliability] = []
    events_by_task: dict[str, list[Event]] = {}
    for ctx in contexts:
        result = await ctx.conductor.complete_run(ctx.task, ctx.run_id)
        reliabilities.append(_task_reliability(ctx.task.task_id, result))
        events_by_task[ctx.task.task_id] = list(await ctx.store.read_events())

    suite = SuiteResult(
        suite_version=suite_version,
        branch=branch,
        n=n,
        seed=seed,
        seed_policy="per-attempt",
        scaffold=scaffold,
        commit=commit,
        tasks=tuple(reliabilities),
    )
    return JudgedRun(suite=suite, events=events_by_task)


def _task_reliability(task_id: str, result: RunResult) -> TaskReliability:
    passes = sum(1 for trajectory in result.trajectories if trajectory.outcome == "pass")
    failures = Counter(
        trajectory.failure_class or trajectory.outcome
        for trajectory in result.trajectories
        if trajectory.outcome != "pass"
    )
    return TaskReliability(
        task_id=task_id,
        n=len(result.trajectories),
        passes=passes,
        mean_cost_usd=result.metrics.mean_cost,
        failure_breakdown=dict(failures),
    )
