"""CLI smoke tests for the flock subcommands (offline, no API keys)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from murmur.cli import app


def test_flock_run_offline_executes_bundled_example() -> None:
    result = CliRunner().invoke(app, ["flock", "run"])
    assert result.exit_code == 0
    assert "goal:" in result.output
    assert "model calls:" in result.output
    assert "[map" in result.output and "[reduce" in result.output


def test_flock_run_writes_trace_and_resumes(tmp_path) -> None:
    runner = CliRunner()
    log = tmp_path / "run.jsonl"
    trace = tmp_path / "trace.md"
    first = runner.invoke(app, ["flock", "run", "--event-log", str(log), "--trace", str(trace)])
    assert first.exit_code == 0
    assert trace.is_file() and "```mermaid" in trace.read_text(encoding="utf-8")

    second = runner.invoke(app, ["flock", "run", "--event-log", str(log)])
    assert second.exit_code == 0
    assert "model calls: 0" in second.output  # fully replayed from the log


def test_flock_plan_offline_emits_valid_yaml(tmp_path) -> None:
    out = tmp_path / "plan.yaml"
    result = CliRunner().invoke(
        app, ["flock", "plan", "summarize tickets", "--source", "tickets", "--out", str(out)]
    )
    assert result.exit_code == 0
    assert "goal:" in result.output
    assert out.is_file()

    from murmur.flock.ir import load_plan_yaml

    plan = load_plan_yaml(out.read_text(encoding="utf-8"))
    assert plan.sources == ("tickets",)


def test_flock_improve_mines_template_into_library(tmp_path) -> None:
    lib = tmp_path / "templates"
    result = CliRunner().invoke(
        app,
        ["flock", "improve", "rank backend resumes", "--source", "resumes", "--library", str(lib)],
    )
    assert result.exit_code == 0
    assert "origin: mined" in result.output
    saved = list(lib.glob("*.json"))
    assert len(saved) == 1
    template = json.loads(saved[0].read_text(encoding="utf-8"))
    assert "plan" in template and template["plan"]["nodes"]
