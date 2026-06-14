"""LangGraph ``AgentPort`` adapter -- integrate, don't replace.

Wraps a *real* LangGraph agent (any compiled graph) and drives it for one
trajectory, recording its model and tool calls through the Chorus gateway. A
wrapped graph therefore inherits Chorus's tracing, divergence, and per-step
diagnosis without any change to the graph itself.

The adapter is **observational**: the graph runs its own model and tools; Chorus
records what happened (``gateway.model`` / ``gateway.record_tool``) rather than
executing tools itself. It reads LangGraph's standard ``astream_events`` stream,
so it does not import ``langgraph`` at all -- it depends only on that event
contract, which keeps it version-tolerant and testable with a fake graph (no
network in CI). Use :meth:`LangGraphAgent.from_react_agent` for the common case.
"""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Any

from murmur.core.ports import ToolGatewayPort
from murmur.core.types import TaskSpec

# What a "graph" must provide: LangChain/LangGraph's streaming-events API.
StreamingGraph = Any
BuildInput = Callable[[TaskSpec], Any]
FinalOutput = Callable[[Any], str]


def _default_input(task: TaskSpec) -> Any:
    # The 2-tuple message form create_react_agent accepts without importing langchain.
    return {"messages": [("user", task.prompt)]}


class LangGraphAgent:
    """Drive a LangGraph graph for one trajectory, recording it through the gateway."""

    def __init__(
        self,
        graph: StreamingGraph,
        *,
        build_input: BuildInput = _default_input,
        final_output: FinalOutput | None = None,
        model_name: str = "langgraph",
        stream_version: str = "v2",
    ) -> None:
        self._graph = graph
        self._build_input = build_input
        self._final_output = final_output
        self._model_name = model_name
        self._stream_version = stream_version

    @classmethod
    def from_react_agent(
        cls, model: Any, tools: list[Any], *, model_name: str = "langgraph", **kwargs: Any
    ) -> LangGraphAgent:
        """Build a prebuilt ReAct agent (requires the optional ``agents`` extra)."""

        try:
            from langgraph.prebuilt import create_react_agent
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "LangGraph is not installed. Install the extra: "
                'pip install "murmur-ai-harness[agents]"'
            ) from exc
        return cls(create_react_agent(model, tools), model_name=model_name, **kwargs)

    @property
    def name(self) -> str:
        return "langgraph"

    async def run(self, task: TaskSpec, gateway: ToolGatewayPort) -> str:
        state = self._build_input(task)
        step = 0
        last_text = ""
        pending: dict[str, Any] | None = None

        async for event in self._graph.astream_events(state, version=self._stream_version):
            kind = _get(event, "event")
            if kind == "on_chat_model_end":
                message = _get(_get(event, "data", {}), "output")
                input_tokens, output_tokens = _usage(message)
                text = _text(message)
                await gateway.step(index=step, phase="model")
                await gateway.model(
                    model=_model_name(message, self._model_name),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    finish_reason="stop",
                    content=text or None,
                )
                if text:
                    last_text = text
                step += 1
            elif kind == "on_tool_start":
                pending = {
                    "name": _get(event, "name", "tool"),
                    "args": _tool_input(event),
                    "t0": perf_counter(),
                }
            elif kind == "on_tool_end":
                if pending is not None:
                    await gateway.record_tool(
                        pending["name"],
                        pending["args"],
                        result=_tool_output(event),
                        latency_ms=(perf_counter() - pending["t0"]) * 1000,
                    )
                    pending = None
            elif kind == "on_tool_error":
                if pending is not None:
                    await gateway.record_tool(
                        pending["name"],
                        pending["args"],
                        error=str(_get(_get(event, "data", {}), "error", "tool error")),
                        error_type="ToolError",
                        latency_ms=(perf_counter() - pending["t0"]) * 1000,
                    )
                    pending = None

        if self._final_output is not None:
            return self._final_output(state)
        return last_text


def _get(obj: Any, attr: str, default: Any = None) -> Any:
    """Attribute-or-key access, tolerant of both objects and dicts."""

    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _usage(message: Any) -> tuple[int, int]:
    meta = _get(message, "usage_metadata")
    if isinstance(meta, dict):
        return int(meta.get("input_tokens") or 0), int(meta.get("output_tokens") or 0)
    response_meta = _get(message, "response_metadata", {}) or {}
    usage = response_meta.get("token_usage") or response_meta.get("usage") or {}
    in_tok = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    out_tok = usage.get("completion_tokens", usage.get("output_tokens", 0))
    return int(in_tok or 0), int(out_tok or 0)


def _model_name(message: Any, default: str) -> str:
    response_meta = _get(message, "response_metadata", {}) or {}
    return str(response_meta.get("model_name") or response_meta.get("model") or default)


def _text(message: Any) -> str:
    content = _get(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(_get(block, "text", ""))
            for block in content
        )
    return str(content)


def _tool_input(event: Any) -> dict[str, Any]:
    data = _get(event, "data", {}) or {}
    value = data.get("input") if isinstance(data, dict) else None
    if isinstance(value, dict):
        return value
    return {"input": value}


def _tool_output(event: Any) -> str:
    data = _get(event, "data", {}) or {}
    output = data.get("output") if isinstance(data, dict) else None
    content = _get(output, "content")
    return content if isinstance(content, str) else str(content if content is not None else output)
