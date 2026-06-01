"""Event -> span mapper (the heart of Phase 1).

Folds an append-only event log into one ``Trace`` per trajectory using the
OpenTelemetry GenAI semantic conventions. Standard fields live under ``gen_ai.*``;
everything Chorus-specific lives under ``chorus.*``. Nothing is invented inside
the ``gen_ai`` namespace.

Privacy is enforced *here*, at the projection boundary: structural attributes
(names, durations, token counts, tool names, status) are always emitted; prompt
and response text and tool arguments are emitted only when ``capture_content`` is
true. The event log may retain content for replay; the spans never leak it by
default.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from typing import Any

from chorus.core.events import Event, EventType
from chorus.trace.spans import Span, Trace

# OTel GenAI semantic conventions are still in Development; pin the version we map
# to so an upstream rename is a one-line change here, not a scattered edit.
GEN_AI_SEMCONV_VERSION = "1.30.0"

# Visual duration for a tool call that raised before returning a result.
_ERRORED_TOOL_MS = 60.0


def _sid(trajectory_id: str, seq: int, name: str) -> str:
    return sha256(f"{trajectory_id}:{seq}:{name}".encode()).hexdigest()[:16]


def _tid(trajectory_id: str) -> str:
    return sha256(trajectory_id.encode()).hexdigest()[:32]


def events_to_traces(
    events: list[Event],
    *,
    capture_content: bool = False,
    replay: bool = False,
) -> list[Trace]:
    """Project every trajectory in ``events`` into a Trace, in first-seen order."""

    by_trajectory: dict[str, list[Event]] = defaultdict(list)
    order: list[str] = []
    for event in events:
        if event.trajectory_id is None:
            continue
        if event.trajectory_id not in by_trajectory:
            order.append(event.trajectory_id)
        by_trajectory[event.trajectory_id].append(event)
    return [
        _trajectory_to_trace(by_trajectory[tid], capture_content=capture_content, replay=replay)
        for tid in order
    ]


def _trajectory_to_trace(events: list[Event], *, capture_content: bool, replay: bool) -> Trace:
    events = sorted(events, key=lambda e: e.seq)
    trajectory_id = events[0].trajectory_id or "unknown"
    run_id = events[0].run_id

    spans: list[Span] = []
    cursor = 0.0  # ms offset from trajectory start
    root: Span | None = None
    step: Span | None = None
    open_tool: Span | None = None
    last_leaf: Span | None = None
    outcome = "pass"
    failure_class: str | None = None
    total_tokens = 0
    total_cost = 0.0

    def add(span: Span) -> Span:
        spans.append(span)
        return span

    def close_step(end: float) -> None:
        nonlocal step
        if step is not None:
            step.duration_ms = max(0.0, end - step.start_ms)
            step = None

    for event in events:
        payload = event.payload
        if event.type == EventType.TRAJECTORY_STARTED:
            root = add(
                Span(
                    span_id=_sid(trajectory_id, event.seq, "agent.run"),
                    parent_id=None,
                    name="agent.run",
                    kind="run",
                    depth=0,
                    start_ms=0.0,
                    duration_ms=0.0,
                    status="unset",
                    attributes={
                        "gen_ai.operation.name": "invoke_agent",
                        "chorus.run.id": run_id,
                        "chorus.trajectory.id": trajectory_id,
                    },
                )
            )
        elif event.type == EventType.STEP_STARTED:
            close_step(cursor)
            phase = str(payload.get("phase", "act"))
            index = int(payload.get("index", 0))
            step = add(
                Span(
                    span_id=_sid(trajectory_id, event.seq, "step"),
                    parent_id=root.span_id if root else None,
                    name=f"step {index} · {phase}",
                    kind="step",
                    depth=1,
                    start_ms=cursor,
                    duration_ms=0.0,
                    status="unset",
                    attributes={
                        "chorus.step.index": index,
                        "chorus.step.phase": phase,
                    },
                )
            )
        elif event.type == EventType.MODEL_CALL:
            model = str(payload.get("model", "model"))
            in_tok = int(payload.get("input_tokens", 0))
            out_tok = int(payload.get("output_tokens", 0))
            total_tokens += in_tok + out_tok
            duration = float(payload.get("latency_ms", 0.0))
            attrs: dict[str, Any] = {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": model,
                "gen_ai.usage.input_tokens": in_tok,
                "gen_ai.usage.output_tokens": out_tok,
                "gen_ai.response.finish_reasons": [str(payload.get("finish_reason", "stop"))],
            }
            if capture_content and "content" in payload:
                attrs["chorus.model.content"] = payload["content"]
            last_leaf = add(
                Span(
                    span_id=_sid(trajectory_id, event.seq, "chat"),
                    parent_id=step.span_id if step else (root.span_id if root else None),
                    name=f"chat {model}",
                    kind="model",
                    depth=2,
                    start_ms=cursor,
                    duration_ms=duration,
                    status="ok",
                    attributes=attrs,
                )
            )
            cursor += duration
        elif event.type == EventType.TOOL_CALL:
            tool = str(payload.get("tool", "tool"))
            tool_attrs: dict[str, Any] = {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": tool,
                "gen_ai.tool.call.id": str(payload.get("command_hash", ""))[:12],
            }
            if capture_content and "args" in payload:
                for key, value in dict(payload["args"]).items():
                    tool_attrs[f"chorus.tool.arg.{key}"] = value
            open_tool = add(
                Span(
                    span_id=_sid(trajectory_id, event.seq, "tool"),
                    parent_id=step.span_id if step else (root.span_id if root else None),
                    name=f"execute_tool {tool}",
                    kind="tool",
                    depth=2,
                    start_ms=cursor,
                    duration_ms=0.0,
                    status="unset",
                    attributes=tool_attrs,
                )
            )
            last_leaf = open_tool
        elif event.type == EventType.TOOL_RESULT:
            duration = float(payload.get("latency_ms", 0.0))
            errored = "error" in payload
            if open_tool is not None:
                open_tool.duration_ms = duration
                open_tool.status = "error" if errored else "ok"
                if errored:
                    # error_type is structural; the message may echo args, so gate it.
                    open_tool.attributes["chorus.tool.error_type"] = payload.get(
                        "error_type", "ToolError"
                    )
                    if capture_content:
                        open_tool.attributes["chorus.tool.error"] = payload["error"]
                open_tool = None
            cursor += duration
        elif event.type == EventType.CONTRACT_CHECK:
            accepted = bool(payload.get("accepted", False))
            attrs: dict[str, Any] = {
                "chorus.contract.result": "pass" if accepted else "fail",
            }
            if "step" in payload and payload.get("step") is not None:
                attrs["chorus.contract.step"] = int(payload["step"])
                if not accepted:
                    attrs["chorus.failure.step"] = int(payload["step"])
            for key in ("side", "field", "expected", "got"):
                if key in payload:
                    attrs[f"chorus.contract.{key}"] = payload[key]
            add(
                Span(
                    span_id=_sid(trajectory_id, event.seq, "contract"),
                    parent_id=root.span_id if root else None,
                    name="contract.check",
                    kind="contract",
                    depth=1,
                    start_ms=cursor,
                    duration_ms=0.0,
                    status="ok" if accepted else "error",
                    attributes=attrs,
                )
            )
        elif event.type == EventType.VERDICT:
            outcome = str(payload.get("outcome", "pass"))
            failure_class = payload.get("failure_class")

    # An open tool span at the end means the tool raised before returning.
    if open_tool is not None:
        open_tool.duration_ms = _ERRORED_TOOL_MS
        open_tool.status = "error"
        cursor += _ERRORED_TOOL_MS

    close_step(cursor)
    if root is not None:
        root.duration_ms = cursor
        root.status = "ok" if outcome == "pass" else "error"

    # Stamp the failure class on the run and on the specific failing leaf span.
    if outcome != "pass" and failure_class:
        if root is not None:
            root.attributes["chorus.failure.class"] = failure_class
            _stamp_failure_details(root.attributes, events)
        failing = last_leaf
        if failing is not None:
            failing.status = "error"
            failing.attributes["chorus.failure.class"] = failure_class
            _stamp_failure_details(failing.attributes, events)

    if replay:
        for span in spans:
            span.attributes["chorus.replay"] = True

    total_cost = _trajectory_cost(events)

    return Trace(
        trace_id=_tid(trajectory_id),
        run_id=run_id,
        trajectory_id=trajectory_id,
        outcome=outcome,
        replay=replay,
        spans=spans,
        total_ms=cursor,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
    )


def _trajectory_cost(events: list[Event]) -> float:
    for event in events:
        if event.type == EventType.TRAJECTORY_FINISHED:
            return float(event.payload.get("cost_usd", 0.0))
    return 0.0


def _stamp_failure_details(attrs: dict[str, Any], events: list[Event]) -> None:
    for event in events:
        if event.type == EventType.VERDICT:
            if event.payload.get("failure_step") is not None:
                attrs["chorus.failure.step"] = int(event.payload["failure_step"])
            if event.payload.get("failure_detail"):
                attrs["chorus.failure.detail"] = event.payload["failure_detail"]
            if event.payload.get("failure_confidence") is not None:
                attrs["chorus.failure.confidence"] = float(event.payload["failure_confidence"])
            return
