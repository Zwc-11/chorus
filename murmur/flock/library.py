"""The template library — Murmur learns which workflow shapes work.

When the candidate-plan tournament (see :mod:`murmur.flock.improve`) picks a winner,
its plan is *distilled* into a reusable :class:`Template` and saved to disk, keyed by
the words of the task it solved. The next time a similar task arrives, the planner can
``find`` that template and reuse it instead of paying for a fresh tournament — so
planning gets cheaper and better the more the library is used.

Matching is deliberately dependency-free: a task's content words are compared by
overlap, no embeddings. Distillation currently preserves the winning plan's structure,
operators, models, and roles; the generalization is that the same shape is replayed
against new inputs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from murmur.flock.ir import WorkflowPlan, parse_plan

_STOPWORD_TEXT = (
    "the a an and or of to for this that these those with in on at is are be by from "
    "into as it its their them then than your you we our run task plan use using"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def keywords(task: str) -> tuple[str, ...]:
    """Content words of *task*, lowercased, de-stopworded, deduped, sorted."""

    toks = re.findall(r"[a-z0-9]+", task.lower())
    return tuple(sorted({t for t in toks if len(t) > 2 and t not in _STOPWORDS}))


def _slug(task: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")
    return (base[:40] or "task").strip("-")


@dataclass(frozen=True, slots=True)
class Template:
    """A mined, reusable workflow shape and the task it was distilled from."""

    name: str
    goal: str
    keywords: tuple[str, ...]
    plan: dict[str, Any]
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "goal": self.goal,
            "keywords": list(self.keywords),
            "plan": self.plan,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Template:
        return cls(
            name=str(data["name"]),
            goal=str(data.get("goal", "")),
            keywords=tuple(data.get("keywords", ()) or ()),
            plan=dict(data.get("plan", {}) or {}),
            score=float(data.get("score", 0.0)),
        )

    def workflow(self) -> WorkflowPlan:
        """Reconstruct (and validate) the stored plan."""

        return parse_plan(self.plan)


class TemplateLibrary:
    """A directory of mined templates (one JSON file each)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def all(self) -> list[Template]:
        if not self.root.exists():
            return []
        out: list[Template] = []
        for path in sorted(self.root.glob("*.json")):
            out.append(Template.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        return out

    def add(self, *, task: str, plan: WorkflowPlan, score: float) -> Template:
        """Distill *plan* into a template for *task* and persist it."""

        template = self._distill(task=task, plan=plan, score=score)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{template.name}.json").write_text(
            json.dumps(template.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return template

    def find(self, task: str, *, min_overlap: int = 1) -> Template | None:
        """Return the best-matching template for *task*, or ``None``.

        Ranks by keyword overlap, then by the template's recorded score.
        """

        wanted = set(keywords(task))
        best: Template | None = None
        best_key = (min_overlap - 1, float("-inf"))
        for template in self.all():
            overlap = len(wanted & set(template.keywords))
            key = (overlap, template.score)
            if overlap >= min_overlap and key > best_key:
                best, best_key = template, key
        return best

    def _distill(self, *, task: str, plan: WorkflowPlan, score: float) -> Template:
        digest = hashlib.sha256(
            json.dumps(plan.to_dict(), sort_keys=True).encode()
        ).hexdigest()[:6]
        return Template(
            name=f"{_slug(task)}-{digest}",
            goal=task,
            keywords=keywords(task),
            plan=plan.to_dict(),
            score=score,
        )
