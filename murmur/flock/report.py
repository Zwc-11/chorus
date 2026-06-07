"""Render a run into a readable markdown trace.

The trace shows the plan as a mermaid DAG, a per-node table (operator, trust,
artifacts produced, model calls, status), the cost totals against the budget, and the
final synthesized output. GitHub renders the mermaid block, so a trace pasted into a PR
is a picture of exactly what the self-written workflow did.
"""

from __future__ import annotations

from murmur.flock.ir import WorkflowPlan
from murmur.flock.scheduler import RunReport

_OP_SHAPE = {  # mermaid node shapes per operator, so the graph reads at a glance
    "map": ("[", "]"),
    "reduce": ("[[", "]]"),
    "tournament": ("{{", "}}"),
    "verify": ("([", "])"),
    "filter": ("[/", "/]"),
    "classify": ("([", "])"),
    "loop": ("[(", ")]"),
}


def render_mermaid(plan: WorkflowPlan) -> str:
    """A mermaid ``flowchart`` of the plan's DAG (sources + operator nodes)."""

    lines = ["```mermaid", "flowchart LR"]
    for src in plan.sources:
        lines.append(f'  {src}[/"{src}"/]')
    for node in plan.nodes:
        lo, hi = _OP_SHAPE.get(node.op, ("[", "]"))
        label = f"{node.id}<br/>{node.op}"
        if node.trust == "untrusted":
            label += " 🔒"
        lines.append(f"  {node.id}{lo}\"{label}\"{hi}")
    for node in plan.nodes:
        for dep in node.inputs:
            lines.append(f"  {dep} --> {node.id}")
    lines.append("```")
    return "\n".join(lines)


def render_run_report(report: RunReport, *, plan: WorkflowPlan | None = None) -> str:
    """Render a full markdown trace for *report* (DAG included when *plan* is given)."""

    trust_of = {n.id: n.trust for n in plan.nodes} if plan else {}
    out: list[str] = [f"# Murmur flock run — {report.goal}", ""]

    if plan is not None:
        out += [render_mermaid(plan), ""]

    out += [
        "| node | op | trust | artifacts | calls | status |",
        "|---|---|---|---|---|---|",
    ]
    for nid, result in report.results.items():
        status = "ok" if result.ok else f"ERROR: {result.error}"
        trust = trust_of.get(nid, "")
        out.append(
            f"| {nid} | {result.op} | {trust} | {len(result.output)} | {result.calls} | {status} |"
        )

    budget = f"/{plan.budget_tokens}" if plan is not None else ""
    out += [
        "",
        f"**model calls:** {report.model_calls}  "
        f"**tokens:** {report.spent_tokens}{budget}  "
        f"**cost:** ${report.spent_cost_usd:.4f}  "
        f"**status:** {'ok ✅' if report.ok else 'failed ❌'}",
        "",
        "## final",
    ]
    for artifact in report.final:
        out += [f"### {artifact.id}", "", "```", artifact.content[:2000], "```", ""]
    return "\n".join(out)
