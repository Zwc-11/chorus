"""Paired-delta regression statistics (the core of the CI gate).

Baseline and candidate run the *same* tasks under the *same* conditions, so the
comparison is paired per task. We bootstrap a confidence interval on the mean
per-task ``pass^k`` delta and return one of three verdicts:

* ``regressed``    -- the whole 95% CI on (candidate - baseline) is below zero.
* ``improved``     -- the whole CI is above zero.
* ``inconclusive`` -- the CI straddles zero; the gate does **not** block.

The third verdict is the point: with N=30 and Wilson CIs like [0.63, 0.90],
run-to-run noise moves ``pass^k`` constantly. Blocking on every dip trains a team
to ignore the gate. Blocking only when a regression is statistically real -- and
saying "inconclusive, widen N" otherwise -- is what makes the gate survive
contact with a real team. The bootstrap is seeded so the same inputs always give
the same verdict; a CI gate that flickers is worse than none.
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

from chorus.core.suite import SuiteResult

DEFAULT_K = 5
DEFAULT_N_BOOT = 10_000


@dataclass(frozen=True, slots=True)
class TaskDelta:
    task_id: str
    baseline_pass_k: float
    candidate_pass_k: float
    delta: float


@dataclass(frozen=True, slots=True)
class RegressionReport:
    decision: str  # regressed | improved | inconclusive | baseline_set
    k: int
    n_tasks: int
    n: int
    seed_policy: str
    mean_delta: float
    delta_ci: tuple[float, float]
    baseline_pass_k: float
    candidate_pass_k: float
    baseline_cost: float
    candidate_cost: float
    per_task: tuple[TaskDelta, ...]
    failure_class_delta: dict[str, int]
    top_regressed: tuple[str, ...]
    baseline_ref: str = ""

    @property
    def blocks(self) -> bool:
        """Only a statistically real regression blocks the PR."""

        return self.decision == "regressed"

    @property
    def cost_delta(self) -> float:
        return self.candidate_cost - self.baseline_cost


def bootstrap_delta_ci(
    deltas: list[float],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Distribution-free CI on the mean of paired deltas via the seeded bootstrap."""

    n = len(deltas)
    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        return (deltas[0], deltas[0])
    rng = Random(seed)
    means: list[float] = []
    for _ in range(n_boot):
        total = 0.0
        for _ in range(n):
            total += deltas[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    lo_index = int((alpha / 2) * n_boot)
    hi_index = min(n_boot - 1, int((1 - alpha / 2) * n_boot) - 1)
    return (means[lo_index], means[hi_index])


def regression_verdict(
    baseline: SuiteResult,
    candidate: SuiteResult,
    *,
    k: int = DEFAULT_K,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = 0,
    baseline_ref: str = "",
) -> RegressionReport:
    """Compare candidate against baseline on shared tasks and return a verdict."""

    shared = sorted(set(baseline.task_ids) & set(candidate.task_ids))
    baseline_map = baseline.task_map()
    candidate_map = candidate.task_map()

    per_task: list[TaskDelta] = []
    deltas: list[float] = []
    for task_id in shared:
        base = baseline_map[task_id].pass_hat_k(k)
        cand = candidate_map[task_id].pass_hat_k(k)
        per_task.append(TaskDelta(task_id, base, cand, cand - base))
        deltas.append(cand - base)

    lo, hi = bootstrap_delta_ci(deltas, n_boot=n_boot, seed=seed)
    if not shared:
        decision = "inconclusive"
    elif hi < 0:
        decision = "regressed"
    elif lo > 0:
        decision = "improved"
    else:
        decision = "inconclusive"

    top_regressed = tuple(
        item.task_id for item in sorted(per_task, key=lambda d: d.delta) if item.delta < 0
    )[:3]

    return RegressionReport(
        decision=decision,
        k=k,
        n_tasks=len(shared),
        n=candidate.n,
        seed_policy=candidate.seed_policy,
        mean_delta=sum(deltas) / len(deltas) if deltas else 0.0,
        delta_ci=(lo, hi),
        baseline_pass_k=_mean(item.baseline_pass_k for item in per_task),
        candidate_pass_k=_mean(item.candidate_pass_k for item in per_task),
        baseline_cost=baseline.mean_cost(),
        candidate_cost=candidate.mean_cost(),
        per_task=tuple(per_task),
        failure_class_delta=_failure_delta(baseline.failure_totals(), candidate.failure_totals()),
        top_regressed=top_regressed,
        baseline_ref=baseline_ref,
    )


def baseline_set_report(candidate: SuiteResult, k: int = DEFAULT_K) -> RegressionReport:
    """Verdict for the first run on a branch: record as baseline, do not block."""

    return RegressionReport(
        decision="baseline_set",
        k=k,
        n_tasks=len(candidate.tasks),
        n=candidate.n,
        seed_policy=candidate.seed_policy,
        mean_delta=0.0,
        delta_ci=(0.0, 0.0),
        baseline_pass_k=candidate.mean_pass_hat_k(k),
        candidate_pass_k=candidate.mean_pass_hat_k(k),
        baseline_cost=candidate.mean_cost(),
        candidate_cost=candidate.mean_cost(),
        per_task=(),
        failure_class_delta={},
        top_regressed=(),
    )


def _failure_delta(baseline: dict[str, int], candidate: dict[str, int]) -> dict[str, int]:
    delta: dict[str, int] = {}
    for label in set(baseline) | set(candidate):
        diff = candidate.get(label, 0) - baseline.get(label, 0)
        if diff:
            delta[label] = diff
    return dict(sorted(delta.items(), key=lambda item: item[1], reverse=True))


def _mean(values) -> float:
    collected = list(values)
    return sum(collected) / len(collected) if collected else 0.0
