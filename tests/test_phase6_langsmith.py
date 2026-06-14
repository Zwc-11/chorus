"""Phase 6 LangSmith export tests.

Verify the LangSmith OTLP contract (endpoint + headers), the project URL, and that
the event->span->emit pipeline LangSmith receives produces balanced ``gen_ai.*``
spans -- all without a live LangSmith account or the network.
"""

from __future__ import annotations

import asyncio
import importlib.util

from murmur.adapters.agents.stochastic import stochastic_agent_factory, stochastic_tools
from murmur.adapters.storage.memory import InMemoryEventStore
from murmur.adapters.trace.memory import InMemoryTraceCollector
from murmur.adapters.trace.otlp import (
    _BACKEND_DEFAULTS,
    LANGSMITH_APP_URL,
    _CountingSpanExporter,
    _langsmith_headers,
    _metrics_endpoint,
    build_otlp_trace_port,
    langsmith_attributes,
    langsmith_project_url,
)
from murmur.core.conductor import RunConductor
from murmur.core.types import TaskSpec
from murmur.trace.emit import emit_traces
from murmur.trace.mapper import events_to_traces

TASK = TaskSpec(
    task_id="demo.echo_uppercase",
    prompt="hello chorus",
    expected_output="HELLO CHORUS",
)


def test_langsmith_endpoint_matches_the_otel_contract() -> None:
    endpoint = _BACKEND_DEFAULTS["langsmith"]
    assert endpoint.startswith("https://api.smith.langchain.com")
    assert endpoint.endswith("/otel/v1/traces")


def test_langsmith_headers_carry_key_and_project(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test-key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
    headers = _langsmith_headers()
    assert headers["x-api-key"] == "ls-test-key"
    assert headers["Langsmith-Project"] == "my-project"


def test_langsmith_headers_omit_key_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setenv("LANGSMITH_PROJECT", "p")
    headers = _langsmith_headers()
    assert "x-api-key" not in headers
    assert headers["Langsmith-Project"] == "p"


def test_project_url_is_well_formed_and_encoded() -> None:
    url = langsmith_project_url("my proj")
    assert url.startswith(LANGSMITH_APP_URL)
    assert "my%20proj" in url  # the project name is URL-encoded


def test_phoenix_metrics_endpoint_is_derived_from_trace_endpoint() -> None:
    assert _metrics_endpoint("http://localhost:6006/v1/traces").endswith("/v1/metrics")
    assert _metrics_endpoint("http://collector/custom") == "http://collector/custom"


def test_langsmith_attributes_mirror_chorus_to_metadata_and_kind() -> None:
    # LangSmith drops raw attributes; chorus.* must be mirrored under
    # langsmith.metadata.* and the span kind sent as langsmith.span.kind.
    out = langsmith_attributes(
        "model",
        {
            "chorus.run.id": "run_x",
            "chorus.failure.class": "tool_error",
            "gen_ai.request.model": "m",
        },
    )
    assert out["langsmith.span.kind"] == "llm"
    assert out["langsmith.metadata.chorus.run.id"] == "run_x"
    assert out["langsmith.metadata.chorus.failure.class"] == "tool_error"
    # gen_ai.* is ingested natively, never mirrored into metadata.
    assert not any(key.endswith("gen_ai.request.model") for key in out)


def test_langsmith_kind_maps_run_types() -> None:
    assert langsmith_attributes("tool", {})["langsmith.span.kind"] == "tool"
    assert langsmith_attributes("run", {})["langsmith.span.kind"] == "chain"
    assert langsmith_attributes("contract", {})["langsmith.span.kind"] == "chain"


def test_export_pipeline_emits_balanced_gen_ai_spans() -> None:
    # The exact path LangSmith receives: events -> traces -> emit -> TracePort.
    store = InMemoryEventStore()
    conductor = RunConductor(
        agent_factory=stochastic_agent_factory(success_rate=1.0, error_rate=0.0, base_seed=1),
        storage=store,
        tools=stochastic_tools(),
    )
    asyncio.run(conductor.run(TASK, n=1))
    events = list(asyncio.run(store.read_events()))

    collector = InMemoryTraceCollector()
    emit_traces(events_to_traces(events), collector)

    assert collector.flushed
    assert collector.depth_balanced
    assert collector.spans[0].name == "agent.run"
    model_spans = [span for span in collector.spans if span.kind == "model"]
    assert model_spans
    assert model_spans[0].attributes["gen_ai.operation.name"] == "chat"


class _FakeInner:
    """Stands in for an OTLP exporter, returning a scripted sequence of results."""

    def __init__(self, results: list) -> None:
        self._results = list(results)

    def export(self, spans):
        return self._results.pop(0)

    def shutdown(self):
        return None

    def force_flush(self, timeout_millis: int = 30000):
        return True


def test_counting_exporter_flags_rejected_batches() -> None:
    # The bug: a 401 was logged and swallowed, so the CLI reported success anyway.
    success = object()  # sentinel standing in for SpanExportResult.SUCCESS
    exporter = _CountingSpanExporter(_FakeInner([success, "FAILURE", success]), success)
    exporter.export([1, 2, 3])
    exporter.export([4])  # rejected (e.g. 401)
    exporter.export([5, 6])

    stats = exporter.stats()
    assert (stats.ok_batches, stats.ok_spans, stats.failed_batches) == (2, 5, 1)
    assert stats.ok is False  # any failed batch -> not ok -> CLI must exit non-zero


def test_counting_exporter_ok_when_all_accepted() -> None:
    success = object()
    exporter = _CountingSpanExporter(_FakeInner([success, success]), success)
    exporter.export([1, 2])
    exporter.export([3])
    stats = exporter.stats()
    assert stats.ok is True
    assert stats.ok_spans == 3 and stats.failed_batches == 0


def test_build_langsmith_port_constructs_when_otel_present() -> None:
    if importlib.util.find_spec("opentelemetry") is None:  # pragma: no cover - extra absent
        return  # the [otel] extra is not installed; construction is a Tier-B/live concern
    # Construct only -- do not emit spans, so the test never touches the network.
    port = build_otlp_trace_port(backend="langsmith")
    assert port._langsmith is True
    assert build_otlp_trace_port(backend="phoenix")._langsmith is False
