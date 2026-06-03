"""Compile failing-test commands into enforceable Chorus contracts."""

from __future__ import annotations

import re
from pathlib import Path

from chorus.domain.contract import (
    BudgetSpec,
    Contract,
    ContractTask,
    FilePolicy,
    ProofSpec,
    RepoSpec,
    RiskSpec,
    ToolPolicy,
)


def compile_fix_test_contract(
    *,
    command: str,
    repo_root: Path,
    failure_output: str = "",
    budget_usd: float = 0.50,
    task_id: str = "",
) -> Contract:
    """Build the MVP least-surprise contract for a failing test command."""

    test_paths = _paths_from_command(command)
    traceback_paths = _paths_from_traceback(failure_output, repo_root=repo_root)
    allow_read = _dedupe(
        (*test_paths, *traceback_paths, "**/*.py", "pyproject.toml", "package.json")
    )
    allow_edit = _dedupe(("**/*.py", *traceback_paths))
    related = _related_tests(command)
    return Contract(
        version=1,
        task=ContractTask(
            id=task_id or _slug(command),
            type="failing_test",
            title=f"Fix failing test: {command}",
            command=command,
        ),
        repo=RepoSpec(root=str(repo_root), base_ref="HEAD", worktree_mode="isolated"),
        risk=RiskSpec(level="medium", reason=("Generated from failing test command",)),
        budget=BudgetSpec(max_cost_usd=budget_usd),
        files=FilePolicy(allow_read=allow_read, allow_edit=allow_edit),
        tools=ToolPolicy(),
        required_proof=ProofSpec(
            related_tests=related,
            static_checks=(),
            max_files_changed=3,
            max_diff_lines=200,
        ),
    )


def _paths_from_command(command: str) -> tuple[str, ...]:
    return tuple(
        token.replace("\\", "/")
        for token in re.split(r"\s+", command)
        if token.endswith(".py") or token.startswith("tests/")
    )


def _paths_from_traceback(output: str, *, repo_root: Path) -> tuple[str, ...]:
    paths: list[str] = []
    for match in re.finditer(r'File "([^"]+)"', output):
        raw = Path(match.group(1))
        try:
            value = raw.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            value = raw.as_posix()
        if value.endswith(".py"):
            paths.append(value)
    return tuple(paths)


def _related_tests(command: str) -> tuple[str, ...]:
    if "pytest" not in command:
        return ()
    paths = [item for item in _paths_from_command(command) if item.startswith("tests/")]
    if not paths:
        return ()
    top = paths[0].split("/")[:2]
    if len(top) >= 2:
        return (f"python -m pytest {'/'.join(top)} -q",)
    return ()


def _slug(command: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", "-", command).strip("-").lower()
    return clean[:64] or "fix-test"


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return tuple(out)
