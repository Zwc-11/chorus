"""Phase 1 runtime: the seven operators and the DAG scheduler.

Everything runs on deterministic fakes — no network — so a hand-written plan is
exercised end-to-end, in parallel, with a real budget ledger.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from murmur.flock.adapters.fake import FakeModel
from murmur.flock.artifact import Artifact
from murmur.flock.gateway import ModelReply
from murmur.flock.ir import Effort, Node, load_plan_yaml
from murmur.flock.models import offline_resolver
from murmur.flock.operators import (
    NodeContext,
    op_filter,
    op_map,
    op_reduce,
    op_tournament,
    op_verify,
)
from murmur.flock.scheduler import execute_plan

EXAMPLE = Path(__file__).resolve().parents[1] / "murmur" / "flock" / "examples" / "resumes.yaml"


def _ctx(node: Node, items: list[Artifact], model: object, *, parallel: int = 8) -> NodeContext:
    return NodeContext(
        node=node,
        items=items,
        model=model,  # type: ignore[arg-type]
        semaphore=asyncio.Semaphore(parallel),
    )


def _arts(*pairs: tuple[str, str]) -> list[Artifact]:
    return [Artifact(id=i, content=c) for i, c in pairs]


# --- operators ---------------------------------------------------------------------


def test_map_fans_out_one_result_per_item() -> None:
    node = Node(id="score", op="map", role="score it")
    items = _arts(("r1", "alice"), ("r2", "bob"), ("r3", "cara"))
    out = asyncio.run(op_map(_ctx(node, items, FakeModel())))
    assert len(out) == 3
    assert {a.meta["source_item"] for a in out} == {"r1", "r2", "r3"}


def test_map_parses_score_from_json_reply() -> None:
    model = FakeModel(responder=lambda s, u, e: '{"score": 42, "reasons": "ok"}')
    node = Node(id="score", op="map")
    out = asyncio.run(op_map(_ctx(node, _arts(("r1", "x")), model)))
    assert out[0].score == 42.0


def test_filter_keeps_top_k_by_score() -> None:
    node = Node(id="f", op="filter", params={"top_k": 2})
    items = [
        Artifact(id="a", content="", score=10),
        Artifact(id="b", content="", score=90),
        Artifact(id="c", content="", score=50),
    ]
    out = asyncio.run(op_filter(_ctx(node, items, FakeModel())))
    assert [a.id for a in out] == ["b", "c"]


def test_reduce_merges_inputs_into_one() -> None:
    node = Node(id="report", op="reduce", role="merge")
    out = asyncio.run(op_reduce(_ctx(node, _arts(("a", "1"), ("b", "2")), FakeModel())))
    assert len(out) == 1
    assert out[0].meta["merged_from"] == ["a", "b"]


def test_tournament_ranks_highest_number_first() -> None:
    # A FakeModel that picks whichever candidate embeds the larger number.
    def judge(system: str, user: str, effort: Effort) -> str:
        import re

        a = user.split("Candidate B:")[0]
        b = user.split("Candidate B:")[1] if "Candidate B:" in user else ""
        na = max((int(x) for x in re.findall(r"\d+", a)), default=0)
        nb = max((int(x) for x in re.findall(r"\d+", b)), default=0)
        return "B wins" if nb > na else "A wins"

    node = Node(id="t", op="tournament")
    items = _arts(("c1", "score 10"), ("c2", "score 80"), ("c3", "score 40"), ("c4", "score 99"))
    out = asyncio.run(op_tournament(_ctx(node, items, FakeModel(responder=judge))))
    assert out[0].id == "c4"  # highest number wins the bracket
    assert all("rank" in a.meta for a in out)


def test_verify_flags_contested_only_when_critiqued() -> None:
    model = FakeModel(
        scripted={"weak-pick": "This candidate lacks testing depth."},
        responder=lambda s, u, e: "OK",
    )
    node = Node(id="v", op="verify", params={"top_k": 2})
    items = _arts(("good", "solid-pick"), ("bad", "weak-pick"))
    out = asyncio.run(op_verify(_ctx(node, items, model)))
    by_id = {a.id: a for a in out}
    assert by_id["good"].meta["contested"] is False
    assert by_id["bad"].meta["contested"] is True


# --- scheduler end-to-end ----------------------------------------------------------


def test_example_plan_runs_end_to_end_offline() -> None:
    plan = load_plan_yaml(EXAMPLE.read_text(encoding="utf-8"))
    resumes = [f"Resume {i}: backend engineer, {i} years" for i in range(6)]
    report = asyncio.run(
        execute_plan(plan, sources={"resumes": resumes}, resolver=offline_resolver())
    )
    assert report.ok, report.errors
    assert set(report.results) == {"score", "shortlist", "bracket", "check", "report"}
    assert report.results["score"].output and len(report.results["score"].output) == 6
    assert len(report.results["shortlist"].output) == 4  # top_k
    assert len(report.final) == 1  # reduce is the single terminal node
    assert report.model_calls > 0
    assert 0 < report.spent_tokens <= plan.budget_tokens


def test_budget_circuit_breaker_records_failure() -> None:
    plan = load_plan_yaml(EXAMPLE.read_text(encoding="utf-8"))
    # A budget so small the very first reserve trips it.
    object.__setattr__(plan, "budget_tokens", 5)
    report = asyncio.run(execute_plan(plan, sources={"resumes": ["a", "b"]}))
    assert not report.ok
    assert any("BudgetExceeded" in e for e in report.errors.values())


def test_scheduler_runs_independent_map_items_in_parallel() -> None:
    # A model that records how many calls are in flight at once.
    class ConcurrencyProbe:
        name = "probe"

        def __init__(self) -> None:
            self.inflight = 0
            self.max_inflight = 0

        async def complete(
            self, *, system: str, user: str, effort: Effort = "low", max_tokens=None
        ) -> ModelReply:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            await asyncio.sleep(0.01)
            self.inflight -= 1
            return ModelReply(text="ok", input_tokens=1, output_tokens=1, model="probe")

    probe = ConcurrencyProbe()
    node = Node(id="m", op="map")
    items = _arts(*[(f"r{i}", str(i)) for i in range(6)])
    asyncio.run(op_map(_ctx(node, items, probe, parallel=4)))
    assert probe.max_inflight > 1  # genuine fan-out

    serial = ConcurrencyProbe()
    asyncio.run(op_map(_ctx(node, items, serial, parallel=1)))
    assert serial.max_inflight == 1  # bulkhead of 1 serializes


def test_scheduler_validates_plan_before_running() -> None:
    import pytest

    bad = load_plan_yaml(EXAMPLE.read_text(encoding="utf-8"))
    object.__setattr__(bad, "budget_tokens", 0)
    with pytest.raises(Exception, match="budget_tokens"):
        asyncio.run(execute_plan(bad, sources={"resumes": ["a"]}))
