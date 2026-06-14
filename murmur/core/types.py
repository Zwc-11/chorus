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
DiagnosticSeverity = Literal["info", "warning", "error"]


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_id: str
    prompt: str
    expected_output: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    step_contracts: dict[int, StepBoundaryContract] = field(default_factory=dict)

    def accepts(self, output: str) -> bool:
        from murmur.core.acceptance import task_accepts

        return task_accepts(self, output)


@dataclass(frozen=True, slots=True)
class StepBoundaryContract:
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ContractDiagnostic:
    """Structured verdict evidence emitted by acceptance contracts.

    The predicate id is the stable machine-readable part. The message and repair
    hint are safe to show to an agent or a human without exposing hidden tests.
    """

    predicate_id: str
    severity: DiagnosticSeverity
    message: str
    evidence: str = ""
    repair_hint: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "predicate_id": self.predicate_id,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
            "repair_hint": self.repair_hint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractDiagnostic:
        return cls(
            predicate_id=str(data["predicate_id"]),
            severity=str(data.get("severity", "error")),  # type: ignore[arg-type]
            message=str(data.get("message", "")),
            evidence=str(data.get("evidence", "")),
            repair_hint=str(data.get("repair_hint", "")),
        )


@dataclass(frozen=True, slots=True)
class AgentAdapterCapabilities:
    """Declared integration surface for a third-party agent adapter."""

    record: bool = False
    replay: bool = False
    hooks: bool = False
    trace_import: bool = False
    live_execution: bool = False
    sandbox: bool = False
    tool_interception: bool = False

    def labels(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, enabled in (
                ("record", self.record),
                ("replay", self.replay),
                ("hooks", self.hooks),
                ("trace-import", self.trace_import),
                ("live", self.live_execution),
                ("sandbox", self.sandbox),
                ("tools", self.tool_interception),
            )
            if enabled
        )


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
