"""Reliability metric calculations.

This file converts many trajectory outcomes into distribution-aware metrics.

Two pass rates are reported, and the difference between them is the whole point
of Chorus:

* ``pass@1`` -- the probability that a *single* run passes, estimated as the
  fraction of trajectories that passed (``c / n``). This is what a one-shot
  evaluation sees.
* ``pass^k`` -- the probability that *all* ``k`` runs pass, projected from the
  observed per-run rate as ``(c / n) ** k``. Reliability compounds, so an agent
  that looks fine at ``pass@1 = 0.7`` is only ``0.7 ** 12 = 1.4%`` reliable
  across 12 attempts. ``pass@1`` cannot tell ``7/10`` apart from ``10/10`` on a
  single noisy run; ``pass^k`` can.
"""

from __future__ import annotations

from statistics import median

from chorus.core.types import ReliabilityMetrics, TrajectoryResult


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score confidence interval for a binomial pass rate."""

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


def reliability_metrics(
    trajectories: tuple[TrajectoryResult, ...],
    *,
    k: int | None = None,
) -> ReliabilityMetrics:
    """Fold trajectory outcomes into a distribution-aware reliability summary.

    ``k`` is the horizon for ``pass^k`` and defaults to the number of
    trajectories observed.
    """

    total = len(trajectories)
    passes = sum(1 for trajectory in trajectories if trajectory.outcome == "pass")
    pass_rate = passes / total if total else 0.0
    horizon = k if k is not None else total
    latencies = [trajectory.latency_ms for trajectory in trajectories]
    costs = [trajectory.cost_usd for trajectory in trajectories]

    return ReliabilityMetrics(
        pass_at_1=pass_rate,
        pass_at_k=pass_rate**horizon if horizon > 0 else 1.0,
        k=horizon,
        variance=pass_rate * (1 - pass_rate),
        wilson_ci=wilson_interval(passes, total),
        mean_cost=sum(costs) / total if total else 0.0,
        p50_latency_ms=median(latencies) if latencies else 0.0,
        p95_latency_ms=percentile(latencies, 0.95),
    )
