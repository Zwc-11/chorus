"""The planner — the self-writing brain.

Run once with a strong thinking model, the planner turns a natural-language task into
a typed :class:`~murmur.flock.ir.WorkflowPlan`. It is *constrained*: the model is asked
for JSON matching the IR schema, the result is validated, and on any rejection the
planner re-prompts with the exact error (a bounded repair loop) so it cannot hand the
runtime garbage. If the model is unavailable or never produces a valid plan, the
planner falls back to a deterministic :func:`template_plan` — the "template-fill first,
then free-form" progression from the build plan.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from murmur.flock.gateway import ModelPort
from murmur.flock.ir import (
    Node,
    PlanValidationError,
    WorkflowPlan,
    parse_plan,
    validate_plan,
)

DEFAULT_BUDGET = 200_000

PLANNER_SYSTEM = """\
You are Murmur's planner. You design a task-specific multi-agent workflow and emit it
as a single JSON object — nothing else, no prose, no code fences.

The workflow is a DAG of operator nodes. Schema:
{
  "goal": string,
  "budget_tokens": integer,
  "sources": [string],          // external inputs available when the run starts
  "nodes": [
    {
      "id": string,             // unique
      "op": "classify"|"map"|"reduce"|"tournament"|"verify"|"filter"|"loop",
      "role": string,           // the subagent's instruction for this node
      "inputs": [string],       // each must be another node's id OR a source name
      "model": "deepseek-v4-flash"|"deepseek-v4-pro"|"ollama:<name>",
      "effort": "low"|"high",
      "trust": "trusted"|"untrusted",
      "params": object          // op-specific, e.g. {"top_k": 10}, {"max_iters": 3}
    }
  ]
}

Operators: classify routes/labels; map fans out one subagent per input item; reduce is
a barrier that merges its inputs into one result; tournament ranks items by pairwise
comparison; verify spawns an adversarial refuter per artifact; filter keeps the top_k
by score; loop refines until done.

Rules you MUST follow:
- The nodes must form a DAG (no cycles).
- Every input must name a node id or a declared source.
- A "trusted" node must not read a node marked "untrusted" — mark it untrusted or put a
  verify node in between.
- Manufacture reliability through volume on cheap models: prefer map fan-out on
  deepseek-v4-flash at effort low, and reserve deepseek-v4-pro at effort high for
  synthesis (reduce) and verification (verify).

Return ONLY the JSON object.
"""


def _planner_user(task: str, budget_tokens: int, sources: Sequence[str]) -> str:
    src = ", ".join(sources) if sources else "(none)"
    return (
        f"Task:\n{task}\n\n"
        f"Token budget for the whole run: {budget_tokens}\n"
        f"Sources available at start: {src}\n\n"
        "Design the cheapest workflow that is reliable for this task and return the JSON plan."
    )


def extract_json(text: str) -> dict:
    """Pull the first balanced JSON object out of a model reply.

    Tolerates ```json fences and leading/trailing prose; scans for the first ``{`` and
    its matching ``}`` (string-aware so braces inside strings don't fool it).
    """

    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s
        s = s[len("json") :] if s.lstrip().lower().startswith("json") else s
    start = s.find("{")
    if start == -1:
        raise ValueError("no JSON object found in planner reply")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start : i + 1])
    raise ValueError("unbalanced JSON object in planner reply")


def template_plan(
    task: str, *, sources: Sequence[str] = (), budget_tokens: int = DEFAULT_BUDGET
) -> WorkflowPlan:
    """A deterministic default workflow: fan out over a source, then synthesize.

    Always valid, model-free, and cheap — the safety net when the planner model is
    unavailable or never returns a schema-valid plan.
    """

    src = sources[0] if sources else "input"
    nodes = (
        Node(
            id="work",
            op="map",
            inputs=(src,),
            model="deepseek-v4-flash",
            effort="low",
            role=f"Work on this item toward the goal: {task}",
        ),
        Node(
            id="synthesize",
            op="reduce",
            inputs=("work",),
            model="deepseek-v4-pro",
            effort="high",
            role=f"Synthesize the items into one final answer for: {task}",
        ),
    )
    plan = WorkflowPlan(goal=task, budget_tokens=budget_tokens, nodes=nodes, sources=(src,))
    validate_plan(plan)
    return plan


async def plan_workflow(
    task: str,
    *,
    model: ModelPort,
    budget_tokens: int = DEFAULT_BUDGET,
    sources: Sequence[str] = (),
    max_repair: int = 2,
    fallback: bool = True,
) -> WorkflowPlan:
    """Compile *task* into a validated :class:`WorkflowPlan` using *model*.

    Re-prompts up to ``max_repair`` times with the validation error on each failure.
    Falls back to :func:`template_plan` when ``fallback`` is set and the model never
    yields a valid plan; otherwise raises :class:`PlanValidationError`.
    """

    base = _planner_user(task, budget_tokens, sources)
    last_error = ""
    for attempt in range(max_repair + 1):
        prompt = base
        if attempt:
            prompt = (
                f"{base}\n\nYour previous plan was rejected:\n{last_error}\n"
                "Return a corrected JSON plan that fixes exactly this problem."
            )
        reply = await model.complete(system=PLANNER_SYSTEM, user=prompt, effort="high")
        try:
            data = extract_json(reply.text)
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = f"reply was not valid JSON: {exc}"
            continue
        try:
            return parse_plan(
                {
                    "goal": data.get("goal") or task,
                    "budget_tokens": data.get("budget_tokens") or budget_tokens,
                    "sources": data.get("sources") or list(sources),
                    "nodes": data.get("nodes", []),
                }
            )
        except PlanValidationError as exc:
            last_error = str(exc)

    if fallback:
        return template_plan(task, sources=sources, budget_tokens=budget_tokens)
    raise PlanValidationError(
        f"planner did not produce a valid plan after {max_repair + 1} attempts: {last_error}"
    )
