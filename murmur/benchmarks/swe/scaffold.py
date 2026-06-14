"""Scaffolds: how an attempt turns a task into a patch.

A scaffold is the harness strategy under test. The headline comparison varies the
scaffold and *nothing else*, so the two built-ins differ in exactly one dimension:

* :class:`SingleShotScaffold` -- one model call, take the diff.
* :class:`SelfRepairScaffold` -- the same first call, then one extra "review your
  own patch and fix obvious mistakes" turn. Model-only (no test feedback), so it
  needs no mid-loop Docker and the diff against single-shot is purely the harness.

These are deliberately a *baseline* agent: the prompt carries the problem statement
and repo coordinates but not retrieved file contents. To benchmark a real agent
(SWE-agent, OpenHands, Aider, ...), implement :class:`~murmur.benchmarks.swe.types.PatchModel`
around it, or subclass and override :meth:`_build_user` to inject retrieved code.
The runner and evaluator do not care which scaffold produced the patch.
"""

from __future__ import annotations

from murmur.benchmarks.swe.types import ModelResponse, PatchModel, ScaffoldOutput
from murmur.core.types import TaskSpec

_SYSTEM = (
    "You are an expert software engineer fixing a bug in an open-source repository. "
    "You will be given the issue text and the repository coordinates. Respond with a "
    "single unified diff (git format) that resolves the issue and makes the failing "
    "tests pass. Output ONLY the diff inside a ```diff code block; no prose."
)

_REPAIR_SYSTEM = (
    "You are reviewing a proposed patch for the issue below. Check it for obvious "
    "mistakes: wrong file paths, malformed hunks, off-by-one context, or logic that "
    "does not address the issue. Return a corrected single unified diff inside a "
    "```diff code block; no prose. If the patch is already correct, return it unchanged."
)


def _build_user(task: TaskSpec) -> str:
    meta = task.metadata
    fail_to_pass = _format_tests("Failing tests to make pass", meta.get("fail_to_pass"))
    pass_to_pass = _format_tests("Passing tests to keep passing", meta.get("pass_to_pass"))
    return (
        f"Repository: {meta.get('repo', '?')}\n"
        f"Base commit: {meta.get('base_commit', '?')}\n\n"
        f"{fail_to_pass}"
        f"{pass_to_pass}"
        f"Issue:\n{task.prompt}\n"
    )


def _format_tests(label: str, tests: object) -> str:
    if not tests:
        return ""
    if isinstance(tests, str):
        values = [tests]
    else:
        try:
            values = [str(item) for item in tests]  # type: ignore[union-attr]
        except TypeError:
            values = [str(tests)]
    body = "\n".join(f"- {item}" for item in values)
    return f"{label}:\n{body}\n\n"


def extract_patch(text: str) -> str:
    """Pull the unified diff out of a model response (fenced or bare)."""

    fence = "```"
    if fence in text:
        # Take the contents of the first fenced block, dropping an optional language tag.
        after = text.split(fence, 1)[1]
        body = after.split(fence, 1)[0]
        if "\n" in body:
            first, rest = body.split("\n", 1)
            if first.strip().lower() in {"diff", "patch", ""}:
                return rest.strip("\n")
        return body.strip("\n")
    return text.strip()


class SingleShotScaffold:
    name = "single-shot"

    def run(self, task: TaskSpec, model: PatchModel, *, seed: int) -> ScaffoldOutput:
        resp = model.complete(system=_SYSTEM, user=_build_user(task), seed=seed)
        return ScaffoldOutput(patch=extract_patch(resp.text), cost_usd=resp.cost_usd)


class SelfRepairScaffold:
    """Single-shot plus one self-review turn -- the harness-only diff to compare."""

    name = "self-repair"

    def run(self, task: TaskSpec, model: PatchModel, *, seed: int) -> ScaffoldOutput:
        first = model.complete(system=_SYSTEM, user=_build_user(task), seed=seed)
        draft = extract_patch(first.text)
        review_user = f"{_build_user(task)}\nProposed patch:\n```diff\n{draft}\n```\n"
        second = model.complete(system=_REPAIR_SYSTEM, user=review_user, seed=seed + 1)
        return ScaffoldOutput(
            patch=extract_patch(second.text),
            cost_usd=_cost(first) + _cost(second),
        )


def _cost(resp: ModelResponse) -> float:
    return resp.cost_usd


BUILTIN_SCAFFOLDS = {
    SingleShotScaffold.name: SingleShotScaffold,
    SelfRepairScaffold.name: SelfRepairScaffold,
}
