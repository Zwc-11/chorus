"""Contract-first failing-test workflow."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from murmur.adapters.agents.contract_lite import build_contract_agent
from murmur.adapters.sandboxes.local_worktree import LocalWorktreeSandbox
from murmur.adapters.tools.contract_proxy import ContractToolProxy
from murmur.application.contract_compiler import compile_fix_test_contract
from murmur.application.event_log import JsonlRunEventLog
from murmur.application.proof_builder import write_proof_package
from murmur.application.tool_summary import RunEvidenceIndex
from murmur.application.tournament import RankDecision, TournamentJudge, rank_attempts
from murmur.application.verifier import verify_contract
from murmur.application.workflow_runtime import (
    OperatorRegistry,
    WorkflowContext,
    WorkflowNodeResult,
    WorkflowRuntime,
)
from murmur.domain.contract import Contract
from murmur.domain.policy import BudgetState, PolicyEngine
from murmur.domain.proof import ProofPackage
from murmur.domain.tool import ExecResult
from murmur.domain.trust import compute_trust_score
from murmur.domain.verification import VerificationResult
from murmur.domain.workflow import WorkflowNode, WorkflowPlan


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
    runtime_seconds: float
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
            "runtime_seconds": self.runtime_seconds,
            "repair_iterations": self.repair_iterations,
            "run_dir": str(self.run_dir),
        }


@dataclass(frozen=True, slots=True)
class FixTestWorkflowConfig:
    attempts: int
    max_repairs: int
    attempt_concurrency: int
    agent_name: str
    provider: str
    model: str
    test_command: str
    judge_provider: str = ""
    judge_model: str = ""


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
    rank_decision: RankDecision | None = None


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
    attempt_concurrency: int = 1,
    judge_provider: str = "",
    judge_model: str = "",
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
        attempt_concurrency=attempt_concurrency,
        judge_provider=judge_provider,
        judge_model=judge_model,
    )
    workflow_issues = workflow.validate()
    if workflow_issues:
        raise RuntimeError("; ".join(workflow_issues))
    workflow_config = validate_fix_test_workflow(workflow, contract=contract)
    workflow_config = replace(
        workflow_config,
        attempt_concurrency=max(1, attempt_concurrency, workflow_config.attempt_concurrency),
    )
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
    tool_summary = _write_fix_test_tool_summary(run_dir)
    budget_state = _budget_state_from_payload(budget)
    trust_score = compute_trust_score(
        contract=contract,
        verification=result.verification,
        budget=budget_state,
        tool_summary=tool_summary,
    )

    decision = runtime_state.rank_decision
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
        tool_summary=tool_summary,
        trust_score=trust_score,
        risk_flags=trust_score.risk_flags,
        winner_id=result.winner.attempt_id if result.winner is not None else "",
        rank=decision.to_dict() if decision is not None else {},
    )
    if result.winner is not None:
        _write_winner_evidence(result.winner, run_dir / "winner")
    _update_runtime_proof(run_dir, tool_summary)
    write_proof_package(proof, run_dir)
    return proof


def run_fix_test_workflow(
    *,
    workflow: WorkflowPlan,
    repo_root: Path,
    out_root: Path,
    contract: Contract | None = None,
    agent_name: str = "scripted",
    provider: str = "",
    model: str = "",
    attempt_concurrency: int = 1,
    run_id: str = "",
) -> ProofPackage:
    """Execute a planned coding_fix_test workflow through the contract harness."""

    command = _workflow_test_command(workflow)
    contract = contract or compile_fix_test_contract(
        command=command,
        repo_root=repo_root,
        budget_usd=float(workflow.budget.get("max_cost_usd", 0.50)),
    )
    workflow_config = validate_fix_test_workflow(workflow, contract=contract)
    workflow_config = replace(
        workflow_config,
        agent_name=agent_name or workflow_config.agent_name,
        provider=provider or workflow_config.provider,
        model=model or workflow_config.model,
        attempt_concurrency=max(
            1,
            attempt_concurrency,
            workflow_config.attempt_concurrency,
        ),
    )

    run_id = run_id or f"run_{uuid4().hex[:12]}"
    run_dir = out_root / run_id
    reproduce_sandbox = LocalWorktreeSandbox.create(repo_root, run_dir / "reproduce")
    before = reproduce_sandbox.run(command, parser="pytest")
    failure_reproduced = not before.passed
    contract.write(run_dir / "contract.yaml")
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
    tool_summary = _write_fix_test_tool_summary(run_dir)
    budget_state = _budget_state_from_payload(budget)
    trust_score = compute_trust_score(
        contract=contract,
        verification=result.verification,
        budget=budget_state,
        tool_summary=tool_summary,
    )
    decision = runtime_state.rank_decision

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
        tool_summary=tool_summary,
        trust_score=trust_score,
        risk_flags=trust_score.risk_flags,
        winner_id=result.winner.attempt_id if result.winner is not None else "",
        rank=decision.to_dict() if decision is not None else {},
    )
    if result.winner is not None:
        _write_winner_evidence(result.winner, run_dir / "winner")
    _update_runtime_proof(run_dir, tool_summary)
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
        contract = context.contract or _require_contract()
        attempts_by_index = _run_attempt_batch(
            contract=contract,
            repo_root=context.repo_root,
            run_dir=context.run_dir,
            config=state.config,
            failure_reproduced=state.failure_reproduced,
        )
        for index in range(state.config.attempts):
            attempt = attempts_by_index[index]
            _merge_attempt_budget(context.budget, attempt)
            state.attempts.append(attempt)
            _emit_attempt_finished(context.events, attempt)
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

    def rank(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
        if not state.attempts:
            return _runtime_node_result(
                node,
                {"winner": None, "reason": "failure_not_reproduced"},
                output="no attempts",
            )
        winner, decision = _tournament_rank(
            state.attempts,
            config=state.config,
            budget=context.budget,
            events=context.events,
        )
        state.winner = winner
        state.rank_decision = decision
        return _runtime_node_result(
            node,
            decision.to_dict(),
            output=f"{decision.winner_id} ({decision.method})",
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


def _run_attempt_batch(
    *,
    contract: Contract,
    repo_root: Path,
    run_dir: Path,
    config: FixTestWorkflowConfig,
    failure_reproduced: bool,
) -> dict[int, AttemptResult]:
    concurrency = max(1, min(config.attempt_concurrency, config.attempts))
    if concurrency == 1:
        return {
            index: _run_one_attempt(
                index=index,
                contract=contract,
                repo_root=repo_root,
                run_dir=run_dir,
                config=config,
                failure_reproduced=failure_reproduced,
            )
            for index in range(config.attempts)
        }

    results: dict[int, AttemptResult] = {}
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(
                _run_one_attempt,
                index=index,
                contract=contract,
                repo_root=repo_root,
                run_dir=run_dir,
                config=config,
                failure_reproduced=failure_reproduced,
            ): index
            for index in range(config.attempts)
        }
        for future in as_completed(futures):
            index = futures[future]
            results[index] = future.result()
    return results


def _run_one_attempt(
    *,
    index: int,
    contract: Contract,
    repo_root: Path,
    run_dir: Path,
    config: FixTestWorkflowConfig,
    failure_reproduced: bool,
) -> AttemptResult:
    attempt_contract = _contract_for_attempt(contract, config.attempts)
    attempt_budget = BudgetState()
    return run_attempt(
        contract=attempt_contract,
        attempt_id=f"attempt_{index + 1}",
        repo_root=repo_root,
        attempt_dir=run_dir / "attempts" / f"attempt_{index + 1}",
        agent_name=config.agent_name,
        provider=config.provider,
        model=config.model,
        budget=attempt_budget,
        failure_reproduced=failure_reproduced,
        max_repairs=config.max_repairs,
        seed=index * 1000,
        test_command=config.test_command,
    )

def _contract_for_attempt(contract: Contract, attempts: int) -> Contract:
    attempts = max(1, attempts)
    budget = replace(
        contract.budget,
        max_cost_usd=contract.budget.max_cost_usd / attempts,
        max_model_calls=max(1, contract.budget.max_model_calls // attempts),
        max_tool_calls=max(1, contract.budget.max_tool_calls // attempts),
    )
    return replace(contract, budget=budget)


def _merge_attempt_budget(budget: BudgetState, attempt: AttemptResult) -> None:
    budget.model_calls += attempt.model_calls
    budget.tool_calls += attempt.tool_calls
    budget.cost_usd += attempt.cost_usd
    budget.runtime_seconds += attempt.runtime_seconds


def _emit_attempt_finished(events: JsonlRunEventLog, attempt: AttemptResult) -> None:
    events.emit(
        "attempt_finished",
        {
            "attempt_id": attempt.attempt_id,
            "passed": attempt.passed,
            "failures": attempt.verification.failures,
            "diff_lines": attempt.verification.diff_lines,
            "model_calls": attempt.model_calls,
            "tool_calls": attempt.tool_calls,
            "runtime_seconds": attempt.runtime_seconds,
        },
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
    runtime_before = budget.runtime_seconds
    summaries: list[str] = []
    test_results: list[ExecResult] = []
    feedback = ""

    for iteration in range(max_repairs + 1):
        phase = "generate" if iteration == 0 else "repair"
        events.emit(f"{phase}_started", {"iteration": iteration})
        policy = PolicyEngine(contract, budget)
        proxy = ContractToolProxy(
            sandbox=sandbox,
            policy=policy,
            budget=budget,
            events=events,
            metadata={"attempt_id": attempt_id, "iteration": iteration, "phase": phase},
        )
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
        runtime_seconds=budget.runtime_seconds - runtime_before,
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
    attempt_concurrency: int = 1,
    judge_provider: str = "",
    judge_model: str = "",
) -> WorkflowPlan:
    return WorkflowPlan(
        version=1,
        schema_version=1,
        name="coding_fix_test",
        goal=f"Closed-loop fix-test: {contract.task.command}",
        description="Closed-loop coding repair from an objective test command.",
        budget={
            "max_cost_usd": contract.budget.max_cost_usd,
            "max_model_calls": contract.budget.max_model_calls,
            "max_tool_calls": contract.budget.max_tool_calls,
            "max_runtime_seconds": contract.budget.max_runtime_seconds,
            "max_candidates": attempts,
            "max_repairs_per_candidate": max_repairs,
            "attempt_concurrency": max(1, attempt_concurrency),
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
                    "attempt_concurrency": max(1, attempt_concurrency),
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
                        "verification.diff_lines asc",
                        "len(verification.changed_files) asc",
                    ],
                    "tie_break": "llm_judge" if judge_provider else "stable_order",
                    **(
                        {"judge_provider": judge_provider, "judge_model": judge_model}
                        if judge_provider
                        else {}
                    ),
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
    attempt_concurrency = int(nodes["generate"].params.get("attempt_concurrency", 1))
    max_repairs = int(nodes["repair"].params.get("max_iterations", -1))
    if attempts < 1:
        raise RuntimeError("workflow generate.n must be at least 1")
    if attempt_concurrency < 1:
        raise RuntimeError("workflow generate.attempt_concurrency must be at least 1")
    if max_repairs < 0:
        raise RuntimeError("workflow repair.max_iterations must be non-negative")
    return FixTestWorkflowConfig(
        attempts=attempts,
        max_repairs=max_repairs,
        attempt_concurrency=attempt_concurrency,
        agent_name=str(nodes["generate"].params.get("agent", "scripted")),
        provider=str(nodes["generate"].params.get("provider", "")),
        model=str(nodes["generate"].params.get("model", "")),
        test_command=test_command,
        judge_provider=str(nodes["rank"].params.get("judge_provider", "")),
        judge_model=str(nodes["rank"].params.get("judge_model", "")),
    )


def run_contract(
    *,
    contract: Contract,
    out_root: Path,
    agent_name: str = "scripted",
    provider: str = "",
    model: str = "",
    judge_provider: str = "",
    judge_model: str = "",
) -> ProofPackage:
    return run_fix_test(
        command=contract.task.command,
        repo_root=Path(contract.repo.root),
        out_root=out_root,
        budget_usd=contract.budget.max_cost_usd,
        agent_name=agent_name,
        provider=provider,
        model=model,
        judge_provider=judge_provider,
        judge_model=judge_model,
    )


def proof_summary(proof: ProofPackage) -> str:
    return json.dumps(
        {
            "run_id": proof.run_id,
            "verdict": proof.verdict,
            "changed_files": proof.verification.changed_files,
            "failures": proof.verification.failures,
            "attempts": len(proof.attempts),
            "trust_score": proof.trust_score.score if proof.trust_score else None,
        },
        indent=2,
    )

def _budget_state_from_payload(payload: dict[str, Any]) -> BudgetState:
    return BudgetState(
        cost_usd=float(payload.get("cost_usd", 0.0)),
        model_calls=int(payload.get("model_calls", 0)),
        tool_calls=int(payload.get("tool_calls", 0)),
        runtime_seconds=float(payload.get("runtime_seconds", 0.0)),
    )


def _workflow_test_command(workflow: WorkflowPlan) -> str:
    nodes = _workflow_nodes(workflow)
    for node_id in ("run_tests", "reproduce"):
        node = nodes.get(node_id)
        if node is not None and node.params.get("command"):
            return str(node.params["command"])
    for node in workflow.nodes:
        if node.op == "exec" and node.params.get("command"):
            return str(node.params["command"])
    raise RuntimeError("coding_fix_test workflow does not contain a test command")


def _write_fix_test_tool_summary(run_dir: Path) -> dict[str, Any]:
    summary = _fix_test_evidence_index(run_dir).tool_summary()
    (run_dir / "tool_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    return summary


def _update_runtime_proof(run_dir: Path, tool_summary: dict[str, Any]) -> None:
    path = run_dir / "proof.json"
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tool_summary"] = tool_summary
    payload["model_retries"] = _fix_test_evidence_index(run_dir).count("model_call_retry")
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _fix_test_evidence_index(run_dir: Path) -> RunEvidenceIndex:
    paths = [run_dir / "events.jsonl"]
    paths.extend(sorted((run_dir / "attempts").glob("attempt_*/events.jsonl")))
    return RunEvidenceIndex.from_paths(paths)


def proof_console_summary(proof: ProofPackage, run_dir: Path) -> str:
    """The block a developer reads in the terminal when a run finishes."""

    passed = sum(1 for attempt in proof.attempts if attempt.get("passed"))
    failed = len(proof.attempts) - passed
    method = str(proof.rank.get("method", "")) if proof.rank else ""
    reason = str(proof.rank.get("rationale", "")) if proof.rank else ""

    lines = [f"Verdict:  {proof.verdict.upper()}"]
    winner = proof.winner_id or "none"
    lines.append(f"Winner:   {winner}" + (f"  (selected by {method})" if method else ""))
    if reason:
        lines.append(f"Reason:   {reason}")
    lines.append(f"Attempts: {len(proof.attempts)}  ({passed} passed / {failed} failed)")
    lines.append(
        f"Cost:     ${proof.cost_usd:.4f}  ·  "
        f"{proof.model_calls} model calls  ·  {proof.tool_calls} tool calls"
    )
    lines.append("")
    lines.append(f"Proof:    {run_dir / 'proof.md'}")
    lines.append(f"Patch:    {run_dir / 'winner.patch'}")
    lines.append(f"Fan:      {run_dir / 'fan.html'}")
    lines.append(f"Report:   {run_dir / 'report.html'}")
    return "\n".join(lines)


def _workflow_nodes(workflow: WorkflowPlan) -> dict[str, WorkflowNode]:
    return {node.id: node for node in workflow.nodes}


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


def _tournament_rank(
    attempts: list[AttemptResult],
    *,
    config: FixTestWorkflowConfig,
    budget: BudgetState,
    events: JsonlRunEventLog,
) -> tuple[AttemptResult, RankDecision]:
    """Objective-first tournament; an LLM judge breaks exact ties when configured."""

    judge: TournamentJudge | None = None
    if config.judge_provider:
        from murmur.adapters.agents.murmur_patch import port_for_provider

        port, model_id = port_for_provider(config.judge_provider, config.judge_model)
        judge = TournamentJudge(model_port=port, model=model_id)
    decision = rank_attempts(list(attempts), judge=judge)
    if decision.method == "llm_judge" or decision.judge_model:
        budget.model_calls += 1
        budget.cost_usd += decision.judge_cost_usd
    events.emit("rank_decided", decision.to_dict())
    by_id = {attempt.attempt_id: attempt for attempt in attempts}
    return by_id[decision.winner_id], decision


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
