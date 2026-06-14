"""Phase 3 judgment cascade tests."""

from __future__ import annotations

import asyncio

from murmur.adapters.storage.memory import InMemoryEventStore
from murmur.core.events import EventRecorder, EventType
from murmur.core.judge import (
    JudgeCallCache,
    JudgePolicy,
    LabelledJudgeCase,
    cached_llm_judge,
    cohen_kappa,
    judge_run,
    measure_judge_cost,
)
from murmur.core.metrics import reliability_metrics
from murmur.core.types import RunResult, TaskSpec, TrajectoryResult
from murmur.gateway.tool_gateway import ToolGateway

TASK = TaskSpec(
    task_id="demo.echo_uppercase",
    prompt="hello chorus",
    expected_output="HELLO CHORUS",
)


def run_async(value):
    return asyncio.run(value)


def test_cascade_resolves_converged_run_without_tier2() -> None:
    result = _run(
        _trajectory("t1", "pass", "HELLO CHORUS"),
        _trajectory("t2", "pass", "HELLO CHORUS"),
    )

    judgment = judge_run(result, TASK, policy=JudgePolicy(tier2_cost_usd=0.25))

    assert judgment.verdict == "pass"
    assert judgment.resolved_tier == 1
    assert judgment.tier2_calls == 0
    assert judgment.cost_ratio == 0.0


def test_cascade_judges_only_unknown_and_minority() -> None:
    result = _run(
        _trajectory("t1", "pass", "HELLO CHORUS"),
        _trajectory("t2", "fail", "wrong"),
        _trajectory("t3", "error", "tool exploded"),
    )

    judgment = judge_run(result, TASK, policy=JudgePolicy(tier2_cost_usd=0.5))

    assert judgment.verdict == "fail"
    assert judgment.resolved_tier == 2
    assert judgment.tier2_calls == 2
    assert judgment.cascade_cost_usd == 1.0
    assert judgment.baseline_cost_usd == 1.5
    assert judgment.escalation_trace[0]["to"] == "ESCALATE"


def test_cost_measurement_reports_accuracy_parity() -> None:
    pass_case = LabelledJudgeCase(
        result=_run(_trajectory("t1", "pass", "HELLO CHORUS")),
        task=TASK,
        ground_truth="pass",
    )
    fail_case = LabelledJudgeCase(
        result=_run(_trajectory("t1", "fail", "wrong")),
        task=TASK,
        ground_truth="fail",
    )

    report = measure_judge_cost([pass_case, fail_case], policy=JudgePolicy(tier2_cost_usd=1.0))

    assert report.baseline_accuracy == 1.0
    assert report.cascade_accuracy == 1.0
    assert report.accuracy_delta == 0.0
    assert report.cost_ratio == 0.0


def test_judge_call_cache_records_once_through_gateway() -> None:
    store = InMemoryEventStore()
    recorder = EventRecorder(store, "run_judge", "run_judge_t1")
    gateway = ToolGateway.record(recorder=recorder, tools={})
    cache = JudgeCallCache()
    trajectory = _trajectory("t1", "fail", "wrong")

    first = run_async(
        cached_llm_judge(
            task=TASK,
            trajectory=trajectory,
            rubric="exact expected output",
            model="judge-model",
            cache=cache,
            evaluator=lambda task, item: "pass" if task.accepts(item.output) else "fail",
            gateway=gateway,
        )
    )
    second = run_async(
        cached_llm_judge(
            task=TASK,
            trajectory=trajectory,
            rubric="exact expected output",
            model="judge-model",
            cache=cache,
            evaluator=lambda task, item: "pass" if task.accepts(item.output) else "fail",
            gateway=gateway,
        )
    )
    events = list(run_async(store.read_events()))

    assert first == second == "fail"
    assert cache.calls == 1
    assert [event.type for event in events].count(EventType.MODEL_CALL) == 1


def test_cohen_kappa_reports_chance_corrected_agreement() -> None:
    value = cohen_kappa(["pass", "fail", "pass"], ["pass", "fail", "fail"])
    assert abs(value - 0.4) < 1e-9


def _trajectory(trajectory_id: str, outcome: str, output: str) -> TrajectoryResult:
    return TrajectoryResult(
        trajectory_id=trajectory_id,
        outcome=outcome,  # type: ignore[arg-type]
        output=output,
        failure_class=None if outcome == "pass" else "contract_violation",
        cost_usd=0.0,
        latency_ms=0.0,
    )


def _run(*trajectories: TrajectoryResult) -> RunResult:
    return RunResult(
        run_id="run_test",
        task_id=TASK.task_id,
        trajectories=trajectories,
        metrics=reliability_metrics(trajectories),
        escalations=0,
        verdict="pass" if all(item.outcome == "pass" for item in trajectories) else "fail",
    )
