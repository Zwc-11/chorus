"""Span and Trace data types.

A ``Trace`` is one trajectory projected into a tree of ``Span``s. Spans are kept
in a flat, start-ordered list with an explicit ``depth`` and ``parent_id`` so a
waterfall view can render them directly. Timing is relative to the trajectory
start (``start_ms``) so the projection never depends on wall-clock skew.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SpanKind = Literal["run", "step", "model", "tool", "contract"]
SpanStatus = Literal["ok", "error", "unset"]


@dataclass(slots=True)
class Span:
    span_id: str
    parent_id: str | None
    name: str
    kind: SpanKind
    depth: int
    start_ms: float
    duration_ms: float
    status: SpanStatus
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def end_ms(self) -> float:
        return self.start_ms + self.duration_ms


@dataclass(slots=True)
class Trace:
    trace_id: str
    run_id: str
    trajectory_id: str
    outcome: str
    replay: bool
    spans: list[Span]
    total_ms: float
    total_tokens: int
    total_cost_usd: float

    @property
    def status(self) -> SpanStatus:
        return "ok" if self.outcome == "pass" else "error"
