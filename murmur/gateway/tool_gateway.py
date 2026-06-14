"""Record/replay tool gateway.

This file wraps tool calls so every request and response can be recorded. In
replay mode it serves recorded responses and raises an error if the path changes.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from enum import StrEnum
from time import perf_counter
from typing import Any

from murmur.core.events import Event, EventRecorder, EventType, hash_payload
from murmur.core.schema import validate_json_schema
from murmur.core.types import TaskSpec

ToolCallable = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class GatewayMode(StrEnum):
    RECORD = "record"
    REPLAY = "replay"


class ReplayDivergenceError(RuntimeError):
    """Raised when replayed execution differs from the recorded event path."""


class ReplayedToolError(RuntimeError):
    """Re-raised during replay to reproduce a tool failure recorded live."""


class ToolGateway:
    def __init__(
        self,
        *,
        mode: GatewayMode,
        recorder: EventRecorder | None = None,
        tools: dict[str, ToolCallable] | None = None,
        replay_events: list[Event] | None = None,
        capture_content: bool = False,
        task: TaskSpec | None = None,
    ) -> None:
        self._mode = mode
        self._recorder = recorder
        self._tools = tools or {}
        self._capture_content = capture_content
        self._task = task
        self._replay_events = [
            event
            for event in replay_events or []
            if event.type in {EventType.MODEL_CALL, EventType.TOOL_CALL, EventType.TOOL_RESULT}
        ]
        self._replay_index = 0
        self._tool_call_count = 0
        self._model_call_count = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._latency_ms = 0.0
        self._current_step_index: int | None = None

    @property
    def tool_call_count(self) -> int:
        """Number of tool calls made through this gateway (one trajectory's steps)."""

        return self._tool_call_count

    @property
    def model_call_count(self) -> int:
        """Number of model calls made through this gateway."""

        return self._model_call_count

    @property
    def input_tokens(self) -> int:
        return self._input_tokens

    @property
    def output_tokens(self) -> int:
        return self._output_tokens

    @property
    def latency_ms(self) -> float:
        """Aggregate duration: simulated model latency plus measured tool time."""

        return self._latency_ms

    @property
    def current_step_index(self) -> int | None:
        return self._current_step_index

    @classmethod
    def record(
        cls,
        *,
        recorder: EventRecorder,
        tools: dict[str, ToolCallable],
        capture_content: bool = False,
        task: TaskSpec | None = None,
    ) -> ToolGateway:
        return cls(
            mode=GatewayMode.RECORD,
            recorder=recorder,
            tools=tools,
            capture_content=capture_content,
            task=task,
        )

    @classmethod
    def replay(cls, events: list[Event]) -> ToolGateway:
        return cls(mode=GatewayMode.REPLAY, replay_events=events)

    async def step(
        self,
        *,
        index: int,
        phase: str = "act",
        input_data: Any | None = None,
        output_data: Any | None = None,
    ) -> None:
        """Mark a step boundary. Structural only — recorded live, skipped on replay."""

        if self._mode == GatewayMode.RECORD:
            if self._recorder is None:
                raise RuntimeError("record mode requires an event recorder")
            self._current_step_index = index
            await self._recorder.emit(EventType.STEP_STARTED, {"index": index, "phase": phase})
            await self._check_step_contract(index, input_data=input_data, output_data=output_data)

    async def _check_step_contract(
        self,
        index: int,
        *,
        input_data: Any | None,
        output_data: Any | None,
    ) -> None:
        if self._recorder is None or self._task is None:
            return
        contract = self._task.step_contracts.get(index)
        if contract is None:
            return
        for side, schema, data in (
            ("input", contract.input_schema, input_data),
            ("output", contract.output_schema, output_data),
        ):
            if schema is None:
                continue
            issues = validate_json_schema(data, schema)
            if not issues:
                await self._recorder.emit(
                    EventType.CONTRACT_CHECK,
                    {
                        "task_id": self._task.task_id,
                        "result": "pass",
                        "accepted": True,
                        "step": index,
                        "side": side,
                    },
                )
                continue
            for issue in issues:
                await self._recorder.emit(
                    EventType.CONTRACT_CHECK,
                    {
                        "task_id": self._task.task_id,
                        "result": "fail",
                        "accepted": False,
                        "step": index,
                        "side": side,
                        "field": issue.field,
                        "expected": issue.expected,
                        "got": issue.got,
                    },
                )

    async def model(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        finish_reason: str = "stop",
        latency_ms: float = 0.0,
        content: str | None = None,
    ) -> None:
        """Record one model call's structural usage. Content is dropped unless captured."""

        if self._mode == GatewayMode.RECORD:
            return await self._record_model(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=finish_reason,
                latency_ms=latency_ms,
                content=content,
            )
        self._replay_model(model)
        return None

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        if self._mode == GatewayMode.RECORD:
            return await self._record_call(name, args)
        return self._replay_call(name, args)

    async def record_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        result: Any = None,
        error: str | None = None,
        error_type: str | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        """Log a tool the agent already executed *itself* -- the observational path.

        Unlike :meth:`call`, this never runs anything: it records an
        already-completed external tool call as ``TOOL_CALL`` + ``TOOL_RESULT`` so a
        wrapped third-party agent (LangGraph, Claude Code, ...) still produces the
        divergence signature and the failure diagnosis. Recording only -- replay is
        not supported for externally driven tools, so it is a no-op in replay mode.
        """

        if self._mode != GatewayMode.RECORD:
            return
        if self._recorder is None:
            raise RuntimeError("record mode requires an event recorder")
        self._tool_call_count += 1
        self._latency_ms += latency_ms
        command_hash = hash_payload({"tool": name, "args": args})
        await self._recorder.emit(
            EventType.TOOL_CALL,
            {"tool": name, "args": args, "command_hash": command_hash},
        )
        if error is not None:
            await self._recorder.emit(
                EventType.TOOL_RESULT,
                {
                    "tool": name,
                    "error": error,
                    "error_type": error_type or "ToolError",
                    "latency_ms": latency_ms,
                },
            )
        else:
            await self._recorder.emit(
                EventType.TOOL_RESULT,
                {
                    "tool": name,
                    "result": result,
                    "result_hash": hash_payload(result),
                    "latency_ms": latency_ms,
                },
            )

    async def _record_model(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        finish_reason: str,
        latency_ms: float,
        content: str | None,
    ) -> None:
        if self._recorder is None:
            raise RuntimeError("record mode requires an event recorder")
        self._model_call_count += 1
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        self._latency_ms += latency_ms
        payload: dict[str, Any] = {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "finish_reason": finish_reason,
            "latency_ms": latency_ms,
        }
        # Privacy: structural attributes always; generated text only when captured.
        if self._capture_content and content is not None:
            payload["content"] = content
        await self._recorder.emit(EventType.MODEL_CALL, payload)

    def _replay_model(self, model: str) -> None:
        event = self._next_replay_event(EventType.MODEL_CALL)
        if event.payload["model"] != model:
            raise ReplayDivergenceError(
                f"model call diverged: expected model {event.payload['model']!r}, got {model!r}"
            )

    async def _record_call(self, name: str, args: dict[str, Any]) -> Any:
        if self._recorder is None:
            raise RuntimeError("record mode requires an event recorder")
        if name not in self._tools:
            raise KeyError(f"tool {name!r} is not registered")

        self._tool_call_count += 1
        command_hash = hash_payload({"tool": name, "args": args})
        await self._recorder.emit(
            EventType.TOOL_CALL,
            {"tool": name, "args": args, "command_hash": command_hash},
        )

        start = perf_counter()
        try:
            result = self._tools[name](args)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            # Record the failure so the trajectory is replayable, then re-raise so
            # the conductor sees the real error and classifies it.
            latency_ms = (perf_counter() - start) * 1000
            self._latency_ms += latency_ms
            await self._recorder.emit(
                EventType.TOOL_RESULT,
                {
                    "tool": name,
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                    "latency_ms": latency_ms,
                },
            )
            raise
        latency_ms = (perf_counter() - start) * 1000
        self._latency_ms += latency_ms
        await self._recorder.emit(
            EventType.TOOL_RESULT,
            {
                "tool": name,
                "result": result,
                "result_hash": hash_payload(result),
                "latency_ms": latency_ms,
            },
        )
        return result

    def _replay_call(self, name: str, args: dict[str, Any]) -> Any:
        call_event = self._next_replay_event(EventType.TOOL_CALL)
        expected_hash = call_event.payload["command_hash"]
        actual_hash = hash_payload({"tool": name, "args": args})
        if call_event.payload["tool"] != name or expected_hash != actual_hash:
            raise ReplayDivergenceError(
                f"tool call diverged: expected {call_event.payload!r}, got "
                f"{ {'tool': name, 'args': args, 'command_hash': actual_hash}!r}"
            )

        result_event = self._next_replay_event(EventType.TOOL_RESULT)
        if result_event.payload["tool"] != name:
            raise ReplayDivergenceError(
                f"tool result diverged: expected tool {name!r}, got "
                f"{result_event.payload['tool']!r}"
            )
        if "error" in result_event.payload:
            raise ReplayedToolError(result_event.payload["error"])
        return result_event.payload["result"]

    def _next_replay_event(self, event_type: EventType) -> Event:
        if self._replay_index >= len(self._replay_events):
            raise ReplayDivergenceError(f"replay exhausted before {event_type.value}")
        event = self._replay_events[self._replay_index]
        self._replay_index += 1
        if event.type != event_type:
            raise ReplayDivergenceError(
                f"replay expected {event_type.value}, got {event.type.value}"
            )
        return event
