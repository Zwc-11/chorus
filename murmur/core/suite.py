"""Suite results: per-task reliability across a task set.

A ``SuiteResult`` is what the regression gate compares. It is a set of per-task
reliability summaries plus the conditions they were measured under (branch, suite
version, N, seed policy, scaffold). Baseline and candidate are both
``SuiteResult``s; the gate compares them paired, task by task, under identical
conditions.

These are pure data types with JSON round-tripping so a baseline can be persisted
and loaded across CI runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from murmur.core.metrics import pass_hat_k_parametric, pass_hat_k_unbiased


@dataclass(frozen=True, slots=True)
class TaskReliability:
    task_id: str
    n: int
    passes: int
    mean_cost_usd: float
    failure_breakdown: dict[str, int]

    @property
    def pass_at_1(self) -> float:
        return self.passes / self.n if self.n else 0.0

    def pass_hat_k(self, k: int) -> float:
        """Projected i.i.d. probability all k runs of this task pass."""

        return pass_hat_k_parametric(self.passes, self.n, k)

    def pass_hat_k_unbiased(self, k: int) -> float:
        return pass_hat_k_unbiased(self.passes, self.n, k)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "n": self.n,
            "passes": self.passes,
            "mean_cost_usd": self.mean_cost_usd,
            "failure_breakdown": dict(self.failure_breakdown),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskReliability:
        return cls(
            task_id=str(data["task_id"]),
            n=int(data["n"]),
            passes=int(data["passes"]),
            mean_cost_usd=float(data.get("mean_cost_usd", 0.0)),
            failure_breakdown=dict(data.get("failure_breakdown", {})),
        )


@dataclass(frozen=True, slots=True)
class SuiteResult:
    suite_version: str
    branch: str
    n: int
    seed: int
    seed_policy: str
    scaffold: str
    tasks: tuple[TaskReliability, ...]
    commit: str = ""

    def task_map(self) -> dict[str, TaskReliability]:
        return {task.task_id: task for task in self.tasks}

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(task.task_id for task in self.tasks)

    def mean_pass_hat_k(self, k: int) -> float:
        if not self.tasks:
            return 0.0
        return sum(task.pass_hat_k(k) for task in self.tasks) / len(self.tasks)

    def mean_cost(self) -> float:
        if not self.tasks:
            return 0.0
        return sum(task.mean_cost_usd for task in self.tasks) / len(self.tasks)

    def failure_totals(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for task in self.tasks:
            for label, count in task.failure_breakdown.items():
                totals[label] = totals.get(label, 0) + count
        return totals

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_version": self.suite_version,
            "branch": self.branch,
            "n": self.n,
            "seed": self.seed,
            "seed_policy": self.seed_policy,
            "scaffold": self.scaffold,
            "commit": self.commit,
            "tasks": [task.to_dict() for task in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SuiteResult:
        return cls(
            suite_version=str(data["suite_version"]),
            branch=str(data["branch"]),
            n=int(data["n"]),
            seed=int(data["seed"]),
            seed_policy=str(data.get("seed_policy", "per-lane")),
            scaffold=str(data.get("scaffold", "")),
            commit=str(data.get("commit", "")),
            tasks=tuple(TaskReliability.from_dict(item) for item in data.get("tasks", [])),
        )
