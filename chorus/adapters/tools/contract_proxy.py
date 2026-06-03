"""Policy-controlled typed tools for contract execution."""

from __future__ import annotations

from time import perf_counter
from typing import Any

from chorus.adapters.sandboxes.local_worktree import LocalWorktreeSandbox
from chorus.application.event_log import JsonlRunEventLog
from chorus.domain.policy import BudgetState, PolicyEngine
from chorus.domain.tool import ToolRequest, ToolResult


class ContractToolProxy:
    def __init__(
        self,
        *,
        sandbox: LocalWorktreeSandbox,
        policy: PolicyEngine,
        budget: BudgetState,
        events: JsonlRunEventLog,
    ) -> None:
        self.sandbox = sandbox
        self.policy = policy
        self.budget = budget
        self.events = events
        self.finished = False
        self.finish_summary = ""

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        request = ToolRequest(name, args)
        self.events.emit("tool_call_requested", {"tool": name, "args": args})
        decision = self.policy.evaluate(request)
        self.events.emit("policy_decision", {"tool": name, "decision": decision})
        if not decision.allowed:
            result = ToolResult(name, False, error=decision.reason)
            self.events.emit("tool_call_denied", {"tool": name, "error": decision.reason})
            return result

        self.budget.tool_calls += 1
        start = perf_counter()
        try:
            payload = self._execute(name, args)
            result = ToolResult(name, True, result=payload, latency_ms=_elapsed(start))
        except Exception as exc:  # noqa: BLE001 - tool proxy records all tool faults
            result = ToolResult(name, False, error=str(exc), latency_ms=_elapsed(start))
        self.events.emit("tool_result", {"tool": name, "result": result})
        return result

    def _execute(self, name: str, args: dict[str, Any]) -> Any:
        if name == "list_files":
            return self.sandbox.list_files(str(args.get("glob", "**/*")))
        if name == "search":
            return self.sandbox.search(str(args.get("query", "")), str(args.get("glob", "**/*")))
        if name == "read_file":
            return self.sandbox.read_file(str(args["path"]))
        if name == "apply_patch":
            proc = self.sandbox.apply_patch(str(args["patch"]))
            if proc.returncode != 0:
                raise RuntimeError(proc.output or "patch apply failed")
            self.events.emit("patch_applied", {"stdout": proc.stdout, "stderr": proc.stderr})
            return "patch applied"
        if name == "run_test":
            proc = self.sandbox.run(
                str(args["command"]),
                timeout_s=self.policy.contract.budget.max_runtime_seconds,
            )
            return {
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "timeout": proc.timeout,
            }
        if name == "git_diff":
            return self.sandbox.git_diff()
        if name == "finish":
            self.finished = True
            self.finish_summary = str(args.get("summary", ""))
            return self.finish_summary
        raise KeyError(f"unknown tool {name!r}")


def _elapsed(start: float) -> float:
    return (perf_counter() - start) * 1000
