"""Artifacts — the uniform value that flows along the plan's edges.

Every operator consumes and produces ``list[Artifact]``. Keeping one shape means the
scheduler can wire any node to any other without per-operator glue: a ``reduce`` that
follows a ``map`` just receives the map's list; a single-output operator returns a
one-element list. ``score`` carries a comparable number for ``filter``/``tournament``;
``meta`` carries operator annotations (rank, contested, critique); ``trust`` is the
quarantine flag that propagates through the run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from murmur.flock.ir import Trust


@dataclass(frozen=True, slots=True)
class Artifact:
    """One unit of work-product flowing between nodes."""

    id: str
    content: str
    score: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    trust: Trust = "trusted"

    def with_meta(self, **updates: Any) -> Artifact:
        from dataclasses import replace

        return replace(self, meta={**self.meta, **updates})

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "score": self.score,
            "meta": dict(self.meta),
            "trust": self.trust,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Artifact:
        return cls(
            id=str(data["id"]),
            content=str(data.get("content", "")),
            score=data.get("score"),
            meta=dict(data.get("meta", {}) or {}),
            trust=str(data.get("trust", "trusted")),  # type: ignore[arg-type]
        )


def parse_score(text: str) -> float | None:
    """Best-effort extract a numeric score from a model reply.

    Recognizes a JSON object with a ``score`` field (what a scoring ``role`` is asked
    to emit) and falls back to the first bare number in the text. Returns ``None`` if
    no number is present, so ``filter`` can tell "unscored" from "scored zero".
    """

    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and "score" in obj:
            try:
                return float(obj["score"])
            except (TypeError, ValueError):
                return None
    import re

    match = re.search(r"-?\d+(?:\.\d+)?", stripped)
    return float(match.group()) if match else None


def as_artifacts(name: str, value: Any) -> list[Artifact]:
    """Coerce a source value into ``list[Artifact]``.

    Accepts a string (one artifact), a list of strings, or a list of dicts shaped
    like ``{"id", "content", "score", "trust"}``. Other scalars are stringified.
    """

    if isinstance(value, str):
        return [Artifact(id=f"{name}-0", content=value)]
    if isinstance(value, list):
        out: list[Artifact] = []
        for i, item in enumerate(value):
            if isinstance(item, Artifact):
                out.append(item)
            elif isinstance(item, dict):
                out.append(
                    Artifact(
                        id=str(item.get("id", f"{name}-{i}")),
                        content=str(item.get("content", "")),
                        score=item.get("score"),
                        meta=dict(item.get("meta", {}) or {}),
                        trust=str(item.get("trust", "trusted")),  # type: ignore[arg-type]
                    )
                )
            else:
                out.append(Artifact(id=f"{name}-{i}", content=str(item)))
        return out
    return [Artifact(id=f"{name}-0", content=str(value))]
