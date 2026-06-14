"""Phase 2 distribution tests.

These tests prove the harness turns many trajectories into a real,
distribution-aware result: a reproducible spread of pass/fail/error outcomes,
correct pass@1 vs pass^k math, and a fan view that reflects the run.
"""

from __future__ import annotations

import asyncio

from murmur.adapters.agents.stochastic import (
    stochastic_agent_factory,
    stochastic_tools,
)
from murmur.adapters.storage.memory import InMemoryEventStore
from murmur.core.conductor import RunConductor
from murmur.core.divergence import build_divergence_overlay, group_trajectory_events
from murmur.core.events import Event, EventType
from murmur.core.metrics import (
    pass_hat_k_unbiased,
    reliability_curve,
    reliability_metrics,
    wilson_interval,
)
from murmur.core.results import result_from_events
from murmur.core.types import ReliabilityMetrics, RunResult, TaskSpec, TrajectoryResult
from murmur.report.fan import render_fan

TASK = TaskSpec(
    task_id="demo.echo_uppercase",
    prompt="hello chorus",
    expected_output="HELLO CHORUS",
)


def run_async(value):
    return asyncio.run(value)


def build_conductor(*, success_rate: float, error_rate: float, seed: int = 7) -> RunConductor:
    return RunConductor(
        agent_factory=stochastic_agent_factory(
            success_rate=success_rate, error_rate=error_rate, base_seed=seed
        ),
        storage=InMemoryEventStore(),
        tools=stochastic_tools(),
    )


def _trajectory(outcome: str) -> TrajectoryResult:
    return TrajectoryResult(
        trajectory_id="t",
        outcome=outcome,
        output="",
        failure_class=None if outcome == "pass" else "fail",
        cost_usd=0.0,
        latency_ms=0.0,
    )


def test_pass_at_k_is_per_run_rate_to_the_k() -> None:
    # 3 of 4 pass -> pass@1 = 0.75, pass^4 = 0.75**4.
    metrics = reliability_metrics(tuple(_trajectory(o) for o in ["pass", "pass", "pass", "fail"]))
    assert metrics.pass_at_1 == 0.75
    assert metrics.k == 4
    assert abs(metrics.pass_at_k - 0.75**4) < 1e-9
    assert abs(metrics.variance - 0.75 * 0.25) < 1e-9


def test_pass_at_k_horizon_is_overridable() -> None:
    metrics = reliability_metrics(tuple(_trajectory(o) for o in ["pass", "fail"]), k=10)
    assert metrics.k == 10
    assert abs(metrics.pass_at_k - 0.5**10) < 1e-12


def test_unbiased_pass_hat_k_drops_when_data_cannot_support_k() -> None:
    assert pass_hat_k_unbiased(3, 4, 2) == 0.5
    assert pass_hat_k_unbiased(3, 4, 4) == 0.0

    curve = reliability_curve(3, 4)
    assert curve[-1].k == 4
    assert curve[-1].projected == 0.75**4
    assert curve[-1].empirical == 0.0


def test_wilson_interval_widens_for_fewer_samples() -> None:
    narrow = wilson_interval(75, 100)
    wide = wilson_interval(3, 4)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_distribution_is_reproducible_for_a_seed() -> None:
    first = run_async(build_conductor(success_rate=0.7, error_rate=0.1).run(TASK, n=24))
    second = run_async(build_conductor(success_rate=0.7, error_rate=0.1).run(TASK, n=24))

    outcomes_first = [t.outcome for t in first.trajectories]
    outcomes_second = [t.outcome for t in second.trajectories]
    assert outcomes_first == outcomes_second
    assert first.metrics.pass_at_1 == second.metrics.pass_at_1
    assert first.metrics.curve == second.metrics.curve


def test_run_produces_a_spread_not_a_constant() -> None:
    result = run_async(build_conductor(success_rate=0.6, error_rate=0.15).run(TASK, n=40))
    outcomes = {t.outcome for t in result.trajectories}
    # A real distribution: more than one outcome class appears.
    assert len(outcomes) > 1
    assert 0.0 < result.metrics.pass_at_1 < 1.0
    assert result.metrics.variance > 0.0
    # pass^k must compound below pass@1 once the agent is flaky.
    assert result.metrics.pass_at_k < result.metrics.pass_at_1
    assert result.metrics.pass_at_k_unbiased <= result.metrics.pass_at_k


def test_run_result_is_derived_from_recorded_events() -> None:
    store = InMemoryEventStore()
    conductor = RunConductor(
        agent_factory=stochastic_agent_factory(success_rate=0.7, error_rate=0.1, base_seed=7),
        storage=store,
        tools=stochastic_tools(),
    )
    live = run_async(conductor.run(TASK, n=12))
    events = list(run_async(store.read_events()))
    projected = result_from_events(events, run_id=live.run_id, task_id=TASK.task_id)

    assert [t.outcome for t in projected.trajectories] == [t.outcome for t in live.trajectories]
    assert projected.metrics.pass_at_1 == live.metrics.pass_at_1
    assert projected.metrics.curve == live.metrics.curve


def test_divergence_overlay_detects_first_split() -> None:
    events = [
        _event("run_1", "run_1_t1", 1, EventType.TRAJECTORY_STARTED, {"index": 0}),
        _event("run_1", "run_1_t1", 2, EventType.STEP_STARTED, {"index": 0}),
        _event("run_1", "run_1_t1", 3, EventType.TOOL_CALL, {"tool": "read", "args": {}}),
        _event("run_1", "run_1_t1", 4, EventType.STEP_STARTED, {"index": 1}),
        _event("run_1", "run_1_t1", 5, EventType.TOOL_CALL, {"tool": "edit", "args": {}}),
        _event("run_1", "run_1_t2", 1, EventType.TRAJECTORY_STARTED, {"index": 1}),
        _event("run_1", "run_1_t2", 2, EventType.STEP_STARTED, {"index": 0}),
        _event("run_1", "run_1_t2", 3, EventType.TOOL_CALL, {"tool": "read", "args": {}}),
        _event("run_1", "run_1_t2", 4, EventType.STEP_STARTED, {"index": 1}),
        _event("run_1", "run_1_t2", 5, EventType.TOOL_CALL, {"tool": "bash", "args": {}}),
    ]

    overlay = build_divergence_overlay(events)
    grouped = group_trajectory_events(events)

    assert overlay.agreement == (1.0, 0.5)
    assert overlay.divergence_step == 1
    assert overlay.low_confidence is True
    assert {cell.state for cell in overlay.cells if cell.step == 1} == {"converged", "diverged"}
    assert grouped["run_1_t1"][0].trajectory_id == "run_1_t1"


def test_error_branch_is_classified_and_costs_accrue() -> None:
    result = run_async(build_conductor(success_rate=0.5, error_rate=0.4).run(TASK, n=30))
    errors = [t for t in result.trajectories if t.outcome == "error"]
    assert errors, "expected some flaky-tool errors at error_rate=0.4"
    assert all(t.failure_class == "tool_error" for t in errors)
    # Cost is derived from recorded tool calls, so every trajectory cost > 0.
    assert all(t.cost_usd > 0.0 for t in result.trajectories)
    assert result.metrics.mean_cost > 0.0


def test_fan_view_reflects_outcomes() -> None:
    metrics = ReliabilityMetrics(
        pass_at_1=0.5,
        pass_at_k=0.25,
        k=2,
        variance=0.25,
        wilson_ci=(0.1, 0.9),
        mean_cost=0.01,
        p50_latency_ms=10.0,
        p95_latency_ms=20.0,
    )
    result = RunResult(
        run_id="run_demo",
        task_id="demo.echo_uppercase",
        trajectories=(_trajectory("pass"), _trajectory("fail")),
        metrics=metrics,
        escalations=0,
        verdict="fail",
    )
    rendered = render_fan(result, color=False, ascii_only=True)
    assert "pass@1" in rendered
    assert "pass^k" in rendered
    assert "run_demo" in rendered


def _event(
    run_id: str,
    trajectory_id: str,
    seq: int,
    event_type: EventType,
    payload: dict,
) -> Event:
    return Event.create(
        run_id=run_id,
        trajectory_id=trajectory_id,
        seq=seq,
        event_type=event_type,
        payload=payload,
    )
