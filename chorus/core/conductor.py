from __future__ import annotations

from time import perf_counter

from chorus.core.classify import classify_failure
from chorus.core.events import Event, EventRecorder, EventType, new_id
from chorus.core.judge import DeterministicJudge
from chorus.core.metrics import reliability_metrics
from chorus.core.ports import AgentPort, StoragePort
from chorus.core.types import RunResult, TaskSpec, TrajectoryResult
from chorus.gateway.tool_gateway import ReplayDivergenceError, ToolCallable, ToolGateway


class RunConductor:
    """Coordinates agent execution without knowing concrete storage, tool, or judge adapters."""

    def __init__(
        self,
        *,
        agent: AgentPort,
        storage: StoragePort,
        tools: dict[str, ToolCallable] | None = None,
    ) -> None:
        self._agent = agent
        self._storage = storage
        self._tools = tools or {}
        self._judge = DeterministicJudge()

    async def run(self, task: TaskSpec, n: int = 1) -> RunResult:
        if n < 1:
            raise ValueError("n must be at least 1")

        run_id = new_id("run")
        run_recorder = EventRecorder(self._storage, run_id)
        await run_recorder.emit(
            EventType.RUN_STARTED,
            {"task_id": task.task_id, "n": n, "metadata": task.metadata},
        )

        trajectories: list[TrajectoryResult] = []
        for index in range(n):
            trajectories.append(await self._run_one(task, run_id, index))

        trajectory_tuple = tuple(trajectories)
        metrics = reliability_metrics(trajectory_tuple)
        verdict = "pass" if all(item.outcome == "pass" for item in trajectory_tuple) else "fail"
        result = RunResult(
            run_id=run_id,
            task_id=task.task_id,
            trajectories=trajectory_tuple,
            metrics=metrics,
            escalations=0,
            verdict=verdict,
        )
        await run_recorder.emit(
            EventType.RUN_FINISHED,
            {
                "task_id": task.task_id,
                "verdict": result.verdict,
                "pass_at_k": result.metrics.pass_at_k,
                "wilson_ci": result.metrics.wilson_ci,
            },
        )
        return result

    async def _run_one(self, task: TaskSpec, run_id: str, index: int) -> TrajectoryResult:
        trajectory_id = f"{run_id}_t{index + 1}"
        recorder = EventRecorder(self._storage, run_id, trajectory_id)
        await recorder.emit(EventType.TRAJECTORY_STARTED, {"task_id": task.task_id, "index": index})

        gateway = ToolGateway.record(recorder=recorder, tools=self._tools)
        start = perf_counter()
        error: BaseException | None = None
        output = ""
        try:
            output = await self._agent.run(task, gateway)
            outcome = await self._judge.judge(task, output)
        except BaseException as exc:
            error = exc
            outcome = "error"
            output = str(exc)

        latency_ms = (perf_counter() - start) * 1000
        failure_class = None if outcome == "pass" else classify_failure(error)

        await recorder.emit(
            EventType.CONTRACT_CHECK,
            {"task_id": task.task_id, "accepted": outcome == "pass", "output": output},
        )
        await recorder.emit(
            EventType.VERDICT,
            {"outcome": outcome, "output": output, "failure_class": failure_class},
        )
        await recorder.emit(
            EventType.TRAJECTORY_FINISHED,
            {"outcome": outcome, "cost_usd": 0.0, "latency_ms": latency_ms},
        )

        return TrajectoryResult(
            trajectory_id=trajectory_id,
            outcome=outcome,
            output=output,
            failure_class=failure_class,
            cost_usd=0.0,
            latency_ms=latency_ms,
        )

    async def replay(
        self,
        *,
        events: list[Event],
        task: TaskSpec,
        trajectory_id: str | None = None,
    ) -> str:
        replay_events = _events_for_trajectory(events, trajectory_id)
        expected_output = _expected_output(replay_events)
        gateway = ToolGateway.replay(replay_events)
        output = await self._agent.run(task, gateway)
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

