"""Drive a projected Trace into a TracePort.

The mapper produces a flat, start-ordered span list with parent links. This
walks that structure depth-first and opens/closes spans on a ``TracePort`` so any
backend adapter (in-memory, OTLP/Phoenix, LangSmith) records the same nesting.
"""

from __future__ import annotations

from collections import defaultdict

from chorus.core.ports import TracePort
from chorus.trace.spans import Span, Trace


def emit_trace(trace: Trace, port: TracePort) -> None:
    children: dict[str, list[Span]] = defaultdict(list)
    roots: list[Span] = []
    for span in trace.spans:
        if span.parent_id is None:
            roots.append(span)
        else:
            children[span.parent_id].append(span)

    def walk(span: Span) -> None:
        attrs = dict(span.attributes)
        attrs.setdefault("chorus.span.kind", span.kind)
        attrs.setdefault("chorus.duration_ms", round(span.duration_ms, 3))
        port.start_span(span.name, kind=span.kind, attrs=attrs)
        for child in children.get(span.span_id, []):
            walk(child)
        port.set_status(span.status)
        port.end_span()

    for root in roots:
        walk(root)
    port.flush()


def emit_traces(traces: list[Trace], port: TracePort) -> None:
    for trace in traces:
        emit_trace(trace, port)
