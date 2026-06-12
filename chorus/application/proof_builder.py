"""Render contract-first proof packages."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from chorus.domain.proof import ProofPackage
from chorus.domain.workflow import WorkflowPlan
from chorus.report.ui_theme import (
    document_close,
    document_head,
    hud_shell_start,
)


def write_proof_package(proof: ProofPackage, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "diff.patch").write_text(proof.diff, encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(proof.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
    if proof.attempts:
        (run_dir / "attempts.json").write_text(
            json.dumps(proof.attempts, indent=2, default=str),
            encoding="utf-8",
        )
    (run_dir / "proof.md").write_text(render_proof_markdown(proof), encoding="utf-8")

    workflow_href: str | None = None
    workflow_path = run_dir / "workflow.yaml"
    if workflow_path.is_file():
        from chorus.report.murmur_workflow_html import write_murmur_workflow_html

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


def render_proof_markdown(proof: ProofPackage) -> str:
    v = proof.verification
    lines = [
        "# Chorus PR Proof",
        "",
        f"## Verdict: {proof.verdict.upper()}",
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

    workflow_link = ""
    if workflow_href:
        workflow_link = (
            f'<p class="proof-links">'
            f'<a href="{escape(workflow_href)}">Open workflow tree</a>'
            f"</p>"
        )

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

    head = document_head(title=f"Chorus PR Proof — {proof.run_id}", extra_css=extra_css)
    shell = hud_shell_start(
        brand="chorus",
        run_line=f"{escape(proof.contract.task.id)} · {escape(proof.run_id)} · contract proof",
        quote="Evidence before fix. Evidence after fix.",
    )
    body = f"""
<section class="hud-widget">
  <div class="hud-widget__hd">contract status</div>
  <div class="hud-widget__bd">
    <p class="proof-verdict">verdict · {escape(proof.verdict)}</p>
    {workflow_link}
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
