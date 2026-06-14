"""CLI adoption-surface smoke tests."""

from __future__ import annotations

from typer.testing import CliRunner

from murmur.cli import app


def test_init_creates_starter_files_without_overwriting(tmp_path) -> None:
    runner = CliRunner()

    first = runner.invoke(app, ["init", "--root", str(tmp_path)])
    second = runner.invoke(app, ["init", "--root", str(tmp_path)])

    assert first.exit_code == 0
    assert (tmp_path / "tasks" / "murmur-smoke.yaml").is_file()
    assert (tmp_path / ".github" / "workflows" / "murmur.yml").is_file()
    assert "created" in first.output
    assert second.exit_code == 0
    assert "use --force" in second.output


def test_agents_list_shows_capabilities() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["agents", "list"])

    assert result.exit_code == 0
    assert "stochastic" in result.output
    assert "record,replay,live,tools" in result.output
    assert "swe-self-repair" in result.output
