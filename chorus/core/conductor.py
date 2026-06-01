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
from dataclasses import replace
from time import perf_counter

from chorus.core.classify import classify_trajectory
from chorus.core.divergence import build_divergence_overlay
from chorus.core.events import Event, EventRecorder, EventType, new_id
from chorus.core.judge import DeterministicJudge, judge_run
from chorus.core.ports import AgentPort, StoragePort
from chorus.core.results import result_from_events
from chorus.core.types import RunResult, TaskSpec, TrajectoryResult
from chorus.gateway.tool_gateway import ReplayDivergenceError, ToolCallable, ToolGateway

# Prices used to turn recorded usage into a simulated cost. Token rates are in
# USD per token (roughly $3 / $15 per million in/out).
DEFAULT_PRICE_PER_TOOL_CALL = 0.002
DEFAULT_PRICE_PER_INPUT_TOKEN = 3e-6
DEFAULT_PRICE_PER_OUTPUT_TOKEN = 15e-6


class RunConductor:
    """Coordinates agent execution without knowing concrete storage, tool, or judge adapters."""

    def __init__(
        self,
        *,
        agent: AgentPort | None = None,
        agent_factory: Callable[[int], AgentPort] | None = None,
        storage: StoragePort,
        tools: dict[str, ToolCallable] | None = None,
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
        self._judge = DeterministicJudge()

    def _agent_for(self, index: int) -> AgentPort:
        if self._agent_factory is not None:
            return self._agent_factory(index)
        assert self._agent is not None  # guaranteed by __init__
        return self._agent

    async def run(self, task: TaskSpec, n: int = 1) -> RunResult:
        if n < 1:
            raise ValueError("n must be at least 1")

        run_id = new_id("run")
        run_recorder = EventRecorder(self._storage, run_id)
        await run_recorder.emit(
            EventType.RUN_STARTED,
            {"task_id": task.task_id, "n": n, "metadata": task.metadata},
        )

        if self._concurrent and n > 1:
            trajectories = tuple(
                await asyncio.gather(*(self._run_one(task, run_id, index) for index in range(n)))
            )
        else:
            trajectories = tuple([await self._run_one(task, run_id, index) for index in range(n)])

        del trajectories
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
        await run_recorder.emit(
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
        try:
            output = await self._agent_for(index).run(task, gateway)
            outcome = await self._judge.judge(task, output)
        except BaseException as exc:  # noqa: BLE001 - the harness must survive any agent fault
            outcome = "error"
            output = str(exc)
        trajectory_events = [
            event
            for event in await self._storage.read_events()
            if event.trajectory_id == trajectory_id
        ]
        if outcome == "pass" and _has_failed_contract_check(trajectory_events):
            outcome = "fail"

        wall_ms = (perf_counter() - start) * 1000
        # Prefer the gateway's aggregate (simulated model latency + measured tool
        # time) so the fan metrics and the Phase 1 trace share one duration; fall
        # back to wall-clock for agents that make no instrumented model calls.
        latency_ms = gateway.latency_ms or wall_ms
        cost_usd = (
            gateway.tool_call_count * self._price_per_tool_call
            + gateway.input_tokens * self._price_per_input_token
            + gateway.output_tokens * self._price_per_output_token
        )
        await recorder.emit(
            EventType.CONTRACT_CHECK,
            {
                "task_id": task.task_id,
                "result": "pass" if outcome == "pass" else "fail",
                "accepted": outcome == "pass",
                "output": output,
                "step": gateway.current_step_index,
            },
        )
        trajectory_events = [
            event
            for event in await self._storage.read_events()
            if event.trajectory_id == trajectory_id
        ]
        diagnosis = None if outcome == "pass" else classify_trajectory(trajectory_events, task=task)
        await recorder.emit(
            EventType.VERDICT,
            {
                "outcome": outcome,
                "output": output,
                "failure_class": diagnosis.cls if diagnosis else None,
                "failure_step": diagnosis.step if diagnosis else None,
                "failure_detail": diagnosis.detail if diagnosis else None,
                "failure_confidence": diagnosis.confidence if diagnosis else None,
            },
        )
        await recorder.emit(
            EventType.TRAJECTORY_FINISHED,
            {"outcome": outcome, "cost_usd": cost_usd, "latency_ms": latency_ms},
        )

        return TrajectoryResult(
            trajectory_id=trajectory_id,
            outcome=outcome,
            output=output,
            failure_class=diagnosis.cls if diagnosis else None,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            failure_step=diagnosis.step if diagnosis else None,
            failure_detail=diagnosis.detail if diagnosis else None,
            failure_confidence=diagnosis.confidence if diagnosis else None,
        )

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
