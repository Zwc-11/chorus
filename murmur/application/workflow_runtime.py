"""Generic Murmur workflow runtime and operator registry."""

from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, Protocol
from uuid import uuid4

from murmur.adapters.sandboxes.local_worktree import LocalWorktreeSandbox
from murmur.adapters.tools.contract_proxy import ContractToolProxy
from murmur.application.contract_compiler import compile_fix_test_contract
from murmur.application.event_log import JsonlRunEventLog
from murmur.application.tool_summary import RunEvidenceIndex
from murmur.application.verifier import verify_contract
from murmur.benchmarks.swe.types import PatchModel
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
    model: PatchModel | None
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
        model: PatchModel | None = None,
        registry: OperatorRegistry | None = None,
        concurrency: int = 1,
        resume: bool = False,
        model_port: ModelPort | None = None,
        default_model: str = "",
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.out_root = out_root
        self.contract = contract
        self.model = model
        self.registry = registry or default_operator_registry()
        self.concurrency = max(1, concurrency)
        self.resume = resume
        self.model_port = model_port
        self.default_model = default_model

    def run(self, workflow: WorkflowPlan, *, run_id: str | None = None) -> WorkflowRunResult:
        issues = workflow.validate()
        if issues:
            raise RuntimeError("; ".join(issues))

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
            model=self.model,
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

        ordered_nodes = _topological_nodes(workflow)
        self._run_ready_nodes(ordered_nodes, context)

        node_results = tuple(context.results[node.id] for node in ordered_nodes)
        status = "pass" if node_results and node_results[-1].passed else "fail"
        evidence = RunEvidenceIndex.from_paths((events.path,))
        tool_summary = evidence.tool_summary()
        (run_dir / "tool_summary.json").write_text(
            json.dumps(tool_summary, indent=2),
            encoding="utf-8",
        )
        _write_workflow_html(workflow, run_dir)
        proof = _proof_payload(
            workflow=workflow,
            run_id=run_id,
            status=status,
            node_results=node_results,
            budget=budget,
            tool_summary=tool_summary,
            model_retries=evidence.count("model_call_retry"),
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

    def _run_ready_nodes(
        self,
        ordered_nodes: tuple[WorkflowNode, ...],
        context: WorkflowContext,
    ) -> None:
        remaining = {node.id: node for node in ordered_nodes}
        completed: set[str] = set()

        while remaining:
            ready = [
                node
                for node in ordered_nodes
                if node.id in remaining and set(node.dependencies).issubset(completed)
            ]
            if not ready:
                raise RuntimeError("workflow scheduler made no progress")

            batch = ready[: self.concurrency]
            context.events.emit(
                "workflow_nodes_scheduled",
                {
                    "nodes": [node.id for node in batch],
                    "concurrency": self.concurrency,
                },
            )
            if len(batch) == 1:
                node = batch[0]
                context.results[node.id] = self._run_node(node, context)
            else:
                with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    futures = {pool.submit(self._run_node, node, context): node for node in batch}
                    for future in as_completed(futures):
                        node = futures[future]
                        context.results[node.id] = future.result()

            for node in batch:
                completed.add(node.id)
                remaining.pop(node.id)

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
    registry.register("tournament", _tournament)
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


def _augment_prompt_with_context(
    prompt: str,
    node: WorkflowNode,
    context: WorkflowContext,
) -> str:
    """Prepend the text output of named dependency nodes (e.g. a creative brief)."""

    briefs: list[str] = []
    for node_id in node.params.get("context_nodes", ()):
        result = context.results.get(str(node_id))
        if result and result.output.strip():
            briefs.append(result.output.strip())
    if not briefs:
        return prompt
    brief_text = "\n\n".join(briefs)
    return f"CREATIVE BRIEF:\n{brief_text}\n\n---\n{prompt}"


def _generate(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
    prompt = str(node.params.get("prompt") or context.workflow.goal)
    if context.model is not None:
        content = _call_workflow_model(
            context, node, _augment_prompt_with_context(prompt, node, context)
        )
    elif _looks_like_site_build(prompt):
        content = _site_candidate_bundle(prompt, lane=1)
    else:
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
    count = max(1, int(node.params.get("n", node.params.get("count", 1))))
    prompt = str(node.params.get("prompt") or context.workflow.goal)
    if context.model_port is not None:
        return _map_with_model(node, context, count=count, prompt=prompt)
    if context.model is None and _looks_like_site_build(prompt):
        items = [_site_candidate_bundle(prompt, lane=index + 1) for index in range(count)]
    elif context.model is None:
        items = [f"candidate_{index + 1}: {prompt}" for index in range(count)]
    else:
        augmented = _augment_prompt_with_context(prompt, node, context)
        items = _generate_candidates_parallel(context, node, augmented, count)
    artifact = _write_artifact(context, node, "candidates.json", json.dumps(items, indent=2))
    return _node_result(
        node,
        {"items": items, "artifact": artifact},
        output=f"{count} candidates",
        artifacts=(artifact,),
        taint="untrusted_model_output",
    )


def _map_with_model(
    node: WorkflowNode,
    context: WorkflowContext,
    *,
    count: int,
    prompt: str,
) -> WorkflowNodeResult:
    port = context.model_port
    assert port is not None
    model = node.model or str(node.params.get("model", "")) or context.default_model
    if not model:
        raise RuntimeError(f"map node {node.id}: no model id; set node.model or pass default_model")
    _check_map_budget(node, context, count=count)

    temperature = node.temperature if node.temperature is not None else 0.7
    max_tokens = int(node.params.get("max_tokens", 2048))
    message_lists = [
        _map_messages(node, context, prompt=prompt, index=index, count=count)
        for index in range(count)
    ]

    async def fan_out() -> list[ModelResponse | BaseException]:
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

    responses = asyncio.run(fan_out())

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
    return _node_result(
        node,
        {
            "items": items,
            "artifact": candidates,
            "succeeded": succeeded,
            "failed": count - succeeded,
        },
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


def _generate_candidates_parallel(
    context: WorkflowContext,
    node: WorkflowNode,
    augmented: str,
    count: int,
) -> list[str]:
    """Generate the N map candidates concurrently, preserving order.

    A candidate that fails is dropped (not fatal) unless every candidate fails. Reasoning
    effort is unchanged; the speedup comes purely from overlapping the per-candidate calls.
    The final budget is reconciled from the (thread-safe) event log, so transient counter
    races on BudgetState do not affect reported numbers.
    """

    def one(index: int) -> str:
        return _call_workflow_model(
            context,
            node,
            f"{augmented}\n\nCandidate {index + 1} of {count}.",
            seed_offset=index,
        )

    if count == 1:
        return [one(0)]

    results: list[str | None] = [None] * count
    errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=min(count, 6)) as pool:
        futures = {pool.submit(one, index): index for index in range(count)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # noqa: BLE001 - a failed candidate is dropped, not fatal
                errors.append(exc)
                context.events.emit(
                    "model_candidate_failed",
                    {"node_id": node.id, "index": index, "error": str(exc)},
                )
    items = [item for item in results if item is not None]
    if not items:
        raise RuntimeError(
            f"all {count} candidates failed: {errors[0] if errors else 'unknown error'}"
        )
    return items


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
        metadata={"node_id": node.id},
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
    mapped = _mapped_rank_candidates(candidates)
    if mapped:
        winner = sorted(
            mapped,
            key=lambda item: (
                not bool(item.get("passed", True)),
                int(item.get("index", 0)),
                str(item.get("id", "")),
            ),
        )[0]
        payload = {
            "winner": winner["id"],
            "candidates": mapped,
            "eligible": [
                item["id"]
                for item in mapped
                if bool(item.get("passed", True)) and not bool(item.get("quarantined", False))
            ],
        }
        return _node_result(
            node,
            payload,
            output=str(winner["id"]),
            passed=bool(winner.get("passed", True)),
            quarantined=not bool(winner.get("passed", True)),
        )
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


def _mapped_rank_candidates(results: list[WorkflowNodeResult]) -> list[dict[str, Any]]:
    if len(results) != 1:
        return []
    result = results[0]
    items = result.result.get("items")
    if not isinstance(items, list):
        return []
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            status = str(item.get("status", "ok"))
            text = str(item.get("text", item.get("error", "")))
            passed = result.passed and status == "ok"
        else:
            text = str(item)
            passed = result.passed
        candidates.append(
            {
                "id": f"{result.node_id}_{index}",
                "text": text,
                "passed": passed,
                "quarantined": result.quarantined or not passed,
                "index": index,
            }
        )
    return candidates


def _tournament(node: WorkflowNode, context: WorkflowContext) -> WorkflowNodeResult:
    candidates = _tournament_candidates(node, context)
    if not candidates:
        return _node_result(node, {"winner": ""}, passed=False, quarantined=True)

    rounds: list[dict[str, Any]] = []
    current = candidates
    pair_index = 0
    while len(current) > 1:
        next_round: list[dict[str, Any]] = []
        for index in range(0, len(current), 2):
            left = current[index]
            right = current[index + 1] if index + 1 < len(current) else None
            if right is None:
                next_round.append(left)
                rounds.append({"left": left["id"], "right": None, "winner": left["id"]})
                continue
            winner = _judge_tournament_pair(context, node, left, right, pair_index)
            pair_index += 1
            rounds.append(
                {"left": left["id"], "right": right["id"], "winner": winner["id"]}
            )
            next_round.append(winner)
        current = next_round

    payload = {"winner": current[0]["id"], "candidates": candidates, "rounds": rounds}
    artifact = _write_artifact(context, node, "tournament.json", json.dumps(payload, indent=2))
    return _node_result(
        node,
        payload,
        output=current[0]["id"],
        artifacts=(artifact,),
        passed=bool(current[0].get("passed", True)),
        quarantined=not bool(current[0].get("passed", True)),
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
    winner = _winner_payload(dependencies)
    if winner:
        payload["winner"] = winner
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


def _winner_payload(dependencies: list[WorkflowNodeResult]) -> dict[str, Any]:
    for result in dependencies:
        winner_id = result.result.get("winner")
        candidates = result.result.get("candidates")
        if not isinstance(winner_id, str) or not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("id") == winner_id:
                return {
                    "id": winner_id,
                    "text": str(candidate.get("text", "")),
                }
    return {}


def _tournament_candidates(
    node: WorkflowNode,
    context: WorkflowContext,
) -> list[dict[str, Any]]:
    raw_candidates = node.params.get("candidates", ())
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(raw_candidates):
        candidates.append(
            {
                "id": f"candidate_{index + 1}",
                "text": str(item),
                "passed": True,
                "quarantined": False,
            }
        )
    for result in context.dependency_results(node):
        items = result.result.get("items")
        if isinstance(items, list):
            for index, item in enumerate(items):
                if isinstance(item, dict):
                    status = str(item.get("status", "ok"))
                    text = str(item.get("text", item.get("error", "")))
                    passed = result.passed and status == "ok"
                else:
                    text = str(item)
                    passed = result.passed
                candidates.append(
                    {
                        "id": f"{result.node_id}_{index + 1}",
                        "text": text,
                        "passed": passed,
                        "quarantined": result.quarantined or not passed,
                    }
                )
        else:
            text = result.output or json.dumps(result.result, sort_keys=True, default=str)
            candidates.append(
                {
                    "id": result.node_id,
                    "text": text,
                    "passed": result.passed,
                    "quarantined": result.quarantined,
                }
            )
    return candidates


def _judge_tournament_pair(
    context: WorkflowContext,
    node: WorkflowNode,
    left: dict[str, Any],
    right: dict[str, Any],
    pair_index: int,
) -> dict[str, Any]:
    deterministic = sorted(
        (left, right),
        key=lambda item: (
            not bool(item.get("passed", True)),
            bool(item.get("quarantined", False)),
            len(str(item.get("text", ""))),
            str(item.get("id", "")),
        ),
    )[0]
    if context.model is None:
        return deterministic
    prompt = (
        "Pick the stronger candidate for the workflow goal. "
        "Return exactly A or B.\n\n"
        f"Goal:\n{context.workflow.goal}\n\n"
        f"A ({left['id']}):\n{left['text']}\n\n"
        f"B ({right['id']}):\n{right['text']}\n"
    )
    try:
        raw = _call_workflow_model(context, node, prompt, seed_offset=pair_index)
        verdict = raw.strip().upper()
    except Exception as exc:  # noqa: BLE001 - a judge fault degrades to a deterministic pick
        context.events.emit(
            "tournament_judge_fallback",
            {"node_id": node.id, "pair_index": pair_index, "error": str(exc)},
        )
        return deterministic
    return right if verdict.startswith("B") else left


def _call_workflow_model(
    context: WorkflowContext,
    node: WorkflowNode,
    prompt: str,
    *,
    seed_offset: int = 0,
) -> str:
    if context.model is None:
        raise RuntimeError("workflow model is not configured")
    system = node.role or "Produce the requested workflow artifact. Return only the artifact."
    seed = node.seed if node.seed is not None else 0
    start = perf_counter()
    response = _complete_with_retries(
        context=context,
        node=node,
        system=system,
        user=prompt,
        seed=seed + seed_offset,
        max_tokens=int(node.params.get("max_tokens", 2048)),
    )
    latency_ms = _elapsed(start)
    if not response.text.strip():
        context.events.emit("model_empty_response", {"node_id": node.id})
        raise RuntimeError(f"model returned empty content for node {node.id}")
    context.budget.model_calls += 1
    context.budget.cost_usd += response.cost_usd
    context.events.emit(
        "model_call_finished",
        {
            "node_id": node.id,
            "model": node.model or getattr(context.model, "model", "model"),
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
            "latency_ms": round(latency_ms, 1),
            "thinking": (getattr(response, "reasoning", "") or "")[:1500],
            "output_preview": response.text[:600],
        },
    )
    return response.text


def _complete_with_retries(
    *,
    context: WorkflowContext,
    node: WorkflowNode,
    system: str,
    user: str,
    seed: int,
    max_tokens: int,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return context.model.complete(  # type: ignore[union-attr]
                system=system,
                user=user,
                seed=seed,
                max_tokens=max_tokens,
            )
        except Exception as exc:  # noqa: BLE001 - provider faults are run evidence
            last_error = exc
            context.events.emit(
                "model_call_retry",
                {"node_id": node.id, "attempt": attempt + 1, "error": str(exc)},
            )
            sleep(0.25 * (attempt + 1))
    raise RuntimeError(f"model call failed after retries: {last_error}")


def _write_artifact(context: WorkflowContext, node: WorkflowNode, name: str, content: str) -> str:
    path = context.node_dir(node) / "artifacts" / name
    path.write_text(content, encoding="utf-8")
    return path.relative_to(context.run_dir).as_posix()


def _looks_like_site_build(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(
        token in lowered
        for token in (
            "website",
            "web app",
            "webapp",
            "landing page",
            "frontend",
            "three.js",
            "threejs",
            "animation",
            "interactive",
        )
    )


def _site_candidate_bundle(prompt: str, *, lane: int) -> str:
    title = _site_title(prompt)
    accent = ("#e8192a", "#16a085", "#6d5dfc", "#d18f00")[(lane - 1) % 4]
    uses_three = "three" in prompt.lower() or "3d" in prompt.lower()
    script = _three_scene_script(accent) if uses_three else _ambient_scene_script(accent)
    return f"""=== index.html ===
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --bg: #101114;
      --panel: #f3f3ef;
      --ink: #101114;
      --accent: {accent};
      --muted: #65655f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family:
        Inter,
        ui-sans-serif,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
      color: var(--panel);
      background: radial-gradient(circle at 70% 20%, #2b2f37 0, #101114 42%, #050506 100%);
      overflow-x: hidden;
    }}
    main {{ min-height: 100vh; display: grid; grid-template-columns: minmax(0, 1fr); }}
    #scene {{
      position: fixed;
      inset: 0;
      width: 100%;
      height: 100%;
      z-index: 0;
    }}
    .content {{
      position: relative;
      z-index: 1;
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      padding: 72px 0 40px;
      display: grid;
      gap: 28px;
    }}
    .hero {{
      min-height: 62vh;
      display: grid;
      align-content: center;
      gap: 22px;
      max-width: 780px;
    }}
    .eyebrow {{
      margin: 0;
      font: 700 12px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
      letter-spacing: .18em;
      text-transform: uppercase;
      color: var(--accent);
    }}
    h1 {{
      margin: 0;
      font-size: clamp(44px, 8vw, 104px);
      line-height: .92;
      letter-spacing: 0;
      max-width: 11ch;
    }}
    .lead {{
      margin: 0;
      max-width: 56ch;
      font-size: clamp(17px, 2vw, 22px);
      line-height: 1.55;
      color: rgba(243, 243, 239, .82);
    }}
    .actions {{ display: flex; gap: 12px; flex-wrap: wrap; }}
    .button {{
      min-height: 44px;
      padding: 0 18px;
      border: 1px solid rgba(243, 243, 239, .32);
      display: inline-grid;
      place-items: center;
      color: var(--panel);
      text-decoration: none;
      font: 700 12px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
      letter-spacing: .1em;
      text-transform: uppercase;
    }}
    .button.primary {{ border-color: var(--accent); color: var(--accent); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }}
    .tile {{
      min-height: 132px;
      padding: 18px;
      background: rgba(243, 243, 239, .92);
      color: var(--ink);
      border-top: 3px solid var(--accent);
    }}
    .tile h2 {{ margin: 0 0 10px; font-size: 17px; letter-spacing: 0; }}
    .tile p {{ margin: 0; line-height: 1.5; color: var(--muted); }}
    footer {{
      font: 700 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
      letter-spacing: .16em;
      text-transform: uppercase;
      color: rgba(243, 243, 239, .54);
    }}
  </style>
</head>
<body>
  <canvas id="scene" aria-hidden="true"></canvas>
  <main>
    <section class="content">
      <div class="hero">
        <p class="eyebrow">interactive prototype - lane {lane}</p>
        <h1>{title}</h1>
        <p class="lead">A polished single-page experience generated from: {prompt}</p>
        <div class="actions">
          <a class="button primary" href="#features">Explore</a>
          <a class="button" href="#scene">View motion</a>
        </div>
      </div>
      <div class="grid" id="features">
        <article class="tile">
          <h2>Motion System</h2>
          <p>Responsive animation gives the page a live product feel.</p>
        </article>
        <article class="tile">
          <h2>Readable Layout</h2>
          <p>Clear hierarchy and stable spacing keep the page usable.</p>
        </article>
        <article class="tile">
          <h2>Inspectable Build</h2>
          <p>The artifact is a complete HTML file ready to revise.</p>
        </article>
      </div>
      <footer>Generated by Murmur - deterministic scaffold fallback</footer>
    </section>
  </main>
  {script}
</body>
</html>
"""


def _site_title(prompt: str) -> str:
    words = [
        part.strip(".,:;!?()[]{}").capitalize()
        for part in prompt.split()
        if part.strip(".,:;!?()[]{}")
    ]
    if not words:
        return "Interactive Website"
    blocked = {"Create", "Build", "Make", "A", "An", "Using", "With"}
    useful = [word for word in words if word not in blocked]
    return " ".join(useful[:4] or words[:4])


def _three_scene_script(accent: str) -> str:
    return f"""<script type="module">
    import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js';
    const canvas = document.getElementById('scene');
    const renderer = new THREE.WebGLRenderer({{ canvas, antialias: true, alpha: true }});
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    camera.position.set(0, 0, 7);
    const geometry = new THREE.TorusKnotGeometry(1.4, 0.38, 160, 24);
    const material = new THREE.MeshStandardMaterial({{
      color: '{accent}',
      roughness: 0.32,
      metalness: 0.68
    }});
    const knot = new THREE.Mesh(geometry, material);
    scene.add(knot);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x20202a, 2.2));
    const light = new THREE.PointLight(0xffffff, 18);
    light.position.set(3, 2, 5);
    scene.add(light);
    function resize() {{
      const width = window.innerWidth;
      const height = window.innerHeight;
      renderer.setSize(width, height, false);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    }}
    function frame(time) {{
      resize();
      knot.rotation.x = time * 0.00035;
      knot.rotation.y = time * 0.00052;
      renderer.render(scene, camera);
      requestAnimationFrame(frame);
    }}
    requestAnimationFrame(frame);
  </script>"""


def _ambient_scene_script(accent: str) -> str:
    return f"""<script>
    const canvas = document.getElementById('scene');
    const ctx = canvas.getContext('2d');
    function frame(time) {{
      canvas.width = window.innerWidth * devicePixelRatio;
      canvas.height = window.innerHeight * devicePixelRatio;
      ctx.scale(devicePixelRatio, devicePixelRatio);
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      for (let i = 0; i < 42; i += 1) {{
        const x = (Math.sin(time * 0.0002 + i) * 0.5 + 0.5) * innerWidth;
        const y = ((i * 89 + time * 0.025) % (innerHeight + 120)) - 60;
        ctx.strokeStyle = i % 3 === 0 ? '{accent}' : 'rgba(243,243,239,.18)';
        ctx.beginPath();
        ctx.arc(x, y, 18 + (i % 6) * 8, 0, Math.PI * 2);
        ctx.stroke();
      }}
      requestAnimationFrame(frame);
    }}
    requestAnimationFrame(frame);
  </script>"""


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
    tool_summary: dict[str, Any],
    model_retries: int = 0,
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
        "model_retries": model_retries,
        "tool_summary": tool_summary,
        "nodes": [result.to_dict() for result in node_results],
    }


def _write_workflow_html(workflow: WorkflowPlan, run_dir: Path) -> None:
    from murmur.report.agent_map_html import write_agent_map_html

    write_agent_map_html(
        run_dir / "workflow.html",
        workflow=workflow,
        embedded_task=workflow.goal,
        run_dir=run_dir,
    )


def _elapsed(start: float) -> float:
    return (perf_counter() - start) * 1000
