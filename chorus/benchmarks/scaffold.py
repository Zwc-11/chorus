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

from collections import Counter
from dataclasses import dataclass

from chorus.adapters.agents.stochastic import stochastic_agent_factory, stochastic_tools
from chorus.adapters.storage.memory import InMemoryEventStore
from chorus.benchmarks.loader import SUITE_VERSION
from chorus.core.conductor import RunConductor
from chorus.core.results import result_from_events
from chorus.core.suite import SuiteResult, TaskReliability
from chorus.core.types import RunResult, TaskSpec


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
