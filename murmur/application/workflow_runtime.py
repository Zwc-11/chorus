"""Generic Murmur workflow runtime and operator registry."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol
from uuid import uuid4

from murmur.adapters.sandboxes.local_worktree import LocalWorktreeSandbox
from murmur.adapters.tools.contract_proxy import ContractToolProxy
from murmur.application.contract_compiler import compile_fix_test_contract
from murmur.application.event_log import JsonlRunEventLog
from murmur.application.verifier import verify_contract
from murmur.core.model_port import ModelPort, ModelResponse
from murmur.domain.contract import Contract
from murmur.domain.policy import BudgetState, PolicyEngine
from murmur.domain.tool import ExecResult
from murmur.domain.workflow import WorkflowNode, WorkflowPlan

Status = str


@dataclass(frozen=True, slots=True)
class WorkflowNodeResult:
    node_id: str
    op: str
    status: Status
    passed: bool
    result: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    artifacts: tuple[str, ...] = ()
    error: str = ""
    skipped_reason: str = ""
    taint: str = ""
    quarantined: bool = False
    latency_ms: float = 0.0
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "op": self.op,
            "status": self.status,
            "passed": self.passed,
            "result": self.result,
            "output": self.output,
            "artifacts": list(self.artifacts),
            "error": self.error,
            "skipped_reason": self.skipped_reason,
            "taint": self.taint,
            "quarantined": self.quarantined,
            "latency_ms": self.latency_ms,
            "reused": self.reused,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowNodeResult:
        return cls(
            node_id=str(data["node_id"]),
            op=str(data["op"]),
            status=str(data["status"]),
            passed=bool(data["passed"]),
            result=dict(data.get("result", {})),
            output=str(data.get("output", "")),
            artifacts=tuple(str(item) for item in data.get("artifacts", ())),
            error=str(data.get("error", "")),
            skipped_reason=str(data.get("skipped_reason", "")),
            taint=str(data.get("taint", "")),
            quarantined=bool(data.get("quarantined", False)),
            latency_ms=float(data.get("latency_ms", 0.0)),
            reused=bool(data.get("reused", False)),
        )


@dataclass(frozen=True, slots=True)
class WorkflowRunResult:
    run_id: str
    status: Status
    run_dir: Path
    node_results: tuple[WorkflowNodeResult, ...]
    proof: dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.status == "pass"


class WorkflowOperator(Protocol):
    def __call__(self, node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult: ...


@dataclass(slots=True)
class WorkflowContext:
    workflow: WorkflowPlan
    run_id: str
    run_dir: Path
    repo_root: Path
    contract: Contract | None
    budget: BudgetState
    events: JsonlRunEventLog
    results: dict[str, WorkflowNodeResult]
    sandbox: LocalWorktreeSandbox | None = None
    model_port: ModelPort | None = None
    default_model: str = ""

    def dependency_results(self, node: WorkflowNode) -> list[WorkflowNodeResult]:
        return [
            self.results[dependency]
            for dependency in node.dependencies
            if dependency in self.results
        ]

    def node_dir(self, node: WorkflowNode) -> Path:
        path = self.run_dir / "nodes" / node.id
        (path / "artifacts").mkdir(parents=True, exist_ok=True)
        return path


class OperatorRegistry:
    def __init__(self) -> None:
        self._operators: dict[str, WorkflowOperator] = {}

    def register(self, op: str, operator: WorkflowOperator) -> None:
        self._operators[op] = operator

    def get(self, op: str) -> WorkflowOperator:
        try:
            return self._operators[op]
        except KeyError as exc:
            raise RuntimeError(f"operator not registered: {op}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._operators))


class WorkflowRuntime:
    def __init__(
        self,
        *,
        repo_root: Path,
        out_root: Path,
        contract: Contract | None = None,
        registry: OperatorRegistry | None = None,
        concurrency: int = 1,
        resume: bool = False,
        model_port: ModelPort | None = None,
        default_model: str = "",
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.out_root = out_root
        self.contract = contract
        self.registry = registry or default_operator_registry()
        self.concurrency = max(1, concurrency)
        self.resume = resume
        self.model_port = model_port
        self.default_model = default_model

    def run(self, workflow: WorkflowPlan, *, run_id: str | None = None) -> WorkflowRunResult:
        issues = workflow.validate()
        if issues:
            raise RuntimeError("; ".join(issues))
        if self.concurrency != 1:
            # The execution loop is deterministic today; expose the limit now so the
            # public CLI/API does not need to change when parallel scheduling lands.
            pass

        run_id = run_id or f"workflow_{uuid4().hex[:12]}"
        run_dir = self.out_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        workflow.write(run_dir / "workflow.yaml")
        events = JsonlRunEventLog(run_dir / "events.jsonl", run_id=run_id)
        budget = BudgetState()
        context = WorkflowContext(
            workflow=workflow,
            run_id=run_id,
            run_dir=run_dir,
            repo_root=self.repo_root,
            contract=self.contract or _contract_from_workflow(workflow, self.repo_root),
            budget=budget,
            events=events,
            results={},
            model_port=self.model_port,
            default_model=self.default_model,
        )
        events.emit(
            "workflow_started",
            {
                "workflow": workflow.to_dict(),
                "concurrency": self.concurrency,
                "resume": self.resume,
            },
        )

        for node in _topological_nodes(workflow):
            result = self._run_node(node, context)
            context.results[node.id] = result

        node_results = tuple(context.results[node.id] for node in _topological_nodes(workflow))
        status = "pass" if node_results and node_results[-1].passed else "fail"
        proof = _proof_payload(
            workflow=workflow,
            run_id=run_id,
            status=status,
            node_results=node_results,
            budget=budget,
        )
        (run_dir / "proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
        events.emit("workflow_finished", {"status": status, "proof": proof})
        return WorkflowRunResult(
            run_id=run_id,
            status=status,
            run_dir=run_dir,
            node_results=node_results,
            proof=proof,
        )

    def _run_node(self, node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
        node_dir = context.node_dir(node)
        result_path = node_dir / "result.json"
        cache_key = _node_cache_key(context.workflow, node)
        if self.resume and result_path.is_file():
            cached = json.loads(result_path.read_text(encoding="utf-8"))
            if cached.get("cache_key") == cache_key and cached.get("status") == "completed":
                result = WorkflowNodeResult.from_dict(cached["result"])
                reused = replace(result, reused=True)
                context.events.emit("workflow_node_reused", {"node_id": node.id, "op": node.op})
                return reused

        dependency_failure = _dependency_failure(node, context)
        if dependency_failure is not None:
            result = WorkflowNodeResult(
                node_id=node.id,
                op=node.op,
                status="skipped",
                passed=False,
                skipped_reason=dependency_failure,
                quarantined=True,
            )
            self._write_node_result(result_path, cache_key, result)
            context.events.emit("workflow_node_skipped", result.to_dict())
            return result

        if _tainted_dependency_block(node, context):
            result = WorkflowNodeResult(
                node_id=node.id,
                op=node.op,
                status="quarantined",
                passed=False,
                error="tainted dependency cannot feed exec without explicit policy",
                taint="blocked_tainted_input",
                quarantined=True,
            )
            self._write_node_result(result_path, cache_key, result)
            context.events.emit("workflow_node_quarantined", result.to_dict())
            return result

        context.events.emit(
            "workflow_node_started",
            {"node_id": node.id, "op": node.op, "dependencies": node.dependencies},
        )
        start = perf_counter()
        try:
            result = self.registry.get(node.op)(node, context)
        except Exception as exc:  # noqa: BLE001 - runtime records failed nodes as evidence
            result = WorkflowNodeResult(
                node_id=node.id,
                op=node.op,
                status="failed",
                passed=False,
                error=str(exc),
                quarantined=True,
                latency_ms=_elapsed(start),
            )
        if result.latency_ms == 0:
            result = replace(result, latency_ms=_elapsed(start))
        self._write_node_result(result_path, cache_key, result)
        event_type = "workflow_node_finished" if result.passed else "workflow_node_failed"
        if result.quarantined:
            event_type = "workflow_node_quarantined"
        context.events.emit(event_type, result.to_dict())
        return result

    def _write_node_result(
        self,
        path: Path,
        cache_key: str,
        result: WorkflowNodeResult,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "cache_key": cache_key,
                    "status": "completed" if result.passed else result.status,
                    "result": result.to_dict(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )


def default_operator_registry() -> OperatorRegistry:
    registry = OperatorRegistry()
    registry.register("classify", _classify)
    registry.register("generate", _generate)
    registry.register("map", _map)
    registry.register("exec", _exec)
    registry.register("loop", _loop)
    registry.register("filter", _filter)
    registry.register("rank", _rank)
    registry.register("tournament", _rank)
    registry.register("verify", _verify)
    registry.register("reduce", _report)
    registry.register("report", _report)
    return registry


def explain_workflow(workflow: WorkflowPlan) -> str:
    lines = [
        f"Workflow: {workflow.name or workflow.goal}",
        f"Schema version: {workflow.schema_version}",
        f"Nodes: {len(workflow.nodes)}",
        "",
        "Execution order:",
    ]
    for index, node in enumerate(_topological_nodes(workflow), start=1):
        dependencies = ", ".join(node.dependencies) or "none"
        budget = node.budget or workflow.budget or workflow.budgets
        budget_text = json.dumps(budget, sort_keys=True) if budget else "{}"
        lines.append(f"{index}. {node.id} [{node.op}] deps={dependencies} budget={budget_text}")
    return "\n".join(lines)


def _classify(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
    text = str(node.params.get("task") or context.workflow.goal).lower()
    if "strategy" in text or "backtest" in text or "sharpe" in text:
        category = "strategy_research_backtest"
    elif "document" in text or "review" in text:
        category = "document_review"
    elif "test" in text or "fix" in text or "code" in text:
        category = "coding_fix_test"
    else:
        category = str(node.params.get("default", "coding_generate_and_test"))
    return _node_result(node, {"category": category}, output=category)


def _generate(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
    prompt = str(node.params.get("prompt") or context.workflow.goal)
    content = str(node.params.get("content") or f"Generated artifact for: {prompt}")
    artifact = _write_artifact(context, node, "generated.txt", content)
    return _node_result(
        node,
        {"content": content, "artifact": artifact},
        output=content,
        artifacts=(artifact,),
        taint="untrusted_model_output",
    )


def _map(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
    """Fan out N independent subagent attempts.

    With a configured :class:`ModelPort`, each attempt is an isolated model call:
    its own message context, its own attempt directory, its own artifacts. With
    no model port (dry runs, CI, budget estimation) it falls back to
    deterministic placeholder candidates so workflows stay runnable offline.
    """

    count = max(1, int(node.params.get("n", node.params.get("count", 1))))
    prompt = str(node.params.get("prompt") or context.workflow.goal)
    if context.model_port is None:
        items = [f"candidate_{index + 1}: {prompt}" for index in range(count)]
        artifact = _write_artifact(context, node, "candidates.json", json.dumps(items, indent=2))
        return _node_result(
            node,
            {"items": items, "artifact": artifact},
            output=f"{count} candidates",
            artifacts=(artifact,),
            taint="untrusted_model_output",
        )
    return _map_with_model(node, context, count=count, prompt=prompt)


def _map_with_model(
    node: WorkflowNode, context: WorkflowContext, *, count: int, prompt: str
) -> WorkflowNodeResult:
    port = context.model_port
    assert port is not None  # guarded by _map
    model = node.model or str(node.params.get("model", "")) or context.default_model
    if not model:
        raise RuntimeError(
            f"map node {node.id}: no model id; set node.model or pass default_model"
        )
    _check_map_budget(node, context, count=count)

    temperature = node.temperature if node.temperature is not None else 0.7
    max_tokens = int(node.params.get("max_tokens", 2048))
    message_lists = [
        _map_messages(node, context, prompt=prompt, index=index, count=count)
        for index in range(count)
    ]

    async def _fan_out() -> list[ModelResponse | BaseException]:
        calls = (
            port.complete(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            for messages in message_lists
        )
        return await asyncio.gather(*calls, return_exceptions=True)

    responses = asyncio.run(_fan_out())

    items: list[dict[str, Any]] = []
    artifacts: list[str] = []
    for index, (messages, response) in enumerate(zip(message_lists, responses, strict=True)):
        attempt_id = f"attempt_{index + 1:02d}"
        attempt_dir = context.node_dir(node) / "attempts" / attempt_id
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "messages.json").write_text(
            json.dumps(messages, indent=2), encoding="utf-8"
        )
        context.budget.model_calls += 1
        if isinstance(response, BaseException):
            item: dict[str, Any] = {
                "attempt": index + 1,
                "status": "error",
                "error": str(response),
            }
        else:
            context.budget.cost_usd += response.cost_usd
            response_path = attempt_dir / "response.txt"
            response_path.write_text(response.text, encoding="utf-8")
            artifacts.append(response_path.relative_to(context.run_dir).as_posix())
            item = {
                "attempt": index + 1,
                "status": "ok",
                "text": response.text,
                "model": response.model,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "cost_usd": response.cost_usd,
                "latency_ms": response.latency_ms,
                "artifact": artifacts[-1],
            }
        (attempt_dir / "result.json").write_text(json.dumps(item, indent=2), encoding="utf-8")
        context.events.emit("workflow_map_attempt", {"node_id": node.id, **item})
        items.append(item)

    succeeded = sum(1 for item in items if item["status"] == "ok")
    candidates = _write_artifact(context, node, "candidates.json", json.dumps(items, indent=2))
    summary = {
        "items": items,
        "artifact": candidates,
        "succeeded": succeeded,
        "failed": count - succeeded,
    }
    return _node_result(
        node,
        summary,
        output=f"{succeeded}/{count} attempts succeeded",
        artifacts=(candidates, *artifacts),
        passed=succeeded >= 1,
        taint="untrusted_model_output",
        quarantined=succeeded == 0,
    )


def _check_map_budget(node: WorkflowNode, context: WorkflowContext, *, count: int) -> None:
    limits = {**context.workflow.budgets, **context.workflow.budget, **node.budget}
    max_calls = limits.get("max_model_calls")
    if max_calls is not None and context.budget.model_calls + count > float(max_calls):
        raise RuntimeError(
            f"map node {node.id}: fan-out of {count} would exceed "
            f"max_model_calls={max_calls} (used {context.budget.model_calls})"
        )
    max_cost = limits.get("max_cost_usd")
    if max_cost is not None and context.budget.cost_usd >= float(max_cost):
        raise RuntimeError(
            f"map node {node.id}: budget exhausted before fan-out "
            f"(cost ${context.budget.cost_usd:.4f} >= max_cost_usd={max_cost})"
        )


def _map_messages(
    node: WorkflowNode,
    context: WorkflowContext,
    *,
    prompt: str,
    index: int,
    count: int,
) -> list[dict[str, str]]:
    """Build one attempt's isolated message context (no state shared across attempts)."""

    role = node.role or "Propose one complete candidate solution for the task."
    system = (
        f"{role} You are attempt {index + 1} of {count} independent attempts; "
        "work alone and do not assume other attempts exist."
    )
    sections = [prompt]
    for dependency in context.dependency_results(node):
        if dependency.output:
            sections.append(
                f"## Context from `{dependency.node_id}` ({dependency.op})\n"
                f"{dependency.output[:4000]}"
            )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def _exec(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
    contract = context.contract
    if contract is None:
        raise RuntimeError("exec requires a contract or command-backed workflow")
    if context.sandbox is None:
        context.sandbox = LocalWorktreeSandbox.create(
            context.repo_root,
            context.run_dir / "sandbox",
        )
    policy = PolicyEngine(contract, context.budget)
    proxy = ContractToolProxy(
        sandbox=context.sandbox,
        policy=policy,
        budget=context.budget,
        events=context.events,
    )
    command = str(node.params.get("command") or contract.task.command)
    parser = str(node.params.get("parser", "pytest"))
    result = proxy.call("run_test", {"command": command})
    exec_result = (
        ExecResult.from_dict(result.result)
        if result.ok and isinstance(result.result, dict)
        else ExecResult(
            command=command,
            returncode=1,
            stdout="",
            stderr=result.error,
            passed=False,
            summary=result.error or "command denied",
            latency_ms=result.latency_ms,
        )
    )
    if parser != "pytest" and exec_result.summary == "passed":
        exec_result = replace(exec_result, summary=f"passed ({parser})")
    artifact = _write_artifact(
        context,
        node,
        "exec_result.json",
        json.dumps(exec_result.to_dict(), indent=2),
    )
    return _node_result(
        node,
        exec_result.to_dict(),
        output=exec_result.output or exec_result.summary,
        artifacts=(artifact,),
        passed=exec_result.passed,
        taint="" if exec_result.passed else "failed_exec",
        quarantined=not exec_result.passed,
    )


def _loop(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
    dependencies = context.dependency_results(node)
    passed = any(result.passed for result in dependencies) if dependencies else True
    return _node_result(
        node,
        {
            "until": node.params.get("until", "passed"),
            "max_iterations": int(node.params.get("max_iterations", 1)),
            "dependency_statuses": [result.status for result in dependencies],
        },
        passed=passed,
        quarantined=not passed,
    )


def _filter(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
    kept = [
        result.node_id
        for result in context.dependency_results(node)
        if result.passed and not result.quarantined
    ]
    return _node_result(node, {"kept": kept}, output=", ".join(kept), passed=bool(kept))


def _rank(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
    candidates = context.dependency_results(node)
    eligible = [result for result in candidates if result.passed and not result.quarantined]
    pool = eligible or candidates
    if not pool:
        return _node_result(node, {"winner": ""}, passed=False, quarantined=True)
    winner = sorted(
        pool,
        key=lambda result: (not result.passed, result.latency_ms, result.node_id),
    )[0]
    return _node_result(
        node,
        {"winner": winner.node_id, "eligible": [result.node_id for result in eligible]},
        output=winner.node_id,
        passed=winner.passed,
        quarantined=not winner.passed,
    )


def _verify(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
    if context.contract is not None and context.sandbox is not None:
        verification = verify_contract(
            contract=context.contract,
            sandbox=context.sandbox,
            policy=PolicyEngine(context.contract, context.budget),
            failure_reproduced=bool(node.params.get("failure_reproduced", True)),
        )
        return _node_result(
            node,
            verification.to_dict(),
            output=", ".join(verification.failures) or "verified",
            passed=verification.passed,
            quarantined=not verification.passed,
        )
    dependencies = context.dependency_results(node)
    passed = all(result.passed for result in dependencies) if dependencies else True
    return _node_result(node, {"dependency_passed": passed}, passed=passed, quarantined=not passed)


def _report(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
    dependencies = context.dependency_results(node)
    payload = {
        "summary": str(node.params.get("summary") or context.workflow.goal),
        "dependencies": [result.to_dict() for result in dependencies],
    }
    artifact = _write_artifact(context, node, "report.json", json.dumps(payload, indent=2))
    passed = all(result.passed for result in dependencies) if dependencies else True
    return _node_result(
        node,
        payload,
        output=payload["summary"],
        artifacts=(artifact,),
        passed=passed,
        quarantined=not passed,
    )


def _node_result(
    node: WorkflowNode,
    result: dict[str, Any],
    *,
    output: str = "",
    artifacts: tuple[str, ...] = (),
    passed: bool = True,
    taint: str = "",
    quarantined: bool = False,
) -> WorkflowNodeResult:
    return WorkflowNodeResult(
        node_id=node.id,
        op=node.op,
        status="completed" if passed else "failed",
        passed=passed,
        result=result,
        output=output,
        artifacts=artifacts,
        taint=taint,
        quarantined=quarantined,
    )


def _write_artifact(context: WorkflowContext, node: WorkflowNode, name: str, content: str) -> str:
    path = context.node_dir(node) / "artifacts" / name
    path.write_text(content, encoding="utf-8")
    return path.relative_to(context.run_dir).as_posix()


def _topological_nodes(workflow: WorkflowPlan) -> tuple[WorkflowNode, ...]:
    issues = workflow.validate()
    if issues:
        raise RuntimeError("; ".join(issues))
    by_id = {node.id: node for node in workflow.nodes}
    ordered: list[WorkflowNode] = []
    visited: set[str] = set()

    def visit(node: WorkflowNode) -> None:
        if node.id in visited:
            return
        for dependency in node.dependencies:
            visit(by_id[dependency])
        visited.add(node.id)
        ordered.append(node)

    for node in workflow.nodes:
        visit(node)
    return tuple(ordered)


def _dependency_failure(node: WorkflowNode, context: WorkflowContext) -> str | None:
    for result in context.dependency_results(node):
        if not result.passed:
            return f"dependency {result.node_id} did not pass"
        if result.quarantined:
            return f"dependency {result.node_id} is quarantined"
    return None


def _tainted_dependency_block(node: WorkflowNode, context: WorkflowContext) -> bool:
    if node.op != "exec":
        return False
    if node.params.get("allow_tainted_inputs") is True or node.policy == "allow_tainted_inputs":
        return False
    return any(result.taint for result in context.dependency_results(node))


def _contract_from_workflow(workflow: WorkflowPlan, repo_root: Path) -> Contract | None:
    for node in workflow.nodes:
        if node.op == "exec" and node.params.get("command"):
            return compile_fix_test_contract(
                command=str(node.params["command"]),
                repo_root=repo_root,
                failure_output="",
                budget_usd=float(workflow.budget.get("max_cost_usd", 0.50)),
            )
    return None


def _node_cache_key(workflow: WorkflowPlan, node: WorkflowNode) -> str:
    payload = {
        "workflow": workflow.to_dict(),
        "node": node.to_dict(),
        "dependencies": node.dependencies,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _proof_payload(
    *,
    workflow: WorkflowPlan,
    run_id: str,
    status: Status,
    node_results: tuple[WorkflowNodeResult, ...],
    budget: BudgetState,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": status,
        "workflow": {
            "name": workflow.name,
            "goal": workflow.goal,
            "schema_version": workflow.schema_version,
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "budget": {
            "model_calls": budget.model_calls,
            "tool_calls": budget.tool_calls,
            "runtime_seconds": budget.runtime_seconds,
            "cost_usd": budget.cost_usd,
        },
        "nodes": [result.to_dict() for result in node_results],
    }


def _elapsed(start: float) -> float:
    return (perf_counter() - start) * 1000.0
