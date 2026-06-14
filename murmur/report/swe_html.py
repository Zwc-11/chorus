"""Standalone HTML report for SWE-bench scaffold comparisons."""

from __future__ import annotations

from html import escape
from pathlib import Path

from murmur.core.metrics import wilson_interval
from murmur.core.regression import RegressionReport
from murmur.core.suite import SuiteResult, TaskReliability
from murmur.report.ui_theme import document_close, document_head, hud_shell_end, hud_shell_start


def render_benchmark_html(
    reference: SuiteResult,
    candidate: SuiteResult,
    comparison: RegressionReport,
    *,
    k: int = 5,
    subset_label: str = "SWE-bench Verified",
    markdown_report: str = "",
) -> str:
    """Render the public benchmark proof as a browser-readable artifact."""

    ref_passes, ref_total = _aggregate(reference)
    cand_passes, cand_total = _aggregate(candidate)
    ref_pass_k = reference.mean_pass_hat_k(k)
    cand_pass_k = candidate.mean_pass_hat_k(k)
    lo, hi = comparison.delta_ci
    run_line = (
        f"{escape(subset_label)} | N={reference.n} | k={k} | "
        f"seed={reference.seed} | {escape(reference.seed_policy)}"
    )
    body = [
        document_head(title="Chorus SWE-bench report", extra_css=_css()),
        "<body>",
        hud_shell_start(
            brand="murmur bench",
            run_line=run_line,
            quote="model fixed | scaffold varied | judged by SWE-bench",
        ),
        '<section class="bench-hero">',
        '<div class="bench-kicker">SWE-bench harness-only comparison</div>',
        f"<h1>{escape(reference.scaffold)} vs {escape(candidate.scaffold)}</h1>",
        f'<p class="bench-summary">{_claim(reference, candidate, k, subset_label)}</p>',
        "</section>",
        '<section class="bench-grid" aria-label="headline metrics">',
        _metric_card("verdict", comparison.decision.upper(), _verdict_detail(comparison, k)),
        _metric_card(
            f"{reference.scaffold} pass^{k}",
            f"{ref_pass_k:.3f}",
            _rate_detail(ref_passes, ref_total),
        ),
        _metric_card(
            f"{candidate.scaffold} pass^{k}",
            f"{cand_pass_k:.3f}",
            _rate_detail(cand_passes, cand_total),
        ),
        _metric_card(
            f"delta pass^{k}",
            f"{comparison.mean_delta:+.3f}",
            f"95% CI [{lo:+.3f}, {hi:+.3f}]",
        ),
        "</section>",
        '<section class="bench-section">',
        "<h2>Scaffold Comparison</h2>",
        _comparison_table(reference, candidate, k),
        "</section>",
        '<section class="bench-section">',
        "<h2>Failure Classes</h2>",
        _failure_table(reference, candidate),
        "</section>",
        '<section class="bench-section">',
        "<h2>Task Outcomes</h2>",
        _task_table(reference, candidate),
        "</section>",
    ]
    if markdown_report:
        body.extend(
            [
                '<section class="bench-section">',
                "<h2>Markdown Artifact</h2>",
                f"<pre>{escape(markdown_report)}</pre>",
                "</section>",
            ]
        )
    body.extend(
        [
            hud_shell_end(footer_left="murmur bench"),
            document_close(),
        ]
    )
    return "\n".join(body)


def write_benchmark_html(
    reference: SuiteResult,
    candidate: SuiteResult,
    comparison: RegressionReport,
    path: Path | str,
    *,
    k: int = 5,
    subset_label: str = "SWE-bench Verified",
    markdown_report: str = "",
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_benchmark_html(
            reference,
            candidate,
            comparison,
            k=k,
            subset_label=subset_label,
            markdown_report=markdown_report,
        ),
        encoding="utf-8",
    )
    return out


def _aggregate(suite: SuiteResult) -> tuple[int, int]:
    passes = sum(task.passes for task in suite.tasks)
    total = sum(task.n for task in suite.tasks)
    return passes, total


def _claim(reference: SuiteResult, candidate: SuiteResult, k: int, subset_label: str) -> str:
    return (
        f"Changing only the scaffold moved mean pass^{k} from "
        f"{reference.mean_pass_hat_k(k):.2f} to {candidate.mean_pass_hat_k(k):.2f} on "
        f"{escape(subset_label)} ({len(reference.tasks)} instances, N={reference.n})."
    )


def _metric_card(label: str, value: str, detail: str) -> str:
    return (
        '<article class="bench-card">'
        f'<div class="bench-card__label">{escape(label)}</div>'
        f'<div class="bench-card__value">{escape(value)}</div>'
        f'<div class="bench-card__detail">{escape(detail)}</div>'
        "</article>"
    )


def _verdict_detail(comparison: RegressionReport, k: int) -> str:
    lo, hi = comparison.delta_ci
    if comparison.decision == "inconclusive":
        return f"no measurable harness effect yet; delta CI [{lo:+.3f}, {hi:+.3f}]"
    return f"delta pass^{k} {comparison.mean_delta:+.3f}; CI [{lo:+.3f}, {hi:+.3f}]"


def _rate_detail(passes: int, total: int) -> str:
    lo, hi = wilson_interval(passes, total)
    return f"pass@1 {_rate(passes, total)} from {passes}/{total}; Wilson95 [{lo:.2f}, {hi:.2f}]"


def _comparison_table(reference: SuiteResult, candidate: SuiteResult, k: int) -> str:
    rows = [_suite_row(reference, k), _suite_row(candidate, k)]
    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>Scaffold</th><th>pass@1</th><th>pass^k</th>"
        "<th>Mean cost/run</th><th>Attempts</th><th>Tasks</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _suite_row(suite: SuiteResult, k: int) -> str:
    passes, total = _aggregate(suite)
    return (
        "<tr>"
        f"<td>{escape(suite.scaffold)}</td>"
        f"<td>{_rate(passes, total)}</td>"
        f"<td>{suite.mean_pass_hat_k(k):.3f}</td>"
        f"<td>${suite.mean_cost():.4f}</td>"
        f"<td>{suite.n}</td>"
        f"<td>{len(suite.tasks)}</td>"
        "</tr>"
    )


def _failure_table(reference: SuiteResult, candidate: SuiteResult) -> str:
    ref = reference.failure_totals()
    cand = candidate.failure_totals()
    labels = sorted(set(ref) | set(cand))
    if not labels:
        rows = '<tr><td colspan="3">none recorded</td></tr>'
    else:
        rows = "".join(
            "<tr>"
            f"<td>{escape(label)}</td>"
            f"<td>{ref.get(label, 0)}</td>"
            f"<td>{cand.get(label, 0)}</td>"
            "</tr>"
            for label in labels
        )
    return (
        '<div class="table-wrap"><table>'
        f"<thead><tr><th>Failure class</th><th>{escape(reference.scaffold)}</th>"
        f"<th>{escape(candidate.scaffold)}</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _task_table(reference: SuiteResult, candidate: SuiteResult) -> str:
    ref = reference.task_map()
    cand = candidate.task_map()
    ids = sorted(set(ref) | set(cand))
    rows = "".join(_task_row(task_id, ref.get(task_id), cand.get(task_id)) for task_id in ids)
    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>Task</th><th>Reference</th><th>Candidate</th>"
        "<th>Reference failures</th><th>Candidate failures</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _task_row(
    task_id: str, reference: TaskReliability | None, candidate: TaskReliability | None
) -> str:
    return (
        "<tr>"
        f"<td><code>{escape(task_id)}</code></td>"
        f"<td>{_task_result(reference)}</td>"
        f"<td>{_task_result(candidate)}</td>"
        f"<td>{_failures(reference)}</td>"
        f"<td>{_failures(candidate)}</td>"
        "</tr>"
    )


def _task_result(task: TaskReliability | None) -> str:
    if task is None:
        return "n/a"
    return f"{task.passes}/{task.n}"


def _failures(task: TaskReliability | None) -> str:
    if task is None or not task.failure_breakdown:
        return "none"
    return ", ".join(
        f"{escape(label)}={count}" for label, count in sorted(task.failure_breakdown.items())
    )


def _rate(passes: int, total: int) -> str:
    return f"{passes / total:.2f}" if total else "0.00"


def _css() -> str:
    return r"""
.bench-hero {
  padding: 34px 0 12px;
}
.bench-kicker {
  color: var(--accent);
  font-family: var(--mono);
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
.bench-hero h1 {
  margin: 10px 0 12px;
  font-size: clamp(32px, 6vw, 64px);
  font-weight: 220;
  letter-spacing: 0;
}
.bench-summary {
  max-width: 780px;
  color: var(--muted);
  font-size: 16px;
  line-height: 1.55;
}
.bench-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 12px;
  margin: 18px 0 28px;
}
.bench-card {
  min-height: 132px;
  border: var(--hud-border);
  background: var(--panel);
  padding: 16px;
}
.bench-card__label {
  color: var(--muted);
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.bench-card__value {
  margin-top: 12px;
  font-family: var(--mono);
  font-size: 28px;
}
.bench-card__detail {
  margin-top: 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.4;
}
.bench-section {
  margin: 28px 0;
}
.bench-section h2 {
  margin: 0 0 12px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
.table-wrap {
  overflow-x: auto;
  border: var(--hud-border);
  background: var(--panel2);
}
table {
  width: 100%;
  border-collapse: collapse;
  min-width: 680px;
}
th, td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}
th {
  color: var(--muted);
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
td {
  font-size: 14px;
}
pre {
  overflow-x: auto;
  padding: 16px;
  border: var(--hud-border);
  background: rgba(255, 255, 255, 0.5);
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.45;
}
code {
  font-family: var(--mono);
}
"""
