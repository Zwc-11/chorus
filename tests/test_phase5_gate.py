"""Phase 5 regression-gate tests.

Cover the paired-delta statistics (the three verdicts + bootstrap determinism),
the baseline store round-trip, the PR-comment shape, and that the suite runner
produces a reproducible, paired SuiteResult.
"""

from __future__ import annotations

import asyncio

from murmur.adapters.storage.baseline import BaselineStore
from murmur.benchmarks.loader import load_suite
from murmur.benchmarks.scaffold import BASELINE_SCAFFOLD, Scaffold, run_suite
from murmur.core.regression import (
    baseline_set_report,
    bootstrap_delta_ci,
    regression_verdict,
)
from murmur.core.suite import SuiteResult, TaskReliability
from murmur.report.regression_md import render_regression_comment


def _suite(
    branch: str,
    passes_by_task: dict[str, int],
    *,
    n: int = 20,
    failures: dict[str, dict[str, int]] | None = None,
    cost: float = 0.05,
) -> SuiteResult:
    failures = failures or {}
    return SuiteResult(
        suite_version="synthetic-v1",
        branch=branch,
        n=n,
        seed=7,
        seed_policy="per-lane",
        scaffold="test",
        tasks=tuple(
            TaskReliability(
                task_id=task_id,
                n=n,
                passes=passes,
                mean_cost_usd=cost,
                failure_breakdown=failures.get(task_id, {}),
            )
            for task_id, passes in passes_by_task.items()
        ),
    )


def _tasks(passes: int, count: int = 12) -> dict[str, int]:
    return {f"t{i}": passes for i in range(count)}


def test_bootstrap_ci_is_deterministic_and_directional() -> None:
    deltas = [0.1, 0.2, 0.15, 0.12, 0.18, 0.09, 0.14, 0.11]
    first = bootstrap_delta_ci(deltas, seed=0)
    second = bootstrap_delta_ci(deltas, seed=0)
    assert first == second  # seeded -> stable verdict
    assert first[0] > 0  # all-positive deltas -> CI above zero

    negative = bootstrap_delta_ci([-d for d in deltas], seed=0)
    assert negative[1] < 0


def test_regression_verdict_regressed_blocks() -> None:
    baseline = _suite("main", _tasks(18))
    candidate = _suite("main", _tasks(10))
    report = regression_verdict(baseline, candidate, k=1, seed=0)
    assert report.decision == "regressed"
    assert report.blocks is True
    assert report.delta_ci[1] < 0


def test_regression_verdict_improved_does_not_block() -> None:
    baseline = _suite("main", _tasks(10))
    candidate = _suite("main", _tasks(18))
    report = regression_verdict(baseline, candidate, k=1, seed=0)
    assert report.decision == "improved"
    assert report.blocks is False
    assert report.delta_ci[0] > 0


def test_regression_verdict_inconclusive_straddles_zero() -> None:
    # Half the tasks tick up, half tick down by the same amount -> mean ~0.
    baseline = _suite("main", {f"t{i}": 14 for i in range(12)})
    candidate_passes = {f"t{i}": (15 if i % 2 == 0 else 13) for i in range(12)}
    candidate = _suite("main", candidate_passes)
    report = regression_verdict(baseline, candidate, k=1, seed=0)
    assert report.decision == "inconclusive"
    assert report.blocks is False
    assert report.delta_ci[0] <= 0 <= report.delta_ci[1]


def test_baseline_set_report_does_not_block() -> None:
    report = baseline_set_report(_suite("main", _tasks(15)), k=5)
    assert report.decision == "baseline_set"
    assert report.blocks is False


def test_failure_class_delta_surfaces_new_failures() -> None:
    baseline = _suite("main", _tasks(18), failures={"t0": {"tool_error": 1}})
    candidate = _suite(
        "main",
        _tasks(10),
        failures={"t0": {"contract_violation": 4, "tool_error": 2}},
    )
    report = regression_verdict(baseline, candidate, k=1, seed=0)
    assert report.failure_class_delta["contract_violation"] == 4
    assert report.failure_class_delta["tool_error"] == 1


def test_baseline_store_round_trip(tmp_path) -> None:
    store = BaselineStore(tmp_path)
    suite = _suite("feature/x", _tasks(12), n=30)
    assert store.load("feature/x", "synthetic-v1", 30) is None

    store.save(suite)
    loaded = store.load("feature/x", "synthetic-v1", 30)
    assert loaded is not None
    assert loaded.task_ids == suite.task_ids
    assert loaded.tasks[0].passes == suite.tasks[0].passes


def test_pr_comment_shows_verdict_and_breakdown() -> None:
    baseline = _suite("main", _tasks(18), failures={"t0": {"tool_error": 1}})
    candidate = _suite("main", _tasks(9), failures={"t0": {"schema_mismatch": 5}})
    report = regression_verdict(baseline, candidate, k=5, seed=0, baseline_ref="main@abc1234")
    comment = render_regression_comment(report, suite_version="synthetic-v1")

    assert "REGRESSED" in comment
    assert "95% CI" in comment
    assert "schema_mismatch" in comment
    assert "main@abc1234" in comment


def test_suite_runner_is_reproducible_and_paired() -> None:
    tasks = load_suite("synthetic")
    first = asyncio.run(run_suite(tasks, scaffold=BASELINE_SCAFFOLD, n=10, seed=7, branch="main"))
    second = asyncio.run(run_suite(tasks, scaffold=BASELINE_SCAFFOLD, n=10, seed=7, branch="main"))
    assert [t.passes for t in first.tasks] == [t.passes for t in second.tasks]
    assert len(first.tasks) == len(tasks)

    # A strictly better scaffold passes at least as often on every task (paired:
    # identical seeds, only the success threshold moves).
    better = asyncio.run(
        run_suite(
            tasks,
            scaffold=Scaffold("better", success_delta=0.15, error_rate=0.0),
            n=10,
            seed=7,
            branch="main",
        )
    )
    assert all(b.passes >= a.passes for a, b in zip(first.tasks, better.tasks, strict=True))
