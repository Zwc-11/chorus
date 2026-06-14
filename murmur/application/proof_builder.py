"""Render contract-first proof packages."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from murmur.domain.proof import ProofPackage
from murmur.domain.workflow import WorkflowPlan
from murmur.report.ui_theme import (
    document_close,
    document_head,
    hud_shell_start,
)


def write_proof_package(proof: ProofPackage, run_dir: Path) -> None:
    """Write the full evidence set for one run.

    A run directory should let a stranger answer "why was this patch accepted?"
    without re-running anything: proof.md (human story), proof/summary.json
    (machine story), winner.patch (the actual change), fan.html (every attempt),
    cost.json (what it cost), report.html (the visual front door).
    """

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "diff.patch").write_text(proof.diff, encoding="utf-8")
    (run_dir / "winner.patch").write_text(proof.diff, encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(proof.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
    (run_dir / "cost.json").write_text(
        json.dumps(_cost_payload(proof), indent=2), encoding="utf-8"
    )
    if proof.attempts:
        (run_dir / "attempts.json").write_text(
            json.dumps(proof.attempts, indent=2, default=str),
            encoding="utf-8",
        )
    (run_dir / "proof.md").write_text(render_proof_markdown(proof), encoding="utf-8")
    (run_dir / "fan.html").write_text(render_attempts_fan_html(proof), encoding="utf-8")

    workflow_href: str | None = None
    workflow_path = run_dir / "workflow.yaml"
    if workflow_path.is_file():
        from murmur.report.murmur_workflow_html import write_murmur_workflow_html

        workflow = WorkflowPlan.read(workflow_path)
        write_murmur_workflow_html(
            run_dir / "workflow.html",
            workflow=workflow,
            embedded_task=workflow.goal,
        )
        workflow_href = "workflow.html"

    (run_dir / "report.html").write_text(
        render_proof_html(proof, workflow_href=workflow_href),
        encoding="utf-8",
    )


def _cost_payload(proof: ProofPackage) -> dict[str, object]:
    passed = sum(1 for attempt in proof.attempts if attempt.get("passed"))
    return {
        "run_id": proof.run_id,
        "verdict": proof.verdict,
        "winner": proof.winner_id,
        "cost_usd": round(proof.cost_usd, 6),
        "model_calls": proof.model_calls,
        "tool_calls": proof.tool_calls,
        "attempts": len(proof.attempts),
        "attempts_passed": passed,
        "attempts_failed": len(proof.attempts) - passed,
    }


def render_proof_markdown(proof: ProofPackage) -> str:
    v = proof.verification
    passed = sum(1 for attempt in proof.attempts if attempt.get("passed"))
    rank_method = str(proof.rank.get("method", "")) if proof.rank else ""
    rank_reason = str(proof.rank.get("rationale", "")) if proof.rank else ""
    lines = [
        f"# Murmur Proof — {proof.run_id}",
        "",
        f"## Verdict: {proof.verdict.upper()}",
        "",
        f"- Winner: {proof.winner_id or 'none'}",
        *(
            [f"- Selected by: {rank_method}" + (f" — {rank_reason}" if rank_reason else "")]
            if rank_method
            else []
        ),
        f"- Attempts: {len(proof.attempts)} ({passed} passed / "
        f"{len(proof.attempts) - passed} failed)",
        f"- Cost: ${proof.cost_usd:.4f} "
        f"({proof.model_calls} model calls, {proof.tool_calls} tool calls)",
        "",
        "## Task",
        f"- ID: `{proof.contract.task.id}`",
        f"- Command: `{proof.contract.task.command}`",
        f"- Risk: `{proof.contract.risk.level}`",
        "",
        "## Evidence",
        f"- Failure reproduced: {_yes(v.failure_reproduced)}",
        f"- Target test passed: {_yes(v.target_test_passed)}",
        f"- Related tests passed: {_yes(v.related_tests_passed)}",
        f"- Static checks passed: {_yes(v.static_checks_passed)}",
        f"- Forbidden files touched: {', '.join(v.forbidden_files_touched) or 'none'}",
        f"- Changed files: {', '.join(v.changed_files) or 'none'}",
        f"- Diff lines: {v.diff_lines}",
        "",
        "## Budget",
        f"- Model calls: {proof.model_calls}",
        f"- Tool calls: {proof.tool_calls}",
        f"- Estimated cost: ${proof.cost_usd:.4f}",
        "",
        "## Failures",
        ", ".join(v.failures) if v.failures else "none",
        "",
        "## Summary",
        proof.summary or "No agent summary provided.",
        "",
        "## Attempts",
        *_attempt_lines(proof),
        "",
        "## Final Diff",
        "```diff",
        proof.diff,
        "```",
    ]
    return "\n".join(lines) + "\n"


def render_proof_html(proof: ProofPackage, *, workflow_href: str | None = None) -> str:
    v = proof.verification
    status = "pass" if proof.verdict == "pass" else "fail"
    status_color = "#146b3a" if status == "pass" else "var(--accent)"

    evidence_rows = [
        ("Failure reproduced", _yes(v.failure_reproduced)),
        ("Target test passed", _yes(v.target_test_passed)),
        ("Related tests passed", _yes(v.related_tests_passed)),
        ("Static checks passed", _yes(v.static_checks_passed)),
        ("Forbidden files", ", ".join(v.forbidden_files_touched) or "none"),
        ("Changed files", ", ".join(v.changed_files) or "none"),
        ("Diff lines", str(v.diff_lines)),
    ]
    evidence_html = "".join(
        f'<div class="kv"><span class="k">{escape(k)}</span><span>{escape(val)}</span></div>'
        for k, val in evidence_rows
    )

    attempts_md = "\n".join(_attempt_lines(proof))
    diff_escaped = escape(proof.diff)
    summary_escaped = escape(proof.summary or "No agent summary provided.")
    failures_escaped = escape(", ".join(v.failures) if v.failures else "none")

    links = ['<a href="fan.html">Attempt fan</a>', '<a href="proof.md">proof.md</a>']
    if proof.diff.strip():
        links.append('<a href="winner.patch">winner.patch</a>')
    if workflow_href:
        links.insert(1, f'<a href="{escape(workflow_href)}">Workflow tree</a>')
    workflow_link = f'<p class="proof-links">{" · ".join(links)}</p>'

    passed_count = sum(1 for attempt in proof.attempts if attempt.get("passed"))
    rank_method = str(proof.rank.get("method", "")) if proof.rank else ""
    rank_reason = str(proof.rank.get("rationale", "")) if proof.rank else ""
    winner_label = "winner · " + escape(rank_method) if rank_method else "winner"
    stats_html = (
        '<div class="proof-stats">'
        f'<div class="proof-stat"><span class="proof-stat__n">{escape(proof.winner_id or "—")}'
        f'</span><span class="proof-stat__l">{winner_label}</span></div>'
        f'<div class="proof-stat"><span class="proof-stat__n">{passed_count}/{len(proof.attempts)}'
        '</span><span class="proof-stat__l">attempts passed</span></div>'
        f'<div class="proof-stat"><span class="proof-stat__n">${proof.cost_usd:.4f}</span>'
        '<span class="proof-stat__l">total cost</span></div>'
        f'<div class="proof-stat"><span class="proof-stat__n">{proof.model_calls}</span>'
        '<span class="proof-stat__l">model calls</span></div>'
        "</div>"
    )
    if rank_reason:
        stats_html += f'<p class="proof-reason">why this winner: {escape(rank_reason)}</p>'

    extra_css = r"""
.proof-verdict {
  font: 500 28px/1 var(--mono);
  color: """ + status_color + r""";
  margin: 0 0 18px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.proof-links { font-family: var(--mono); font-size: 12px; margin: 0 0 16px; }
.proof-links a { text-decoration: underline; text-underline-offset: 3px; }
.proof-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
.proof-actions button {
  border: 1px solid var(--line);
  background: var(--panel-solid);
  font: 11px var(--mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 10px 14px;
  cursor: pointer;
  color: var(--txt);
}
.proof-actions button:hover { border-color: var(--accent); color: var(--accent); }
.proof-pre {
  margin: 0;
  padding: 14px;
  background: rgba(255,255,255,0.65);
  border: var(--hud-border);
  font: 12px/1.45 var(--mono);
  overflow: auto;
  max-height: 50vh;
  white-space: pre-wrap;
}
.proof-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin: 0 0 14px;
}
.proof-stat { border: 1px solid var(--line); background: var(--panel-solid); padding: 12px 14px; }
.proof-stat__n { display: block; font: 600 20px/1.2 var(--mono); }
.proof-stat__l {
  display: block;
  font: 10px var(--mono);
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-top: 4px;
}
.proof-reason { font: 12px/1.5 var(--mono); color: var(--muted); margin: 0 0 16px; }
"""

    script = f"""
document.getElementById("proof-diff-open").addEventListener("click", () => {{
  chorusOpenModal("final diff", '<pre class="proof-pre">{diff_escaped}</pre>');
}});
document.getElementById("proof-attempts-open").addEventListener("click", () => {{
  chorusOpenModal("attempts", '<pre class="proof-pre">{escape(attempts_md)}</pre>');
}});
document.getElementById("proof-summary-open").addEventListener("click", () => {{
  chorusOpenModal("summary", '<p class="lead">{summary_escaped}</p>');
}});
document.getElementById("proof-failures-open").addEventListener("click", () => {{
  chorusOpenModal("failures", '<p class="lead">{failures_escaped}</p>');
}});
"""

    head = document_head(title=f"Murmur Proof — {proof.run_id}", extra_css=extra_css)
    shell = hud_shell_start(
        brand="murmur",
        run_line=f"{escape(proof.contract.task.id)} · {escape(proof.run_id)} · contract proof",
        quote="Evidence before fix. Evidence after fix.",
    )
    body = f"""
<section class="hud-widget">
  <div class="hud-widget__hd">contract status</div>
  <div class="hud-widget__bd">
    <p class="proof-verdict">verdict · {escape(proof.verdict)}</p>
    {workflow_link}
    {stats_html}
    {evidence_html}
    <div class="kv"><span class="k">model calls</span><span>{proof.model_calls}</span></div>
    <div class="kv"><span class="k">tool calls</span><span>{proof.tool_calls}</span></div>
    <div class="kv"><span class="k">cost</span><span>${proof.cost_usd:.4f}</span></div>
    <div class="proof-actions">
      <button type="button" id="proof-diff-open">View diff</button>
      <button type="button" id="proof-attempts-open">View attempts</button>
      <button type="button" id="proof-summary-open">Agent summary</button>
      <button type="button" id="proof-failures-open">Failures</button>
    </div>
  </div>
</section>
"""
    return head + "<body>" + shell + body + document_close(extra_script=script)


def render_attempts_fan_html(proof: ProofPackage) -> str:
    """One lane per attempt: who passed, who failed, who won, and why."""

    rank_method = str(proof.rank.get("method", "")) if proof.rank else ""
    rank_reason = str(proof.rank.get("rationale", "")) if proof.rank else ""
    lanes: list[str] = []
    for attempt in proof.attempts:
        attempt_id = str(attempt.get("attempt_id", "?"))
        verification = attempt.get("verification", {})
        tests = attempt.get("test_results", [])
        last_test = tests[-1] if tests else {}
        passed = bool(attempt.get("passed"))
        is_winner = attempt_id == proof.winner_id
        classes = "fan-lane" + (" fan-lane--winner" if is_winner else "")
        status = "pass" if passed else "fail"
        crown = '<span class="fan-lane__crown">winner</span>' if is_winner else ""
        lanes.append(
            f'<div class="{classes}">'
            f'<span class="fan-lane__id">{escape(attempt_id)}</span>'
            f'<span class="fan-lane__status fan-lane__status--{status}">{status}</span>'
            f'<span class="fan-lane__meta">diff {verification.get("diff_lines", 0)} lines'
            f' · {len(verification.get("changed_files", []))} files'
            f' · test: {escape(str(last_test.get("summary", "not run")))}</span>'
            f"{crown}"
            f"</div>"
        )
    if not lanes:
        lanes.append('<div class="fan-lane"><span class="fan-lane__meta">no attempts</span></div>')

    decision_line = ""
    if rank_method:
        decision_line = (
            f'<p class="fan-decision">selected by <strong>{escape(rank_method)}</strong>'
            f"{' — ' + escape(rank_reason) if rank_reason else ''}</p>"
        )

    extra_css = """
.fan-decision { font: 12px var(--mono); margin: 0 0 14px; color: var(--muted); }
.fan-lane {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 12px; margin-bottom: 6px;
  border: 1px solid var(--line); background: var(--panel-solid);
}
.fan-lane--winner { border-color: #146b3a; box-shadow: inset 3px 0 0 #146b3a; }
.fan-lane__id { font: 600 12px var(--mono); min-width: 110px; }
.fan-lane__status { font: 11px var(--mono); text-transform: uppercase; letter-spacing: 0.08em; }
.fan-lane__status--pass { color: #146b3a; }
.fan-lane__status--fail { color: var(--accent); }
.fan-lane__meta { font: 11px var(--mono); color: var(--muted); flex: 1; }
.fan-lane__crown {
  font: 600 10px var(--mono); text-transform: uppercase; letter-spacing: 0.1em;
  color: #146b3a; border: 1px solid #146b3a; padding: 2px 8px;
}
"""
    head = document_head(title=f"Murmur attempts — {proof.run_id}", extra_css=extra_css)
    shell = hud_shell_start(
        brand="murmur",
        run_line=f"{escape(proof.run_id)} · attempt fan",
        quote="One task. Many attempts. Keep the proven one.",
    )
    body = f"""
<section class="hud-widget">
  <div class="hud-widget__hd">attempts ({len(proof.attempts)})</div>
  <div class="hud-widget__bd">
    {decision_line}
    {"".join(lanes)}
  </div>
</section>
"""
    return head + "<body>" + shell + body + document_close()


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _attempt_lines(proof: ProofPackage) -> list[str]:
    if not proof.attempts:
        return ["none"]
    lines: list[str] = []
    for attempt in proof.attempts:
        verification = attempt.get("verification", {})
        tests = attempt.get("test_results", [])
        last_test = tests[-1] if tests else {}
        status = "pass" if attempt.get("passed") else "fail"
        lines.append(
            "- "
            f"{attempt.get('attempt_id')}: {status}; "
            f"diff lines {verification.get('diff_lines', 0)}; "
            f"test {last_test.get('summary', 'not run')}"
        )
    return lines
