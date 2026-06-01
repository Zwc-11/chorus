"""Markdown report renderer.

This file turns a RunResult into human-readable Markdown so CLI output and
future CI comments can show the same reliability summary.
"""

from __future__ import annotations

from collections import Counter

from chorus.core.types import RunResult


def _failure_breakdown(result: RunResult) -> list[str]:
    classes = Counter(
        trajectory.failure_class or trajectory.outcome
        for trajectory in result.trajectories
        if trajectory.outcome != "pass"
    )
    if not classes:
        return []
    lines = ["", "Failure breakdown:"]
    lines.extend(f"- `{label}`: {count}" for label, count in classes.most_common())
    return lines


def render_run_report(result: RunResult) -> str:
    metrics = result.metrics
    lower, upper = metrics.wilson_ci
    passes = sum(1 for t in result.trajectories if t.outcome == "pass")
    lines = [
        f"# Chorus Run {result.run_id}",
        "",
        f"- task: `{result.task_id}`",
        f"- verdict: `{result.verdict}`",
        f"- trajectories: `{len(result.trajectories)}` ({passes} passed)",
        f"- pass@1 (single-run reliability): `{metrics.pass_at_1:.2f}`",
        f"- pass^k (all {metrics.k} runs pass): `{metrics.pass_at_k:.4f}`",
        f"- variance: `{metrics.variance:.4f}`",
        f"- Wilson 95% CI on pass@1: `[{lower:.2f}, {upper:.2f}]`",
        f"- mean cost: `${metrics.mean_cost:.4f}`",
        f"- p50 latency: `{metrics.p50_latency_ms:.2f} ms`",
        f"- p95 latency: `{metrics.p95_latency_ms:.2f} ms`",
        f"- escalations: `{result.escalations}`",
    ]
    lines.extend(_failure_breakdown(result))
    return "\n".join(lines)
