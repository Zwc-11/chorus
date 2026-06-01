"""Core data types.

This file defines the small domain objects Chorus passes around: task specs,
trajectory results, run metrics, and final run verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Outcome = Literal["pass", "fail", "error"]
JudgeOutcome = Literal["pass", "fail", "unknown"]
RunVerdict = Literal["pass", "fail", "needs_more_evidence"]
EscalationAction = Literal["repair", "strong_judge", "human_gate"]


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_id: str
    prompt: str
    expected_output: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    step_contracts: dict[int, StepBoundaryContract] = field(default_factory=dict)

    def accepts(self, output: str) -> bool:
        if self.expected_output is None:
            return True
        return output.strip() == self.expected_output.strip()


@dataclass(frozen=True, slots=True)
class StepBoundaryContract:
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TrajectoryResult:
    trajectory_id: str
    outcome: Outcome
    output: str
    failure_class: str | None
    cost_usd: float
    latency_ms: float
    failure_step: int | None = None
    failure_detail: str | None = None
    failure_confidence: float | None = None


@dataclass(frozen=True, slots=True)
class ReliabilityCurvePoint:
    k: int
    projected: float
    empirical: float


@dataclass(frozen=True, slots=True)
class ReliabilityMetrics:
    pass_at_1: float
    pass_at_k: float
    k: int
    variance: float
    wilson_ci: tuple[float, float]
    mean_cost: float
    p50_latency_ms: float
    p95_latency_ms: float
    pass_at_k_unbiased: float = 0.0
    curve: tuple[ReliabilityCurvePoint, ...] = ()
    failure_breakdown: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    task_id: str
    trajectories: tuple[TrajectoryResult, ...]
    metrics: ReliabilityMetrics
    escalations: int
    verdict: RunVerdict
    judge_summary: dict[str, Any] = field(default_factory=dict)
    escalation_trace: tuple[dict[str, Any], ...] = ()
