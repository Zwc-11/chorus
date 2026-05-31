from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Outcome = Literal["pass", "fail", "error"]
RunVerdict = Literal["pass", "fail", "needs_more_evidence"]


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_id: str
    prompt: str
    expected_output: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def accepts(self, output: str) -> bool:
        if self.expected_output is None:
            return True
        return output.strip() == self.expected_output.strip()


@dataclass(frozen=True, slots=True)
class TrajectoryResult:
    trajectory_id: str
    outcome: Outcome
    output: str
    failure_class: str | None
    cost_usd: float
    latency_ms: float


@dataclass(frozen=True, slots=True)
class ReliabilityMetrics:
    pass_at_1: float
    pass_at_k: float
    variance: float
    wilson_ci: tuple[float, float]
    mean_cost: float
    p50_latency_ms: float
    p95_latency_ms: float


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    task_id: str
    trajectories: tuple[TrajectoryResult, ...]
    metrics: ReliabilityMetrics
    escalations: int
    verdict: RunVerdict

