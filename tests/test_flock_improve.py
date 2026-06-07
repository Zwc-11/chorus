"""Phase 4: candidate-plan tournament, the template library, and the learning loop."""

from __future__ import annotations

import asyncio
import json

from murmur.flock.adapters.fake import FakeModel
from murmur.flock.improve import (
    best_of_k,
    default_scorer,
    self_improving_plan,
)
from murmur.flock.ir import Effort
from murmur.flock.library import TemplateLibrary, keywords
from murmur.flock.models import offline_resolver
from murmur.flock.scheduler import execute_plan


def _plan_json(nodes: list[dict]) -> str:
    return json.dumps(
        {"goal": "rank items", "budget_tokens": 50000, "sources": ["items"], "nodes": nodes}
    )


def _varied_planner(system: str, user: str, effort: Effort) -> str:
    """Return plans of different sizes depending on the style hint in the prompt."""

    if "heavy fan-out" in user:  # 4 nodes
        return _plan_json(
            [
                {"id": "score", "op": "map", "inputs": ["items"]},
                {"id": "check", "op": "verify", "inputs": ["score"]},
                {"id": "best", "op": "filter", "inputs": ["check"], "params": {"top_k": 3}},
                {"id": "out", "op": "reduce", "inputs": ["best"]},
            ]
        )
    if "lean" in user:  # 2 nodes
        return _plan_json(
            [
                {"id": "work", "op": "map", "inputs": ["items"]},
                {"id": "out", "op": "reduce", "inputs": ["work"]},
            ]
        )
    return _plan_json(  # 3 nodes
        [
            {"id": "score", "op": "map", "inputs": ["items"]},
            {"id": "best", "op": "filter", "inputs": ["score"], "params": {"top_k": 3}},
            {"id": "out", "op": "reduce", "inputs": ["best"]},
        ]
    )


# --- library -----------------------------------------------------------------------


def test_keywords_drops_stopwords_and_short_tokens() -> None:
    assert keywords("Rank the resumes for a backend role") == ("backend", "rank", "resumes", "role")


def test_library_add_find_round_trip(tmp_path) -> None:
    from murmur.flock.planner import template_plan

    lib = TemplateLibrary(tmp_path / "templates")
    assert lib.find("anything") is None
    plan = template_plan("rank resumes for a backend role", sources=["resumes"])
    tmpl = lib.add(task="rank resumes for a backend role", plan=plan, score=12.5)
    assert len(lib.all()) == 1
    hit = lib.find("please rank these backend resumes")
    assert hit is not None and hit.name == tmpl.name
    assert hit.workflow().node_ids == plan.node_ids  # reconstructs + validates


def test_library_find_prefers_higher_overlap_then_score(tmp_path) -> None:
    from murmur.flock.planner import template_plan

    lib = TemplateLibrary(tmp_path / "templates")
    lib.add(
        task="summarize support tickets",
        plan=template_plan("summarize tickets", sources=["t"]),
        score=1.0,
    )
    lib.add(
        task="rank backend resumes carefully",
        plan=template_plan("rank backend resumes", sources=["r"]),
        score=1.0,
    )
    hit = lib.find("rank the backend resumes")
    assert hit is not None and "resumes" in hit.keywords


# --- tournament --------------------------------------------------------------------


def test_best_of_k_picks_winner_by_scorer() -> None:
    model = FakeModel(responder=_varied_planner)
    result = asyncio.run(
        best_of_k(
            "rank items",
            model=model,
            sources=["items"],
            source_values={"items": ["x", "y", "z"]},
            k=3,
            scorer=lambda plan, report: float(len(plan.nodes)),  # prefer the biggest plan
            resolver=offline_resolver(),
        )
    )
    assert len(result.candidates) == 3
    assert len(result.winner.plan.nodes) == 4  # the heavy fan-out candidate
    # candidates are returned best-first
    scores = [c.score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)


def test_default_scorer_ranks_success_above_failure() -> None:
    from murmur.flock.planner import template_plan

    plan = template_plan("summarize the docs", sources=["docs"])
    ok = asyncio.run(execute_plan(plan, sources={"docs": ["a", "b", "c"]}))

    cheap = template_plan("summarize the docs", sources=["docs"])
    object.__setattr__(cheap, "budget_tokens", 3)
    failed = asyncio.run(execute_plan(cheap, sources={"docs": ["a", "b"]}))

    assert default_scorer(plan, ok) > default_scorer(cheap, failed)


# --- the learning loop -------------------------------------------------------------


def test_self_improving_plan_mines_then_reuses(tmp_path) -> None:
    lib = TemplateLibrary(tmp_path / "templates")
    model = FakeModel(responder=_varied_planner)
    common = dict(
        model=model,
        library=lib,
        sources=["items"],
        source_values={"items": ["x", "y", "z"]},
        k=3,
        resolver=offline_resolver(),
    )

    first = asyncio.run(self_improving_plan("rank backend engineer resumes", **common))
    assert first.origin == "mined"
    assert first.tournament is not None and len(first.tournament.candidates) == 3
    assert len(lib.all()) == 1
    calls_after_mining = model.call_count
    assert calls_after_mining > 0

    # A similar task should reuse the mined template — no new planning calls, no new file.
    second = asyncio.run(self_improving_plan("please rank these backend resumes", **common))
    assert second.origin == "reused"
    assert second.template is not None
    assert len(lib.all()) == 1  # library did not grow
    assert model.call_count == calls_after_mining  # tournament was skipped entirely


def test_self_improving_plan_can_force_fresh_mining(tmp_path) -> None:
    lib = TemplateLibrary(tmp_path / "templates")
    model = FakeModel(responder=_varied_planner)
    common = dict(
        model=model,
        library=lib,
        sources=["items"],
        source_values={"items": ["x", "y"]},
        k=2,
        resolver=offline_resolver(),
    )
    asyncio.run(self_improving_plan("rank backend resumes", **common))
    again = asyncio.run(self_improving_plan("rank backend resumes", reuse=False, **common))
    assert again.origin == "mined"  # skipped the library lookup, ran a fresh tournament
    assert again.tournament is not None and len(again.tournament.candidates) == 2
    # The deterministic winner re-distills to the same content-hashed template (idempotent).
    assert len(lib.all()) == 1
