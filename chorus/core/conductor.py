"""Run conductor.

This file is the orchestrator. It starts a run, fans out ``N`` trajectories,
records every step as events, asks the judge for outcomes, aggregates the
distribution-aware metrics, and can replay a recorded trajectory to catch
divergence.

Trajectories fan out concurrently with ``asyncio``. Each trajectory gets its own
agent instance (via ``agent_factory``) so a flaky agent can be seeded
independently per lane; a single shared ``agent`` is reused across lanes for the
deterministic case. Concurrency never affects a verdict: every lane records to
its own append-only event stream and measures its own latency.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from time import perf_counter

from chorus.core.acceptance import repair_feedback, task_diagnostics
from chorus.core.classify import classify_trajectory
from chorus.core.divergence import build_divergence_overlay
from chorus.core.events import Event, EventRecorder, EventType, new_id
from chorus.core.judge import DeterministicJudge, judge_run
from chorus.core.ports import AgentPort, JudgePort, StoragePort
from chorus.core.results import result_from_events
from chorus.core.types import RunResult, TaskSpec, TrajectoryResult
from chorus.gateway.tool_gateway import ReplayDivergenceError, ToolCallable, ToolGateway

# Prices used to turn recorded usage into a simulated cost. Token rates are in
# USD per token (roughly $3 / $15 per million in/out).
DEFAULT_PRICE_PER_TOOL_CALL = 0.002
DEFAULT_PRICE_PER_INPUT_TOKEN = 3e-6
DEFAULT_PRICE_PER_OUTPUT_TOKEN = 15e-6


@dataclass(frozen=True, slots=True)
class PendingTrajectory:
    """A trajectory whose agent has run and been recorded, but not yet judged.

    Carries the state the verdict phase needs so judging can be deferred -- e.g.
    until every trajectory's patch is collected and evaluated in one batch.
    """

    trajectory_id: str
    recorder: EventRecorder
    output: str
    latency_ms: float
    cost_usd: float
    current_step: int
    error: bool


class RunConductor:
    """Coordinates agent execution without knowing concrete storage, tool, or judge adapters."""

    def __init__(
        self,
        *,
        agent: AgentPort | None = None,
        agent_factory: Callable[[int], AgentPort] | None = None,
        storage: StoragePort,
        tools: dict[str, ToolCallable] | None = None,
        judge: JudgePort | None = None,
        concurrent: bool = True,
        capture_content: bool = False,
        price_per_tool_call: float = DEFAULT_PRICE_PER_TOOL_CALL,
        price_per_input_token: float = DEFAULT_PRICE_PER_INPUT_TOKEN,
        price_per_output_token: float = DEFAULT_PRICE_PER_OUTPUT_TOKEN,
    ) -> None:
        if agent is None and agent_factory is None:
            raise ValueError("provide either agent or agent_factory")
        self._agent = agent
        self._agent_factory = agent_factory
        self._storage = storage
        self._tools = tools or {}
        self._concurrent = concurrent
        self._capture_content = capture_content
        self._price_per_tool_call = price_per_tool_call
        self._price_per_input_token = price_per_input_token
        self._price_per_output_token = price_per_output_token
        # The judge decides each trajectory's outcome from the agent's output. It
        # defaults to the Tier-0 contract check; a real suite injects an evaluator
        # (e.g. the SWE-bench test harness) that actually runs the candidate.
        self._judge = judge or DeterministicJudge()
        self._run_recorders: dict[str, EventRecorder] = {}

    def _agent_for(self, index: int) -> AgentPort:
        if self._agent_factory is not None:
            return self._agent_factory(index)
        assert self._agent is not None  # guaranteed by __init__
        return self._agent

    async def run(self, task: TaskSpec, n: int = 1) -> RunResult:
        run_id = await self.begin_run(task, n)

        if self._concurrent and n > 1:
            await asyncio.gather(*(self._run_one(task, run_id, index) for index in range(n)))
        else:
            for index in range(n):
                await self._run_one(task, run_id, index)

        return await self.complete_run(task, run_id)

    # -- Two-phase API ------------------------------------------------------
    # ``run`` judges each trajectory inline. The methods below split a run into
    # (1) execute+record the agent, (2) judge, (3) finalize -- so a caller can run
    # every agent first, evaluate the patches in one batch (the SWE-bench harness
    # is a batch operation), then finalize, without losing the recorded trace.

    async def begin_run(self, task: TaskSpec, n: int = 1) -> str:
        if n < 1:
            raise ValueError("n must be at least 1")
        run_id = new_id("run")
        recorder = EventRecorder(self._storage, run_id)
        self._run_recorders[run_id] = recorder
        await recorder.emit(
            EventType.RUN_STARTED,
            {"task_id": task.task_id, "n": n, "metadata": task.metadata},
        )
        return run_id

    async def run_agent_deferred(
        self, task: TaskSpec, run_id: str, index: int
    ) -> PendingTrajectory:
        """Phase 1: execute and record one agent trajectory; defer the verdict."""

        trajectory_id = f"{run_id}_t{index + 1}"
        recorder = EventRecorder(self._storage, run_id, trajectory_id)
        await recorder.emit(EventType.TRAJECTORY_STARTED, {"task_id": task.task_id, "index": index})

        gateway = ToolGateway.record(
            recorder=recorder,
            tools=self._tools,
            capture_content=self._capture_content,
            task=task,
        )
        start = perf_counter()
        output = ""
        error = False
        try:
            output = await self._agent_for(index).run(task, gateway)
        except BaseException as exc:  # noqa: BLE001 - the harness must survive any agent fault
            output = str(exc)
            error = True
        wall_ms = (perf_counter() - start) * 1000
        # Prefer the gateway's aggregate (simulated model latency + measured tool
        # time) so the fan metrics and the Phase 1 trace share one duration; fall
        # back to wall-clock for agents that make no instrumented model calls.
        return PendingTrajectory(
            trajectory_id=trajectory_id,
            recorder=recorder,
            output=output,
            latency_ms=gateway.latency_ms or wall_ms,
            cost_usd=(
                gateway.tool_call_count * self._price_per_tool_call
                + gateway.input_tokens * self._price_per_input_token
                + gateway.output_tokens * self._price_per_output_token
            ),
            current_step=gateway.current_step_index,
            error=error,
        )

    async def finalize_trajectory(
        self, task: TaskSpec, pending: PendingTrajectory, outcome: str
    ) -> TrajectoryResult:
        """Phase 3: record the contract check, verdict, and diagnosis for a verdict."""

        if pending.error:
            outcome = "error"
        events = [
            e for e in await self._storage.read_events() if e.trajectory_id == pending.trajectory_id
        ]
        diagnostics = task_diagnostics(task, pending.output)
        if outcome == "pass" and (_has_failed_contract_check(events) or diagnostics):
            outcome = "fail"

        diagnostic_payload = [diagnostic.to_dict() for diagnostic in diagnostics]
        await pending.recorder.emit(
            EventType.CONTRACT_CHECK,
            {
                "task_id": task.task_id,
                "result": "pass" if outcome == "pass" else "fail",
                "accepted": outcome == "pass",
                "output": pending.output,
                "step": pending.current_step,
                "diagnostics": diagnostic_payload,
                "diagnostic_ids": [item["predicate_id"] for item in diagnostic_payload],
                "repair_feedback": repair_feedback(diagnostics) if diagnostics else "",
            },
        )
        events = [
            e for e in await self._storage.read_events() if e.trajectory_id == pending.trajectory_id
        ]
        diagnosis = None if outcome == "pass" else classify_trajectory(events, task=task)
        await pending.recorder.emit(
            EventType.VERDICT,
            {
                "outcome": outcome,
                "output": pending.output,
                "failure_class": diagnosis.cls if diagnosis else None,
                "failure_step": diagnosis.step if diagnosis else None,
                "failure_detail": diagnosis.detail if diagnosis else None,
                "failure_confidence": diagnosis.confidence if diagnosis else None,
            },
        )
        await pending.recorder.emit(
            EventType.TRAJECTORY_FINISHED,
            {"outcome": outcome, "cost_usd": pending.cost_usd, "latency_ms": pending.latency_ms},
        )
        return TrajectoryResult(
            trajectory_id=pending.trajectory_id,
            outcome=outcome,  # type: ignore[arg-type]
            output=pending.output,
            failure_class=diagnosis.cls if diagnosis else None,
            cost_usd=pending.cost_usd,
            latency_ms=pending.latency_ms,
            failure_step=diagnosis.step if diagnosis else None,
            failure_detail=diagnosis.detail if diagnosis else None,
            failure_confidence=diagnosis.confidence if diagnosis else None,
        )

    async def complete_run(self, task: TaskSpec, run_id: str) -> RunResult:
        """Aggregate the recorded trajectories into a judged RunResult."""

        events = list(await self._storage.read_events())
        result = result_from_events(events, run_id=run_id, task_id=task.task_id)
        overlay = build_divergence_overlay(events)
        judgment = judge_run(result, task, divergence_step=overlay.divergence_step)
        result = replace(
            result,
            verdict=judgment.verdict,  # type: ignore[arg-type]
            escalations=judgment.escalations,
            judge_summary={
                "resolved_tier": judgment.resolved_tier,
                "tier_hits": judgment.tier_hits,
                "tier2_calls": judgment.tier2_calls,
                "cascade_cost_usd": judgment.cascade_cost_usd,
                "baseline_cost_usd": judgment.baseline_cost_usd,
                "cost_ratio": judgment.cost_ratio,
            },
            escalation_trace=judgment.escalation_trace,
        )
        recorder = self._run_recorders.pop(run_id, None) or EventRecorder(self._storage, run_id)
        await recorder.emit(
            EventType.RUN_FINISHED,
            {
                "task_id": task.task_id,
                "verdict": result.verdict,
                "pass_at_1": result.metrics.pass_at_1,
                "pass_hat_k_projected": result.metrics.pass_at_k,
                "pass_hat_k_unbiased": result.metrics.pass_at_k_unbiased,
                "wilson_ci": result.metrics.wilson_ci,
                "divergence_step": overlay.divergence_step,
                "judge_summary": result.judge_summary,
            },
        )
        return result

    async def _run_one(self, task: TaskSpec, run_id: str, index: int) -> TrajectoryResult:
        pending = await self.run_agent_deferred(task, run_id, index)
        if pending.error:
            outcome = "error"
        else:
            try:
                outcome = await self._judge.judge(task, pending.output)
            except BaseException:  # noqa: BLE001 - a judge fault must not kill the run
                outcome = "error"
        return await self.finalize_trajectory(task, pending, outcome)

    async def replay(
        self,
        *,
        events: list[Event],
        task: TaskSpec,
        trajectory_id: str | None = None,
        index: int = 0,
    ) -> str:
        replay_events = _events_for_trajectory(events, trajectory_id)
        expected_output = _expected_output(replay_events)
        recorded_outcome = _recorded_outcome(replay_events)
        gateway = ToolGateway.replay(replay_events)
        try:
            output = await self._agent_for(index).run(task, gateway)
        except ReplayDivergenceError:
            raise
        except BaseException as exc:  # noqa: BLE001 - reproduce the recorded error path
            if recorded_outcome == "error":
                return str(exc)
            raise ReplayDivergenceError(
                f"replay raised unexpectedly for a {recorded_outcome!r} trajectory: {exc!r}"
            ) from exc
        if recorded_outcome == "error":
            raise ReplayDivergenceError(
                "expected the recorded error path but replay produced output"
            )
        if output != expected_output:
            raise ReplayDivergenceError(
                f"replay output diverged: expected {expected_output!r}, got {output!r}"
            )
        return output


def _events_for_trajectory(events: list[Event], trajectory_id: str | None) -> list[Event]:
    if trajectory_id is None:
        trajectory_id = next(
            event.trajectory_id for event in events if event.type == EventType.TRAJECTORY_STARTED
        )
    return [event for event in events if event.trajectory_id == trajectory_id]


def _expected_output(events: list[Event]) -> str:
    for event in events:
        if event.type == EventType.VERDICT:
            return str(event.payload["output"])
    raise ReplayDivergenceError("recorded trajectory has no verdict event")


def _recorded_outcome(events: list[Event]) -> str:
    for event in events:
        if event.type == EventType.VERDICT:
            return str(event.payload.get("outcome", "pass"))
    return "pass"


def _has_failed_contract_check(events: list[Event]) -> bool:
    return any(
        event.type == EventType.CONTRACT_CHECK and not bool(event.payload.get("accepted", True))
        for event in events
    )
