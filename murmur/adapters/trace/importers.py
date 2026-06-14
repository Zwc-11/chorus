"""External trace importers.

These adapters normalize public SDK traces/transcripts into Chorus's append-only
event log. They are intentionally observational: they do not execute tools or
judge outputs; they make an already-recorded trajectory analyzable by Chorus.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from murmur.core.events import Event, EventType, hash_payload
from murmur.core.types import AgentAdapterCapabilities


class PublicSdkTraceImporter:
    """Best-effort importer for OpenAI/Claude/Google/LangGraph-style records."""

    source = "public-sdk"
    capabilities = AgentAdapterCapabilities(
        record=True,
        hooks=True,
        trace_import=True,
        live_execution=False,
        sandbox=False,
        tool_interception=False,
    )

    def import_events(
        self,
        records: Sequence[dict[str, Any]],
        *,
        run_id: str,
        task_id: str,
        trajectory_id: str | None = None,
    ) -> list[Event]:
        trajectory_id = trajectory_id or f"{run_id}_imported_t1"
        events: list[Event] = [
            _event(
                run_id,
                None,
                1,
                EventType.RUN_STARTED,
                {
                    "task_id": task_id,
                    "n": 1,
                    "metadata": {"imported": True, "source": self.source},
                },
            ),
            _event(
                run_id,
                trajectory_id,
                1,
                EventType.TRAJECTORY_STARTED,
                {"task_id": task_id, "index": 0, "imported": True, "source": self.source},
            ),
        ]
        seq = 2
        current_step = -1
        pending_tool: dict[str, Any] | None = None

        def open_step(phase: str) -> None:
            nonlocal current_step, seq
            current_step += 1
            events.append(
                _event(
                    run_id,
                    trajectory_id,
                    seq,
                    EventType.STEP_STARTED,
                    {"index": current_step, "phase": phase, "source": self.source},
                )
            )
            seq += 1

        for record in records:
            kind = self.kind(record)
            if kind == "step":
                open_step(str(_first(record, "phase", "name", default="step")))
                continue
            if current_step < 0:
                open_step("import")

            if kind == "model":
                events.append(
                    _event(run_id, trajectory_id, seq, EventType.MODEL_CALL, _model_payload(record))
                )
                seq += 1
            elif kind == "tool_call":
                pending_tool = _tool_call_payload(record)
                events.append(
                    _event(run_id, trajectory_id, seq, EventType.TOOL_CALL, pending_tool)
                )
                seq += 1
                if _has_inline_tool_result(record):
                    events.append(
                        _event(
                            run_id,
                            trajectory_id,
                            seq,
                            EventType.TOOL_RESULT,
                            _tool_result_payload(record),
                        )
                    )
                    seq += 1
                    pending_tool = None
            elif kind in {"tool_result", "tool_error"}:
                events.append(
                    _event(
                        run_id,
                        trajectory_id,
                        seq,
                        EventType.TOOL_RESULT,
                        _tool_result_payload(record, pending_tool=pending_tool),
                    )
                )
                seq += 1
                pending_tool = None
            elif kind == "verdict":
                events.append(
                    _event(run_id, trajectory_id, seq, EventType.VERDICT, _verdict_payload(record))
                )
                seq += 1

        events.append(
            _event(
                run_id,
                trajectory_id,
                seq,
                EventType.TRAJECTORY_FINISHED,
                {
                    "outcome": _last_outcome(events),
                    "cost_usd": 0.0,
                    "latency_ms": _total_latency(events),
                    "imported": True,
                },
            )
        )
        return events

    def kind(self, record: dict[str, Any]) -> str:
        raw = str(_first(record, "type", "event", "kind", "name", default="")).lower()
        if raw in {"step", "agent_step", "span.agent_step"}:
            return "step"
        if raw in {"verdict", "evaluation", "eval_result"}:
            return "verdict"
        if raw in {"assistant", "message", "model", "model_call", "response.completed"}:
            return "model"
        if "chat_model" in raw and raw.endswith("end"):
            return "model"
        if raw in {"tool_use", "tool_call", "function_call", "on_tool_start"}:
            return "tool_call"
        if raw in {"tool_result", "tool_response", "function_result", "on_tool_end"}:
            return "tool_result"
        if raw in {"tool_error", "on_tool_error"}:
            return "tool_error"
        if _first(record, "tool", "function", default=None) is not None:
            return "tool_call"
        if _first(record, "model", "model_name", default=None) is not None:
            return "model"
        return "unknown"


class OpenAIAgentsTraceImporter(PublicSdkTraceImporter):
    source = "openai-agents-sdk"
    capabilities = AgentAdapterCapabilities(record=True, trace_import=True, live_execution=False)


class ClaudeCodeTranscriptImporter(PublicSdkTraceImporter):
    source = "claude-code"
    capabilities = AgentAdapterCapabilities(
        record=True,
        hooks=True,
        trace_import=True,
        live_execution=False,
        tool_interception=True,
    )


class GoogleAdkTraceImporter(PublicSdkTraceImporter):
    source = "google-adk"
    capabilities = AgentAdapterCapabilities(record=True, trace_import=True, live_execution=False)


class LangGraphTraceImporter(PublicSdkTraceImporter):
    source = "langgraph"
    capabilities = AgentAdapterCapabilities(
        record=True,
        hooks=True,
        trace_import=True,
        live_execution=True,
        tool_interception=True,
    )


def _event(
    run_id: str,
    trajectory_id: str | None,
    seq: int,
    event_type: EventType,
    payload: dict[str, Any],
) -> Event:
    return Event.create(
        run_id=run_id,
        trajectory_id=trajectory_id,
        seq=seq,
        event_type=event_type,
        payload=payload,
    )


def _model_payload(record: dict[str, Any]) -> dict[str, Any]:
    usage = _first(record, "usage", "token_usage", default={})
    span_data = _dict(record.get("span_data"))
    data = _dict(record.get("data"))
    output = data.get("output")
    usage = _dict(usage or span_data.get("usage") or _attr(output, "usage_metadata") or {})
    response_metadata = _dict(_attr(output, "response_metadata") or {})
    token_usage = _dict(response_metadata.get("token_usage") or response_metadata.get("usage"))
    usage = usage or token_usage
    return {
        "model": str(
            _first(
                record,
                "model",
                "model_name",
                default=span_data.get("model") or response_metadata.get("model_name") or "model",
            )
        ),
        "input_tokens": _int(
            usage.get("input_tokens", usage.get("prompt_tokens", record.get("input_tokens", 0)))
        ),
        "output_tokens": _int(
            usage.get(
                "output_tokens",
                usage.get("completion_tokens", record.get("output_tokens", 0)),
            )
        ),
        "finish_reason": str(
            _first(record, "finish_reason", default=span_data.get("finish_reason") or "stop")
        ),
        "latency_ms": _float(_first(record, "latency_ms", "duration_ms", "elapsed_ms", default=0)),
        "content": _text(
            _first(record, "content", "text", "output", default=_attr(output, "content") or "")
        ),
    }


def _tool_call_payload(record: dict[str, Any]) -> dict[str, Any]:
    args = _first(record, "args", "arguments", "input", default=None)
    data = _dict(record.get("data"))
    if args is None:
        args = data.get("input")
    if not isinstance(args, dict):
        args = {"input": args}
    name = _tool_name(record)
    return {"tool": name, "args": args, "command_hash": hash_payload({"tool": name, "args": args})}


def _tool_result_payload(
    record: dict[str, Any], *, pending_tool: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = _dict(record.get("data"))
    tool = _tool_name(record)
    if tool == "tool" and pending_tool is not None:
        tool = str(pending_tool.get("tool", "tool"))
    error = _first(record, "error", default=data.get("error"))
    base: dict[str, Any] = {
        "tool": tool,
        "latency_ms": _float(_first(record, "latency_ms", "duration_ms", "elapsed_ms", default=0)),
    }
    if error is not None:
        base["error"] = str(error)
        base["error_type"] = str(_first(record, "error_type", default="ToolError"))
        return base
    result = _first(record, "result", "output", "content", default=data.get("output"))
    base["result"] = result
    base["result_hash"] = hash_payload(result)
    return base


def _verdict_payload(record: dict[str, Any]) -> dict[str, Any]:
    outcome = str(_first(record, "outcome", "result", "verdict", default="unknown")).lower()
    if outcome not in {"pass", "fail", "error"}:
        outcome = "fail"
    return {
        "outcome": outcome,
        "output": str(_first(record, "output", default="")),
        "failure_class": _first(record, "failure_class", default=None),
        "failure_detail": _first(record, "failure_detail", default=None),
    }


def _tool_name(record: dict[str, Any]) -> str:
    function = _dict(record.get("function"))
    data = _dict(record.get("data"))
    return str(
        _first(
            record,
            "tool",
            "tool_name",
            "name",
            default=function.get("name") or data.get("name") or "tool",
        )
    )


def _has_inline_tool_result(record: dict[str, Any]) -> bool:
    return any(key in record for key in ("result", "error")) or "output" in _dict(
        record.get("data")
    )


def _last_outcome(events: list[Event]) -> str:
    for event in reversed(events):
        if event.type == EventType.VERDICT:
            return str(event.payload.get("outcome", "fail"))
    return "pass"


def _total_latency(events: list[Event]) -> float:
    total = 0.0
    for event in events:
        if event.type in {EventType.MODEL_CALL, EventType.TOOL_RESULT}:
            total += _float(event.payload.get("latency_ms", 0.0))
    return total


def _first(record: dict[str, Any], *keys: str, default: Any = "") -> Any:
    span_data = _dict(record.get("span_data"))
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
        if key in span_data and span_data[key] is not None:
            return span_data[key]
    return default


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _attr(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(_attr(item, "text") or item)
            for item in value
        )
    return str(value)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "ClaudeCodeTranscriptImporter",
    "GoogleAdkTraceImporter",
    "LangGraphTraceImporter",
    "OpenAIAgentsTraceImporter",
    "PublicSdkTraceImporter",
]
