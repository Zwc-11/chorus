"""OTLP TracePort adapter (Phoenix default, LangSmith via config).

Exports projected spans over OTLP using the OpenTelemetry SDK. Both Phoenix and
LangSmith ingest native OTLP, so the same adapter targets either by endpoint;
selecting a backend is configuration, not a code change.

The OpenTelemetry packages are an optional dependency. Importing this module is
cheap; the SDK is imported only when you actually build a port, so the rest of
Chorus runs with no tracing dependency installed. Install with::

    pip install "chorus-harness[otel]"
"""

from __future__ import annotations

import os
from typing import Any

# Pinned GenAI semantic-convention opt-in (the spec is still in Development).
SEMCONV_OPT_IN = "gen_ai_latest_experimental"

_BACKEND_DEFAULTS = {
    "phoenix": "http://localhost:6006/v1/traces",
    "langsmith": "https://api.smith.langchain.com/otel/v1/traces",
}


class OtelNotInstalled(RuntimeError):
    """Raised when the optional OpenTelemetry packages are not available."""


def _require_otel() -> Any:
    try:
        from opentelemetry import context, trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise OtelNotInstalled(
            'OpenTelemetry is not installed. Install the extra: pip install "chorus-harness[otel]"'
        ) from exc
    return (
        context,
        trace,
        OTLPSpanExporter,
        Resource,
        TracerProvider,
        BatchSpanProcessor,
    )


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
        OTLPSpanExporter,
        Resource,
        TracerProvider,
        BatchSpanProcessor,
    ) = _require_otel()

    # Opt into the GenAI semconv explicitly so attribute names are stable.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", SEMCONV_OPT_IN)

    resolved_endpoint = endpoint or _BACKEND_DEFAULTS.get(backend, _BACKEND_DEFAULTS["phoenix"])
    resolved_headers = headers
    if resolved_headers is None and backend == "langsmith":
        resolved_headers = _langsmith_headers()

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=resolved_endpoint, headers=resolved_headers)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    tracer = provider.get_tracer("chorus.trace")
    return OtlpTracePort(tracer=tracer, provider=provider, context=context, trace=trace)


class OtlpTracePort:
    """``TracePort`` that opens/closes real OTel spans with correct nesting."""

    def __init__(self, *, tracer: Any, provider: Any, context: Any, trace: Any) -> None:
        self._tracer = tracer
        self._provider = provider
        self._context = context
        self._trace = trace
        self._stack: list[tuple[Any, Any]] = []

    def start_span(self, name: str, *, kind: str, attrs: dict[str, Any]) -> None:
        span = self._tracer.start_span(name, attributes=_otel_attrs(attrs))
        token = self._context.attach(self._trace.set_span_in_context(span))
        self._stack.append((span, token))

    def set_status(self, status: str) -> None:
        if not self._stack:
            return
        span, _ = self._stack[-1]
        codes = self._trace.StatusCode
        status_code = codes.ERROR if status == "error" else codes.OK
        span.set_status(self._trace.Status(status_code))

    def end_span(self) -> None:
        if not self._stack:
            return
        span, token = self._stack.pop()
        self._context.detach(token)
        span.end()

    def flush(self) -> None:
        self._provider.force_flush()


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
