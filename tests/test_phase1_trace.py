"""Phase 1 trace tests.

Cover the event->span mapping (names + gen_ai.*/chorus.* attributes), failure
stamping, replay marking, content-off privacy, and that the emitter drives a
TracePort with balanced nesting.
"""

from __future__ import annotations

import asyncio

from murmur.adapters.agents.stochastic import (
    MODEL_NAME,
    stochastic_agent_factory,
    stochastic_tools,
)
from murmur.adapters.storage.memory import InMemoryEventStore
from murmur.adapters.trace.memory import InMemoryTraceCollector
from murmur.core.conductor import RunConductor
from murmur.core.types import TaskSpec
from murmur.trace.emit import emit_trace
from murmur.trace.mapper import events_to_traces

TASK = TaskSpec(
    task_id="demo.echo_uppercase",
    prompt="hello chorus",
    expected_output="HELLO CHORUS",
)


def record(*, success_rate: float, error_rate: float, seed: int = 1, capture: bool = False):
    store = InMemoryEventStore()
    conductor = RunConductor(
        agent_factory=stochastic_agent_factory(
            success_rate=success_rate, error_rate=error_rate, base_seed=seed
        ),
        storage=store,
        tools=stochastic_tools(),
        capture_content=capture,
    )
    asyncio.run(conductor.run(TASK, n=1))
    return list(asyncio.run(store.read_events()))


def _attr_index(spans, key, value=None):
    return [
        s for s in spans if key in s.attributes and (value is None or s.attributes[key] == value)
    ]


def test_run_span_uses_gen_ai_invoke_agent() -> None:
    events = record(success_rate=1.0, error_rate=0.0)
    trace = events_to_traces(events)[0]
    root = trace.spans[0]
    assert root.name == "agent.run"
    assert root.kind == "run"
    assert root.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert root.attributes["chorus.run.id"] == trace.run_id
    assert root.attributes["chorus.trajectory.id"] == trace.trajectory_id
    assert root.status == "ok"


def test_model_and_tool_spans_carry_semconv_attributes() -> None:
    events = record(success_rate=1.0, error_rate=0.0)
    spans = events_to_traces(events)[0].spans

    model_spans = [s for s in spans if s.kind == "model"]
    assert model_spans
    chat = model_spans[0]
    assert chat.name == f"chat {MODEL_NAME}"
    assert chat.attributes["gen_ai.operation.name"] == "chat"
    assert chat.attributes["gen_ai.request.model"] == MODEL_NAME
    assert chat.attributes["gen_ai.usage.input_tokens"] > 0
    assert isinstance(chat.attributes["gen_ai.response.finish_reasons"], list)

    tool_spans = [s for s in spans if s.kind == "tool"]
    assert tool_spans
    assert tool_spans[0].attributes["gen_ai.operation.name"] == "execute_tool"
    assert "gen_ai.tool.name" in tool_spans[0].attributes

    # Steps nest under the run; model/tool nest under a step.
    step_ids = {s.span_id for s in spans if s.kind == "step"}
    assert chat.parent_id in step_ids


def test_step_spans_named_with_index_and_phase() -> None:
    events = record(success_rate=1.0, error_rate=0.0)
    steps = [s for s in events_to_traces(events)[0].spans if s.kind == "step"]
    assert steps
    first = steps[0]
    assert first.attributes["chorus.step.index"] == 0
    assert first.attributes["chorus.step.phase"] == "plan"
    assert first.name.startswith("step 0")


def test_error_trajectory_stamps_failure_class() -> None:
    events = record(success_rate=0.0, error_rate=1.0)
    trace = events_to_traces(events)[0]
    assert trace.outcome == "error"
    assert trace.spans[0].attributes["chorus.failure.class"] == "tool_error"
    erroring = [s for s in trace.spans if s.status == "error" and s.kind == "tool"]
    assert erroring, "the failing tool span should be marked error"
    assert erroring[0].attributes["gen_ai.tool.name"] == "bash"


def test_replay_marks_every_span() -> None:
    events = record(success_rate=1.0, error_rate=0.0)
    live = events_to_traces(events, replay=False)[0]
    replayed = events_to_traces(events, replay=True)[0]
    assert all("chorus.replay" not in s.attributes for s in live.spans)
    assert replayed.replay is True
    assert all(s.attributes.get("chorus.replay") is True for s in replayed.spans)


def test_content_stays_off_by_default() -> None:
    # Record *with* content so the events contain it, then prove the projection
    # drops it unless explicitly asked for.
    events = record(success_rate=1.0, error_rate=0.0, capture=True)

    off = events_to_traces(events, capture_content=False)[0]
    blob_off = repr([s.attributes for s in off.spans])
    assert "hello chorus" not in blob_off
    assert "working on" not in blob_off
    assert all("chorus.model.content" not in s.attributes for s in off.spans)
    assert all(not k.startswith("chorus.tool.arg.") for s in off.spans for k in s.attributes)

    on = events_to_traces(events, capture_content=True)[0]
    assert _attr_index(on.spans, "chorus.model.content")
    assert any(k.startswith("chorus.tool.arg.") for s in on.spans for k in s.attributes)


def test_emitter_drives_port_with_balanced_nesting() -> None:
    events = record(success_rate=1.0, error_rate=0.0)
    trace = events_to_traces(events)[0]
    collector = InMemoryTraceCollector()
    emit_trace(trace, collector)

    assert collector.flushed
    assert collector.depth_balanced
    assert collector.spans[0].name == "agent.run"
    assert collector.spans[0].parent is None
    # Every non-root span names a parent that was opened.
    opened = {s.name for s in collector.spans}
    assert all(s.parent in opened for s in collector.spans if s.parent is not None)


def test_emitter_records_gen_ai_metrics() -> None:
    events = record(success_rate=1.0, error_rate=0.0)
    trace = events_to_traces(events)[0]
    collector = InMemoryTraceCollector()
    emit_trace(trace, collector)

    names = [metric.name for metric in collector.metrics]
    assert "gen_ai.client.operation.duration" in names
    assert "gen_ai.client.token.usage" in names

    token_metrics = [m for m in collector.metrics if m.name == "gen_ai.client.token.usage"]
    token_types = {metric.attributes["gen_ai.token.type"] for metric in token_metrics}
    assert token_types == {"input", "output"}
    assert all(metric.value > 0 for metric in token_metrics)
