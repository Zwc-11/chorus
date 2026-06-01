"""Phase 4 diagnosis and contract-boundary tests."""

from __future__ import annotations

import asyncio

from chorus.adapters.storage.memory import InMemoryEventStore
from chorus.core.classify import FailurePolicy, classify_trajectory, validate_classifier
from chorus.core.conductor import RunConductor
from chorus.core.events import Event, EventType
from chorus.core.ports import ToolGatewayPort
from chorus.core.types import StepBoundaryContract, TaskSpec

TASK = TaskSpec(
    task_id="demo.echo_uppercase",
    prompt="hello chorus",
    expected_output="HELLO CHORUS",
)


def run_async(value):
    return asyncio.run(value)


class SchemaMismatchAgent:
    async def run(self, task: TaskSpec, gateway: ToolGatewayPort) -> str:
        await gateway.step(index=0, phase="emit", output_data={"value": 123})
        return task.expected_output or ""


def test_step_boundary_schema_mismatch_fails_and_is_stamped() -> None:
    task = TaskSpec(
        task_id="demo.schema",
        prompt="hello chorus",
        expected_output="HELLO CHORUS",
        step_contracts={
            0: StepBoundaryContract(
                output_schema={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "string"}},
                }
            )
        },
    )
    store = InMemoryEventStore()
    conductor = RunConductor(agent=SchemaMismatchAgent(), storage=store, tools={})

    result = run_async(conductor.run(task, n=1))
    events = list(run_async(store.read_events()))
    checks = [event for event in events if event.type == EventType.CONTRACT_CHECK]

    assert result.verdict == "fail"
    assert result.trajectories[0].failure_class == "schema_mismatch"
    assert result.trajectories[0].failure_step == 0
    assert any(check.payload.get("field") == "$.value" for check in checks)


def test_classifier_uses_priority_order_for_root_cause() -> None:
    events = [
        _event(1, EventType.TRAJECTORY_STARTED, {}),
        _event(2, EventType.STEP_STARTED, {"index": 0}),
        _event(3, EventType.TOOL_RESULT, {"tool": "bash", "error": "exit 1"}),
        _event(
            4,
            EventType.CONTRACT_CHECK,
            {"accepted": False, "step": 0, "field": "$.value", "expected": "string", "got": "int"},
        ),
    ]

    diagnosis = classify_trajectory(events, task=TASK)

    assert diagnosis is not None
    assert diagnosis.cls == "tool_error"
    assert diagnosis.step == 0


def test_timeout_budget_loop_and_unknown_detectors_are_deterministic() -> None:
    timeout = classify_trajectory(
        [
            _event(1, EventType.STEP_STARTED, {"index": 0}),
            _event(2, EventType.MODEL_CALL, {"latency_ms": 5000}),
        ],
        task=TASK,
        policy=FailurePolicy(timeout_ms=100),
    )
    budget = classify_trajectory(
        [_event(1, EventType.TRAJECTORY_FINISHED, {"cost_usd": 9.0})],
        task=TASK,
        policy=FailurePolicy(budget_usd=1.0),
    )
    loop = classify_trajectory(
        [
            _event(1, EventType.STEP_STARTED, {"index": 0}),
            _event(2, EventType.TOOL_CALL, {"tool": "search", "args": {"q": "x"}}),
            _event(3, EventType.STEP_STARTED, {"index": 1}),
            _event(4, EventType.TOOL_CALL, {"tool": "search", "args": {"q": "x"}}),
            _event(5, EventType.STEP_STARTED, {"index": 2}),
            _event(6, EventType.TOOL_CALL, {"tool": "search", "args": {"q": "x"}}),
        ],
        task=TASK,
        policy=FailurePolicy(loop_threshold=2),
    )
    unknown = classify_trajectory([], task=TASK)

    assert timeout is not None and timeout.cls == "timeout"
    assert budget is not None and budget.cls == "budget_exceeded"
    assert loop is not None and loop.cls == "nondeterministic_loop"
    assert unknown is not None and unknown.cls == "unknown"


def test_classifier_validation_reports_precision_recall_f1() -> None:
    fixtures = {
        "tool_error": [
            _event(1, EventType.STEP_STARTED, {"index": 0}),
            _event(2, EventType.TOOL_RESULT, {"tool": "bash", "error": "exit 1"}),
        ],
        "schema_mismatch": [
            _event(1, EventType.CONTRACT_CHECK, _schema_failure_payload()),
        ],
        "contract_violation": [
            _event(1, EventType.CONTRACT_CHECK, {"accepted": False, "step": 3}),
        ],
    }

    report = validate_classifier(fixtures, task=TASK)

    assert report.precision["tool_error"] == 1.0
    assert report.recall["schema_mismatch"] == 1.0
    assert report.f1["contract_violation"] == 1.0
    assert report.confusion["schema_mismatch"]["schema_mismatch"] == 1


def _schema_failure_payload() -> dict[str, object]:
    return {
        "accepted": False,
        "step": 2,
        "side": "output",
        "field": "$.value",
        "expected": "string",
        "got": "int",
    }


def _event(seq: int, event_type: EventType, payload: dict[str, object]) -> Event:
    return Event.create(
        run_id="run_diag",
        trajectory_id="run_diag_t1",
        seq=seq,
        event_type=event_type,
        payload=payload,
    )
