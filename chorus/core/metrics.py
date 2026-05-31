from __future__ import annotations

from statistics import median

from chorus.core.types import ReliabilityMetrics, TrajectoryResult


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)

    p_hat = successes / total
    denominator = 1 + z**2 / total
    center = (p_hat + z**2 / (2 * total)) / denominator
    margin = z * ((p_hat * (1 - p_hat) + z**2 / (4 * total)) / total) ** 0.5 / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile_value)
    return ordered[index]


def reliability_metrics(trajectories: tuple[TrajectoryResult, ...]) -> ReliabilityMetrics:
    total = len(trajectories)
    passes = sum(1 for trajectory in trajectories if trajectory.outcome == "pass")
    pass_rate = passes / total if total else 0.0
    latencies = [trajectory.latency_ms for trajectory in trajectories]
    costs = [trajectory.cost_usd for trajectory in trajectories]

    return ReliabilityMetrics(
        pass_at_1=1.0 if total and trajectories[0].outcome == "pass" else 0.0,
        pass_at_k=pass_rate,
        variance=pass_rate * (1 - pass_rate),
        wilson_ci=wilson_interval(passes, total),
        mean_cost=sum(costs) / total if total else 0.0,
        p50_latency_ms=median(latencies) if latencies else 0.0,
        p95_latency_ms=percentile(latencies, 0.95),
    )

