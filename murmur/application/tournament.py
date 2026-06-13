"""Objective-first tournament ranking with an optional LLM tie-break judge.

The ranking philosophy: objective metrics decide whenever they can, and a model
is consulted only when they genuinely tie. Order of objectives:

    passed tests > fewer verification failures > smaller diff > fewer touched files

Every decision is recorded in a :class:`RankDecision` so the proof package can
show *why* a candidate won, not just which one did. The judge is blind: tied
candidates are presented as anonymous labels (A, B, C ...) with their diffs.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from murmur.core.model_port import ModelPort, ModelResponse

_JUDGE_SYSTEM = (
    "You are Murmur's blind tournament judge. Several candidate patches all pass "
    "the same objective checks. Pick the best one by this rubric, in order: "
    "least risk of overfitting to the test, smallest meaningful change, most "
    "readable code. Return ONLY a JSON object like "
    '{"winner": "A", "reason": "one sentence"}.'
)


class RankableVerification(Protocol):
    passed: bool
    failures: tuple[str, ...]
    changed_files: tuple[str, ...]
    diff_lines: int


class RankableAttempt(Protocol):
    attempt_id: str
    diff: str
    summary: str

    @property
    def verification(self) -> RankableVerification: ...


@dataclass(frozen=True, slots=True)
class ObjectiveKey:
    """The deterministic part of the ranking. Lower tuples are better."""

    failed: bool
    failures: int
    diff_lines: int
    touched_files: int

    @classmethod
    def for_attempt(cls, attempt: RankableAttempt) -> ObjectiveKey:
        verification = attempt.verification
        return cls(
            failed=not verification.passed,
            failures=len(verification.failures),
            diff_lines=int(verification.diff_lines),
            touched_files=len(verification.changed_files),
        )

    def as_tuple(self) -> tuple[bool, int, int, int]:
        return (self.failed, self.failures, self.diff_lines, self.touched_files)


@dataclass(frozen=True, slots=True)
class RankDecision:
    """Which attempt won, in what order the rest follow, and why."""

    winner_id: str
    ranking: tuple[str, ...]
    method: str  # "objective" | "llm_judge" | "stable_order"
    tied: tuple[str, ...] = ()
    rationale: str = ""
    judge_model: str = ""
    judge_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "winner": self.winner_id,
            "ranking": list(self.ranking),
            "method": self.method,
            "tied": list(self.tied),
            "rationale": self.rationale,
            "judge_model": self.judge_model,
            "judge_cost_usd": self.judge_cost_usd,
        }


class TournamentJudge:
    """Blind ModelPort judge consulted only when objective metrics tie."""

    def __init__(
        self,
        *,
        model_port: ModelPort,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
        max_diff_chars: int = 4000,
    ) -> None:
        self.model_port = model_port
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_diff_chars = max_diff_chars

    def pick(
        self, candidates: list[RankableAttempt]
    ) -> tuple[str, str, ModelResponse | None]:
        """Return (winner attempt_id or "", rationale, raw response)."""

        labels = {chr(ord("A") + index): item for index, item in enumerate(candidates)}
        sections = [
            f"## Candidate {label}\n```diff\n{item.diff[: self.max_diff_chars]}\n```"
            for label, item in labels.items()
        ]
        try:
            response = asyncio.run(
                self.model_port.complete(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": _JUDGE_SYSTEM},
                        {"role": "user", "content": "\n\n".join(sections)},
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
            )
        except Exception as exc:  # noqa: BLE001 - a judge outage must not kill the run
            return "", f"judge unavailable: {exc}", None
        verdict = _parse_judge_verdict(response.text)
        if verdict is None:
            return "", f"judge reply was not parseable: {response.text[:200]}", response
        label, reason = verdict
        chosen = labels.get(label)
        if chosen is None:
            return "", f"judge picked unknown candidate {label!r}", response
        return chosen.attempt_id, reason, response


def rank_attempts(
    attempts: list[RankableAttempt],
    *,
    judge: TournamentJudge | None = None,
) -> RankDecision:
    """Objective-first ranking; the judge breaks exact ties; stable order is last."""

    if not attempts:
        raise RuntimeError("no attempts were run")
    ordered = sorted(
        attempts, key=lambda item: (ObjectiveKey.for_attempt(item).as_tuple(), item.attempt_id)
    )
    best_key = ObjectiveKey.for_attempt(ordered[0])
    tied = [item for item in ordered if ObjectiveKey.for_attempt(item) == best_key]

    if len(tied) == 1:
        return RankDecision(
            winner_id=ordered[0].attempt_id,
            ranking=tuple(item.attempt_id for item in ordered),
            method="objective",
            rationale="objective metrics fully ordered the candidates",
        )

    tied_ids = tuple(item.attempt_id for item in tied)
    if judge is not None:
        winner_id, rationale, response = judge.pick(tied)
        if winner_id:
            return RankDecision(
                winner_id=winner_id,
                ranking=_winner_first(ordered, winner_id),
                method="llm_judge",
                tied=tied_ids,
                rationale=rationale,
                judge_model=(response.model if response else judge.model),
                judge_cost_usd=(response.cost_usd if response else 0.0),
            )
        return RankDecision(
            winner_id=ordered[0].attempt_id,
            ranking=tuple(item.attempt_id for item in ordered),
            method="stable_order",
            tied=tied_ids,
            rationale=rationale or "judge could not decide; fell back to stable order",
            judge_model=(response.model if response else judge.model),
            judge_cost_usd=(response.cost_usd if response else 0.0),
        )

    return RankDecision(
        winner_id=ordered[0].attempt_id,
        ranking=tuple(item.attempt_id for item in ordered),
        method="stable_order",
        tied=tied_ids,
        rationale="objective metrics tied and no judge was configured",
    )


def _winner_first(ordered: list[RankableAttempt], winner_id: str) -> tuple[str, ...]:
    ids = [item.attempt_id for item in ordered]
    ids.remove(winner_id)
    return (winner_id, *ids)


def _parse_judge_verdict(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    raw = fenced.group(1) if fenced else stripped
    if not fenced:
        match = re.search(r"\{.*?\}", raw, re.DOTALL)
        raw = match.group(0) if match else raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "winner" not in data:
        return None
    return str(data["winner"]).strip().upper(), str(data.get("reason", ""))
