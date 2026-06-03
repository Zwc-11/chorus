"""Verify contract satisfaction after an agent run."""

from __future__ import annotations

from chorus.adapters.sandboxes.local_worktree import LocalWorktreeSandbox
from chorus.domain.contract import Contract
from chorus.domain.policy import PolicyEngine
from chorus.domain.verification import VerificationResult


def verify_contract(
    *,
    contract: Contract,
    sandbox: LocalWorktreeSandbox,
    policy: PolicyEngine,
    failure_reproduced: bool,
) -> VerificationResult:
    failures: list[str] = []
    target = sandbox.run(
        contract.task.command,
        timeout_s=contract.budget.max_runtime_seconds,
    )
    target_passed = target.returncode == 0
    if not target_passed:
        failures.append("test_still_failing")

    related_outputs: dict[str, str] = {}
    related_passed = True
    for command in contract.required_proof.related_tests:
        result = sandbox.run(command, timeout_s=contract.budget.max_runtime_seconds)
        related_outputs[command] = result.output
        if result.returncode != 0:
            related_passed = False
            failures.append("related_test_regression")

    static_outputs: dict[str, str] = {}
    static_passed = True
    for command in contract.required_proof.static_checks:
        result = sandbox.run(command, timeout_s=contract.budget.max_runtime_seconds)
        static_outputs[command] = result.output
        if result.returncode != 0:
            static_passed = False
            failures.append("static_check_failed")

    changed = sandbox.changed_files()
    forbidden = tuple(
        path for path in changed if not policy.check_changed_file(path).allowed
    )
    if forbidden:
        failures.append("forbidden_file_touched")
    if len(changed) > contract.required_proof.max_files_changed:
        failures.append("too_many_files_changed")

    diff = sandbox.git_diff()
    diff_lines = sum(1 for line in diff.splitlines() if line.startswith(("+", "-")))
    if diff_lines > contract.required_proof.max_diff_lines:
        failures.append("diff_too_large")
    if not failure_reproduced:
        failures.append("failure_not_reproduced")

    passed = (
        failure_reproduced
        and target_passed
        and related_passed
        and static_passed
        and not forbidden
        and len(changed) <= contract.required_proof.max_files_changed
        and diff_lines <= contract.required_proof.max_diff_lines
    )
    return VerificationResult(
        passed=passed,
        failure_reproduced=failure_reproduced,
        target_test_passed=target_passed,
        related_tests_passed=related_passed,
        static_checks_passed=static_passed,
        forbidden_files_touched=forbidden,
        changed_files=changed,
        diff_lines=diff_lines,
        failures=tuple(dict.fromkeys(failures)),
        target_output=target.output,
        related_outputs=related_outputs,
        static_outputs=static_outputs,
    )
