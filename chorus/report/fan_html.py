"""Trajectory-fan visualizer (standalone HTML/SVG).

This file writes a single self-contained HTML file — no app shell, no external
assets — that renders the trajectory fan as SVG. It is the screenshot-able
artifact: open it in any browser to see the ``N`` lanes for a run, color-coded by
outcome, with the ``pass@1`` vs ``pass^k`` reliability gap drawn to scale.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from chorus.core.types import RunResult

_FILL = {"pass": "#22c55e", "fail": "#ef4444", "error": "#f59e0b"}

_ROW_H = 26
_TOP = 150
_TRACK_X = 150
_TRACK_W = 560
_AXIS_HINT = "each lane = one independent run · dot at right = outcome"


def _lane_rows(result: RunResult) -> str:
    rows: list[str] = []
    for index, t in enumerate(result.trajectories):
        y = _TOP + index * _ROW_H
        fill = _FILL.get(t.outcome, "#94a3b8")
        label = escape(t.trajectory_id.rsplit("_t", 1)[-1].rjust(2, "0"))
        note = escape(t.output if t.outcome == "pass" else (t.failure_class or t.outcome))
        rows.append(
            f'<text x="{_TRACK_X - 12}" y="{y + 5}" class="lbl" text-anchor="end">t{label}</text>'
            f'<line x1="{_TRACK_X}" y1="{y}" x2="{_TRACK_X + _TRACK_W}" y2="{y}" class="track"/>'
            f'<circle cx="{_TRACK_X + _TRACK_W}" cy="{y}" r="6" fill="{fill}"/>'
            f'<text x="{_TRACK_X + 10}" y="{y + 5}" class="note" fill="{fill}">{note}</text>'
            f'<text x="{_TRACK_X + _TRACK_W + 16}" y="{y + 5}" class="meta">'
            f"${t.cost_usd:.3f} · {t.latency_ms:.0f}ms</text>"
        )
    return "\n".join(rows)


def _metric_bar(label: str, value: float, sub: str, y: int, color: str) -> str:
    width = round(max(0.0, min(1.0, value)) * _TRACK_W)
    return (
        f'<text x="{_TRACK_X - 12}" y="{y + 13}" class="lbl" text-anchor="end">{label}</text>'
        f'<rect x="{_TRACK_X}" y="{y}" width="{_TRACK_W}" height="18" rx="3" class="barbg"/>'
        f'<rect x="{_TRACK_X}" y="{y}" width="{width}" height="18" rx="3" fill="{color}"/>'
        f'<text x="{_TRACK_X + _TRACK_W + 16}" y="{y + 13}" class="meta">{sub}</text>'
    )


def render_fan_html(result: RunResult) -> str:
    metrics = result.metrics
    passes = sum(1 for t in result.trajectories if t.outcome == "pass")
    total = len(result.trajectories)
    lower, upper = metrics.wilson_ci

    lanes_bottom = _TOP + total * _ROW_H
    bars_y = lanes_bottom + 24
    height = bars_y + 90
    verdict_color = _FILL["pass"] if result.verdict == "pass" else _FILL["fail"]

    bars = "\n".join(
        [
            _metric_bar(
                "pass@1",
                metrics.pass_at_1,
                f"{metrics.pass_at_1:.2f} — {passes}/{total} single runs pass",
                bars_y,
                "#22c55e",
            ),
            _metric_bar(
                "pass^k",
                metrics.pass_at_k,
                f"{metrics.pass_at_k:.4f} — all {metrics.k} runs pass",
                bars_y + 30,
                "#ef4444",
            ),
        ]
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Chorus fan — {escape(result.task_id)}</title>
<style>
  body {{ background:#0b1020; color:#e2e8f0; margin:0;
         font:14px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .wrap {{ max-width:920px; margin:0 auto; padding:28px; }}
  h1 {{ font-size:18px; margin:0 0 2px; }}
  .sub {{ color:#94a3b8; margin:0 0 18px; }}
  .pill {{ padding:2px 10px; border-radius:999px; font-weight:700; color:#0b1020; }}
  .track {{ stroke:#1e293b; stroke-width:2; }}
  .barbg {{ fill:#1e293b; }}
  .lbl {{ fill:#94a3b8; font-size:12px; }}
  .note {{ font-size:12px; }}
  .meta {{ fill:#64748b; font-size:11px; }}
  .axis {{ fill:#475569; font-size:11px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Chorus trajectory fan
    <span class="pill" style="background:{verdict_color}">{escape(result.verdict)}</span>
  </h1>
  <p class="sub">task <b>{escape(result.task_id)}</b> · {total} trajectories ·
     pass@1 {metrics.pass_at_1:.2f} · pass^k {metrics.pass_at_k:.4f} ·
     Wilson95 [{lower:.2f}, {upper:.2f}] · mean ${metrics.mean_cost:.4f} ·
     p50 {metrics.p50_latency_ms:.0f}ms / p95 {metrics.p95_latency_ms:.0f}ms</p>
  <svg viewBox="0 0 920 {height}" width="100%" role="img"
       aria-label="Trajectory fan for {escape(result.task_id)}">
    <text x="{_TRACK_X}" y="120" class="axis">{_AXIS_HINT}</text>
    {_lane_rows(result)}
    {bars}
  </svg>
</div>
</body>
</html>
"""


def write_fan_html(result: RunResult, path: Path | str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_fan_html(result), encoding="utf-8")
    return out
