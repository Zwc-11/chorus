"""Core port definitions.

This file defines the interfaces the pure core depends on. Concrete adapters
implement these ports so the core does not know about specific tools or vendors.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from murmur.core.events import Event
    from murmur.core.types import AgentAdapterCapabilities, RunResult, TaskSpec


class ToolGatewayPort(Protocol):
    async def call(self, name: str, args: dict[str, Any]) -> Any:
        """Call a tool through the single record/replay choke point."""

    async def record_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        result: Any = ...,
        error: str | None = ...,
        error_type: str | None = ...,
        latency_ms: float = ...,
    ) -> None:
        """Record a tool the agent executed itself (observational adapters)."""

    async def step(
        self,
        *,
        index: int,
        phase: str = ...,
        input_data: Any | None = ...,
        output_data: Any | None = ...,
    ) -> None:
        """Mark a step boundary in the trajectory."""

    async def model(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        finish_reason: str = ...,
        latency_ms: float = ...,
        content: str | None = ...,
    ) -> None:
        """Record one model call's structural usage through the choke point."""


class AgentPort(Protocol):
    async def run(self, task: TaskSpec, gateway: ToolGatewayPort) -> str:
        """Drive one trajectory for the agent under test."""


class StoragePort(Protocol):
    async def append(self, event: Event) -> None:
        """Append one event without mutating previous events."""

    async def read_events(self) -> Sequence[Event]:
        """Read events in storage order."""


class TracePort(Protocol):
    """Sink for projected spans. Adapters export to a backend; the core stays pure.

    The core walks a projected trace and drives these methods; it never imports a
    concrete tracing SDK. ``attrs`` are already ``gen_ai.*`` / ``chorus.*`` flat
    key-value pairs produced by the event->span mapper.
    """

    def start_span(self, name: str, *, kind: str, attrs: dict[str, Any]) -> None:
        """Open a span. Calls nest by start/end ordering."""

    def set_status(self, status: str) -> None:
        """Set the status (ok / error) of the most recently opened span."""

    def end_span(self) -> None:
        """Close the most recently opened span."""

    def record_metric(self, name: str, value: float, *, attrs: dict[str, Any]) -> None:
        """Record an OTel metric point derived from the projected trace."""

    def flush(self) -> None:
        """Flush buffered spans to the backend."""


class JudgePort(Protocol):
    async def judge(self, task: TaskSpec, output: str) -> str:
        """Return pass, fail, or error for one trajectory."""


class ReportPort(Protocol):
    async def write(self, result: RunResult) -> str:
        """Write a report and return its location or body."""


class ExternalTraceImporter(Protocol):
    """Convert a provider/framework trace or transcript into Chorus events."""

    source: str
    capabilities: AgentAdapterCapabilities

    def import_events(
        self,
        records: Sequence[dict[str, Any]],
        *,
        run_id: str,
        task_id: str,
        trajectory_id: str | None = None,
    ) -> list[Event]:
        """Return append-only events preserving the external trace order."""
