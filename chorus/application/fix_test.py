"""Contract-first failing-test workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from chorus.adapters.agents.contract_lite import build_contract_agent
from chorus.adapters.sandboxes.local_worktree import LocalWorktreeSandbox
from chorus.adapters.tools.contract_proxy import ContractToolProxy
from chorus.application.contract_compiler import compile_fix_test_contract
from chorus.application.event_log import JsonlRunEventLog
from chorus.application.proof_builder import write_proof_package
from chorus.application.verifier import verify_contract
from chorus.application.workflow_runtime import (
    OperatorRegistry,
    WorkflowContext,
    WorkflowNodeResult,
    WorkflowRuntime,
)
from chorus.domain.contract import Contract
from chorus.domain.policy import BudgetState, PolicyEngine
from chorus.domain.proof import ProofPackage
from chorus.domain.tool import ExecResult
from chorus.domain.verification import VerificationResult
from chorus.domain.workflow import WorkflowNode, WorkflowPlan


@dataclass(frozen=True, slots=True)
class AttemptResult:
    attempt_id: str
    passed: bool
    summary: str
    diff: str
    verification: VerificationResult
    test_results: tuple[ExecResult, ...]
    model_calls: int
    tool_calls: int
    cost_usd: float
    repair_iterations: int
    run_dir: Path

    @property
    def target_latency_ms(self) -> float:
        if not self.test_results:
            return 0.0
        return self.test_results[-1].latency_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "passed": self.passed,
            "summary": self.summary,
            "diff": self.diff,
            "verification": self.verification.to_dict(),
            "test_results": [result.to_dict() for result in self.test_results],
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "cost_usd": self.cost_usd,
            "repair_iterations": self.repair_iterations,
            "run_dir": str(self.run_dir),
        }


@dataclass(frozen=True, slots=True)
class FixTestWorkflowConfig:
    attempts: int
    max_repairs: int
    agent_name: str
    provider: str
    model: str
    test_command: str


@dataclass(frozen=True, slots=True)
class FixTestWorkflowResult:
    verification: VerificationResult
    diff: str
    summary: str
    attempts: tuple[AttemptResult, ...]
    winner: AttemptResult | None = None


@dataclass(slots=True)
class FixTestRuntimeState:
    config: FixTestWorkflowConfig
    reproduce_result: ExecResult
    reproduce_sandbox: LocalWorktreeSandbox
    failure_reproduced: bool
    attempts: list[AttemptResult]
    winner: AttemptResult | None = None
    verification: VerificationResult | None = None
    diff: str = ""
    summary: str = ""


def run_fix_test(
    *,
    command: str,
    repo_root: Path,
    out_root: Path,
    budget_usd: float = 0.50,
    agent_name: str = "scripted",
    provider: str = "",
    model: str = "",
    attempts: int = 1,
    max_repairs: int = 0,
) -> ProofPackage:
    if attempts < 1:
        raise RuntimeError("attempts must be at least 1")
    if max_repairs < 0:
        raise RuntimeError("max_repairs must be non-negative")

    run_id = f"run_{uuid4().hex[:12]}"
    run_dir = out_root / run_id
    reproduce_sandbox = LocalWorktreeSandbox.create(repo_root, run_dir / "reproduce")
    before = reproduce_sandbox.run(command, parser="pytest")
    failure_reproduced = not before.passed

    contract = compile_fix_test_contract(
        command=command,
        repo_root=repo_root,
        failure_output=before.output,
        budget_usd=budget_usd,
    )
    contract.write(run_dir / "contract.yaml")
    workflow = compile_fix_test_workflow(
        contract=contract,
        attempts=attempts,
        max_repairs=max_repairs,
        agent_name=agent_name,
        provider=provider,
        model=model,
    )
    workflow_issues = workflow.validate()
    if workflow_issues:
        raise RuntimeError("; ".join(workflow_issues))
    workflow_config = validate_fix_test_workflow(workflow, contract=contract)
    workflow.write(run_dir / "workflow.yaml")

    runtime_state = FixTestRuntimeState(
        config=workflow_config,
        reproduce_result=before,
        reproduce_sandbox=reproduce_sandbox,
        failure_reproduced=failure_reproduced,
        attempts=[],
    )
    runtime = WorkflowRuntime(
        repo_root=repo_root,
        out_root=out_root,
        contract=contract,
        registry=_fix_test_operator_registry(runtime_state),
    )
    runtime_result = runtime.run(workflow, run_id=run_id)
    result = _fix_test_result_from_state(runtime_state)
    budget = runtime_result.proof["budget"]

    proof = ProofPackage(
        run_id=run_id,
        verdict="pass" if result.verification.passed else "fail",
        contract=contract,
        verification=result.verification,
        diff=result.diff,
        model_calls=int(budget.get("model_calls", 0)),
        tool_calls=int(budget.get("tool_calls", 0)),
        cost_usd=float(budget.get("cost_usd", 0.0)),
        summary=result.summary,
        attempts=tuple(attempt.to_dict() for attempt in result.attempts),
    )
    if result.winner is not None:
        _write_winner_evidence(result.winner, run_dir / "winner")
    write_proof_package(proof, run_dir)
    return proof


def _fix_test_operator_registry(state: FixTestRuntimeState) -> OperatorRegistry:
    registry = OperatorRegistry()

    def reproduce(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
        context.events.emit(
            "test_finished",
            {"phase": "reproduce", **state.reproduce_result.to_dict()},
        )
        return _runtime_node_result(
            node,
            {
                "result": state.reproduce_result.to_dict(),
                "failure_reproduced": state.failure_reproduced,
            },
            output=state.reproduce_result.output or state.reproduce_result.summary,
        )

    def generate(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
        if not state.failure_reproduced:
            return _runtime_node_result(
                node,
                {"attempts": [], "reason": "failure_not_reproduced"},
                output="failure not reproduced",
            )
        state.attempts.clear()
        for index in range(state.config.attempts):
            attempt = run_attempt(
                contract=context.contract or _require_contract(),
                attempt_id=f"attempt_{index + 1}",
                repo_root=context.repo_root,
                attempt_dir=context.run_dir / "attempts" / f"attempt_{index + 1}",
                agent_name=state.config.agent_name,
                provider=state.config.provider,
                model=state.config.model,
                budget=context.budget,
                failure_reproduced=state.failure_reproduced,
                max_repairs=state.config.max_repairs,
                seed=index * 1000,
                test_command=state.config.test_command,
            )
            state.attempts.append(attempt)
            context.events.emit(
                "attempt_finished",
                {
                    "attempt_id": attempt.attempt_id,
                    "passed": attempt.passed,
                    "failures": attempt.verification.failures,
                    "diff_lines": attempt.verification.diff_lines,
                },
            )
        return _runtime_node_result(
            node,
            {"attempts": [attempt.to_dict() for attempt in state.attempts]},
            output=f"{len(state.attempts)} attempts generated",
        )

    def run_tests(node: WorkflowNode, _context: WorkflowContext) -> WorkflowNodeResult:
        test_count = sum(len(attempt.test_results) for attempt in state.attempts)
        return _runtime_node_result(
            node,
            {
                "test_results": test_count,
                "attempts": [
                    {
                        "attempt_id": attempt.attempt_id,
                        "tests": [result.to_dict() for result in attempt.test_results],
                    }
                    for attempt in state.attempts
                ],
            },
            output=f"{test_count} test executions",
        )

    def repair(node: WorkflowNode, _context: WorkflowContext) -> WorkflowNodeResult:
        return _runtime_node_result(
            node,
            {
                "max_iterations": state.config.max_repairs,
                "repair_iterations": {
                    attempt.attempt_id: attempt.repair_iterations for attempt in state.attempts
                },
            },
            output=f"max repairs {state.config.max_repairs}",
        )

    def rank(node: WorkflowNode, _context: WorkflowContext) -> WorkflowNodeResult:
        if not state.attempts:
            return _runtime_node_result(
                node,
                {"winner": None, "reason": "failure_not_reproduced"},
                output="no attempts",
            )
        state.winner = _select_winner(state.attempts)
        return _runtime_node_result(
            node,
            {
                "winner": state.winner.attempt_id,
                "ranking": [
                    attempt.attempt_id for attempt in sorted(state.attempts, key=_attempt_rank_key)
                ],
            },
            output=state.winner.attempt_id,
        )

    def verify(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
        contract = context.contract or _require_contract()
        if state.winner is None:
            policy = PolicyEngine(contract, context.budget)
            verification = verify_contract(
                contract=contract,
                sandbox=state.reproduce_sandbox,
                policy=policy,
                failure_reproduced=False,
            )
            diff = state.reproduce_sandbox.git_diff()
        else:
            verification = state.winner.verification
            diff = state.winner.diff
        state.verification = verification
        state.diff = diff
        return _runtime_node_result(
            node,
            {"verification": verification.to_dict()},
            output=", ".join(verification.failures) or "verified",
        )

    def report(node: WorkflowNode, _context: WorkflowContext) -> WorkflowNodeResult:
        verification = state.verification
        if verification is None:
            raise RuntimeError("fix-test report ran before verification")
        if state.winner is None:
            summary = "failure not reproduced"
        else:
            summary = _winner_summary(state.winner, state.attempts)
        state.summary = summary
        return _runtime_node_result(
            node,
            {
                "summary": summary,
                "verification": verification.to_dict(),
                "attempts": [attempt.to_dict() for attempt in state.attempts],
                "winner": state.winner.attempt_id if state.winner is not None else None,
            },
            output=summary,
            passed=verification.passed,
            quarantined=not verification.passed,
        )

    def exec_operator(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
        if node.id == "reproduce":
            return reproduce(node, context)
        return run_tests(node, context)

    registry.register("exec", exec_operator)
    registry.register("generate", generate)
    registry.register("loop", repair)
    registry.register("rank", rank)
    registry.register("verify", verify)
    registry.register("report", report)
    return registry


def _fix_test_result_from_state(state: FixTestRuntimeState) -> FixTestWorkflowResult:
    if state.verification is None:
        raise RuntimeError("fix-test workflow did not produce verification")
    return FixTestWorkflowResult(
        verification=state.verification,
        diff=state.diff,
        summary=state.summary,
        attempts=tuple(state.attempts),
        winner=state.winner,
    )


def _runtime_node_result(
    node: WorkflowNode,
    result: dict[str, Any],
    *,
    output: str = "",
    passed: bool = True,
    quarantined: bool = False,
) -> WorkflowNodeResult:
    return WorkflowNodeResult(
        node_id=node.id,
        op=node.op,
        status="completed" if passed else "failed",
        passed=passed,
        result=result,
        output=output,
        quarantined=quarantined,
    )


def _require_contract() -> Contract:
    raise RuntimeError("fix-test workflow requires a contract")


def execute_fix_test_workflow(
    *,
    workflow: WorkflowPlan,
    config: FixTestWorkflowConfig,
    contract: Contract,
    repo_root: Path,
    run_dir: Path,
    events: JsonlRunEventLog,
    budget: BudgetState,
    failure_reproduced: bool,
    reproduce_result: ExecResult,
    reproduce_sandbox: LocalWorktreeSandbox,
) -> FixTestWorkflowResult:
    nodes = _workflow_nodes(workflow)
    _finish_node(events, nodes["reproduce"], {"result": reproduce_result.to_dict()})

    if not failure_reproduced:
        events.emit("verification_failed", {"failure": "failure_not_reproduced"})
        _skip_node(events, nodes["generate"], "failure_not_reproduced")
        _skip_node(events, nodes["run_tests"], "failure_not_reproduced")
        _skip_node(events, nodes["repair"], "failure_not_reproduced")
        _skip_node(events, nodes["rank"], "failure_not_reproduced")
        _start_node(events, nodes["verify"])
        policy = PolicyEngine(contract, budget)
        verification = verify_contract(
            contract=contract,
            sandbox=reproduce_sandbox,
            policy=policy,
            failure_reproduced=False,
        )
        diff = reproduce_sandbox.git_diff()
        _finish_node(events, nodes["verify"], {"verification": verification.to_dict()})
        _start_node(events, nodes["report"])
        _finish_node(events, nodes["report"], {"summary": "failure not reproduced"})
        return FixTestWorkflowResult(
            verification=verification,
            diff=diff,
            summary="failure not reproduced",
            attempts=(),
        )

    _start_node(events, nodes["generate"])
    _start_node(events, nodes["run_tests"])
    _start_node(events, nodes["repair"])
    attempt_results: list[AttemptResult] = []
    for index in range(config.attempts):
        attempt = run_attempt(
            contract=contract,
            attempt_id=f"attempt_{index + 1}",
            repo_root=repo_root,
            attempt_dir=run_dir / "attempts" / f"attempt_{index + 1}",
            agent_name=config.agent_name,
            provider=config.provider,
            model=config.model,
            budget=budget,
            failure_reproduced=failure_reproduced,
            max_repairs=config.max_repairs,
            seed=index * 1000,
            test_command=config.test_command,
        )
        attempt_results.append(attempt)
        events.emit(
            "attempt_finished",
            {
                "attempt_id": attempt.attempt_id,
                "passed": attempt.passed,
                "failures": attempt.verification.failures,
                "diff_lines": attempt.verification.diff_lines,
            },
        )
    _finish_node(events, nodes["generate"], {"attempts": len(attempt_results)})
    _finish_node(
        events,
        nodes["run_tests"],
        {"test_results": sum(len(attempt.test_results) for attempt in attempt_results)},
    )
    _finish_node(events, nodes["repair"], {"max_iterations": config.max_repairs})

    _start_node(events, nodes["rank"])
    winner = _select_winner(attempt_results)
    _finish_node(events, nodes["rank"], {"winner": winner.attempt_id})

    _start_node(events, nodes["verify"])
    _finish_node(events, nodes["verify"], {"verification": winner.verification.to_dict()})

    summary = _winner_summary(winner, attempt_results)
    _start_node(events, nodes["report"])
    _finish_node(events, nodes["report"], {"summary": summary})
    return FixTestWorkflowResult(
        verification=winner.verification,
        diff=winner.diff,
        summary=summary,
        attempts=tuple(attempt_results),
        winner=winner,
    )


def run_attempt(
    *,
    contract: Contract,
    attempt_id: str,
    repo_root: Path,
    attempt_dir: Path,
    agent_name: str,
    provider: str,
    model: str,
    budget: BudgetState,
    failure_reproduced: bool,
    max_repairs: int,
    seed: int,
    test_command: str | None = None,
) -> AttemptResult:
    sandbox = LocalWorktreeSandbox.create(repo_root, attempt_dir)
    events = JsonlRunEventLog(attempt_dir / "events.jsonl", run_id=attempt_id)
    events.emit("attempt_started", {"attempt_id": attempt_id, "max_repairs": max_repairs})

    model_calls_before = budget.model_calls
    tool_calls_before = budget.tool_calls
    cost_before = budget.cost_usd
    summaries: list[str] = []
    test_results: list[ExecResult] = []
    feedback = ""

    for iteration in range(max_repairs + 1):
        phase = "generate" if iteration == 0 else "repair"
        events.emit(f"{phase}_started", {"iteration": iteration})
        policy = PolicyEngine(contract, budget)
        proxy = ContractToolProxy(sandbox=sandbox, policy=policy, budget=budget, events=events)
        agent = build_contract_agent(
            agent=agent_name,
            provider=provider,
            model=model,
            seed=seed + iteration,
        )
        try:
            summary = agent.run(contract=contract, tools=proxy, feedback=feedback)
        except Exception as exc:  # noqa: BLE001 - one candidate should not kill the run
            summary = f"agent error: {exc}"
            events.emit("agent_error", {"iteration": iteration, "error": str(exc)})
        summaries.append(f"{phase} {iteration}: {summary}")

        test_result = _run_policy_test(proxy, test_command or contract.task.command)
        test_results.append(test_result)
        events.emit("attempt_test_finished", {"iteration": iteration, **test_result.to_dict()})
        if test_result.passed:
            break
        feedback = test_result.output or test_result.summary

    policy = PolicyEngine(contract, budget)
    verification = verify_contract(
        contract=contract,
        sandbox=sandbox,
        policy=policy,
        failure_reproduced=failure_reproduced,
    )
    diff = sandbox.git_diff()
    result = AttemptResult(
        attempt_id=attempt_id,
        passed=verification.passed,
        summary="\n".join(summaries),
        diff=diff,
        verification=verification,
        test_results=tuple(test_results),
        model_calls=budget.model_calls - model_calls_before,
        tool_calls=budget.tool_calls - tool_calls_before,
        cost_usd=budget.cost_usd - cost_before,
        repair_iterations=max(0, len(test_results) - 1),
        run_dir=attempt_dir,
    )
    _write_attempt_evidence(result, attempt_dir)
    events.emit("attempt_verified", result.to_dict())
    return result


def compile_fix_test_workflow(
    *,
    contract: Contract,
    attempts: int,
    max_repairs: int,
    agent_name: str,
    provider: str,
    model: str,
) -> WorkflowPlan:
    return WorkflowPlan(
        version=1,
        goal=f"Closed-loop fix-test: {contract.task.command}",
        budget={
            "max_cost_usd": contract.budget.max_cost_usd,
            "max_model_calls": contract.budget.max_model_calls,
            "max_tool_calls": contract.budget.max_tool_calls,
            "max_runtime_seconds": contract.budget.max_runtime_seconds,
            "max_candidates": attempts,
            "max_repairs_per_candidate": max_repairs,
        },
        nodes=(
            WorkflowNode(
                id="reproduce",
                op="exec",
                params={"command": contract.task.command, "parser": "pytest"},
            ),
            WorkflowNode(
                id="generate",
                op="generate",
                inputs=("reproduce",),
                params={
                    "n": attempts,
                    "agent": agent_name,
                    "provider": provider,
                    "model": model,
                    "isolation": "worktree_per_attempt",
                },
            ),
            WorkflowNode(
                id="run_tests",
                op="exec",
                inputs=("generate",),
                params={"command": contract.task.command, "parser": "pytest"},
            ),
            WorkflowNode(
                id="repair",
                op="loop",
                inputs=("run_tests",),
                params={
                    "max_iterations": max_repairs,
                    "until": "passed",
                    "feedback": "test_output",
                },
            ),
            WorkflowNode(
                id="rank",
                op="rank",
                inputs=("repair",),
                params={
                    "eligible": "verification.passed",
                    "order": [
                        "verification.passed desc",
                        "len(verification.failures) asc",
                        "len(verification.changed_files) asc",
                        "verification.diff_lines asc",
                        "target_latency_ms asc",
                    ],
                },
            ),
            WorkflowNode(
                id="verify",
                op="verify",
                inputs=("rank",),
                params={"contract_checks": True},
            ),
            WorkflowNode(id="report", op="report", inputs=("verify",)),
        ),
    )


def validate_fix_test_workflow(
    workflow: WorkflowPlan,
    *,
    contract: Contract,
) -> FixTestWorkflowConfig:
    nodes = _workflow_nodes(workflow)
    expected_ops = {
        "reproduce": "exec",
        "generate": "generate",
        "run_tests": "exec",
        "repair": "loop",
        "rank": "rank",
        "verify": "verify",
        "report": "report",
    }
    for node_id, op in expected_ops.items():
        node = nodes.get(node_id)
        if node is None:
            raise RuntimeError(f"workflow missing node {node_id!r}")
        if node.op != op:
            raise RuntimeError(f"workflow node {node_id!r} must use op {op!r}")

    reproduce_command = str(nodes["reproduce"].params.get("command", ""))
    test_command = str(nodes["run_tests"].params.get("command", ""))
    if reproduce_command != contract.task.command:
        raise RuntimeError("workflow reproduce command must match contract task command")
    if test_command != contract.task.command:
        raise RuntimeError("workflow run_tests command must match contract task command")

    attempts = int(nodes["generate"].params.get("n", 0))
    max_repairs = int(nodes["repair"].params.get("max_iterations", -1))
    if attempts < 1:
        raise RuntimeError("workflow generate.n must be at least 1")
    if max_repairs < 0:
        raise RuntimeError("workflow repair.max_iterations must be non-negative")
    return FixTestWorkflowConfig(
        attempts=attempts,
        max_repairs=max_repairs,
        agent_name=str(nodes["generate"].params.get("agent", "scripted")),
        provider=str(nodes["generate"].params.get("provider", "")),
        model=str(nodes["generate"].params.get("model", "")),
        test_command=test_command,
    )


def run_contract(
    *,
    contract: Contract,
    out_root: Path,
    agent_name: str = "scripted",
    provider: str = "",
    model: str = "",
) -> ProofPackage:
    return run_fix_test(
        command=contract.task.command,
        repo_root=Path(contract.repo.root),
        out_root=out_root,
        budget_usd=contract.budget.max_cost_usd,
        agent_name=agent_name,
        provider=provider,
        model=model,
    )


def proof_summary(proof: ProofPackage) -> str:
    return json.dumps(
        {
            "run_id": proof.run_id,
            "verdict": proof.verdict,
            "changed_files": proof.verification.changed_files,
            "failures": proof.verification.failures,
            "attempts": len(proof.attempts),
        },
        indent=2,
    )


def _workflow_nodes(workflow: WorkflowPlan) -> dict[str, WorkflowNode]:
    return {node.id: node for node in workflow.nodes}


def _start_node(events: JsonlRunEventLog, node: WorkflowNode) -> None:
    events.emit(
        "workflow_node_started",
        {"node_id": node.id, "op": node.op, "inputs": node.inputs, "params": node.params},
    )


def _finish_node(
    events: JsonlRunEventLog,
    node: WorkflowNode,
    payload: dict[str, Any] | None = None,
) -> None:
    if payload is None:
        payload = {}
    events.emit(
        "workflow_node_finished",
        {"node_id": node.id, "op": node.op, **payload},
    )


def _skip_node(events: JsonlRunEventLog, node: WorkflowNode, reason: str) -> None:
    events.emit(
        "workflow_node_skipped",
        {"node_id": node.id, "op": node.op, "reason": reason},
    )


def _run_policy_test(proxy: ContractToolProxy, command: str) -> ExecResult:
    result = proxy.call("run_test", {"command": command})
    if result.ok and isinstance(result.result, dict):
        return ExecResult.from_dict(result.result)
    return ExecResult(
        command=command,
        returncode=1,
        stdout="",
        stderr=result.error,
        passed=False,
        summary=result.error or "test command denied",
        latency_ms=result.latency_ms,
    )


def _select_winner(attempts: list[AttemptResult]) -> AttemptResult:
    if not attempts:
        raise RuntimeError("no attempts were run")
    passing = [attempt for attempt in attempts if attempt.verification.passed]
    pool = passing or attempts
    return sorted(pool, key=_attempt_rank_key)[0]


def _attempt_rank_key(attempt: AttemptResult) -> tuple[bool, int, int, int, float, str]:
    verification = attempt.verification
    return (
        not verification.passed,
        len(verification.failures),
        len(verification.changed_files),
        verification.diff_lines,
        attempt.target_latency_ms,
        attempt.attempt_id,
    )


def _winner_summary(winner: AttemptResult, attempts: list[AttemptResult]) -> str:
    passing = sum(1 for attempt in attempts if attempt.verification.passed)
    prefix = (
        f"winner {winner.attempt_id} selected from {len(attempts)} attempts "
        f"({passing} passing)."
    )
    if not winner.verification.passed:
        prefix = f"no passing attempts; best attempt was {winner.attempt_id}."
    return f"{prefix}\n\n{winner.summary}"


def _write_attempt_evidence(attempt: AttemptResult, attempt_dir: Path) -> None:
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "diff.patch").write_text(attempt.diff, encoding="utf-8")
    (attempt_dir / "summary.json").write_text(
        json.dumps(attempt.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )


def _write_winner_evidence(winner: AttemptResult, winner_dir: Path) -> None:
    winner_dir.mkdir(parents=True, exist_ok=True)
    (winner_dir / "attempt_id.txt").write_text(winner.attempt_id + "\n", encoding="utf-8")
    (winner_dir / "diff.patch").write_text(winner.diff, encoding="utf-8")
    (winner_dir / "summary.json").write_text(
        json.dumps(winner.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
