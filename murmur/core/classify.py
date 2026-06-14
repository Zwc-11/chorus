"""Failure diagnosis derived from recorded trajectory events."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from murmur.core.divergence import step_signature
from murmur.core.events import Event, EventType
from murmur.core.types import TaskSpec


@dataclass(frozen=True, slots=True)
class FailureDiagnosis:
    cls: str
    step: int | None
    detail: str = ""
    confidence: float = 1.0
    secondary: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FailurePolicy:
    loop_threshold: int = 3
    min_confidence: float = 0.6
    timeout_ms: float | None = None
    budget_usd: float | None = None
    llm_fallback: Callable[[list[Event], TaskSpec | None], FailureDiagnosis | None] | None = None


@dataclass(frozen=True, slots=True)
class ClassificationReport:
    precision: dict[str, float] = field(default_factory=dict)
    recall: dict[str, float] = field(default_factory=dict)
    f1: dict[str, float] = field(default_factory=dict)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)


def classify_failure(error: BaseException | None) -> str | None:
    """Compatibility helper for older callers that only have an exception."""

    if error is None:
        return None
    error_name = error.__class__.__name__.lower()
    if "divergence" in error_name:
        return "nondeterministic_loop"
    if "timeout" in error_name:
        return "timeout"
    if "key" in error_name or "value" in error_name:
        return "schema_mismatch"
    return "tool_error"


def classify_trajectory(
    events: Iterable[Event],
    *,
    task: TaskSpec | None = None,
    policy: FailurePolicy | None = None,
) -> FailureDiagnosis | None:
    """Classify one failed trajectory using deterministic detectors first."""

    trajectory_events = sorted(events, key=lambda event: event.seq)
    policy = policy or _policy_from_task(task)
    for detector in (
        _detect_tool_error,
        _detect_schema_mismatch,
        _detect_budget_exceeded,
        _detect_timeout,
        _detect_nondeterministic_loop,
        _detect_contract_violation,
        _detect_context_drift,
    ):
        hit = detector(trajectory_events, task, policy)
        if hit is not None:
            return hit
    if policy.llm_fallback is not None:
        fallback = policy.llm_fallback(trajectory_events, task)
        if fallback is not None and fallback.confidence >= policy.min_confidence:
            return fallback
    return FailureDiagnosis(cls="unknown", step=None, detail="no deterministic detector matched")


def validate_classifier(
    fixtures: dict[str, list[Event]],
    *,
    task: TaskSpec | None = None,
    policy: FailurePolicy | None = None,
) -> ClassificationReport:
    """Validate diagnosis against injected failures with known labels."""

    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    labels = set(fixtures)
    for expected, events in fixtures.items():
        actual = classify_trajectory(events, task=task, policy=policy)
        actual_label = actual.cls if actual is not None else "unknown"
        labels.add(actual_label)
        confusion[expected][actual_label] += 1

    precision: dict[str, float] = {}
    recall: dict[str, float] = {}
    f1: dict[str, float] = {}
    for label in labels:
        true_positive = confusion[label][label]
        predicted = sum(row[label] for row in confusion.values())
        actual = sum(confusion[label].values())
        precision[label] = true_positive / predicted if predicted else 0.0
        recall[label] = true_positive / actual if actual else 0.0
        denom = precision[label] + recall[label]
        f1[label] = 2 * precision[label] * recall[label] / denom if denom else 0.0

    return ClassificationReport(
        precision=precision,
        recall=recall,
        f1=f1,
        confusion={label: dict(row) for label, row in confusion.items()},
    )


def _policy_from_task(task: TaskSpec | None) -> FailurePolicy:
    if task is None:
        return FailurePolicy()
    return FailurePolicy(
        timeout_ms=_float_meta(task, "timeout_ms"),
        budget_usd=_float_meta(task, "budget_usd"),
    )


def _float_meta(task: TaskSpec, key: str) -> float | None:
    value = task.metadata.get(key)
    return float(value) if value is not None else None


def _detect_tool_error(
    events: list[Event], task: TaskSpec | None, policy: FailurePolicy
) -> FailureDiagnosis | None:
    del task, policy
    current_step = None
    for event in events:
        if event.type == EventType.STEP_STARTED:
            current_step = int(event.payload.get("index", 0))
        elif event.type == EventType.TOOL_RESULT and "error" in event.payload:
            error_type = event.payload.get("error_type", "ToolError")
            return FailureDiagnosis(
                cls="tool_error",
                step=current_step,
                detail=f"{error_type}: {event.payload.get('error', '')}",
            )
    return None


def _detect_schema_mismatch(
    events: list[Event], task: TaskSpec | None, policy: FailurePolicy
) -> FailureDiagnosis | None:
    del task, policy
    for event in events:
        if event.type != EventType.CONTRACT_CHECK or bool(event.payload.get("accepted", True)):
            continue
        if "field" not in event.payload:
            continue
        return FailureDiagnosis(
            cls="schema_mismatch",
            step=_payload_step(event),
            detail=(
                f"{event.payload.get('side', 'boundary')} {event.payload.get('field')} "
                f"expected {event.payload.get('expected')} got {event.payload.get('got')}"
            ),
        )
    return None


def _detect_budget_exceeded(
    events: list[Event], task: TaskSpec | None, policy: FailurePolicy
) -> FailureDiagnosis | None:
    del task
    if policy.budget_usd is None:
        return None
    for event in events:
        if event.type == EventType.TRAJECTORY_FINISHED:
            cost = float(event.payload.get("cost_usd", 0.0))
            if cost > policy.budget_usd:
                return FailureDiagnosis(
                    cls="budget_exceeded",
                    step=_last_step(events),
                    detail=f"${cost:.4f} exceeded budget ${policy.budget_usd:.4f}",
                )
    return None


def _detect_timeout(
    events: list[Event], task: TaskSpec | None, policy: FailurePolicy
) -> FailureDiagnosis | None:
    del task
    if policy.timeout_ms is None:
        return None
    current_step = None
    for event in events:
        if event.type == EventType.STEP_STARTED:
            current_step = int(event.payload.get("index", 0))
        elif event.type in {EventType.MODEL_CALL, EventType.TOOL_RESULT}:
            latency = float(event.payload.get("latency_ms", 0.0))
            if latency > policy.timeout_ms:
                return FailureDiagnosis(
                    cls="timeout",
                    step=current_step,
                    detail=f"{latency:.1f} ms exceeded limit {policy.timeout_ms:.1f} ms",
                )
    return None


def _detect_nondeterministic_loop(
    events: list[Event], task: TaskSpec | None, policy: FailurePolicy
) -> FailureDiagnosis | None:
    del task
    # A loop is the same action producing the same result in *consecutive* steps:
    # the agent spins without advancing state. Counting total occurrences instead
    # would mislabel ordinary iteration -- a trajectory that legitimately revisits
    # a tool (e.g. reads a file early and again later) is not stuck, and must fall
    # through to its real root cause (e.g. contract_violation). Requiring identical
    # (action, result) back to back is the "no state change between repeats" guard.
    previous: tuple | None = None
    run_length = 0
    for step in range((_last_step(events) or -1) + 1):
        fingerprint = _step_fingerprint(events, step)
        if fingerprint is None:
            previous = None
            run_length = 0
            continue
        if fingerprint == previous:
            run_length += 1
        else:
            previous = fingerprint
            run_length = 1
        if run_length > policy.loop_threshold:
            return FailureDiagnosis(
                cls="nondeterministic_loop",
                step=step,
                detail=f"action repeated with no state change {run_length} times",
            )
    return None


def _step_fingerprint(events: list[Event], step: int) -> tuple | None:
    """The (action, result) a trajectory took at a step -- identical fingerprints
    in adjacent steps mean no state changed between them."""

    action = step_signature(events, step)
    if action is None:
        return None
    return (action, _step_result_key(events, step))


def _step_result_key(events: list[Event], step: int) -> tuple | None:
    for event in _step_window(events, step):
        if event.type == EventType.TOOL_RESULT:
            if "error" in event.payload:
                return ("error", event.payload.get("error_type", "ToolError"))
            return ("result", event.payload.get("result_hash"))
    return None


def _step_window(events: list[Event], step: int) -> list[Event]:
    """Events emitted within one step (after its STEP_STARTED, before the next)."""

    start_index = None
    end_seq = None
    for index, event in enumerate(events):
        if event.type == EventType.STEP_STARTED and int(event.payload.get("index", -1)) == step:
            start_index = index
            for later in events[index + 1 :]:
                if later.type == EventType.STEP_STARTED:
                    end_seq = later.seq
                    break
            break
    if start_index is None:
        return []
    return [event for event in events[start_index + 1 :] if end_seq is None or event.seq < end_seq]


def _detect_contract_violation(
    events: list[Event], task: TaskSpec | None, policy: FailurePolicy
) -> FailureDiagnosis | None:
    del task, policy
    for event in events:
        if event.type == EventType.CONTRACT_CHECK and not bool(event.payload.get("accepted", True)):
            ids = tuple(str(item) for item in event.payload.get("diagnostic_ids", ()) if item)
            detail = (
                "failed predicates: " + ", ".join(ids)
                if ids
                else "acceptance predicate returned false"
            )
            return FailureDiagnosis(
                cls="contract_violation",
                step=_payload_step(event),
                detail=detail,
                secondary=ids,
            )
    return None


def _detect_context_drift(
    events: list[Event], task: TaskSpec | None, policy: FailurePolicy
) -> FailureDiagnosis | None:
    del task, policy
    current_step = None
    needles = ("absent state", "not present", "contradict", "context drift")
    for event in events:
        if event.type == EventType.STEP_STARTED:
            current_step = int(event.payload.get("index", 0))
        elif event.type == EventType.MODEL_CALL:
            content = str(event.payload.get("content", "")).lower()
            if any(needle in content for needle in needles):
                return FailureDiagnosis(
                    cls="context_drift",
                    step=current_step,
                    detail="model referenced unavailable or contradictory state",
                    confidence=0.7,
                )
    return None


def _payload_step(event: Event) -> int | None:
    value = event.payload.get("step")
    return int(value) if value is not None else None


def _last_step(events: list[Event]) -> int | None:
    last = None
    for event in events:
        if event.type == EventType.STEP_STARTED:
            last = int(event.payload.get("index", 0))
    return last
