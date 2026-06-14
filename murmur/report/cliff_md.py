"""Render the reliability-cliff artifact (Phase 6's headline output).

The cliff is the thesis made concrete: an agent whose ``pass@1`` looks shippable
on one run but whose ``pass^k`` collapses across N, with the per-class failure
breakdown that says *what* breaks. It is computed entirely from a ``SuiteResult``
the conductor already produced -- no new statistics, just foregrounding the gap.
"""

from __future__ import annotations

from murmur.core.metrics import wilson_interval
from murmur.core.suite import SuiteResult


def render_cliff_report(suite: SuiteResult, *, k: int = 5, agent_label: str = "") -> str:
    passes = sum(task.passes for task in suite.tasks)
    total = sum(task.n for task in suite.tasks)
    pass_at_1 = passes / total if total else 0.0
    lo, hi = wilson_interval(passes, total)
    mean_pk = suite.mean_pass_hat_k(k)
    drop = pass_at_1 - mean_pk
    cliff = pass_at_1 >= 0.6 and mean_pk <= 0.5 and drop >= 0.2

    label = agent_label or suite.scaffold or "agent"
    header = "RELIABILITY CLIFF ⚠️" if cliff else "reliability profile"
    lines = [
        f"# Chorus {header} — {label}",
        "",
        f"agent: {label}   tasks: {len(suite.tasks)}   N: {suite.n}   "
        f"seed-policy={suite.seed_policy}   suite={suite.suite_version}",
        "",
        "```",
        f"pass@1   {pass_at_1:.2f}   Wilson95 [{lo:.2f}, {hi:.2f}]"
        + ("      <- looks shippable on one run" if pass_at_1 >= 0.6 else ""),
        f"pass^{k}   {mean_pk:.2f}"
        + (
            f"                        <- collapses across {k} runs (Δ -{drop:.2f})"
            if cliff
            else ""
        ),
        "```",
    ]

    failures = suite.failure_totals()
    if failures:
        lines.append("")
        lines.append("Failures by class (Phase 4 labels):")
        for label_, count in sorted(failures.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"  {count:>3}  {label_}")

    worst = sorted(suite.tasks, key=lambda t: t.pass_hat_k(k))[:3]
    if worst:
        lines.append("")
        lines.append(f"Worst tasks (lowest pass^{k}):")
        for task in worst:
            lines.append(
                f"  {task.pass_hat_k(k):.2f}  {task.task_id}  "
                f"({task.passes}/{task.n} pass)"
            )

    lines.append("")
    if cliff:
        lines.append(
            f"**The cliff:** one run shows pass@1 {pass_at_1:.2f} and reads shippable, but the "
            f"agent only clears all {k} runs {mean_pk:.0%} of the time. A pass@1 eval cannot see "
            "this; pass^k does."
        )
    else:
        lines.append(
            f"No cliff at this N: pass@1 {pass_at_1:.2f} and pass^{k} {mean_pk:.2f} are close. "
            "Widen N or pick a harder task set to stress it."
        )
    return "\n".join(lines)
