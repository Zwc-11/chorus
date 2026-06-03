"""Typed YAML contract for AI-generated code changes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class ContractTask:
    id: str
    type: str
    title: str
    command: str


@dataclass(frozen=True, slots=True)
class RepoSpec:
    root: str = "."
    base_ref: str = "HEAD"
    worktree_mode: str = "isolated"


@dataclass(frozen=True, slots=True)
class RiskSpec:
    level: str = "medium"
    reason: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BudgetSpec:
    max_cost_usd: float = 0.50
    max_model_calls: int = 12
    max_tool_calls: int = 80
    max_runtime_seconds: int = 600


@dataclass(frozen=True, slots=True)
class FilePolicy:
    allow_read: tuple[str, ...] = ("**/*.py", "pyproject.toml", "package.json")
    allow_edit: tuple[str, ...] = ("**/*.py",)
    deny_read: tuple[str, ...] = (".env", ".env.*", "secrets/**")
    deny_edit: tuple[str, ...] = (
        ".env",
        ".env.*",
        "secrets/**",
        ".github/workflows/**",
        "migrations/**",
    )


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    allow: tuple[str, ...] = (
        "list_files",
        "search",
        "read_file",
        "apply_patch",
        "run_test",
        "git_diff",
        "finish",
    )
    deny: tuple[str, ...] = ("network", "install_dependency", "delete_file", "push_branch")


@dataclass(frozen=True, slots=True)
class ProofSpec:
    reproduce_before_fix: bool = True
    target_test_passes_after_fix: bool = True
    related_tests: tuple[str, ...] = ()
    static_checks: tuple[str, ...] = ()
    forbidden_files_unchanged: bool = True
    max_files_changed: int = 3
    max_diff_lines: int = 200


@dataclass(frozen=True, slots=True)
class Contract:
    version: int
    task: ContractTask
    repo: RepoSpec = field(default_factory=RepoSpec)
    risk: RiskSpec = field(default_factory=RiskSpec)
    budget: BudgetSpec = field(default_factory=BudgetSpec)
    files: FilePolicy = field(default_factory=FilePolicy)
    tools: ToolPolicy = field(default_factory=ToolPolicy)
    required_proof: ProofSpec = field(default_factory=ProofSpec)

    def validate(self) -> list[str]:
        issues: list[str] = []
        if self.version != 1:
            issues.append("version must be 1")
        if not self.task.id:
            issues.append("task.id is required")
        if self.task.type != "failing_test":
            issues.append("only task.type='failing_test' is supported in the MVP")
        if not self.task.command:
            issues.append("task.command is required")
        if self.budget.max_cost_usd < 0:
            issues.append("budget.max_cost_usd must be non-negative")
        if self.budget.max_tool_calls < 1:
            issues.append("budget.max_tool_calls must be at least 1")
        if self.required_proof.max_files_changed < 1:
            issues.append("required_proof.max_files_changed must be at least 1")
        if self.required_proof.max_diff_lines < 1:
            issues.append("required_proof.max_diff_lines must be at least 1")
        return issues

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Contract:
        return cls(
            version=int(data.get("version", 1)),
            task=ContractTask(**dict(data["task"])),
            repo=RepoSpec(**dict(data.get("repo", {}))),
            risk=RiskSpec(
                level=str(data.get("risk", {}).get("level", "medium")),
                reason=tuple(data.get("risk", {}).get("reason", ())),
            ),
            budget=BudgetSpec(**dict(data.get("budget", {}))),
            files=FilePolicy(
                allow_read=tuple(data.get("files", {}).get("allow_read", FilePolicy.allow_read)),
                allow_edit=tuple(data.get("files", {}).get("allow_edit", FilePolicy.allow_edit)),
                deny_read=tuple(data.get("files", {}).get("deny_read", FilePolicy.deny_read)),
                deny_edit=tuple(data.get("files", {}).get("deny_edit", FilePolicy.deny_edit)),
            ),
            tools=ToolPolicy(
                allow=tuple(data.get("tools", {}).get("allow", ToolPolicy.allow)),
                deny=tuple(data.get("tools", {}).get("deny", ToolPolicy.deny)),
            ),
            required_proof=ProofSpec(
                reproduce_before_fix=bool(
                    data.get("required_proof", {}).get("reproduce_before_fix", True)
                ),
                target_test_passes_after_fix=bool(
                    data.get("required_proof", {}).get("target_test_passes_after_fix", True)
                ),
                related_tests=tuple(data.get("required_proof", {}).get("related_tests", ())),
                static_checks=tuple(data.get("required_proof", {}).get("static_checks", ())),
                forbidden_files_unchanged=bool(
                    data.get("required_proof", {}).get("forbidden_files_unchanged", True)
                ),
                max_files_changed=int(data.get("required_proof", {}).get("max_files_changed", 3)),
                max_diff_lines=int(data.get("required_proof", {}).get("max_diff_lines", 200)),
            ),
        )

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False)

    @classmethod
    def from_yaml(cls, text: str) -> Contract:
        data = yaml.safe_load(text) or {}
        return cls.from_dict(dict(data))

    @classmethod
    def read(cls, path: Path) -> Contract:
        return cls.from_yaml(path.read_text(encoding="utf-8"))

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_yaml(), encoding="utf-8")
        return path
