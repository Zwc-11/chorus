"""Standalone reliability, divergence, judgment, and diagnosis report."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from html import escape
from pathlib import Path

from murmur.core.divergence import DivergenceOverlay, build_divergence_overlay, signature_label
from murmur.core.events import Event
from murmur.core.types import RunResult
from murmur.report.ui_theme import (
    document_close,
    document_head,
    hud_shell_end,
    hud_shell_start,
)

_FILL = {
    "pass": "#2d2d2a",
    "fail": "#e8192a",
    "error": "#c45c00",
    "converged": "#3d3d38",
    "diverged": "#8a6a20",
    "failed": "#a01020",
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
    failure_index = _failure_index(result, trace_href)

    run_line = (
        f"{escape(result.task_id)} · {escape(result.run_id)} · "
        f"n={len(result.trajectories)} · seed-driven fan"
    )
    head = document_head(title=f"Chorus — {result.task_id}")
    shell = hud_shell_start(
        brand="chorus",
        run_line=run_line,
        quote="People always look at the sun. Then they can't see anything.",
    )
    cards = "\n".join(
        [
            _metric_card(
                "pass@1",
                f"{metrics.pass_at_1:.2f}",
                f"95% CI {lower:.2f}–{upper:.2f}",
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
                f"{failures - errors} fail · {errors} error",
                clickable=bool(failure_index),
                data_attrs=' data-chorus-failures-summary="1"' if failure_index else "",
            ),
        ]
    )

    body = (
        f"{shell}"
        '<section class="cards">'
        f"{cards}"
        "</section>"
        '<section class="hud-widget">'
        '<div class="hud-widget__hd">pass^k decay · projected vs empirical</div>'
        '<div class="hud-widget__bd">'
        '<div class="legend">'
        '<span><span class="dot dot--line"></span>projected (i.i.d.)</span>'
        '<span><span class="dot dot--accent"></span>empirical (unbiased)</span>'
        "</div>"
        f"{_decay_curve(result)}"
        "</div></section>"
        f"{_overlay_section(overlay, trace_href)}"
        '<section class="cols">'
        f"{_judgment_panel(result)}"
        f"{_failure_panel(result, failure_index)}"
        "</section>"
        f"{hud_shell_end()}"
        f'<script type="application/json" id="chorus-failure-data">{json.dumps(failure_index)}</script>'
        f"<script>{_fan_interaction_js()}</script>"
    )
    return head + body + document_close()


def _failure_index(result: RunResult, trace_href: str | None) -> dict[str, list[dict[str, str]]]:
    by_class: dict[str, list[dict[str, str]]] = defaultdict(list)
    for index, item in enumerate(result.trajectories):
        if item.outcome == "pass":
            continue
        label = item.failure_class or item.outcome
        link = ""
        if trace_href:
            link = f"{trace_href}#{item.trajectory_id}"
        by_class[label].append(
            {
                "lane": f"#{index + 1:02d}",
                "outcome": item.outcome,
                "failure_class": item.failure_class or "—",
                "trajectory_id": item.trajectory_id,
                "trace_href": link,
            }
        )
    return dict(by_class)


def _fan_interaction_js() -> str:
    return r"""
(function () {
  const dataEl = document.getElementById("chorus-failure-data");
  const failureData = dataEl ? JSON.parse(dataEl.textContent || "{}") : {};

  document.querySelectorAll("[data-failure-class]").forEach((seg) => {
    seg.addEventListener("click", () => {
      const label = seg.getAttribute("data-failure-class");
      const items = failureData[label] || [];
      let html = '<p class="lead">' + label + ' · ' + items.length + ' trajectory(ies)</p><ul class="modal-list">';
      if (!items.length) {
        html += "<li>none</li>";
      } else {
        items.forEach((item) => {
          const link = item.trace_href
            ? '<a href="' + item.trace_href + '">' + item.lane + " · trace</a>"
            : item.lane;
          html += "<li>" + link + " · " + item.outcome + " · " + item.failure_class + "</li>";
        });
      }
      html += "</ul>";
      chorusOpenModal("diagnosis · " + label, html);
    });
  });

  document.querySelectorAll("[data-chorus-cell]").forEach((cell) => {
    cell.addEventListener("click", (e) => {
      e.stopPropagation();
      const state = cell.getAttribute("data-state") || "";
      const step = cell.getAttribute("data-step") || "";
      const lane = cell.getAttribute("data-lane") || "";
      const sig = cell.getAttribute("data-signature") || "";
      const html =
        '<div class="modal-kv"><span class="k">lane</span><span>' + lane + "</span></div>" +
        '<div class="modal-kv"><span class="k">step</span><span>s' + step + "</span></div>" +
        '<div class="modal-kv"><span class="k">state</span><span>' + state + "</span></div>" +
        '<div class="modal-kv"><span class="k">signature</span><span>' + sig + "</span></div>";
      chorusOpenModal("overlay cell", html);
    });
  });

  const summary = document.querySelector("[data-chorus-failures-summary]");
  if (summary) {
    summary.addEventListener("click", () => {
      let html = '<p class="lead">click a diagnosis segment for per-class trajectories</p>';
      const labels = Object.keys(failureData).sort((a, b) => failureData[b].length - failureData[a].length);
      if (!labels.length) html = '<p class="lead">all trajectories passed</p>';
      labels.forEach((label) => {
        html += '<div class="modal-kv"><span class="k">' + label + "</span><span>" + failureData[label].length + " run(s)</span></div>";
      });
      chorusOpenModal("failure breakdown", html);
    });
  }
})();
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


def _metric_card(
    label: str,
    value: str,
    subline: str,
    *,
    clickable: bool = False,
    data_attrs: str = "",
) -> str:
    cls = "card card--clickable" if clickable else "card"
    return (
        f'<div class="{cls}"{data_attrs}>'
        f'<div class="label">{escape(label)}</div>'
        f'<div class="num">{escape(value)}</div>'
        f'<div class="sub">{escape(subline)}</div>'
        "</div>"
    )


def _decay_curve(result: RunResult) -> str:
    curve = result.metrics.curve
    if not curve:
        return '<p class="sub">no reliability curve yet</p>'
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
        'r="3.5" fill="var(--accent)" stroke="var(--accent)" stroke-width="1"/>'
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
        return (
            '<section class="hud-widget"><div class="hud-widget__hd">divergence overlay</div>'
            '<div class="hud-widget__bd"><p class="sub">no event log available</p></div></section>'
        )
    if not overlay.steps:
        return (
            '<section class="hud-widget"><div class="hud-widget__hd">divergence overlay</div>'
            '<div class="hud-widget__bd"><p class="sub">no runs yet — murmur run … --n 30</p></div></section>'
        )

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
            "<title>click to isolate lanes that split here</title></rect>"
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
        '<p class="sub" style="margin:0 0 10px">low confidence: fewer than 5 trajectories</p>'
        if overlay.low_confidence
        else ""
    )
    return (
        '<section class="hud-widget">'
        '<div class="hud-widget__hd">divergence overlay · agreement strip + lanes</div>'
        '<div class="hud-widget__bd t-skel" data-chorus-reveal>'
        '<div class="t-skel-skeleton" aria-hidden="true"></div>'
        '<div class="t-skel-content">'
        f"{confidence}"
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="divergence overlay">'
        f'<rect class="overlay-bg" x="0" y="0" width="{width}" height="{height}" rx="2"/>'
        f"{band}{bars}{step_labels}{labels}{cells}"
        '<text class="axis" x="42" y="76">agree</text>'
        f'<text class="axis" x="42" y="{height - 13}">'
        "converged / diverged / failed / inactive</text>"
        "</svg></div></div></section>"
    )


def _agreement_bar(x: int, value: float | None, cell_w: int, base: int = 92) -> str:
    if value is None:
        return f'<rect class="inactive" x="{x}" y="{base - 24}" width="{cell_w - 8}" height="24"/>'
    height = max(3, int(value * 40))
    y = base - height
    fill = "#3d3d38" if value >= 1.0 else "#c45c00"
    return f'<rect x="{x}" y="{y}" width="{cell_w - 8}" height="{height}" rx="1" fill="{fill}"/>'


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
    sig = escape(signature_label(cell.signature))
    lane = f"#{row_index + 1:02d}"
    data = (
        f'data-chorus-cell="1" data-state="{escape(cell.state)}" '
        f'data-step="{cell.step}" data-lane="{lane}" data-signature="{sig}"'
    )
    if cell.state == "inactive":
        rect = (
            f'<rect class="cell inactive" {data} x="{x}" y="{y}" '
            f'width="{cell_w - 8}" height="20"/>'
        )
    else:
        rect = (
            f'<rect class="cell" {data} x="{x}" y="{y}" width="{cell_w - 8}" height="20" '
            f'fill="{_FILL.get(cell.state, "#334155")}"/>'
        )
    outline = (
        f'<rect class="div-outline" x="{x}" y="{y}" width="{cell_w - 8}" height="20" rx="2"/>'
        if div == cell.step
        else ""
    )
    classes = ["cellwrap"]
    if div == cell.step and not cell.in_majority:
        classes.append("split")
    return f'<g class="{" ".join(classes)}">{rect}{outline}</g>'


def _judgment_panel(result: RunResult) -> str:
    summary = result.judge_summary
    if not summary:
        return (
            '<div class="hud-widget"><div class="hud-widget__hd">judgment</div>'
            '<div class="hud-widget__bd"><p class="sub">not run</p></div></div>'
        )
    baseline = float(summary.get("baseline_cost_usd", 0.0))
    cascade = float(summary.get("cascade_cost_usd", 0.0))
    ratio = float(summary.get("cost_ratio", 0.0))
    tier_hits = summary.get("tier_hits", {})
    return (
        '<div class="hud-widget">'
        '<div class="hud-widget__hd">judgment cascade</div>'
        '<div class="hud-widget__bd">'
        '<p class="sub">synthetic-validated — real accuracy-parity lands in Phase 5</p>'
        f'<div class="kv"><span class="k">cost ratio</span><span>{ratio:.2f}</span></div>'
        f'<div class="kv"><span class="k">judge-every-run</span><span>${baseline:.4f}</span></div>'
        f'<div class="kv"><span class="k">cascade</span><span>${cascade:.4f}</span></div>'
        '<div class="kv"><span class="k">tier hits</span>'
        f"<span>{escape(str(tier_hits))}</span></div>"
        f'<div class="kv"><span class="k">escalations</span><span>{result.escalations}</span></div>'
        "</div></div>"
    )


def _failure_panel(result: RunResult, failure_index: dict[str, list[dict[str, str]]]) -> str:
    counts = Counter(
        item.failure_class or item.outcome for item in result.trajectories if item.outcome != "pass"
    )
    if not counts:
        rows = '<p class="sub">all trajectories passed</p>'
    else:
        total = sum(counts.values())
        segs = "".join(
            '<span class="seg" data-failure-class="'
            f'{escape(label)}" style="width:{(count / total) * 100:.1f}%;'
            f'background:{_class_color(label)}" title="{escape(label)}: {count}"></span>'
            for label, count in counts.items()
        )
        items = "".join(
            f'<div class="kv"><span class="k">{escape(label)}</span><span>{count}</span></div>'
            for label, count in counts.items()
        )
        rows = (
            f'<p class="sub" style="margin:0 0 8px">click a segment for trajectory list</p>'
            f'<div class="bar">{segs}</div>{items}'
        )
    return (
        '<div class="hud-widget">'
        '<div class="hud-widget__hd">diagnosis</div>'
        f'<div class="hud-widget__bd">{rows}</div></div>'
    )


def _class_color(label: str) -> str:
    return {
        "tool_error": "#c45c00",
        "schema_mismatch": "#e8192a",
        "context_drift": "#5a5a56",
        "nondeterministic_loop": "#3d3d38",
        "budget_exceeded": "#a01020",
        "timeout": "#8a4020",
        "contract_violation": "#e8192a",
        "unknown": "#8a8a84",
        "fail": "#e8192a",
        "error": "#c45c00",
    }.get(label, "#8a8a84")
