"""Tournament ranking: objective metrics first, LLM judge only on exact ties."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from chorus.adapters.models.fake import FakeModel
from chorus.application.tournament import (
    ObjectiveKey,
    TournamentJudge,
    rank_attempts,
)
from chorus.core.model_port import ModelUnavailable


@dataclass(frozen=True, slots=True)
class FakeVerification:
    passed: bool = True
    failures: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ("a.py",)
    diff_lines: int = 4


@dataclass(frozen=True, slots=True)
class FakeAttempt:
    attempt_id: str
    diff: str = "--- a/a.py\n+++ b/a.py\n"
    summary: str = "patched"
    verification: FakeVerification = field(default_factory=FakeVerification)


def _attempt(
    attempt_id: str,
    *,
    passed: bool = True,
    failures: int = 0,
    diff_lines: int = 4,
    touched: int = 1,
    diff: str = "",
) -> FakeAttempt:
    return FakeAttempt(
        attempt_id=attempt_id,
        diff=diff or f"--- a/{attempt_id}.py\n+++ b/{attempt_id}.py\n",
        verification=FakeVerification(
            passed=passed,
            failures=tuple(f"f{i}" for i in range(failures)),
            changed_files=tuple(f"file{i}.py" for i in range(touched)),
            diff_lines=diff_lines,
        ),
    )


def test_objective_order_passed_then_diff_then_files() -> None:
    attempts = [
        _attempt("attempt_1", passed=False),
        _attempt("attempt_2", diff_lines=20, touched=1),
        _attempt("attempt_3", diff_lines=4, touched=3),
        _attempt("attempt_4", diff_lines=4, touched=1),
    ]
    decision = rank_attempts(attempts)

    assert decision.winner_id == "attempt_4"
    assert decision.method == "objective"
    # passed+small diff beats passed+large diff beats failed; smaller diff outranks fewer files
    assert decision.ranking == ("attempt_4", "attempt_3", "attempt_2", "attempt_1")
    assert decision.tied == ()


def test_smaller_diff_outranks_fewer_touched_files() -> None:
    a = ObjectiveKey.for_attempt(_attempt("x", diff_lines=2, touched=5))
    b = ObjectiveKey.for_attempt(_attempt("y", diff_lines=10, touched=1))
    assert a.as_tuple() < b.as_tuple()


def test_exact_tie_without_judge_falls_back_to_stable_order() -> None:
    attempts = [_attempt("attempt_2"), _attempt("attempt_1")]
    decision = rank_attempts(attempts)

    assert decision.method == "stable_order"
    assert decision.winner_id == "attempt_1"  # deterministic by id
    assert set(decision.tied) == {"attempt_1", "attempt_2"}
    assert "no judge" in decision.rationale


def test_exact_tie_invokes_blind_judge() -> None:
    port = FakeModel(responses=['{"winner": "B", "reason": "smaller blast radius"}'])
    judge = TournamentJudge(model_port=port, model="judge-model")
    attempts = [_attempt("attempt_1"), _attempt("attempt_2")]

    decision = rank_attempts(attempts, judge=judge)

    assert decision.method == "llm_judge"
    assert decision.winner_id == "attempt_2"  # label B = second tied candidate
    assert decision.rationale == "smaller blast radius"
    assert decision.ranking[0] == "attempt_2"
    # The judge is blind: prompt shows labels and diffs, never attempt ids.
    prompt = port.calls[0].messages[1]["content"]
    assert "Candidate A" in prompt and "Candidate B" in prompt
    assert "attempt_1" not in prompt.replace("attempt_1.py", "")


def test_judge_not_consulted_when_objective_decides() -> None:
    port = FakeModel(responses=['{"winner": "A"}'])
    judge = TournamentJudge(model_port=port, model="judge-model")
    attempts = [_attempt("attempt_1", diff_lines=2), _attempt("attempt_2", diff_lines=9)]

    decision = rank_attempts(attempts, judge=judge)

    assert decision.method == "objective"
    assert port.calls == []  # objective metrics decided; no model spend


def test_unparseable_judge_reply_falls_back_to_stable_order() -> None:
    port = FakeModel(responses=["I like both patches equally!"])
    judge = TournamentJudge(model_port=port, model="judge-model")
    attempts = [_attempt("attempt_1"), _attempt("attempt_2")]

    decision = rank_attempts(attempts, judge=judge)

    assert decision.method == "stable_order"
    assert decision.winner_id == "attempt_1"
    assert "not parseable" in decision.rationale


def test_judge_outage_falls_back_to_stable_order() -> None:
    class DeadPort:
        async def complete(self, **kwargs: object) -> None:
            raise ModelUnavailable("provider down")

    judge = TournamentJudge(model_port=DeadPort(), model="judge-model")
    attempts = [_attempt("attempt_1"), _attempt("attempt_2")]

    decision = rank_attempts(attempts, judge=judge)

    assert decision.method == "stable_order"
    assert "judge unavailable" in decision.rationale


def test_judge_picking_unknown_label_falls_back() -> None:
    port = FakeModel(responses=['{"winner": "Z", "reason": "?"}'])
    judge = TournamentJudge(model_port=port, model="judge-model")
    decision = rank_attempts([_attempt("attempt_1"), _attempt("attempt_2")], judge=judge)
    assert decision.method == "stable_order"
    assert "unknown candidate" in decision.rationale


def test_fenced_judge_reply_is_parsed() -> None:
    port = FakeModel(
        responses=['```json\n{"winner": "a", "reason": "cleaner"}\n```']  # lowercase label
    )
    judge = TournamentJudge(model_port=port, model="judge-model")
    decision = rank_attempts([_attempt("attempt_1"), _attempt("attempt_2")], judge=judge)
    assert decision.method == "llm_judge"
    assert decision.winner_id == "attempt_1"


def test_empty_attempts_raise() -> None:
    with pytest.raises(RuntimeError, match="no attempts"):
        rank_attempts([])
