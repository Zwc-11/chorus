"""Policy decisions and deterministic contract enforcement."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Literal

from murmur.domain.contract import Contract
from murmur.domain.tool import ToolRequest

Decision = Literal["allow", "deny", "ask_human", "escalate"]


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision: Decision
    rule_id: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


@dataclass(slots=True)
class BudgetState:
    cost_usd: float = 0.0
    model_calls: int = 0
    tool_calls: int = 0
    runtime_seconds: float = 0.0


class PolicyEngine:
    def __init__(self, contract: Contract, budget: BudgetState | None = None) -> None:
        self.contract = contract
        self.budget = budget or BudgetState()

    def evaluate(self, request: ToolRequest) -> PolicyDecision:
        budget = self._budget_decision()
        if not budget.allowed:
            return budget
        if request.tool_name in self.contract.tools.deny:
            return PolicyDecision("deny", "tool_denied", f"tool {request.tool_name!r} is denied")
        if request.tool_name not in self.contract.tools.allow:
            return PolicyDecision(
                "deny", "tool_not_allowed", f"tool {request.tool_name!r} is not in allow list"
            )
        if request.tool_name == "read_file":
            return self._file_decision(str(request.args.get("path", "")), write=False)
        if request.tool_name == "apply_patch":
            return PolicyDecision("allow", "tool_allowed", "patch text will be checked after apply")
        if request.tool_name == "run_test":
            return self._shell_decision(str(request.args.get("command", "")))
        return PolicyDecision("allow", "tool_allowed", f"tool {request.tool_name!r} is allowed")

    def check_changed_file(self, path: str) -> PolicyDecision:
        return self._file_decision(path, write=True)

    def _budget_decision(self) -> PolicyDecision:
        budget = self.contract.budget
        if self.budget.cost_usd > budget.max_cost_usd:
            return PolicyDecision("deny", "budget_exceeded", "max cost exceeded")
        if self.budget.model_calls > budget.max_model_calls:
            return PolicyDecision("deny", "budget_exceeded", "max model calls exceeded")
        if self.budget.tool_calls >= budget.max_tool_calls:
            return PolicyDecision("deny", "budget_exceeded", "max tool calls exceeded")
        if self.budget.runtime_seconds > budget.max_runtime_seconds:
            return PolicyDecision("deny", "budget_exceeded", "max runtime exceeded")
        return PolicyDecision("allow", "budget_ok", "budget remains available")

    def _file_decision(self, path: str, *, write: bool) -> PolicyDecision:
        clean = path.replace("\\", "/").lstrip("/")
        deny = self.contract.files.deny_edit if write else self.contract.files.deny_read
        allow = self.contract.files.allow_edit if write else self.contract.files.allow_read
        if _matches(clean, deny):
            return PolicyDecision(
                "deny",
                "file_denied",
                f"{'edit' if write else 'read'} denied by file policy: {clean}",
            )
        if _matches(clean, allow):
            return PolicyDecision(
                "allow",
                "file_allowed",
                f"{'edit' if write else 'read'} allowed by file policy: {clean}",
            )
        return PolicyDecision(
            "deny",
            "file_not_allowed",
            f"{'edit' if write else 'read'} not allowed by file policy: {clean}",
        )

    def _shell_decision(self, command: str) -> PolicyDecision:
        normalized = command.strip()
        lowered = normalized.lower()
        blocked = ("rm -rf", "curl ", "wget ", "git push", "pip install", "npm install")
        if any(item in lowered for item in blocked):
            return PolicyDecision("deny", "shell_denied", f"command is blocked: {normalized}")
        allowed_prefixes = (
            "pytest",
            "python -m pytest",
            "python -m compileall",
            "npm test",
            "npm run test",
            "npm run typecheck",
        )
        explicit = (
            self.contract.task.command,
            *self.contract.required_proof.related_tests,
            *self.contract.required_proof.static_checks,
        )
        if normalized in explicit or lowered.startswith(allowed_prefixes):
            return PolicyDecision("allow", "shell_allowed", f"command allowed: {normalized}")
        return PolicyDecision("deny", "shell_not_allowed", f"command not allowed: {normalized}")


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, f"**/{pattern}"):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(path, pattern.removeprefix("**/")):
            return True
    return False
