"""Markdown report renderer.

This file turns a RunResult into human-readable Markdown so CLI output and
future CI comments can show the same reliability summary.
"""

from __future__ import annotations

from collections import Counter

from murmur.core.types import RunResult


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
    failures = len(result.trajectories) - passes
    errors = sum(1 for t in result.trajectories if t.outcome == "error")
    lines = [
        f"# murmur Run {result.run_id}",
        "",
        f"- task: `{result.task_id}`",
        f"- verdict: `{result.verdict}`",
        f"- trajectories: `{len(result.trajectories)}` ({passes} passed)",
        f"- pass@1 with Wilson 95% CI: `{metrics.pass_at_1:.2f}` `[{lower:.2f}, {upper:.2f}]`",
        f"- pass^k projected (i.i.d., k={metrics.k}): `{metrics.pass_at_k:.4f}`",
        f"- pass^k empirical unbiased (k={metrics.k}): `{metrics.pass_at_k_unbiased:.4f}`",
        f"- variance: `{metrics.variance:.4f}`",
        f"- failures: `{failures} / {len(result.trajectories)}` ({errors} errors)",
        f"- mean cost: `${metrics.mean_cost:.4f}`",
        f"- p50 latency: `{metrics.p50_latency_ms:.2f} ms`",
        f"- p95 latency: `{metrics.p95_latency_ms:.2f} ms`",
        f"- escalations: `{result.escalations}`",
    ]
    if result.judge_summary:
        lines.extend(
            [
                f"- judge resolved tier: `{result.judge_summary.get('resolved_tier')}`",
                f"- judge cost ratio: `{result.judge_summary.get('cost_ratio', 0.0):.2f}`"
                " (synthetic-validated; real accuracy-parity number lands in Phase 5)",
                f"- tier hits: `{result.judge_summary.get('tier_hits', {})}`",
            ]
        )
    lines.extend(_failure_breakdown(result))
    return "\n".join(lines)
