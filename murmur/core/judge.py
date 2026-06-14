"""Tiered judgment, escalation, and judge-cost measurement."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from math import sqrt
from typing import Any

from murmur.core.events import hash_payload
from murmur.core.ports import ToolGatewayPort
from murmur.core.types import JudgeOutcome, RunResult, TaskSpec, TrajectoryResult

JudgeCallable = Callable[[TaskSpec, TrajectoryResult], JudgeOutcome]


class DeterministicJudge:
    """Tier 0 judge: use the task contract before any expensive evaluator."""

    async def judge(self, task: TaskSpec, output: str) -> str:
        return "pass" if task.accepts(output) else "fail"


@dataclass(frozen=True, slots=True)
class JudgePolicy:
    converge_tol: float = 1.0
    tier2_cost_usd: float = 0.01
    escalation_action: str = "strong_judge"
    strong_judge: JudgeCallable | None = None
    human_gate_verdict: JudgeOutcome = "unknown"


@dataclass(frozen=True, slots=True)
class CascadeJudgment:
    verdict: str
    resolved_tier: int
    trajectory_verdicts: dict[str, JudgeOutcome]
    tier_hits: dict[str, int]
    tier2_calls: int
    cascade_cost_usd: float
    baseline_cost_usd: float
    cost_ratio: float
    escalations: int
    escalation_trace: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class LabelledJudgeCase:
    result: RunResult
    task: TaskSpec
    ground_truth: JudgeOutcome


@dataclass(frozen=True, slots=True)
class JudgeCostReport:
    cost_ratio: float
    cascade_cost_usd: float
    baseline_cost_usd: float
    baseline_accuracy: float
    cascade_accuracy: float
    accuracy_delta: float
    accuracy_delta_ci: tuple[float, float]
    tier_hits: dict[str, int]


@dataclass(slots=True)
class JudgeCallCache:
    values: dict[str, JudgeOutcome] = field(default_factory=dict)
    calls: int = 0


def judge_run(
    result: RunResult,
    task: TaskSpec,
    *,
    policy: JudgePolicy | None = None,
    divergence_step: int | None = None,
) -> CascadeJudgment:
    """Run the three-tier cascade over an already-recorded run."""

    policy = policy or JudgePolicy()
    verdicts = {
        trajectory.trajectory_id: _tier0_check(task, trajectory)
        for trajectory in result.trajectories
    }
    known = {key: value for key, value in verdicts.items() if value != "unknown"}
    agreement = agreement_on_outcome(known.values())
    tier_hits = {"tier0": len(known), "tier1": 0, "tier2": 0}

    if len(known) == len(verdicts) and agreement >= policy.converge_tol:
        tier_hits["tier1"] = len(verdicts)
        verdict = _aggregate(verdicts.values())
        return CascadeJudgment(
            verdict=verdict,
            resolved_tier=1,
            trajectory_verdicts=verdicts,
            tier_hits=tier_hits,
            tier2_calls=0,
            cascade_cost_usd=0.0,
            baseline_cost_usd=len(verdicts) * policy.tier2_cost_usd,
            cost_ratio=0.0,
            escalations=0,
        )

    ambiguous = _ambiguous_trajectory_ids(verdicts)
    for trajectory in result.trajectories:
        if trajectory.trajectory_id not in ambiguous:
            continue
        verdicts[trajectory.trajectory_id] = _tier2_judge(task, trajectory, policy)
        tier_hits["tier2"] += 1

    cascade_cost = tier_hits["tier2"] * policy.tier2_cost_usd
    baseline_cost = len(verdicts) * policy.tier2_cost_usd
    final = _aggregate(verdicts.values())
    trace = escalation_state_machine(
        verdicts,
        action=policy.escalation_action,
        divergence_step=divergence_step,
        final_verdict=final,
    )
    return CascadeJudgment(
        verdict=final,
        resolved_tier=2,
        trajectory_verdicts=verdicts,
        tier_hits=tier_hits,
        tier2_calls=tier_hits["tier2"],
        cascade_cost_usd=cascade_cost,
        baseline_cost_usd=baseline_cost,
        cost_ratio=cascade_cost / baseline_cost if baseline_cost else 0.0,
        escalations=1 if trace else 0,
        escalation_trace=trace,
    )


def agreement_on_outcome(verdicts: Iterable[JudgeOutcome]) -> float:
    values = list(verdicts)
    if not values:
        return 0.0
    counts = Counter(values)
    return counts.most_common(1)[0][1] / len(values)


def escalation_state_machine(
    verdicts: dict[str, JudgeOutcome],
    *,
    action: str,
    divergence_step: int | None,
    final_verdict: str,
) -> tuple[dict[str, Any], ...]:
    if not verdicts:
        return ()
    if len(set(verdicts.values())) == 1 and "unknown" not in set(verdicts.values()):
        return ()
    step = divergence_step
    return (
        {
            "from": "RUN_CHEAP",
            "trigger": "divergence_or_unknown",
            "step": step,
            "to": "ESCALATE",
        },
        {"from": "ESCALATE", "action": action, "step": step, "to": "RE_EVAL"},
        {
            "from": "RE_EVAL",
            "outcome": final_verdict,
            "to": "DONE" if final_verdict == "pass" else "FAIL",
        },
    )


def measure_judge_cost(
    cases: Iterable[LabelledJudgeCase],
    *,
    policy: JudgePolicy | None = None,
) -> JudgeCostReport:
    policy = policy or JudgePolicy()
    items = list(cases)
    baseline_correct = 0
    cascade_correct = 0
    baseline_cost = 0.0
    cascade_cost = 0.0
    tier_hits: Counter[str] = Counter()

    for case in items:
        baseline = _baseline_verdict(case.result, case.task, policy)
        cascade = judge_run(case.result, case.task, policy=policy)
        baseline_correct += int(baseline == case.ground_truth)
        cascade_correct += int(cascade.verdict == case.ground_truth)
        baseline_cost += cascade.baseline_cost_usd
        cascade_cost += cascade.cascade_cost_usd
        tier_hits.update(cascade.tier_hits)

    total = len(items)
    baseline_accuracy = baseline_correct / total if total else 0.0
    cascade_accuracy = cascade_correct / total if total else 0.0
    delta = cascade_accuracy - baseline_accuracy
    half = (
        1.96 * sqrt(max(cascade_accuracy * (1 - cascade_accuracy), 0.0) / total) if total else 0.0
    )
    return JudgeCostReport(
        cost_ratio=cascade_cost / baseline_cost if baseline_cost else 0.0,
        cascade_cost_usd=cascade_cost,
        baseline_cost_usd=baseline_cost,
        baseline_accuracy=baseline_accuracy,
        cascade_accuracy=cascade_accuracy,
        accuracy_delta=delta,
        accuracy_delta_ci=(delta - half, delta + half),
        tier_hits=dict(tier_hits),
    )


def cohen_kappa(left: list[JudgeOutcome], right: list[JudgeOutcome]) -> float:
    if len(left) != len(right):
        raise ValueError("kappa inputs must have the same length")
    total = len(left)
    if total == 0:
        return 0.0
    observed = sum(1 for a, b in zip(left, right, strict=True) if a == b) / total
    labels = set(left) | set(right)
    expected = sum((left.count(label) / total) * (right.count(label) / total) for label in labels)
    if expected == 1.0:
        return 1.0
    return (observed - expected) / (1 - expected)


async def cached_llm_judge(
    *,
    task: TaskSpec,
    trajectory: TrajectoryResult,
    rubric: str,
    model: str,
    cache: JudgeCallCache,
    evaluator: JudgeCallable,
    gateway: ToolGatewayPort | None = None,
) -> JudgeOutcome:
    """Record and cache a Tier-2 judge call by stable input hash."""

    key = hash_payload(
        {
            "rubric": rubric,
            "trajectory": {
                "output": trajectory.output,
                "outcome": trajectory.outcome,
                "failure_class": trajectory.failure_class,
            },
            "model": model,
        }
    )
    if key in cache.values:
        return cache.values[key]
    if gateway is not None:
        await gateway.model(
            model=model,
            input_tokens=max(1, len(rubric) // 4),
            output_tokens=32,
            finish_reason="stop",
            latency_ms=250.0,
            content=f"judge {trajectory.trajectory_id}",
        )
    cache.calls += 1
    verdict = evaluator(task, trajectory)
    cache.values[key] = verdict
    return verdict


def _tier0_check(task: TaskSpec, trajectory: TrajectoryResult) -> JudgeOutcome:
    if trajectory.outcome == "error":
        return "unknown"
    if trajectory.outcome == "fail":
        return "fail"
    return "pass" if task.accepts(trajectory.output) else "fail"


def _tier2_judge(task: TaskSpec, trajectory: TrajectoryResult, policy: JudgePolicy) -> JudgeOutcome:
    if policy.strong_judge is not None:
        return policy.strong_judge(task, trajectory)
    if trajectory.outcome == "pass" and task.accepts(trajectory.output):
        return "pass"
    return "fail"


def _ambiguous_trajectory_ids(verdicts: dict[str, JudgeOutcome]) -> set[str]:
    unknown = {key for key, value in verdicts.items() if value == "unknown"}
    known = [value for value in verdicts.values() if value != "unknown"]
    if not known:
        return set(verdicts)
    majority = Counter(known).most_common(1)[0][0]
    minority = {key for key, value in verdicts.items() if value != "unknown" and value != majority}
    return unknown | minority


def _aggregate(verdicts: Iterable[JudgeOutcome]) -> str:
    values = list(verdicts)
    if any(value == "unknown" for value in values):
        return "needs_more_evidence"
    return "pass" if values and all(value == "pass" for value in values) else "fail"


def _baseline_verdict(result: RunResult, task: TaskSpec, policy: JudgePolicy) -> JudgeOutcome:
    verdicts = [_tier2_judge(task, trajectory, policy) for trajectory in result.trajectories]
    return "pass" if verdicts and all(verdict == "pass" for verdict in verdicts) else "fail"
