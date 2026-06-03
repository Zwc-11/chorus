"""Render contract-first proof packages."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from chorus.domain.proof import ProofPackage


def write_proof_package(proof: ProofPackage, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "diff.patch").write_text(proof.diff, encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(proof.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
    (run_dir / "proof.md").write_text(render_proof_markdown(proof), encoding="utf-8")
    (run_dir / "report.html").write_text(render_proof_html(proof), encoding="utf-8")


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
        "## Final Diff",
        "```diff",
        proof.diff,
        "```",
    ]
    return "\n".join(lines) + "\n"


def render_proof_html(proof: ProofPackage) -> str:
    md = render_proof_markdown(proof)
    status = "pass" if proof.verdict == "pass" else "fail"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Chorus PR Proof</title>
<style>
body {{
  font-family: "Segoe UI", sans-serif;
  margin: 0;
  padding: 32px;
  background: #e4e4e0;
  color: #0a0a0a;
}}
main {{ max-width: 980px; margin: 0 auto; }}
.badge {{
  display: inline-block;
  padding: 6px 10px;
  border: 1px solid #0a0a0a;
  font-family: monospace;
}}
.pass {{ color: #146b3a; }}
.fail {{ color: #e8192a; }}
pre {{
  overflow: auto;
  padding: 16px;
  background: rgba(255,255,255,.65);
  border: 1px solid rgba(0,0,0,.16);
}}
</style>
</head>
<body>
<main>
<p class="badge {status}">verdict: {escape(proof.verdict.upper())}</p>
<pre>{escape(md)}</pre>
</main>
</body>
</html>
"""


def _yes(value: bool) -> str:
    return "yes" if value else "no"
