"""Drive a projected Trace into a TracePort.

The mapper produces a flat, start-ordered span list with parent links. This
walks that structure depth-first and opens/closes spans on a ``TracePort`` so any
backend adapter (in-memory, OTLP/Phoenix, LangSmith) records the same nesting.
"""

from __future__ import annotations

from collections import defaultdict

from murmur.core.ports import TracePort
from murmur.trace.spans import Span, Trace


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
    _emit_trace_metrics(trace, port)
    port.flush()


def emit_traces(traces: list[Trace], port: TracePort) -> None:
    for trace in traces:
        emit_trace(trace, port)


def _emit_trace_metrics(trace: Trace, port: TracePort) -> None:
    for span in trace.spans:
        common = {
            "chorus.span.kind": span.kind,
            "murmur.trace.id": trace.trace_id,
            "chorus.run.id": trace.run_id,
            "chorus.trajectory.id": trace.trajectory_id,
        }
        if span.duration_ms > 0:
            port.record_metric(
                "gen_ai.client.operation.duration",
                span.duration_ms,
                attrs={
                    **common,
                    "gen_ai.operation.name": span.attributes.get(
                        "gen_ai.operation.name", span.kind
                    ),
                },
            )
        if span.kind == "model":
            for token_type, attr in (
                ("input", "gen_ai.usage.input_tokens"),
                ("output", "gen_ai.usage.output_tokens"),
            ):
                value = float(span.attributes.get(attr, 0) or 0)
                if value <= 0:
                    continue
                port.record_metric(
                    "gen_ai.client.token.usage",
                    value,
                    attrs={
                        **common,
                        "gen_ai.request.model": span.attributes.get("gen_ai.request.model", ""),
                        "gen_ai.token.type": token_type,
                    },
                )
