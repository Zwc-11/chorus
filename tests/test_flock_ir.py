"""Workflow IR: schema validation, the DAG rule, the taint rule, and round-trips."""

from __future__ import annotations

import pytest

from murmur.flock.ir import (
    Node,
    PlanValidationError,
    WorkflowPlan,
    dump_plan_yaml,
    load_plan_yaml,
    parse_plan,
    validate_plan,
)

# The worked example from the design doc: score -> filter -> tournament -> verify -> reduce.
RESUME_PLAN = {
    "goal": "rank 80 resumes for a backend role; verify the top 10",
    "budget_tokens": 200000,
    "sources": ["resumes"],
    "nodes": [
        {"id": "score", "op": "map", "inputs": ["resumes"], "model": "deepseek-v4-flash"},
        {"id": "shortlist", "op": "filter", "inputs": ["score"], "params": {"top_k": 20}},
        {"id": "bracket", "op": "tournament", "inputs": ["shortlist"]},
        {"id": "check", "op": "verify", "inputs": ["bracket"], "params": {"top_k": 10}},
        {"id": "report", "op": "reduce", "inputs": ["check"], "effort": "high"},
    ],
}


def test_worked_example_plan_validates() -> None:
    plan = parse_plan(RESUME_PLAN)
    assert plan.goal.startswith("rank 80 resumes")
    assert plan.node_ids == ("score", "shortlist", "bracket", "check", "report")


def test_topological_order_respects_dependencies() -> None:
    plan = parse_plan(RESUME_PLAN)
    order = [n.id for n in plan.topological_order()]
    assert order.index("score") < order.index("shortlist") < order.index("bracket")
    assert order.index("check") < order.index("report")


def test_dict_round_trip_is_stable() -> None:
    plan = parse_plan(RESUME_PLAN)
    again = parse_plan(plan.to_dict())
    assert again.to_dict() == plan.to_dict()


def test_yaml_round_trip() -> None:
    plan = parse_plan(RESUME_PLAN)
    reparsed = load_plan_yaml(dump_plan_yaml(plan))
    assert reparsed.to_dict() == plan.to_dict()


def test_rejects_nonpositive_budget() -> None:
    bad = {**RESUME_PLAN, "budget_tokens": 0}
    with pytest.raises(PlanValidationError, match="budget_tokens must be > 0"):
        parse_plan(bad)


def test_rejects_empty_plan() -> None:
    with pytest.raises(PlanValidationError, match="no nodes"):
        parse_plan({"goal": "x", "budget_tokens": 10, "nodes": []})


def test_rejects_duplicate_node_id() -> None:
    plan = WorkflowPlan(
        goal="x",
        budget_tokens=10,
        nodes=(Node(id="a", op="map"), Node(id="a", op="reduce", inputs=("a",))),
    )
    with pytest.raises(PlanValidationError, match="duplicate node id"):
        validate_plan(plan)


def test_rejects_unknown_op() -> None:
    plan = WorkflowPlan(goal="x", budget_tokens=10, nodes=(Node(id="a", op="frobnicate"),))  # type: ignore[arg-type]
    with pytest.raises(PlanValidationError, match="unknown op"):
        validate_plan(plan)


def test_rejects_dangling_input() -> None:
    plan = WorkflowPlan(
        goal="x", budget_tokens=10, nodes=(Node(id="a", op="reduce", inputs=("ghost",)),)
    )
    with pytest.raises(PlanValidationError, match="not a known node id or source"):
        validate_plan(plan)


def test_rejects_self_dependency() -> None:
    plan = WorkflowPlan(goal="x", budget_tokens=10, nodes=(Node(id="a", op="loop", inputs=("a",)),))
    with pytest.raises(PlanValidationError, match="depends on itself"):
        validate_plan(plan)


def test_rejects_cycle() -> None:
    plan = WorkflowPlan(
        goal="x",
        budget_tokens=10,
        sources=("seed",),
        nodes=(
            Node(id="a", op="map", inputs=("seed", "b")),
            Node(id="b", op="map", inputs=("a",)),
        ),
    )
    with pytest.raises(PlanValidationError, match="dependency cycle"):
        validate_plan(plan)


def test_taint_cannot_be_laundered_into_trusted_node() -> None:
    plan = WorkflowPlan(
        goal="x",
        budget_tokens=100,
        sources=("web",),
        nodes=(
            Node(id="read", op="map", inputs=("web",), trust="untrusted"),
            Node(id="act", op="reduce", inputs=("read",), trust="trusted"),
        ),
    )
    with pytest.raises(PlanValidationError, match="reads untrusted input"):
        validate_plan(plan)


def test_untrusted_node_may_consume_untrusted_input() -> None:
    plan = WorkflowPlan(
        goal="x",
        budget_tokens=100,
        sources=("web",),
        nodes=(
            Node(id="read", op="map", inputs=("web",), trust="untrusted"),
            Node(id="summarize", op="reduce", inputs=("read",), trust="untrusted"),
        ),
    )
    validate_plan(plan)  # does not raise
