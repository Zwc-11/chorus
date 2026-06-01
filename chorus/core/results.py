"""Build run results from the append-only event log."""

from __future__ import annotations

from chorus.core.divergence import group_trajectory_events
from chorus.core.events import Event, EventType
from chorus.core.metrics import reliability_metrics
from chorus.core.types import RunResult, TrajectoryResult


def result_from_events(
    events: list[Event],
    *,
    run_id: str | None = None,
    task_id: str | None = None,
) -> RunResult:
    """Project the event log into the aggregate result shown by reports."""

    if run_id is None:
        run_id = _run_id(events)
    if task_id is None:
        task_id = _task_id(events)
    trajectories = tuple(
        _trajectory_result(trajectory_id, trajectory_events)
        for trajectory_id, trajectory_events in group_trajectory_events(events).items()
    )
    metrics = reliability_metrics(trajectories)
    verdict = "pass" if trajectories and all(t.outcome == "pass" for t in trajectories) else "fail"
    return RunResult(
        run_id=run_id,
        task_id=task_id,
        trajectories=trajectories,
        metrics=metrics,
        escalations=0,
        verdict=verdict,
    )


def _run_id(events: list[Event]) -> str:
    return events[0].run_id if events else "run_empty"


def _task_id(events: list[Event]) -> str:
    for event in events:
        if "task_id" in event.payload:
            return str(event.payload["task_id"])
    return "unknown"


def _trajectory_result(trajectory_id: str, events: list[Event]) -> TrajectoryResult:
    outcome = "error"
    output = ""
    failure_class = None
    failure_step = None
    failure_detail = None
    failure_confidence = None
    cost_usd = 0.0
    latency_ms = 0.0

    for event in events:
        if event.type == EventType.VERDICT:
            outcome = str(event.payload.get("outcome", outcome))
            output = str(event.payload.get("output", ""))
            failure_class = event.payload.get("failure_class")
            failure_step = event.payload.get("failure_step")
            failure_detail = event.payload.get("failure_detail")
            failure_confidence = event.payload.get("failure_confidence")
        elif event.type == EventType.TRAJECTORY_FINISHED:
            outcome = str(event.payload.get("outcome", outcome))
            cost_usd = float(event.payload.get("cost_usd", 0.0))
            latency_ms = float(event.payload.get("latency_ms", 0.0))

    return TrajectoryResult(
        trajectory_id=trajectory_id,
        outcome=outcome,  # type: ignore[arg-type]
        output=output,
        failure_class=str(failure_class) if failure_class is not None else None,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        failure_step=int(failure_step) if failure_step is not None else None,
        failure_detail=str(failure_detail) if failure_detail is not None else None,
        failure_confidence=float(failure_confidence) if failure_confidence is not None else None,
    )
