"""Template planner for Murmur workflow YAML."""

from __future__ import annotations

from murmur.domain.workflow import WorkflowNode, WorkflowPlan

TEMPLATES = (
    "coding_fix_test",
    "coding_generate_and_test",
    "strategy_research_backtest",
    "document_review",
)


def plan_from_task(
    *,
    task: str,
    template: str = "auto",
    command: str = "",
    attempts: int = 1,
    max_repairs: int = 0,
) -> WorkflowPlan:
    selected = _select_template(task, template)
    if selected == "coding_fix_test":
        if not command:
            raise RuntimeError("coding_fix_test requires --cmd")
        return _coding_fix_test(task, command, attempts, max_repairs)
    if selected == "strategy_research_backtest":
        return _strategy_research(task, command)
    if selected == "document_review":
        return _document_review(task)
    return _coding_generate_and_test(task, command)


def _select_template(task: str, template: str) -> str:
    if template != "auto":
        if template not in TEMPLATES:
            raise RuntimeError(f"unknown workflow template: {template}")
        return template
    lowered = task.lower()
    if "strategy" in lowered or "backtest" in lowered or "sharpe" in lowered:
        return "strategy_research_backtest"
    if "document" in lowered or "review" in lowered:
        return "document_review"
    if "fix" in lowered or "test" in lowered or "bug" in lowered:
        if command_placeholder_possible(task):
            return "coding_fix_test"
        return "coding_generate_and_test"
    return "coding_generate_and_test"


def command_placeholder_possible(task: str) -> bool:
    return "pytest" in task.lower() or "test" in task.lower()


def _coding_fix_test(task: str, command: str, attempts: int, max_repairs: int) -> WorkflowPlan:
    attempts = max(1, attempts)
    max_repairs = max(0, max_repairs)
    return WorkflowPlan(
        version=1,
        schema_version=1,
        name="coding_fix_test",
        goal=task,
        description="Closed-loop coding repair from an objective test command.",
        budget={"max_cost_usd": 0.50, "max_candidates": attempts, "max_repairs": max_repairs},
        nodes=(
            WorkflowNode(
                id="reproduce",
                op="exec",
                params={"command": command, "parser": "pytest"},
            ),
            WorkflowNode(
                id="generate",
                op="map",
                inputs=("reproduce",),
                params={"n": attempts, "prompt": task},
                role="Generate independent repair candidates.",
            ),
            WorkflowNode(
                id="run_tests",
                op="exec",
                inputs=("generate",),
                params={"command": command, "parser": "pytest", "allow_tainted_inputs": True},
                policy="allow_tainted_inputs",
            ),
            WorkflowNode(
                id="repair",
                op="loop",
                inputs=("run_tests",),
                params={"until": "passed", "max_iterations": max_repairs},
            ),
            WorkflowNode(id="rank", op="rank", inputs=("repair",)),
            WorkflowNode(id="verify", op="verify", inputs=("rank",)),
            WorkflowNode(id="report", op="report", inputs=("verify",)),
        ),
    )


def _coding_generate_and_test(task: str, command: str) -> WorkflowPlan:
    nodes: list[WorkflowNode] = [
        WorkflowNode(id="classify", op="classify", params={"task": task}),
        WorkflowNode(id="generate", op="generate", inputs=("classify",), params={"prompt": task}),
    ]
    if command:
        nodes.append(
            WorkflowNode(
                id="test",
                op="exec",
                inputs=("generate",),
                params={"command": command, "parser": "pytest", "allow_tainted_inputs": True},
                policy="allow_tainted_inputs",
            )
        )
        nodes.append(WorkflowNode(id="report", op="report", inputs=("test",)))
    else:
        nodes.append(WorkflowNode(id="report", op="report", inputs=("generate",)))
    return WorkflowPlan(
        version=1,
        schema_version=1,
        name="coding_generate_and_test",
        goal=task,
        description="Generate a coding artifact and optionally check it.",
        budget={"max_cost_usd": 0.25},
        nodes=tuple(nodes),
    )


def _strategy_research(task: str, command: str) -> WorkflowPlan:
    backtest_command = command or "python -m pytest tests/test_strategy_fixture.py -q"
    return WorkflowPlan(
        version=1,
        schema_version=1,
        name="strategy_research_backtest",
        goal=task,
        description="Research-only strategy workflow with fixture-backed backtest execution.",
        budget={"max_cost_usd": 0.50, "max_candidates": 3},
        nodes=(
            WorkflowNode(id="classify", op="classify", params={"task": task}),
            WorkflowNode(
                id="generate",
                op="map",
                inputs=("classify",),
                params={"n": 3, "prompt": task},
            ),
            WorkflowNode(
                id="backtest",
                op="exec",
                inputs=("generate",),
                params={
                    "command": backtest_command,
                    "parser": "pytest",
                    "allow_tainted_inputs": True,
                },
                policy="allow_tainted_inputs",
            ),
            WorkflowNode(id="rank", op="rank", inputs=("backtest",)),
            WorkflowNode(id="verify", op="verify", inputs=("rank",)),
            WorkflowNode(
                id="report",
                op="report",
                inputs=("verify",),
                params={"summary": "research only; no trading execution"},
            ),
        ),
    )


def _document_review(task: str) -> WorkflowPlan:
    return WorkflowPlan(
        version=1,
        schema_version=1,
        name="document_review",
        goal=task,
        description="Classify, review, verify, and summarize a document task.",
        budget={"max_cost_usd": 0.10},
        nodes=(
            WorkflowNode(id="classify", op="classify", params={"task": task}),
            WorkflowNode(id="review", op="generate", inputs=("classify",), params={"prompt": task}),
            WorkflowNode(id="verify", op="verify", inputs=("review",)),
            WorkflowNode(id="report", op="report", inputs=("verify",)),
        ),
    )
