"""The benchmark runner: attempts -> outcomes -> SuiteResult.

Runs a scaffold against the task set N times, evaluates every attempt's patch, and
folds the resolved/not outcomes into the same ``SuiteResult`` the regression gate
consumes -- so the headline number and the CI gate share one code path and one set
of estimators. Holding the model and tasks fixed and swapping only the scaffold is
the whole experiment; :func:`compare_scaffolds` reuses the paired-delta gate to
say whether the harness change reliably moved ``pass^k``.
"""

from __future__ import annotations

from collections import Counter

from chorus.benchmarks.swe.types import (
    PatchModel,
    SweEvaluator,
    SwePrediction,
    SweScaffold,
)
from chorus.core.regression import RegressionReport, regression_verdict
from chorus.core.suite import SuiteResult, TaskReliability
from chorus.core.types import TaskSpec

SEED_STRIDE = 1000  # keep per-(attempt, task) seeds from colliding


def _attempt_seed(base: int, attempt: int, task_index: int) -> int:
    return base + attempt * SEED_STRIDE + task_index


def run_scaffold(
    tasks: list[TaskSpec],
    *,
    scaffold: SweScaffold,
    model: PatchModel,
    evaluator: SweEvaluator,
    n: int,
    seed: int = 0,
    branch: str = "bench",
    suite_version: str = "swe-bench-verified",
    commit: str = "",
) -> SuiteResult:
    """Run ``scaffold`` ×N over ``tasks`` and fold outcomes into a SuiteResult."""

    passes: dict[str, int] = {task.task_id: 0 for task in tasks}
    cost_total: dict[str, float] = {task.task_id: 0.0 for task in tasks}
    categories: dict[str, Counter[str]] = {task.task_id: Counter() for task in tasks}

    for attempt in range(n):
        predictions: list[SwePrediction] = []
        for index, task in enumerate(tasks):
            out = scaffold.run(task, model, seed=_attempt_seed(seed, attempt, index))
            cost_total[task.task_id] += out.cost_usd
            predictions.append(SwePrediction(task.task_id, out.patch, out.cost_usd))
        outcomes = evaluator.evaluate(predictions, run_id=f"{scaffold.name}__a{attempt}")
        for task in tasks:
            outcome = outcomes.get(task.task_id)
            if outcome is not None and outcome.resolved:
                passes[task.task_id] += 1
            else:
                categories[task.task_id][outcome.category if outcome else "eval_error"] += 1

    reliabilities = tuple(
        TaskReliability(
            task_id=task.task_id,
            n=n,
            passes=passes[task.task_id],
            mean_cost_usd=cost_total[task.task_id] / n if n else 0.0,
            failure_breakdown=dict(categories[task.task_id]),
        )
        for task in tasks
    )
    return SuiteResult(
        suite_version=suite_version,
        branch=branch,
        n=n,
        seed=seed,
        seed_policy="per-attempt",
        scaffold=scaffold.name,
        commit=commit,
        tasks=reliabilities,
    )


def compare_scaffolds(
    reference: SuiteResult, candidate: SuiteResult, *, k: int = 5, boot_seed: int = 0
) -> RegressionReport:
    """Paired-delta verdict for a harness-only change (candidate scaffold vs reference)."""

    return regression_verdict(
        reference,
        candidate,
        k=k,
        seed=boot_seed,
        baseline_ref=f"scaffold:{reference.scaffold}",
    )
