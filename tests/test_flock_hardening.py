"""Phase 3 hardening: resumable runs, runtime quarantine, budget cap, trace report."""

from __future__ import annotations

import asyncio
from pathlib import Path

from murmur.flock.eventlog import (
    NODE_FINISHED,
    FlockEvent,
    InMemoryFlockLog,
    JsonlFlockLog,
    completed_nodes,
)
from murmur.flock.ir import Node, WorkflowPlan, load_plan_yaml
from murmur.flock.models import offline_resolver
from murmur.flock.report import render_mermaid, render_run_report
from murmur.flock.scheduler import execute_plan

EXAMPLE = Path(__file__).resolve().parents[1] / "murmur" / "flock" / "examples" / "resumes.yaml"


def _example():
    return load_plan_yaml(EXAMPLE.read_text(encoding="utf-8"))


# --- event log ---------------------------------------------------------------------


def test_event_round_trip() -> None:
    e = FlockEvent(kind=NODE_FINISHED, node_id="x", op="map", calls=2)
    assert FlockEvent.from_dict(e.to_dict()) == e


def test_completed_nodes_ignores_failed_and_takes_last_finish() -> None:
    from murmur.flock.artifact import Artifact

    events = [
        FlockEvent(kind="node_failed", node_id="a", op="map", error="boom"),
        FlockEvent.node_finished("a", "map", (Artifact(id="a1", content="ok"),), calls=1),
        FlockEvent.node_finished("a", "map", (Artifact(id="a2", content="redo"),), calls=1),
    ]
    done = completed_nodes(events)
    assert set(done) == {"a"}
    assert done["a"].output[0].id == "a2"  # latest finish wins


# --- resume ------------------------------------------------------------------------


def test_resume_replays_finished_nodes_for_free() -> None:
    plan = _example()
    log = InMemoryFlockLog()
    first = asyncio.run(execute_plan(plan, sources={"resumes": ["a", "b", "c"]}, event_log=log))
    assert first.ok and first.model_calls > 0

    second = asyncio.run(execute_plan(plan, sources={"resumes": ["a", "b", "c"]}, event_log=log))
    assert second.ok
    assert second.model_calls == 0  # every node restored from the log
    assert [a.content for a in second.final] == [a.content for a in first.final]


def test_resume_reruns_only_failed_nodes(tmp_path) -> None:
    plan = _example()
    log = JsonlFlockLog(tmp_path / "run.jsonl", reset=True)

    # First run: a tiny budget trips partway, so some nodes finish and others fail.
    object.__setattr__(plan, "budget_tokens", 80)
    first = asyncio.run(execute_plan(plan, sources={"resumes": ["a", "b"]}, event_log=log))
    assert not first.ok
    finished = set(completed_nodes(log.read()))
    assert finished  # at least the early map work got recorded

    # Resume with a generous budget: finished nodes are skipped, the rest complete.
    object.__setattr__(plan, "budget_tokens", 200_000)
    second = asyncio.run(execute_plan(plan, sources={"resumes": ["a", "b"]}, event_log=log))
    assert second.ok, second.errors
    # Replayed nodes report their original call counts, but the ledger only counts work
    # actually done this run — so the ledger total is strictly less than the per-node sum.
    total_node_calls = sum(r.calls for r in second.results.values())
    assert second.model_calls < total_node_calls
    for nid in finished:
        assert nid in second.results  # restored, present in the resumed report


# --- runtime quarantine ------------------------------------------------------------


def test_untrusted_source_into_trusted_node_is_quarantined() -> None:
    plan = WorkflowPlan(
        goal="read the web and act",
        budget_tokens=10_000,
        sources=("web",),
        nodes=(Node(id="act", op="reduce", inputs=("web",), trust="trusted"),),
    )
    report = asyncio.run(
        execute_plan(plan, sources={"web": ["scraped text"]}, untrusted_sources=["web"])
    )
    assert not report.ok
    assert "QuarantineViolation" in report.results["act"].error


def test_untrusted_source_into_untrusted_node_is_allowed() -> None:
    plan = WorkflowPlan(
        goal="summarize scraped text",
        budget_tokens=10_000,
        sources=("web",),
        nodes=(Node(id="summarize", op="reduce", inputs=("web",), trust="untrusted"),),
    )
    report = asyncio.run(
        execute_plan(plan, sources={"web": ["scraped text"]}, untrusted_sources=["web"])
    )
    assert report.ok
    assert report.final[0].trust == "untrusted"  # taint propagated


# --- budget cap --------------------------------------------------------------------


def test_budget_cap_is_respected() -> None:
    plan = _example()
    report = asyncio.run(execute_plan(plan, sources={"resumes": ["a", "b", "c", "d"]}))
    assert report.spent_tokens <= plan.budget_tokens


# --- trace report ------------------------------------------------------------------


def test_render_mermaid_includes_sources_edges_and_lock() -> None:
    plan = WorkflowPlan(
        goal="g",
        budget_tokens=100,
        sources=("web",),
        nodes=(
            Node(id="read", op="map", inputs=("web",), trust="untrusted"),
            Node(id="sum", op="reduce", inputs=("read",), trust="untrusted"),
        ),
    )
    md = render_mermaid(plan)
    assert "flowchart LR" in md
    assert "web --> read" in md and "read --> sum" in md
    assert "🔒" in md  # untrusted node marked


def test_render_run_report_has_table_and_final() -> None:
    plan = _example()
    report = asyncio.run(
        execute_plan(plan, sources={"resumes": ["a", "b"]}, resolver=offline_resolver())
    )
    md = render_run_report(report, plan=plan)
    assert "# Murmur flock run" in md
    assert "| node | op | trust | artifacts | calls | status |" in md
    assert "## final" in md
    assert "```mermaid" in md


def test_render_run_report_marks_failure() -> None:
    plan = _example()
    object.__setattr__(plan, "budget_tokens", 5)
    report = asyncio.run(execute_plan(plan, sources={"resumes": ["a"]}))
    md = render_run_report(report, plan=plan)
    assert "failed ❌" in md
    assert "ERROR" in md
