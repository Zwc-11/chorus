from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from chorus.core.events import Event, EventRecorder, EventType, hash_payload

ToolCallable = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class GatewayMode(StrEnum):
    RECORD = "record"
    REPLAY = "replay"


class ReplayDivergenceError(RuntimeError):
    """Raised when replayed execution differs from the recorded event path."""


class ToolGateway:
    def __init__(
        self,
        *,
        mode: GatewayMode,
        recorder: EventRecorder | None = None,
        tools: dict[str, ToolCallable] | None = None,
        replay_events: list[Event] | None = None,
    ) -> None:
        self._mode = mode
        self._recorder = recorder
        self._tools = tools or {}
        self._replay_events = [
            event
            for event in replay_events or []
            if event.type in {EventType.TOOL_CALL, EventType.TOOL_RESULT}
        ]
        self._replay_index = 0

    @classmethod
    def record(cls, *, recorder: EventRecorder, tools: dict[str, ToolCallable]) -> ToolGateway:
        return cls(mode=GatewayMode.RECORD, recorder=recorder, tools=tools)

    @classmethod
    def replay(cls, events: list[Event]) -> ToolGateway:
        return cls(mode=GatewayMode.REPLAY, replay_events=events)

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        if self._mode == GatewayMode.RECORD:
            return await self._record_call(name, args)
        return self._replay_call(name, args)

    async def _record_call(self, name: str, args: dict[str, Any]) -> Any:
        if self._recorder is None:
            raise RuntimeError("record mode requires an event recorder")
        if name not in self._tools:
            raise KeyError(f"tool {name!r} is not registered")

        command_hash = hash_payload({"tool": name, "args": args})
        await self._recorder.emit(
            EventType.TOOL_CALL,
            {"tool": name, "args": args, "command_hash": command_hash},
        )

        result = self._tools[name](args)
        if inspect.isawaitable(result):
            result = await result
        await self._recorder.emit(
            EventType.TOOL_RESULT,
            {"tool": name, "result": result, "result_hash": hash_payload(result)},
        )
        return result

    def _replay_call(self, name: str, args: dict[str, Any]) -> Any:
        call_event = self._next_replay_event(EventType.TOOL_CALL)
        expected_hash = call_event.payload["command_hash"]
        actual_hash = hash_payload({"tool": name, "args": args})
        if call_event.payload["tool"] != name or expected_hash != actual_hash:
            raise ReplayDivergenceError(
                f"tool call diverged: expected {call_event.payload!r}, got "
                f"{ {'tool': name, 'args': args, 'command_hash': actual_hash}!r }"
            )

        result_event = self._next_replay_event(EventType.TOOL_RESULT)
        if result_event.payload["tool"] != name:
            raise ReplayDivergenceError(
                f"tool result diverged: expected tool {name!r}, got "
                f"{result_event.payload['tool']!r}"
            )
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
