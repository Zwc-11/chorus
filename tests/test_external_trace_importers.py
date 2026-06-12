"""External public-SDK trace importers."""

from __future__ import annotations

from chorus.adapters.trace.importers import (
    ClaudeCodeTranscriptImporter,
    LangGraphTraceImporter,
    OpenAIAgentsTraceImporter,
)
from chorus.core.events import EventType
from chorus.trace.mapper import events_to_traces


def test_openai_agents_trace_imports_model_and_tool_events() -> None:
    importer = OpenAIAgentsTraceImporter()
    events = importer.import_events(
        [
            {
                "type": "response.completed",
                "model": "gpt-5.1",
                "usage": {"input_tokens": 100, "output_tokens": 25},
                "duration_ms": 42,
                "content": "inspect repository",
            },
            {
                "type": "tool_call",
                "tool": "read_file",
                "args": {"path": "app.py"},
                "result": "print('ok')",
            },
        ],
        run_id="run_import",
        task_id="demo.external",
    )

    kinds = [event.type for event in events]
    assert EventType.RUN_STARTED in kinds
    assert EventType.TRAJECTORY_STARTED in kinds
    assert kinds.count(EventType.MODEL_CALL) == 1
    assert kinds.count(EventType.TOOL_CALL) == 1
    assert kinds.count(EventType.TOOL_RESULT) == 1

    trace = events_to_traces(events)[0]
    assert trace.total_tokens == 125
    assert {span.kind for span in trace.spans} >= {"run", "step", "model", "tool"}


def test_claude_transcript_imports_tool_errors_for_diagnosis() -> None:
    importer = ClaudeCodeTranscriptImporter()
    events = importer.import_events(
        [
            {"type": "assistant", "model": "claude-code", "content": "run tests"},
            {"type": "tool_use", "name": "bash", "input": {"command": "pytest"}},
            {"type": "tool_error", "name": "bash", "error": "exit 1", "error_type": "ShellError"},
        ],
        run_id="run_claude",
        task_id="demo.external",
        trajectory_id="run_claude_t1",
    )
    result = [event for event in events if event.type == EventType.TOOL_RESULT][0]

    assert result.payload["tool"] == "bash"
    assert result.payload["error"] == "exit 1"
    assert importer.capabilities.hooks is True
    assert importer.capabilities.tool_interception is True


def test_langgraph_importer_declares_live_trace_capabilities() -> None:
    capabilities = LangGraphTraceImporter.capabilities

    assert capabilities.trace_import is True
    assert capabilities.live_execution is True
    assert "trace-import" in capabilities.labels()
