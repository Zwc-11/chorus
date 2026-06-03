"""In-memory TracePort adapter.

Collects emitted spans into a flat list with parent links and open/close order.
Zero-config and dependency-free: used as the default sink and in tests that assert
the emitter drives spans in the right nesting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CollectedSpan:
    name: str
    kind: str
    parent: str | None
    status: str = "unset"
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CollectedMetric:
    name: str
    value: float
    attributes: dict[str, Any] = field(default_factory=dict)


class InMemoryTraceCollector:
    """A ``TracePort`` that keeps spans in memory."""

    def __init__(self) -> None:
        self.spans: list[CollectedSpan] = []
        self.metrics: list[CollectedMetric] = []
        self._stack: list[CollectedSpan] = []
        self.flushed = False

    def start_span(self, name: str, *, kind: str, attrs: dict[str, Any]) -> None:
        parent = self._stack[-1].name if self._stack else None
        span = CollectedSpan(name=name, kind=kind, parent=parent, attributes=dict(attrs))
        self.spans.append(span)
        self._stack.append(span)

    def set_status(self, status: str) -> None:
        if self._stack:
            self._stack[-1].status = status

    def end_span(self) -> None:
        if self._stack:
            self._stack.pop()

    def record_metric(self, name: str, value: float, *, attrs: dict[str, Any]) -> None:
        self.metrics.append(CollectedMetric(name=name, value=value, attributes=dict(attrs)))

    def flush(self) -> None:
        self.flushed = True

    @property
    def depth_balanced(self) -> bool:
        """True when every started span was ended."""

        return not self._stack
