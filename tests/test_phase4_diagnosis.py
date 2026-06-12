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


class BadWebsiteAgent:
    async def run(self, task: TaskSpec, gateway: ToolGatewayPort) -> str:
        await gateway.step(index=0, phase="write")
        return (
            '<!DOCTYPE html><html><body><h1>chorus</h1><section id="metrics">'
            "pass@1 pass@k variance</section></body></html>"
            "<style>:root{--accent:#e8192a}</style>"
        )


def test_final_contract_check_carries_structured_diagnostics() -> None:
    from chorus.core.agent_tasks import hard_website_task

    store = InMemoryEventStore()
    conductor = RunConductor(agent=BadWebsiteAgent(), storage=store, tools={})
    result = run_async(conductor.run(hard_website_task(), n=1))
    events = list(run_async(store.read_events()))
    final_check = [
        event
        for event in events
        if event.type == EventType.CONTRACT_CHECK and event.payload.get("diagnostic_ids")
    ][0]

    assert result.trajectories[0].failure_class == "contract_violation"
    assert result.trajectories[0].failure_detail == (
        "failed predicates: missing_metric_pass_hat_k"
    )
    assert final_check.payload["diagnostic_ids"] == ["missing_metric_pass_hat_k"]
    assert "missing_metric_pass_hat_k" in final_check.payload["repair_feedback"]


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


def _tool_step(step: int, base_seq: int, tool: str, args: dict, result_hash: str) -> list[Event]:
    return [
        _event(base_seq, EventType.STEP_STARTED, {"index": step}),
        _event(base_seq + 1, EventType.TOOL_CALL, {"tool": tool, "args": args}),
        _event(base_seq + 2, EventType.TOOL_RESULT, {"tool": tool, "result_hash": result_hash}),
    ]


def _revisits_then_violates_contract() -> list[Event]:
    """A contract violation whose trace legitimately revisits read_file three
    times, non-consecutively -- ordinary iteration, not a loop."""

    events: list[Event] = [_event(1, EventType.TRAJECTORY_STARTED, {})]
    seq = 2
    plan = [
        ("read_file", {"path": "a"}, "ha"),
        ("bash", {"command": "pytest"}, "hb"),
        ("read_file", {"path": "a"}, "ha"),  # revisit
        ("edit", {"text": "y"}, "he"),
        ("read_file", {"path": "a"}, "ha"),  # revisit
        ("bash", {"command": "ls"}, "hl"),
        ("read_file", {"path": "a"}, "ha"),  # revisit -> 4 total, never adjacent
    ]
    for step, (tool, args, result_hash) in enumerate(plan):
        events.extend(_tool_step(step, seq, tool, args, result_hash))
        seq += 3
    events.append(_event(seq, EventType.CONTRACT_CHECK, {"accepted": False, "step": len(plan)}))
    return events


def _spins_in_place() -> list[Event]:
    """A genuine loop: the same action and result in four consecutive steps."""

    events: list[Event] = [_event(1, EventType.TRAJECTORY_STARTED, {})]
    seq = 2
    for step in range(4):
        events.extend(_tool_step(step, seq, "search", {"q": "x"}, "hs"))
        seq += 3
    return events


def test_revisiting_a_tool_is_not_a_loop() -> None:
    # The bug: total-count loop detection mislabelled this contract failure as a
    # loop because read_file appeared four times. State advanced between repeats,
    # so it is contract_violation, not nondeterministic_loop.
    diagnosis = classify_trajectory(_revisits_then_violates_contract(), task=TASK)
    assert diagnosis is not None
    assert diagnosis.cls == "contract_violation"


def test_consecutive_identical_action_is_still_a_loop() -> None:
    diagnosis = classify_trajectory(_spins_in_place(), task=TASK)
    assert diagnosis is not None
    assert diagnosis.cls == "nondeterministic_loop"


def test_confusion_matrix_has_no_contract_to_loop_bleed() -> None:
    fixtures = {
        "contract_violation": _revisits_then_violates_contract(),
        "nondeterministic_loop": _spins_in_place(),
        "tool_error": [
            _event(1, EventType.STEP_STARTED, {"index": 0}),
            _event(2, EventType.TOOL_RESULT, {"tool": "bash", "error": "exit 1"}),
        ],
    }
    report = validate_classifier(fixtures, task=TASK)

    assert report.confusion["contract_violation"].get("nondeterministic_loop", 0) == 0
    assert report.precision["contract_violation"] == 1.0
    assert report.precision["nondeterministic_loop"] == 1.0
    assert report.recall["contract_violation"] == 1.0


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
