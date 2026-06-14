"""Report adapter exports.

Reports are derived from run results. This package currently exposes a Markdown
renderer for the demo CLI output.
"""

from murmur.report.markdown import render_run_report

__all__ = ["render_run_report"]
