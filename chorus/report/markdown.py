from __future__ import annotations

from chorus.core.types import RunResult


def render_run_report(result: RunResult) -> str:
    lower, upper = result.metrics.wilson_ci
    return "\n".join(
        [
            f"# Chorus Run {result.run_id}",
            "",
            f"- task: `{result.task_id}`",
            f"- verdict: `{result.verdict}`",
            f"- trajectories: `{len(result.trajectories)}`",
            f"- pass@1: `{result.metrics.pass_at_1:.2f}`",
            f"- pass^k: `{result.metrics.pass_at_k:.2f}`",
            f"- variance: `{result.metrics.variance:.4f}`",
            f"- Wilson CI: `[{lower:.2f}, {upper:.2f}]`",
            f"- mean cost: `${result.metrics.mean_cost:.4f}`",
            f"- p50 latency: `{result.metrics.p50_latency_ms:.2f} ms`",
            f"- p95 latency: `{result.metrics.p95_latency_ms:.2f} ms`",
        ]
    )

