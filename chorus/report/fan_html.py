"""Standalone reliability, divergence, judgment, and diagnosis report."""

from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path

from chorus.core.divergence import DivergenceOverlay, build_divergence_overlay, signature_label
from chorus.core.events import Event
from chorus.core.types import RunResult

_FILL = {
    "pass": "#22c55e",
    "fail": "#ef4444",
    "error": "#f59e0b",
    "converged": "#14532d",
    "diverged": "#854d0e",
    "failed": "#7f1d1d",
    "inactive": "transparent",
}


def render_fan_html(
    result: RunResult,
    *,
    events: list[Event] | None = None,
    trace_href: str | None = "trace.html",
) -> str:
    metrics = result.metrics
    passes = sum(1 for item in result.trajectories if item.outcome == "pass")
    failures = len(result.trajectories) - passes
    errors = sum(1 for item in result.trajectories if item.outcome == "error")
    lower, upper = metrics.wilson_ci
    overlay = build_divergence_overlay(events) if events else None

    cards = "\n".join(
        [
            _metric_card(
                "pass@1",
                f"{metrics.pass_at_1:.2f}",
                f"95% CI {lower:.2f}-{upper:.2f}",
            ),
            _metric_card(
                "pass^k projected",
                f"{metrics.pass_at_k:.3f}",
                f"k = {metrics.k}, i.i.d.",
            ),
            _metric_card(
                "variance",
                f"{metrics.variance:.2f}",
                f"across {len(result.trajectories)} runs",
            ),
            _metric_card(
                "failures",
                f"{failures} / {len(result.trajectories)}",
                f"{failures - errors} fail - {errors} error",
            ),
        ]
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Chorus reliability - {escape(result.task_id)}</title>
<style>
  :root {{
    --bg:#050505; --panel:#242423; --panel2:#30302e; --line:#5e5e59;
    --txt:#f5f5f2; --muted:#b8b8b2; --dim:#8b8b84; --blue:#2f8ee5;
    --green:#10b981; --warn:#f59e0b; --err:#ef4444;
    --mono: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    --sans: ui-sans-serif,system-ui,Segoe UI,Roboto,sans-serif;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--txt); font-family:var(--sans); }}
  main {{ max-width:1120px; margin:0 auto; padding:26px; }}
  h1 {{ margin:0 0 16px; font-size:16px; font-weight:600; color:var(--muted); }}
  .cards {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:24px; }}
  .card {{ background:var(--panel); border-radius:8px; padding:20px 24px; min-height:126px; }}
  .label {{ color:var(--muted); font-size:13px; text-transform:lowercase; }}
  .num {{ font:700 34px/1.1 var(--mono); margin-top:12px; }}
  .sub {{ color:var(--muted); font:600 13px/1.3 var(--mono); margin-top:6px; }}
  .zone {{ margin-top:28px; }}
  .legend {{ display:flex; gap:28px; font-weight:700; color:var(--muted); margin:0 0 10px; }}
  .dot {{ width:11px; height:11px; border-radius:50%; display:inline-block; margin-right:8px; }}
  svg {{ width:100%; height:auto; display:block; }}
  .axis {{ fill:var(--muted); font:12px var(--mono); }}
  .grid {{ stroke:var(--line); stroke-width:1; opacity:.55; }}
  .projected {{ fill:none; stroke:var(--blue); stroke-width:3; }}
  .empirical {{ fill:none; stroke:var(--green); stroke-width:3; stroke-dasharray:5 6; }}
  .shade {{ fill:#7f1d1d; opacity:.7; }}
  .note {{ fill:#fca5a5; font:12px var(--mono); }}
  .overlay-bg {{ fill:var(--panel2); stroke:var(--line); stroke-width:1; }}
  .lane-label {{ fill:var(--muted); font:13px var(--mono); }}
  .cell {{ stroke:#3f3f3a; stroke-width:1; rx:4; }}
  .inactive {{ fill:transparent; stroke:#6b6b65; stroke-dasharray:5 4; }}
  .div-band {{ fill:#f59e0b; opacity:.18; }}
  .div-toggle {{ cursor:pointer; }}
  .div-outline {{ fill:none; stroke:#f59e0b; stroke-width:2; }}
  .split-only .cellwrap:not(.split) {{ opacity:.18; }}
  .panel {{ background:var(--panel); border-radius:8px; padding:16px; }}
  .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
  .bar {{ height:10px; background:#171717; border-radius:3px; overflow:hidden; display:flex; }}
  .seg {{ height:10px; }}
  .kv {{ display:grid; grid-template-columns:170px 1fr; gap:8px;
         font:13px var(--mono); margin:7px 0; }}
  .k {{ color:var(--muted); }}
  a {{ color:inherit; text-decoration:none; }}
  @media (max-width: 820px) {{
    .cards, .cols {{ grid-template-columns:1fr; }}
    main {{ padding:16px; }}
  }}
</style>
</head>
<body>
<main>
  <h1>Chorus - {escape(result.task_id)} - {escape(result.run_id)}</h1>
  <section class="cards">{cards}</section>
  <section class="zone">
    <div class="legend">
      <span><span class="dot" style="background:var(--blue)"></span>projected (i.i.d. model)</span>
      <span><span class="dot" style="background:var(--green)"></span>empirical (unbiased)</span>
    </div>
    {_decay_curve(result)}
  </section>
  <section class="zone">
    {_overlay_section(overlay, trace_href)}
  </section>
  <section class="zone cols">
    {_judgment_panel(result)}
    {_failure_panel(result)}
  </section>
</main>
</body>
</html>
"""


def write_fan_html(
    result: RunResult,
    path: Path | str,
    *,
    events: list[Event] | None = None,
    trace_href: str | None = "trace.html",
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_fan_html(result, events=events, trace_href=trace_href), encoding="utf-8")
    return out


def _metric_card(label: str, value: str, subline: str) -> str:
    return (
        '<div class="card">'
        f'<div class="label">{escape(label)}</div>'
        f'<div class="num">{escape(value)}</div>'
        f'<div class="sub">{escape(subline)}</div>'
        "</div>"
    )


def _decay_curve(result: RunResult) -> str:
    curve = result.metrics.curve
    if not curve:
        return '<div class="panel sub">no reliability curve yet</div>'
    width = 1000
    height = 310
    left = 70
    top = 24
    chart_w = 880
    chart_h = 230
    max_k = max(point.k for point in curve)
    passes = sum(1 for item in result.trajectories if item.outcome == "pass")

    def xy(k: int, value: float) -> tuple[float, float]:
        x = left + ((k - 1) / max(max_k - 1, 1)) * chart_w
        y = top + (1 - max(0.0, min(1.0, value))) * chart_h
        return x, y

    projected = " ".join(f"{x:.1f},{y:.1f}" for x, y in (xy(p.k, p.projected) for p in curve))
    empirical = " ".join(f"{x:.1f},{y:.1f}" for x, y in (xy(p.k, p.empirical) for p in curve))
    unsupported_x, _ = xy(min(passes + 1, max_k), 0.0)
    shade = ""
    if passes < max_k:
        shade_w = left + chart_w - unsupported_x
        shade = (
            f'<rect class="shade" x="{unsupported_x:.1f}" y="{top}" '
            f'width="{shade_w:.1f}" height="{chart_h}"/>'
            f'<text class="note" x="{unsupported_x + 10:.1f}" y="{top + 20}">'
            "data cannot support</text>"
        )
    points = "\n".join(
        f'<circle cx="{xy(p.k, p.empirical)[0]:.1f}" cy="{xy(p.k, p.empirical)[1]:.1f}" '
        'r="4" fill="var(--green)"/>'
        for p in curve
    )
    y_ticks = "\n".join(
        f'<line class="grid" x1="{left}" y1="{top + chart_h * (1 - v):.1f}" '
        f'x2="{left + chart_w}" y2="{top + chart_h * (1 - v):.1f}"/>'
        f'<text class="axis" x="24" y="{top + chart_h * (1 - v) + 4:.1f}">{v:.2f}</text>'
        for v in (0.0, 0.25, 0.5, 0.75, 1.0)
    )
    x_labels = "\n".join(
        f'<text class="axis" x="{xy(p.k, 0)[0] - 6:.1f}" y="{top + chart_h + 28}">k{p.k}</text>'
        for p in curve
        if p.k == 1 or p.k == max_k or p.k % max(1, max_k // 6) == 0
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        'aria-label="pass to the k reliability decay curve">'
        f"{y_ticks}{shade}"
        f'<polyline class="projected" points="{projected}"/>'
        f'<polyline class="empirical" points="{empirical}"/>'
        f"{points}{x_labels}</svg>"
    )


def _overlay_section(overlay: DivergenceOverlay | None, trace_href: str | None) -> str:
    if overlay is None:
        return '<div class="panel sub">no event log available for divergence overlay</div>'
    if not overlay.steps:
        return '<div class="panel sub">no runs yet - chorus run ... --n 30</div>'

    # Vertical bands, top to bottom: agreement bars (grow up to bar_base), the
    # step labels, the divergence caption, then the cell grid. Keeping each in its
    # own row stops the caption colliding with the bars and the first lanes.
    cell_w = 48
    cell_h = 28
    left = 110
    bar_base = 92
    grid_top = 142
    n_rows = len(overlay.trajectory_ids)
    n_steps = len(overlay.steps)
    grid_bottom = grid_top + n_rows * cell_h
    width = left + n_steps * cell_w + 40
    height = grid_bottom + 34
    div = overlay.divergence_step
    band = ""
    if div is not None:
        x = left + div * cell_w
        band = (
            '<rect class="div-band div-toggle" '
            "onclick=\"document.body.classList.toggle('split-only')\" "
            f'x="{x}" y="48" width="{cell_w - 6}" height="{grid_bottom - 48}">'
            "<title>click to isolate the lanes that split here</title></rect>"
            f'<text class="note" x="{x}" y="128">↑ divergence · step {div}</text>'
        )
    bars = "\n".join(
        _agreement_bar(left + i * cell_w, value, cell_w, bar_base)
        for i, value in enumerate(overlay.agreement)
    )
    step_labels = "\n".join(
        f'<text class="axis" x="{left + i * cell_w + 14}" y="110">s{step}</text>'
        for i, step in enumerate(overlay.steps)
    )
    row_map = {trajectory_id: index for index, trajectory_id in enumerate(overlay.trajectory_ids)}
    cells = "\n".join(
        _overlay_cell(cell, left, grid_top, cell_w, cell_h, div, row_map) for cell in overlay.cells
    )
    labels = "\n".join(
        _lane_label(trajectory_id, index, grid_top + index * cell_h + 15, trace_href)
        for index, trajectory_id in enumerate(overlay.trajectory_ids)
    )
    confidence = (
        '<div class="sub">low confidence: fewer than 5 trajectories</div>'
        if overlay.low_confidence
        else ""
    )
    return (
        '<div class="panel">'
        f"{confidence}"
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="divergence overlay">'
        f'<rect class="overlay-bg" x="0" y="0" width="{width}" height="{height}" rx="8"/>'
        f"{band}{bars}{step_labels}{labels}{cells}"
        '<text class="axis" x="42" y="76">agree</text>'
        f'<text class="axis" x="42" y="{height - 13}">'
        "converged / diverged / failed / inactive</text>"
        "</svg></div>"
    )


def _agreement_bar(x: int, value: float | None, cell_w: int, base: int = 92) -> str:
    if value is None:
        return f'<rect class="inactive" x="{x}" y="{base - 24}" width="{cell_w - 8}" height="24"/>'
    height = max(3, int(value * 40))
    y = base - height
    fill = "#10b981" if value >= 1.0 else "#f59e0b"
    return f'<rect x="{x}" y="{y}" width="{cell_w - 8}" height="{height}" rx="3" fill="{fill}"/>'


def _lane_label(trajectory_id: str, index: int, y: int, trace_href: str | None) -> str:
    label = f"#{index + 1:02d}"
    if trace_href is None:
        return f'<text class="lane-label" x="42" y="{y}">{label}</text>'
    href = f"{trace_href}#{escape(trajectory_id)}"
    return f'<a href="{href}"><text class="lane-label" x="42" y="{y}">{label}</text></a>'


def _overlay_cell(
    cell,
    left: int,
    top: int,
    cell_w: int,
    cell_h: int,
    div: int | None,
    row_map: dict[str, int],
) -> str:
    x = left + cell.step * cell_w
    row_index = row_map[cell.trajectory_id]
    y = top + row_index * cell_h
    title = escape(signature_label(cell.signature))
    if cell.state == "inactive":
        rect = f'<rect class="cell inactive" x="{x}" y="{y}" width="{cell_w - 8}" height="20"/>'
    else:
        rect = (
            f'<rect class="cell" x="{x}" y="{y}" width="{cell_w - 8}" height="20" '
            f'fill="{_FILL.get(cell.state, "#334155")}"/>'
        )
    outline = (
        f'<rect class="div-outline" x="{x}" y="{y}" width="{cell_w - 8}" height="20" rx="4"/>'
        if div == cell.step
        else ""
    )
    classes = ["cellwrap"]
    if div == cell.step and not cell.in_majority:
        classes.append("split")
    return f'<g class="{" ".join(classes)}"><title>{title}</title>{rect}{outline}</g>'


def _judgment_panel(result: RunResult) -> str:
    summary = result.judge_summary
    if not summary:
        return (
            '<div class="panel"><div class="label">judgment</div>'
            '<div class="sub">not run</div></div>'
        )
    baseline = float(summary.get("baseline_cost_usd", 0.0))
    cascade = float(summary.get("cascade_cost_usd", 0.0))
    ratio = float(summary.get("cost_ratio", 0.0))
    tier_hits = summary.get("tier_hits", {})
    return (
        '<div class="panel">'
        '<div class="label">judgment cascade</div>'
        '<div class="sub">synthetic-validated &mdash; real accuracy-parity number'
        " lands in Phase 5</div>"
        f'<div class="kv"><span class="k">cost ratio</span><span>{ratio:.2f}</span></div>'
        f'<div class="kv"><span class="k">judge-every-run</span><span>${baseline:.4f}</span></div>'
        f'<div class="kv"><span class="k">cascade</span><span>${cascade:.4f}</span></div>'
        '<div class="kv"><span class="k">tier hits</span>'
        f"<span>{escape(str(tier_hits))}</span></div>"
        f'<div class="kv"><span class="k">escalations</span><span>{result.escalations}</span></div>'
        "</div>"
    )


def _failure_panel(result: RunResult) -> str:
    counts = Counter(
        item.failure_class or item.outcome for item in result.trajectories if item.outcome != "pass"
    )
    if not counts:
        rows = '<div class="sub">all trajectories passed</div>'
    else:
        total = sum(counts.values())
        segs = "".join(
            '<span class="seg" style="'
            f"width:{(count / total) * 100:.1f}%;"
            f'background:{_class_color(label)}"></span>'
            for label, count in counts.items()
        )
        items = "".join(
            f'<div class="kv"><span class="k">{escape(label)}</span><span>{count}</span></div>'
            for label, count in counts.items()
        )
        rows = f'<div class="bar">{segs}</div>{items}'
    return f'<div class="panel"><div class="label">diagnosis</div>{rows}</div>'


def _class_color(label: str) -> str:
    return {
        "tool_error": "#f59e0b",
        "schema_mismatch": "#ef4444",
        "context_drift": "#a78bfa",
        "nondeterministic_loop": "#38bdf8",
        "budget_exceeded": "#fb7185",
        "timeout": "#f97316",
        "contract_violation": "#dc2626",
        "unknown": "#64748b",
    }.get(label, "#64748b")
