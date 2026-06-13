"""The model-backed map operator: isolated fan-out through a ModelPort.

These tests run whole workflows through ``WorkflowRuntime`` with fake ports, so
they cover the operator plus its budget checks, artifacts, and proof wiring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from murmur.adapters.models.fake import FakeModel
from murmur.application.workflow_runtime import WorkflowRuntime
from murmur.core.model_port import ModelResponse
from murmur.domain.workflow import WorkflowNode, WorkflowPlan


def _map_plan(n: int = 3, *, budget: dict[str, Any] | None = None) -> WorkflowPlan:
    return WorkflowPlan(
        version=1,
        schema_version=1,
        goal="fix the checkout discount bug",
        budget=budget or {},
        nodes=(
            WorkflowNode(id="fan", op="map", params={"n": n, "prompt": "propose a patch"}),
        ),
    )


def _runtime(tmp_path: Path, **kwargs: Any) -> WorkflowRuntime:
    return WorkflowRuntime(repo_root=tmp_path, out_root=tmp_path / "runs", **kwargs)


class AttemptAwarePort:
    """Fails attempts whose system prompt names them in ``failing`` (deterministic)."""

    def __init__(self, failing: tuple[int, ...]) -> None:
        self.failing = failing

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> ModelResponse:
        system = messages[0]["content"]
        for index in self.failing:
            if f"attempt {index} of" in system:
                raise RuntimeError(f"simulated provider failure for attempt {index}")
        return ModelResponse(text=f"patch for: {system[:40]}", model=model, cost_usd=0.01)


def test_map_without_model_port_keeps_placeholder(tmp_path: Path) -> None:
    result = _runtime(tmp_path).run(_map_plan(2))
    node = result.node_results[0]
    assert node.passed
    assert node.result["items"] == [
        "candidate_1: propose a patch",
        "candidate_2: propose a patch",
    ]
    assert result.proof["budget"]["model_calls"] == 0


def test_map_with_model_fans_out_isolated_attempts(tmp_path: Path) -> None:
    port = FakeModel(responses=["patch-a", "patch-b", "patch-c"], cost_per_call_usd=0.02)
    result = _runtime(tmp_path, model_port=port, default_model="deepseek-chat").run(_map_plan(3))

    assert result.passed
    assert len(port.calls) == 3
    assert all(call.model == "deepseek-chat" for call in port.calls)
    # Isolation: every attempt gets its own context, addressed individually.
    systems = [call.messages[0]["content"] for call in port.calls]
    assert len(set(systems)) == 3
    assert any("attempt 1 of 3" in system for system in systems)

    node = result.node_results[0]
    assert node.output == "3/3 attempts succeeded"
    assert node.taint == "untrusted_model_output"
    texts = {item["text"] for item in node.result["items"]}
    assert texts == {"patch-a", "patch-b", "patch-c"}

    # Per-attempt workspaces with messages + response + result on disk.
    for attempt in ("attempt_01", "attempt_02", "attempt_03"):
        attempt_dir = result.run_dir / "nodes" / "fan" / "attempts" / attempt
        assert (attempt_dir / "messages.json").is_file()
        assert (attempt_dir / "response.txt").is_file()
        assert (attempt_dir / "result.json").is_file()
    candidates = result.run_dir / "nodes" / "fan" / "artifacts" / "candidates.json"
    assert len(json.loads(candidates.read_text(encoding="utf-8"))) == 3

    assert result.proof["budget"]["model_calls"] == 3
    assert result.proof["budget"]["cost_usd"] == pytest.approx(0.06)


def test_map_survives_partial_attempt_failures(tmp_path: Path) -> None:
    port = AttemptAwarePort(failing=(1, 3))
    result = _runtime(tmp_path, model_port=port, default_model="m").run(_map_plan(3))

    node = result.node_results[0]
    assert node.passed  # one survivor is enough to continue
    assert node.output == "1/3 attempts succeeded"
    statuses = [item["status"] for item in node.result["items"]]
    assert statuses == ["error", "ok", "error"]
    assert "simulated provider failure" in node.result["items"][0]["error"]


def test_map_fails_and_quarantines_when_all_attempts_fail(tmp_path: Path) -> None:
    port = AttemptAwarePort(failing=(1, 2))
    result = _runtime(tmp_path, model_port=port, default_model="m").run(_map_plan(2))
    node = result.node_results[0]
    assert not node.passed
    assert node.quarantined
    assert result.status == "fail"


def test_map_enforces_max_model_calls_before_fanout(tmp_path: Path) -> None:
    port = FakeModel()
    plan = _map_plan(3, budget={"max_model_calls": 2})
    result = _runtime(tmp_path, model_port=port, default_model="m").run(plan)
    node = result.node_results[0]
    assert not node.passed
    assert "max_model_calls=2" in node.error
    assert port.calls == []  # nothing launched past the budget gate


def test_map_requires_a_model_id(tmp_path: Path) -> None:
    result = _runtime(tmp_path, model_port=FakeModel()).run(_map_plan(1))
    node = result.node_results[0]
    assert not node.passed
    assert "no model id" in node.error


def test_map_temperature_node_override_and_default(tmp_path: Path) -> None:
    port = FakeModel()
    plan = WorkflowPlan(
        version=1,
        schema_version=1,
        goal="g",
        budget={},
        nodes=(
            WorkflowNode(id="hot", op="map", params={"n": 1}, temperature=1.0),
            WorkflowNode(id="warm", op="map", params={"n": 1}),
        ),
    )
    _runtime(tmp_path, model_port=port, default_model="m").run(plan)
    assert port.calls[0].temperature == 1.0
    assert port.calls[1].temperature == 0.7  # fan-out default favors diversity


def test_map_feeds_dependency_output_into_attempt_context(tmp_path: Path) -> None:
    port = FakeModel()
    plan = WorkflowPlan(
        version=1,
        schema_version=1,
        goal="fix it",
        budget={},
        nodes=(
            WorkflowNode(id="classify", op="classify", params={"task": "fix the test"}),
            WorkflowNode(id="fan", op="map", inputs=("classify",), params={"n": 1}),
        ),
    )
    _runtime(tmp_path, model_port=port, default_model="m").run(plan)
    user = port.calls[0].messages[1]["content"]
    assert "Context from `classify`" in user
