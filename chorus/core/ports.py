from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from chorus.core.events import Event
    from chorus.core.types import RunResult, TaskSpec


class ToolGatewayPort(Protocol):
    async def call(self, name: str, args: dict[str, Any]) -> Any:
        """Call a tool through the single record/replay choke point."""


class AgentPort(Protocol):
    async def run(self, task: TaskSpec, gateway: ToolGatewayPort) -> str:
        """Drive one trajectory for the agent under test."""


class StoragePort(Protocol):
    async def append(self, event: Event) -> None:
        """Append one event without mutating previous events."""

    async def read_events(self) -> Sequence[Event]:
        """Read events in storage order."""


class TracePort(Protocol):
    async def emit(self, event: Event) -> None:
        """Project one event into a trace backend."""


class JudgePort(Protocol):
    async def judge(self, task: TaskSpec, output: str) -> str:
        """Return pass, fail, or error for one trajectory."""


class ReportPort(Protocol):
    async def write(self, result: RunResult) -> str:
        """Write a report and return its location or body."""
