"""Render a regression report as the PR comment reviewers actually see.

The per-class failure breakdown (Phase 4 labels) is the part that turns "your
number went down" into "here is *what* broke" -- the output that reads as a real
tool. All four verdicts share the shape; only the header and guidance differ.
"""

from __future__ import annotations

from chorus.core.regression import RegressionReport

_HEADER = {
    "regressed": "REGRESSED ❌",
    "improved": "IMPROVED ✅",
    "inconclusive": "INCONCLUSIVE ⚠️",
    "baseline_set": "BASELINE SET \U0001f4cc",
}

_GUIDANCE = {
    "regressed": "This PR made reliability **reliably worse** than baseline (the delta CI is "
    "entirely below zero). Blocking.",
    "improved": "This PR made reliability **reliably better** (the delta CI is entirely above "
    "zero). Not blocking; update the baseline on merge.",
    "inconclusive": "The delta CI straddles zero -- run-to-run noise can't be told from a real "
    "change at this N. **Not blocking.** Widen N to tighten the interval.",
    "baseline_set": "First run on this branch/suite -- recorded as the baseline. Not blocking.",
}


def render_regression_comment(report: RegressionReport, *, suite_version: str = "") -> str:
    lo, hi = report.delta_ci
    lines = [f"## Chorus reliability gate — {_HEADER.get(report.decision, report.decision)}"]
    lines.append("")
    lines.append(_GUIDANCE.get(report.decision, ""))
    lines.append("")
    lines.append("```")
    if report.decision == "baseline_set":
        lines.append(
            f"pass^{report.k}: {report.candidate_pass_k:.2f}  (mean over {report.n_tasks} tasks)"
        )
    else:
        sign = "below" if hi < 0 else "above" if lo > 0 else "straddles"
        lines.append(
            f"pass^{report.k}: {report.baseline_pass_k:.2f} -> {report.candidate_pass_k:.2f}"
            f"   (Δ {report.mean_delta:+.2f}, 95% CI [{lo:+.2f}, {hi:+.2f}])"
            f"   <- {sign} 0"
        )
    lines.append(_cost_line(report))
    lines.append("```")

    failure_block = _failure_block(report)
    if failure_block:
        lines.extend(["", *failure_block])

    if report.top_regressed:
        lines.extend(["", f"Top regressed tasks: {', '.join(report.top_regressed)}"])

    lines.extend(
        [
            "",
            f"Baseline: `{report.baseline_ref or 'n/a'}` · N={report.n} · "
            f"seed-policy={report.seed_policy}"
            + (f" · suite={suite_version}" if suite_version else ""),
        ]
    )
    return "\n".join(lines)


def _cost_line(report: RegressionReport) -> str:
    pct = (report.cost_delta / report.baseline_cost * 100) if report.baseline_cost else 0.0
    return f"cost/run: ${report.baseline_cost:.4f} -> ${report.candidate_cost:.4f} ({pct:+.0f}%)"


def _failure_block(report: RegressionReport) -> list[str]:
    if not report.failure_class_delta:
        return []
    lines = ["New failures by class (candidate vs baseline):"]
    for label, delta in report.failure_class_delta.items():
        lines.append(f"  {delta:+d}  {label}")
    return lines
