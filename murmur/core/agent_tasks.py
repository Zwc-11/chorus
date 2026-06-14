"""Agent-facing task specs — demo smoke tests and hard integration tasks."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from murmur.core.types import TaskSpec

HARD_WEBSITE_ACCEPTANCE = "hard_website_v1"

_HARD_PROMPT = """\
Build a complete, production-quality single-page marketing website for a fictional \
product called **Chorus** — an open-source reliability harness for coding agents.

Deliverables (all required in your final artifact bundle):
1. `index.html` — semantic HTML5 with: hero, three feature cards, a monospace \
metrics strip, a testimonial quote, and a footer with social placeholders.
2. `styles.css` — linked from the HTML; use CSS variables for a tech-noir palette \
(light gray background `#e4e4e0`, accent red `#e8192a`, thin borders, wide letter-spacing \
on headings). No external CSS frameworks.
3. Responsive layout: usable from 320px to 1200px width.
4. Include the word "chorus" in the hero headline (case-insensitive) and an element \
with id `metrics` showing three fake reliability stats (pass@1, pass^k, variance).

Constraints:
- No JavaScript required (CSS-only interactions OK).
- No placeholder "lorem ipsum" — write real microcopy about agent reliability.
- Output your final deliverable as a single message containing both files, each in a \
fenced code block: ```html for index.html and ```css for styles.css.

This is intentionally difficult: reasoning, structure, visual design, and copy must \
all be correct in one shot.
"""


def demo_task(*, mutate: bool = False) -> TaskSpec:
    prompt = "hello chorus"
    if mutate:
        prompt = "hello mutated chorus"
    return TaskSpec(
        task_id="demo.echo_uppercase",
        prompt=prompt,
        expected_output="HELLO CHORUS",
        metadata={"kind": "demo"},
    )


def hard_website_task() -> TaskSpec:
    return TaskSpec(
        task_id="hard.landing_site",
        prompt=_HARD_PROMPT,
        expected_output=None,
        metadata={
            "kind": "hard_website",
            "acceptance": HARD_WEBSITE_ACCEPTANCE,
            "difficulty": 0.12,
        },
    )


def load_agent_task(name: str | None = None) -> TaskSpec:
    """Resolve the active agent task from *name*, ``CHORUS_TASK``, or YAML under ``tasks/``."""

    key = (name or os.environ.get("CHORUS_TASK") or "hard").strip().lower()
    if key in ("demo", "echo", "demo.echo_uppercase"):
        return demo_task()
    if key in ("hard", "website", "hard.landing_site", HARD_WEBSITE_ACCEPTANCE):
        return hard_website_task()
    path = Path("tasks") / f"{key}.yaml"
    if path.is_file():
        return task_from_yaml(path)
    raise ValueError(
        f"unknown task {key!r}; use demo, hard, or a tasks/<name>.yaml file. "
        "Set CHORUS_TASK or pass --task."
    )


def task_from_yaml(path: Path) -> TaskSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid task file {path}")
    metadata = dict(data.get("metadata") or {})
    if acceptance := data.get("acceptance"):
        metadata["acceptance"] = acceptance
    return TaskSpec(
        task_id=str(data["task_id"]),
        prompt=str(data["prompt"]).strip(),
        expected_output=data.get("expected_output"),
        metadata=metadata,
    )
