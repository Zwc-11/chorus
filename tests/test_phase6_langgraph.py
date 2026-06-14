"""Phase 6 LangGraph adapter tests.

A fake graph yields LangGraph's standard ``astream_events`` stream, so the adapter
is exercised end to end with no langgraph install and no network: it should record
the agent's model and tool calls through the gateway, surface tool errors to the
diagnosis classifier, and flow through the conductor + trace projection.
"""

from __future__ import annotations

import asyncio

from murmur.adapters.agents.langgraph import LangGraphAgent
from murmur.adapters.storage.memory import InMemoryEventStore
from murmur.core.classify import classify_trajectory
from murmur.core.conductor import RunConductor
from murmur.core.events import EventRecorder, EventType
from murmur.core.types import TaskSpec
from murmur.gateway.tool_gateway import ToolGateway
from murmur.trace.mapper import events_to_traces

TASK = TaskSpec(
    task_id="demo.echo_uppercase",
    prompt="hello chorus",
    expected_output="HELLO CHORUS",
)


class _FakeMessage:
    def __init__(self, content, input_tokens, output_tokens, model="claude-sim"):
        self.content = content
        self.usage_metadata = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        self.response_metadata = {"model_name": model}


class _FakeGraph:
    """Minimal stand-in for a compiled LangGraph: replays a scripted event stream."""

    def __init__(self, events):
        self._events = events

    async def astream_events(self, state, version="v2"):
        for event in self._events:
            yield event


def _react_events():
    return [
        {"event": "on_chat_model_end", "data": {"output": _FakeMessage("let me search", 100, 20)}},
        {"event": "on_tool_start", "name": "search", "data": {"input": {"query": "x"}}},
        {"event": "on_tool_end", "name": "search", "data": {"output": "found it"}},
        # Final answer arrives as Anthropic-style content blocks (list form).
        {
            "event": "on_chat_model_end",
            "data": {"output": _FakeMessage([{"type": "text", "text": "HELLO CHORUS"}], 120, 8)},
        },
    ]


def _record_gateway() -> tuple[InMemoryEventStore, ToolGateway]:
    store = InMemoryEventStore()
    recorder = EventRecorder(store, "run_lg", "run_lg_t1")
    return store, ToolGateway.record(recorder=recorder, tools={})


def test_adapter_records_model_tool_and_step_events() -> None:
    store, gateway = _record_gateway()
    agent = LangGraphAgent(_FakeGraph(_react_events()))

    output = asyncio.run(agent.run(TASK, gateway))
    events = list(asyncio.run(store.read_events()))
    kinds = [event.type for event in events]

    assert output == "HELLO CHORUS"  # list-form content is flattened
    assert kinds.count(EventType.MODEL_CALL) == 2
    assert kinds.count(EventType.TOOL_CALL) == 1
    assert kinds.count(EventType.TOOL_RESULT) == 1
    assert EventType.STEP_STARTED in kinds

    model = next(e for e in events if e.type == EventType.MODEL_CALL)
    assert model.payload["input_tokens"] == 100
    assert model.payload["model"] == "claude-sim"

    tool_call = next(e for e in events if e.type == EventType.TOOL_CALL)
    assert tool_call.payload["tool"] == "search"
    assert tool_call.payload["args"] == {"query": "x"}


def test_tool_error_is_recorded_and_diagnosed() -> None:
    store, gateway = _record_gateway()
    stream = [
        {"event": "on_chat_model_end", "data": {"output": _FakeMessage("run tests", 50, 10)}},
        {"event": "on_tool_start", "name": "bash", "data": {"input": {"cmd": "pytest"}}},
        {"event": "on_tool_error", "name": "bash", "data": {"error": "exit 1"}},
    ]
    asyncio.run(LangGraphAgent(_FakeGraph(stream)).run(TASK, gateway))
    events = list(asyncio.run(store.read_events()))

    result = next(e for e in events if e.type == EventType.TOOL_RESULT)
    assert result.payload["error"] == "exit 1"

    diagnosis = classify_trajectory(events, task=TASK)
    assert diagnosis is not None
    assert diagnosis.cls == "tool_error"


def test_runs_through_conductor_and_projects_spans() -> None:
    store = InMemoryEventStore()
    agent = LangGraphAgent(_FakeGraph(_react_events()))
    conductor = RunConductor(agent=agent, storage=store, tools={})

    result = asyncio.run(conductor.run(TASK, n=1))
    assert len(result.trajectories) == 1
    assert result.trajectories[0].outcome == "pass"  # output matches the contract

    events = list(asyncio.run(store.read_events()))
    spans = events_to_traces(events)[0].spans
    kinds = {span.kind for span in spans}
    assert {"run", "step", "model", "tool"} <= kinds


def test_from_react_agent_requires_the_extra() -> None:
    # Without the optional langgraph install, the helper must fail loudly and clearly.
    import importlib.util

    if importlib.util.find_spec("langgraph") is not None:  # pragma: no cover - extra installed
        return
    try:
        LangGraphAgent.from_react_agent(object(), [])
        raise AssertionError("expected ImportError without the agents extra")
    except ImportError as exc:
        assert "murmur-ai-harness[agents]" in str(exc)
