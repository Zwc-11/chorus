"""OTLP TracePort adapter (Phoenix default, LangSmith via config).

Exports projected spans over OTLP using the OpenTelemetry SDK. Both Phoenix and
LangSmith ingest native OTLP, so the same adapter targets either by endpoint;
selecting a backend is configuration, not a code change.

The OpenTelemetry packages are an optional dependency. Importing this module is
cheap; the SDK is imported only when you actually build a port, so the rest of
Chorus runs with no tracing dependency installed. Install with::

    pip install "murmur-ai-harness[otel]"
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

# Pinned GenAI semantic-convention opt-in (the spec is still in Development).
SEMCONV_OPT_IN = "gen_ai_latest_experimental"

_BACKEND_DEFAULTS = {
    "phoenix": "http://localhost:6006/v1/traces",
    "langsmith": "https://api.smith.langchain.com/otel/v1/traces",
}

# LangSmith web app (the loop's first hop: open the project the run landed in).
LANGSMITH_APP_URL = "https://smith.langchain.com"


def langsmith_project_url(project: str) -> str:
    """A LangSmith projects-list URL filtered to ``project`` (default-org shortcut)."""

    return f"{LANGSMITH_APP_URL}/o/-/projects?searchValue={quote(project)}"


# LangSmith reads only its own OTEL attribute conventions: arbitrary attributes are
# dropped, custom metadata needs the langsmith.metadata.* prefix, and the run type
# comes from langsmith.span.kind. Chorus span kinds map to LangSmith run types here.
_LANGSMITH_KIND = {
    "run": "chain",
    "step": "chain",
    "model": "llm",
    "tool": "tool",
    "contract": "chain",
}


def langsmith_attributes(kind: str, attrs: dict[str, Any]) -> dict[str, Any]:
    """Translate Chorus span attributes into LangSmith's OTEL conventions.

    ``chorus.*`` attributes are mirrored under ``langsmith.metadata.*`` so they land
    in the run's metadata (queryable in the MCP loop); the span kind becomes
    ``langsmith.span.kind`` (the run type). ``gen_ai.*`` attributes are left alone --
    LangSmith ingests those natively.
    """

    extra: dict[str, Any] = {"langsmith.span.kind": _LANGSMITH_KIND.get(kind, "chain")}
    for key, value in attrs.items():
        if key.startswith("chorus."):
            extra[f"langsmith.metadata.{key}"] = value
    return extra


class OtelNotInstalled(RuntimeError):
    """Raised when the optional OpenTelemetry packages are not available."""


@dataclass(frozen=True, slots=True)
class ExportStats:
    """Honest tally of what the OTLP backend actually accepted."""

    ok_spans: int
    ok_batches: int
    failed_batches: int

    @property
    def ok(self) -> bool:
        return self.failed_batches == 0


def _require_otel() -> Any:
    try:
        from opentelemetry import context, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise OtelNotInstalled(
            "OpenTelemetry is not installed. Install the extra: "
            'pip install "murmur-ai-harness[otel]"'
        ) from exc
    return (
        context,
        trace,
        OTLPMetricExporter,
        OTLPSpanExporter,
        Resource,
        MeterProvider,
        PeriodicExportingMetricReader,
        TracerProvider,
        BatchSpanProcessor,
        SpanExportResult,
    )


class _CountingSpanExporter:
    """Wraps a span exporter and records which batches the backend accepted.

    The OTel SDK swallows export failures (it logs ``Failed to export span batch
    code: 401`` and moves on), so the CLI could not tell a rejected export from a
    real one. This delegates every ``export`` and tallies SUCCESS vs FAILURE so the
    caller can report the truth and exit non-zero.
    """

    def __init__(self, inner: Any, success_result: Any) -> None:
        self._inner = inner
        self._success = success_result
        self.ok_spans = 0
        self.ok_batches = 0
        self.failed_batches = 0

    def export(self, spans: Any) -> Any:
        result = self._inner.export(spans)
        if result == self._success:
            self.ok_batches += 1
            self.ok_spans += len(spans)
        else:
            self.failed_batches += 1
        return result

    def shutdown(self) -> Any:
        return self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> Any:
        return self._inner.force_flush(timeout_millis)

    def stats(self) -> ExportStats:
        return ExportStats(self.ok_spans, self.ok_batches, self.failed_batches)


def _langsmith_headers() -> dict[str, str]:
    api_key = os.environ.get("LANGSMITH_API_KEY", "")
    project = os.environ.get("LANGSMITH_PROJECT", "chorus")
    headers = {"Langsmith-Project": project}
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def build_otlp_trace_port(
    *,
    backend: str = "phoenix",
    endpoint: str | None = None,
    service_name: str = "chorus",
    headers: dict[str, str] | None = None,
) -> OtlpTracePort:
    """Build an OTLP-backed ``TracePort`` for Phoenix (default) or LangSmith."""

    (
        context,
        trace,
        OTLPMetricExporter,
        OTLPSpanExporter,
        Resource,
        MeterProvider,
        PeriodicExportingMetricReader,
        TracerProvider,
        BatchSpanProcessor,
        SpanExportResult,
    ) = _require_otel()

    # Opt into the GenAI semconv explicitly so attribute names are stable.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", SEMCONV_OPT_IN)

    resolved_endpoint = endpoint or _BACKEND_DEFAULTS.get(backend, _BACKEND_DEFAULTS["phoenix"])
    resolved_headers = headers
    if resolved_headers is None and backend == "langsmith":
        resolved_headers = _langsmith_headers()

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = _CountingSpanExporter(
        OTLPSpanExporter(endpoint=resolved_endpoint, headers=resolved_headers),
        SpanExportResult.SUCCESS,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    tracer = provider.get_tracer("murmur.trace")
    meter_provider = None
    token_histogram = None
    duration_histogram = None
    if backend != "langsmith":
        metrics_endpoint = _metrics_endpoint(resolved_endpoint)
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=metrics_endpoint, headers=resolved_headers)
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        meter = meter_provider.get_meter("murmur.trace")
        token_histogram = meter.create_histogram(
            "gen_ai.client.token.usage",
            unit="{token}",
            description="Input and output token usage derived from Chorus spans.",
        )
        duration_histogram = meter.create_histogram(
            "gen_ai.client.operation.duration",
            unit="ms",
            description="Operation duration derived from Chorus spans.",
        )
    return OtlpTracePort(
        tracer=tracer,
        provider=provider,
        meter_provider=meter_provider,
        token_histogram=token_histogram,
        duration_histogram=duration_histogram,
        context=context,
        trace=trace,
        langsmith=backend == "langsmith",
        exporter=exporter,
    )


class OtlpTracePort:
    """``TracePort`` that opens/closes real OTel spans with correct nesting.

    When ``langsmith`` is set, span attributes are translated into LangSmith's OTEL
    conventions (``langsmith.metadata.*``, ``langsmith.span.kind``) and error spans
    record an ``exception`` event -- LangSmith derives a run's error status from that
    event, not from the OTel status code alone.
    """

    def __init__(
        self,
        *,
        tracer: Any,
        provider: Any,
        context: Any,
        trace: Any,
        meter_provider: Any | None = None,
        token_histogram: Any | None = None,
        duration_histogram: Any | None = None,
        langsmith: bool = False,
        exporter: _CountingSpanExporter | None = None,
    ) -> None:
        self._tracer = tracer
        self._provider = provider
        self._meter_provider = meter_provider
        self._token_histogram = token_histogram
        self._duration_histogram = duration_histogram
        self._context = context
        self._trace = trace
        self._langsmith = langsmith
        self._exporter = exporter
        self._stack: list[tuple[Any, Any, dict[str, Any]]] = []

    def export_stats(self) -> ExportStats:
        """What the backend actually accepted (after ``flush``)."""

        return self._exporter.stats() if self._exporter else ExportStats(0, 0, 0)

    def start_span(self, name: str, *, kind: str, attrs: dict[str, Any]) -> None:
        span_attrs = dict(attrs)
        if self._langsmith:
            span_attrs.update(langsmith_attributes(kind, attrs))
        span = self._tracer.start_span(name, attributes=_otel_attrs(span_attrs))
        token = self._context.attach(self._trace.set_span_in_context(span))
        self._stack.append((span, token, attrs))

    def set_status(self, status: str) -> None:
        if not self._stack:
            return
        span, _, attrs = self._stack[-1]
        codes = self._trace.StatusCode
        span.set_status(self._trace.Status(codes.ERROR if status == "error" else codes.OK))
        if status == "error" and self._langsmith:
            message = attrs.get("chorus.tool.error") or attrs.get("chorus.failure.class") or "error"
            etype = (
                attrs.get("chorus.tool.error_type") or attrs.get("chorus.failure.class") or "Error"
            )
            span.add_event(
                "exception",
                {"exception.message": str(message), "exception.type": str(etype)},
            )

    def end_span(self) -> None:
        if not self._stack:
            return
        span, token, _ = self._stack.pop()
        self._context.detach(token)
        span.end()

    def record_metric(self, name: str, value: float, *, attrs: dict[str, Any]) -> None:
        histogram = {
            "gen_ai.client.token.usage": self._token_histogram,
            "gen_ai.client.operation.duration": self._duration_histogram,
        }.get(name)
        if histogram is None:
            return
        histogram.record(value, attributes=_otel_attrs(attrs))

    def flush(self) -> None:
        self._provider.force_flush()
        if self._meter_provider is not None:
            self._meter_provider.force_flush()


def _otel_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    """Coerce span attributes to OTel-legal primitives (drop Nones, stringify dicts)."""

    clean: dict[str, Any] = {}
    for key, value in attrs.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            clean[key] = value
        elif isinstance(value, (list, tuple)) and all(
            isinstance(item, (str, bool, int, float)) for item in value
        ):
            clean[key] = list(value)
        else:
            clean[key] = str(value)
    return clean


def _metrics_endpoint(trace_endpoint: str) -> str:
    if trace_endpoint.endswith("/v1/traces"):
        return trace_endpoint[: -len("/v1/traces")] + "/v1/metrics"
    return trace_endpoint
