"""Trajectory-fan visualizer (terminal).

This file renders the signature Chorus view: ``N`` trajectory lanes for one task,
each marked pass / fail / error, above a side-by-side bar comparing single-run
reliability (``pass@1``) with all-runs reliability (``pass^k``). It reads only the
aggregated run result, which itself is derived from the event log.
"""

from __future__ import annotations

import sys

from murmur.core.types import RunResult, TrajectoryResult

_RESET = "\033[0m"
_COLORS = {"pass": "\033[32m", "fail": "\033[31m", "error": "\033[33m"}
_UNICODE_GLYPHS = {"pass": "✓", "fail": "✗", "error": "!"}
_ASCII_GLYPHS = {"pass": "P", "fail": "x", "error": "!"}
_UNICODE_BAR = ("█", "░")
_ASCII_BAR = ("#", "-")


def _supports_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or ""
    try:
        "✓░".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def _tint(text: str, outcome: str, color: bool) -> str:
    if not color:
        return text
    return f"{_COLORS.get(outcome, '')}{text}{_RESET}"


def _glyph(outcome: str, color: bool, ascii_only: bool) -> str:
    glyphs = _ASCII_GLYPHS if ascii_only else _UNICODE_GLYPHS
    return _tint(glyphs.get(outcome, "?"), outcome, color)


def _bar(rate: float, width: int, color: bool, ascii_only: bool, outcome: str = "pass") -> str:
    full, empty = _ASCII_BAR if ascii_only else _UNICODE_BAR
    filled = round(max(0.0, min(1.0, rate)) * width)
    bar = full * filled + empty * (width - filled)
    return _tint(bar, outcome, color)


def _lane(trajectory: TrajectoryResult, color: bool, ascii_only: bool) -> str:
    short_id = trajectory.trajectory_id.rsplit("_t", 1)[-1].rjust(2, "0")
    label = trajectory.failure_class or trajectory.outcome
    note = "" if trajectory.outcome == "pass" else f"  {label}"
    output = trajectory.output if len(trajectory.output) <= 22 else trajectory.output[:19] + "..."
    return (
        f"  t{short_id}  {_glyph(trajectory.outcome, color, ascii_only)}  "
        f"{output:<22}  ${trajectory.cost_usd:6.3f}  {trajectory.latency_ms:6.1f}ms{note}"
    )


def render_fan(
    result: RunResult,
    *,
    color: bool = True,
    width: int = 24,
    ascii_only: bool | None = None,
) -> str:
    if ascii_only is None:
        ascii_only = not _supports_unicode()
    metrics = result.metrics
    passes = sum(1 for t in result.trajectories if t.outcome == "pass")
    total = len(result.trajectories)

    legend = (
        f"{_glyph('pass', color, ascii_only)} pass   "
        f"{_glyph('fail', color, ascii_only)} fail   "
        f"{_glyph('error', color, ascii_only)} error"
    )
    lines = [f"Trajectory fan - {result.run_id}", legend, ""]
    lines.extend(_lane(t, color, ascii_only) for t in result.trajectories)

    strip = " ".join(_glyph(t.outcome, color, ascii_only) for t in result.trajectories)
    lower, upper = metrics.wilson_ci
    lines += [
        "",
        f"  fan: {strip}",
        "",
        f"  pass@1  {_bar(metrics.pass_at_1, width, color, ascii_only)}  "
        f"{metrics.pass_at_1:.2f}   Wilson95 [{lower:.2f}, {upper:.2f}]",
        f"  pass^k projected  {_bar(metrics.pass_at_k, width, color, ascii_only, 'fail')}  "
        f"{metrics.pass_at_k:.4f}  (i.i.d. k={metrics.k})",
        "  pass^k empirical  "
        f"{_bar(metrics.pass_at_k_unbiased, width, color, ascii_only, 'fail')}  "
        f"{metrics.pass_at_k_unbiased:.4f}  (unbiased; {passes}/{total} pass)",
    ]
    return "\n".join(lines)
