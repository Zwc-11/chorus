"""Phase 2 planner: JSON extraction, schema-constrained planning, repair, fallback."""

from __future__ import annotations

import asyncio
import json

import pytest

from murmur.flock.adapters.fake import FakeModel
from murmur.flock.ir import Effort, PlanValidationError
from murmur.flock.planner import extract_json, plan_workflow, template_plan
from murmur.flock.scheduler import execute_plan

VALID_PLAN = {
    "goal": "rank items",
    "budget_tokens": 50000,
    "sources": ["items"],
    "nodes": [
        {"id": "score", "op": "map", "inputs": ["items"], "model": "deepseek-v4-flash"},
        {"id": "best", "op": "filter", "inputs": ["score"], "params": {"top_k": 3}},
        {
            "id": "out",
            "op": "reduce",
            "inputs": ["best"],
            "model": "deepseek-v4-pro",
            "effort": "high",
        },
    ],
}

# A plan that fails validation: 'out' depends on a node that doesn't exist.
INVALID_PLAN = {
    "goal": "rank items",
    "budget_tokens": 50000,
    "sources": ["items"],
    "nodes": [{"id": "out", "op": "reduce", "inputs": ["ghost"]}],
}


def test_extract_json_plain() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_fence_and_prose() -> None:
    text = 'Here is the plan:\n```json\n{"goal": "x", "n": [1, 2]}\n```\nDone.'
    assert extract_json(text) == {"goal": "x", "n": [1, 2]}


def test_extract_json_ignores_braces_in_strings() -> None:
    assert extract_json('{"role": "emit {score}"}') == {"role": "emit {score}"}


def test_extract_json_raises_without_object() -> None:
    with pytest.raises(ValueError, match="no JSON object"):
        extract_json("no json here")


def test_template_plan_is_valid_and_runs() -> None:
    plan = template_plan("summarize the docs", sources=["docs"])
    assert plan.node_ids == ("work", "synthesize")
    report = asyncio.run(execute_plan(plan, sources={"docs": ["a", "b", "c"]}))
    assert report.ok
    assert len(report.final) == 1


def test_plan_workflow_accepts_valid_model_plan() -> None:
    model = FakeModel(responder=lambda s, u, e: json.dumps(VALID_PLAN))
    plan = asyncio.run(plan_workflow("rank items", model=model, sources=["items"]))
    assert plan.node_ids == ("score", "best", "out")
    assert plan.budget_tokens == 50000


def test_plan_workflow_repairs_after_invalid_then_valid() -> None:
    replies = iter([json.dumps(INVALID_PLAN), json.dumps(VALID_PLAN)])

    def responder(system: str, user: str, effort: Effort) -> str:
        # On the repair attempt the prompt carries the rejection reason.
        return next(replies)

    model = FakeModel(responder=responder)
    plan = asyncio.run(plan_workflow("rank items", model=model, sources=["items"], max_repair=2))
    assert plan.node_ids == ("score", "best", "out")
    assert model.call_count == 2  # first rejected, second accepted


def test_plan_workflow_falls_back_to_template_on_garbage() -> None:
    model = FakeModel(responder=lambda s, u, e: "I cannot help with that.")
    plan = asyncio.run(
        plan_workflow("do the thing", model=model, sources=["stuff"], max_repair=1)
    )
    assert plan.node_ids == ("work", "synthesize")  # template fallback


def test_plan_workflow_raises_when_fallback_disabled() -> None:
    model = FakeModel(responder=lambda s, u, e: "nope")
    with pytest.raises(PlanValidationError, match="did not produce a valid plan"):
        asyncio.run(
            plan_workflow("x", model=model, sources=["s"], max_repair=0, fallback=False)
        )


def test_planner_output_is_executable_end_to_end() -> None:
    # The whole self-writing loop, offline: plan from NL, then run the plan.
    model = FakeModel(responder=lambda s, u, e: json.dumps(VALID_PLAN))
    plan = asyncio.run(plan_workflow("rank items", model=model, sources=["items"]))
    report = asyncio.run(execute_plan(plan, sources={"items": ["x", "y", "z", "w"]}))
    assert report.ok, report.errors
    assert len(report.final) == 1
