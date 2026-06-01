"""Render the SWE-bench harness-only comparison -- the headline artifact.

Two scaffolds, one model, the same tasks: this shows ``pass^k`` (with a Wilson CI
on the underlying per-run rate) for each, the paired-delta verdict, and the
one-line claim. The claim is built from measured numbers only; there is no
placeholder path -- if a run did not happen, this function is not called.
"""

from __future__ import annotations

from chorus.core.metrics import wilson_interval
from chorus.core.regression import RegressionReport
from chorus.core.suite import SuiteResult

_VERDICT = {
    "regressed": "the scaffold change made it reliably WORSE",
    "improved": "the scaffold change made it reliably BETTER",
    "inconclusive": "no reliable difference at this N (widen N)",
    "baseline_set": "baseline recorded",
}


def _aggregate(suite: SuiteResult) -> tuple[int, int]:
    passes = sum(task.passes for task in suite.tasks)
    total = sum(task.n for task in suite.tasks)
    return passes, total


def render_benchmark_report(
    reference: SuiteResult,
    candidate: SuiteResult,
    comparison: RegressionReport,
    *,
    k: int = 5,
    subset_label: str = "SWE-bench Verified",
) -> str:
    ref_pass, ref_total = _aggregate(reference)
    cand_pass, cand_total = _aggregate(candidate)
    ref_k = reference.mean_pass_hat_k(k)
    cand_k = candidate.mean_pass_hat_k(k)
    lo, hi = comparison.delta_ci

    lines = [
        f"# SWE-bench harness-only comparison — {subset_label}",
        "",
        f"Model held fixed · {reference.n} attempts/task · "
        f"{len(reference.tasks)} tasks · seed-policy={reference.seed_policy}",
        "",
        "```",
        f"scaffold A  {reference.scaffold:<14} "
        f"pass@1 {_rate(ref_pass, ref_total)}  {_wilson(ref_pass, ref_total)}  "
        f"pass^{k} {ref_k:.3f}",
        f"scaffold B  {candidate.scaffold:<14} "
        f"pass@1 {_rate(cand_pass, cand_total)}  {_wilson(cand_pass, cand_total)}  "
        f"pass^{k} {cand_k:.3f}",
        "",
        f"verdict     {comparison.decision.upper()}  "
        f"(Δpass^{k} {comparison.mean_delta:+.3f}, 95% CI [{lo:+.3f}, {hi:+.3f}])",
        f"            {_VERDICT.get(comparison.decision, '')}",
        "```",
        "",
        "**Claim:** changing only the scaffold "
        f"({reference.scaffold} → {candidate.scaffold}) moved mean "
        f"pass^{k} from {ref_k:.2f} to {cand_k:.2f} on {subset_label} "
        f"({len(reference.tasks)} instances, N={reference.n}).",
    ]
    if comparison.decision == "inconclusive":
        lines.append(
            "\n_The CI on the delta straddles zero: report this as 'no measurable harness "
            "effect at this N', not as a win. Widen N or the subset to tighten it._"
        )
    return "\n".join(lines)


def _rate(passes: int, total: int) -> str:
    return f"{passes / total:.2f}" if total else "0.00"


def _wilson(passes: int, total: int) -> str:
    lo, hi = wilson_interval(passes, total)
    return f"Wilson95 [{lo:.2f}, {hi:.2f}]"
