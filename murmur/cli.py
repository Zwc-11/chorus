"""Command-line interface for Murmur.

This file turns the Phase 0 harness into commands a user can run: record a
dummy run, replay it, and intentionally mutate it to prove divergence detection.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from murmur.adapters.agents.fake import FakeAgent, fake_tools
from murmur.adapters.agents.stochastic import stochastic_agent_factory, stochastic_tools
from murmur.adapters.storage.baseline import BaselineStore
from murmur.adapters.storage.jsonl import JsonlEventStore
from murmur.adapters.tools.registry import default_tool_metadata
from murmur.application.contract_compiler import compile_fix_test_contract
from murmur.application.fix_test import (
    proof_console_summary,
    run_contract,
    run_fix_test,
    run_fix_test_workflow,
    validate_fix_test_workflow,
)
from murmur.application.pr_verify import verify_pr
from murmur.application.workflow_planner import (
    TEMPLATES,
    choose_workflow_size,
    plan_task,
)
from murmur.application.workflow_runtime import WorkflowRuntime, explain_workflow
from murmur.benchmarks.loader import load_suite, suite_version_for
from murmur.benchmarks.scaffold import Scaffold, run_suite
from murmur.benchmarks.swe.types import BenchDependencyMissing
from murmur.benchmarks.swebench import BenchmarkDataUnavailable
from murmur.config import load_project_env
from murmur.core.agent_tasks import demo_task, load_agent_task
from murmur.core.conductor import RunConductor
from murmur.core.events import Event, EventType
from murmur.core.regression import baseline_set_report, regression_verdict
from murmur.domain.contract import Contract
from murmur.domain.workflow import WorkflowPlan
from murmur.gateway.tool_gateway import ReplayDivergenceError
from murmur.report.agent_map_html import write_agent_map_html
from murmur.report.fan import render_fan
from murmur.report.fan_html import write_fan_html
from murmur.report.markdown import render_run_report
from murmur.report.regression_md import render_regression_comment
from murmur.report.swe_html import write_benchmark_html
from murmur.report.trace_html import write_traces_html
from murmur.trace.mapper import events_to_traces

app = typer.Typer(no_args_is_help=True)
agents_app = typer.Typer(help="List and exercise registered agent modules.")
contract_app = typer.Typer(help="Create and validate Murmur engineering contracts.")
workflow_app = typer.Typer(help="Create and validate Murmur workflow plans.")
tools_app = typer.Typer(help="Inspect registered Murmur tool adapters.")
proof_app = typer.Typer(help="Inspect Murmur proof artifacts.")
flock_app = typer.Typer(help="Self-writing workflows: plan a task, run a Workflow IR plan.")
app.add_typer(agents_app, name="agents")
app.add_typer(contract_app, name="contract")
app.add_typer(workflow_app, name="workflow")
app.add_typer(tools_app, name="tools")
app.add_typer(proof_app, name="proof")
app.add_typer(flock_app, name="flock")


@app.callback()
def _main() -> None:
    """Murmur — contract and proof layer for AI-generated code changes."""

    loaded = load_project_env()
    if loaded is not None:
        os.environ.setdefault("MURMUR_ENV_LOADED", str(loaded))

    # Prefer UTF-8 so the trajectory-fan glyphs render on modern terminals
    # (Windows consoles default to a legacy code page). The fan renderer falls
    # back to ASCII if this is not possible.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(encoding="utf-8")


@app.command("init")
def init_project(
    root: Annotated[Path, typer.Option(help="Project root to initialize.")] = Path("."),
    force: Annotated[bool, typer.Option(help="Overwrite existing starter files.")] = False,
) -> None:
    """Create a minimal Murmur starter setup for an agent repository."""

    targets = {
        root / "tasks" / "murmur-smoke.yaml": _starter_task(),
        root / ".github" / "workflows" / "murmur.yml": _starter_workflow(),
        root / ".murmur" / "README.md": _starter_notes(),
    }
    written: list[Path] = []
    skipped: list[Path] = []
    for path, content in targets.items():
        if path.exists() and not force:
            skipped.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)

    for path in written:
        typer.echo(f"created {path}")
    for path in skipped:
        typer.echo(f"exists  {path}  (use --force to overwrite)")
    typer.echo("\nNext: run `murmur run --n 5` or wire your agent through AgentPort.")


def _starter_task() -> str:
    return """# Minimal Murmur task. Replace this with a repo-specific agent task.
task_id: murmur.smoke
expected_output: HELLO MURMUR
metadata:
  kind: smoke
prompt: |
  Reply with exactly: HELLO MURMUR
"""


def _starter_workflow() -> str:
    return """name: Murmur reliability gate

on:
  pull_request:
  workflow_dispatch:

jobs:
  murmur:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install -e ".[dev]"
      - run: murmur gate --suite synthetic --n 20 --k 5
"""


def _starter_notes() -> str:
    return """# Murmur local notes

This directory is for event logs, fan reports, traces, and baseline files.

Useful commands:

- `murmur agents list`
- `murmur run --n 30`
- `murmur trace --n 12 --replay`
- `murmur gate --suite synthetic --n 20 --k 5`
- `murmur fix-test --cmd "python -m pytest tests/test_example.py -q"`
"""


def _write_local_preview_index(root: Path) -> Path:
    """Write the small static launcher for generated local reports."""

    write_agent_map_html(root / "agent-map.html", preview=True)

    links = [
        (
            "agent map",
            "Operator map",
            "agent-map.html",
            "draggable modules · fan-out agents · flow playback",
        ),
        (
            "phase 2-4",
            "Reliability report",
            "fan.html",
            "pass@1 | pass^k decay | divergence overlay | diagnosis modals",
        ),
        (
            "phase 1",
            "Trace viewer",
            "trace.html",
            "waterfall | inspector | expand span modal",
        ),
        (
            "benchmark",
            "SWE-bench report",
            "bench/report.html",
            "scaffold comparison | pass^k | failure classes | task outcomes",
        ),
    ]
    cards = "\n".join(
        f"""    <a class="card" href="{href}">
      <h2>{phase}</h2>
      <div class="title">{title}</div>
      <div class="hint">{hint}</div>
    </a>"""
        for phase, title, href, hint in links
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Murmur local preview</title>
<style>
  :root {{
    --bg: #e4e4e0;
    --panel: rgba(255, 255, 255, 0.55);
    --line: rgba(10, 10, 10, 0.14);
    --txt: #0a0a0a;
    --muted: #5a5a56;
    --accent: #e8192a;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    --sans: "Segoe UI", ui-sans-serif, system-ui, sans-serif;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    min-height: 100vh;
    background: radial-gradient(ellipse 120% 80% at 50% 0%, #f5f5f2, #e4e4e0);
    font-family: var(--sans);
    color: var(--txt);
    padding: 48px 28px;
  }}
  h1 {{
    font-weight: 200;
    letter-spacing: 0.28em;
    text-transform: lowercase;
    font-size: 36px;
    margin: 0 0 8px;
  }}
  p {{ color: var(--muted); max-width: 58ch; line-height: 1.5; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 16px;
    margin-top: 36px;
    max-width: 1040px;
  }}
  a.card {{
    display: block;
    min-height: 148px;
    padding: 22px;
    background: var(--panel);
    border: 1px solid var(--line);
    text-decoration: none;
    color: inherit;
    backdrop-filter: blur(10px);
    transition: border-color 150ms, box-shadow 150ms;
  }}
  a.card:hover {{
    border-color: var(--accent);
    box-shadow: 0 0 0 1px rgba(232, 25, 42, 0.15);
  }}
  .card h2 {{
    margin: 0 0 8px;
    font-size: 11px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--muted);
    font-weight: 500;
  }}
  .card .title {{ font-size: 18px; font-weight: 500; margin-bottom: 10px; }}
  .card .hint {{
    font-family: var(--mono);
    font-size: 12px;
    color: var(--muted);
    line-height: 1.45;
  }}
  .meta {{
    margin-top: 40px;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.06em;
  }}
</style>
</head>
<body>
  <h1>murmur</h1>
  <p>
    Local HUD preview generated from Murmur reliability runs, traces, and
    benchmark comparisons.
  </p>
  <div class="grid">
{cards}
  </div>
  <p class="meta">static HTML | no server required</p>
</body>
</html>
"""
    out = root / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


@app.command("agent-map-preview")
def agent_map_preview(
    out_dir: Annotated[
        Path, typer.Option(help="Directory for agent-map.html and index.html.")
    ] = Path(".murmur/preview"),
    serve: Annotated[
        bool, typer.Option("--serve", help="Start a local HTTP server after writing.")
    ] = False,
    port: Annotated[int, typer.Option(help="Port for --serve.")] = 8765,
) -> None:
    """Write the agent operator map demo and refresh the local preview index."""

    out_dir.mkdir(parents=True, exist_ok=True)
    stale = out_dir / "murmur.html"
    if stale.is_file():
        stale.unlink()
    html_out = write_agent_map_html(out_dir / "agent-map.html", preview=True)
    index_out = _write_local_preview_index(out_dir)
    typer.echo(f"Agent map written to {html_out}")
    typer.echo(f"Preview index written to {index_out}")
    if serve:
        preview_serve(dir=out_dir, port=port)


@app.command("preview-serve")
def preview_serve(
    dir: Annotated[
        Path, typer.Option("--dir", help="Directory to serve.")
    ] = Path(".murmur/preview"),
    port: Annotated[int, typer.Option(help="HTTP port.")] = 8765,
) -> None:
    """Serve the local Murmur preview HUD (agent map, fan, trace)."""

    from murmur.ui.server import serve_preview_dir

    if not dir.is_dir():
        typer.echo(f"error: {dir} does not exist; run `murmur agent-map-preview` first", err=True)
        raise typer.Exit(2)

    try:
        serve_preview_dir(directory=dir, repo_root=Path("."), port=port)
    except KeyboardInterrupt:
        typer.echo("\nStopped.")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048 or exc.errno in {48, 98}:
            typer.echo(
                f"error: port {port} is already in use "
                "(another preview server may still be running).",
                err=True,
            )
            typer.echo(
                f"Open http://127.0.0.1:{port}/agent-map.html in your browser, "
                "or stop the other process and retry.",
                err=True,
            )
            typer.echo(f"Or pick another port: murmur preview-serve --port {port + 1}", err=True)
            raise typer.Exit(1) from exc
        raise


@app.command("murmur-preview")
def murmur_preview(
    out_dir: Annotated[
        Path, typer.Option(help="Deprecated alias for agent-map-preview.")
    ] = Path(".murmur/preview"),
) -> None:
    """Deprecated: use `murmur agent-map-preview`."""

    typer.echo("murmur-preview is deprecated; use agent-map-preview", err=True)
    agent_map_preview(out_dir=out_dir)


@app.command("murmur-web")
def murmur_web(
    out_dir: Annotated[
        Path, typer.Option(help="Directory for the local Murmur web workbench.")
    ] = Path(".murmur/preview"),
    port: Annotated[int, typer.Option(help="HTTP port.")] = 8765,
) -> None:
    """Write and serve the local prompt-to-agent-result workbench."""

    out_dir.mkdir(parents=True, exist_ok=True)
    write_agent_map_html(out_dir / "agent-map.html", preview=True)
    _write_local_preview_index(out_dir)
    preview_serve(dir=out_dir, port=port)


@app.command("serve")
def serve(
    out_dir: Annotated[
        Path, typer.Option(help="Workbench directory.")
    ] = Path(".murmur/preview"),
    port: Annotated[int, typer.Option(help="HTTP port.")] = 8765,
) -> None:
    """Build and serve the local workbench in one command (the easiest way to start)."""

    _ensure_env_ready()
    out_dir.mkdir(parents=True, exist_ok=True)
    write_agent_map_html(out_dir / "agent-map.html", preview=True)
    _write_local_preview_index(out_dir)
    typer.echo(f"Workbench ready: http://127.0.0.1:{port}/agent-map.html")
    preview_serve(dir=out_dir, port=port)


def _ensure_env_ready() -> None:
    """Create .env from the example if missing and warn when no API key is set."""

    env = Path(".env")
    example = Path(".env.example")
    if not env.is_file() and example.is_file():
        env.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        typer.echo("Created .env from .env.example - add DEEPSEEK_API_KEY to enable 'Use model'.")
    load_project_env(start=Path("."))
    if not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        typer.echo(
            "No API key found. The workbench runs offline (leave 'Use model' unchecked); "
            "set DEEPSEEK_API_KEY in .env for live model runs.",
        )


@app.command("fix-test")
def fix_test(
    cmd: Annotated[str, typer.Option("--cmd", help="Failing test command to reproduce/fix.")],
    budget: Annotated[float, typer.Option(help="Maximum model/tool budget in USD.")] = 0.50,
    agent: Annotated[
        str, typer.Option(help="Contract agent: scripted | murmur-lite | murmur.")
    ] = "scripted",
    repo_root: Annotated[Path, typer.Option(help="Repository root to execute in.")] = Path("."),
    out_dir: Annotated[Path, typer.Option(help="Root directory for proof runs.")] = Path(
        ".murmur/runs"
    ),
    provider: Annotated[str, typer.Option(help="Provider for murmur/murmur-lite.")] = "",
    model: Annotated[str, typer.Option(help="Model id for murmur/murmur-lite.")] = "",
    n: Annotated[int, typer.Option(min=1, help="Number of isolated repair attempts.")] = 1,
    max_repairs: Annotated[
        int, typer.Option(min=0, help="Maximum repair iterations per failed attempt.")
    ] = 0,
    attempt_concurrency: Annotated[
        int, typer.Option(min=1, help="Maximum isolated attempts to run at once.")
    ] = 1,
    judge_provider: Annotated[
        str,
        typer.Option(help="LLM judge for rank ties: fake | ollama | openai | deepseek."),
    ] = "",
    judge_model: Annotated[str, typer.Option(help="Model id for the tie-break judge.")] = "",
) -> None:
    """Run the contract-first failing-test repair workflow."""

    try:
        proof = run_fix_test(
            command=cmd,
            repo_root=repo_root,
            out_root=out_dir,
            budget_usd=budget,
            agent_name=agent,
            provider=provider,
            model=model,
            attempts=n,
            max_repairs=max_repairs,
            attempt_concurrency=attempt_concurrency,
            judge_provider=judge_provider,
            judge_model=judge_model,
        )
    except (KeyError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    run_path = out_dir / proof.run_id
    typer.echo(f"# Murmur proof - {proof.run_id}\n")
    typer.echo(proof_console_summary(proof, run_path))
    raise typer.Exit(0 if proof.verdict == "pass" else 1)


@contract_app.command("create")
def contract_create(
    from_test: Annotated[
        str, typer.Option("--from-test", help="Failing test command to compile into a contract.")
    ],
    repo_root: Annotated[Path, typer.Option(help="Repository root.")] = Path("."),
    budget: Annotated[float, typer.Option(help="Maximum budget in USD.")] = 0.50,
    out: Annotated[Path, typer.Option(help="Contract YAML output path.")] = Path(
        ".murmur/contracts/fix-test.yaml"
    ),
) -> None:
    """Compile a failing-test command into a Murmur contract YAML file."""

    contract = compile_fix_test_contract(
        command=from_test,
        repo_root=repo_root,
        budget_usd=budget,
    )
    contract.write(out)
    typer.echo(f"Contract written to {out}")


@contract_app.command("check")
def contract_check(path: Annotated[Path, typer.Argument(help="Contract YAML path.")]) -> None:
    """Validate a Murmur contract YAML file."""

    contract = Contract.read(path)
    issues = contract.validate()
    if issues:
        for issue in issues:
            typer.echo(f"error: {issue}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Contract OK: {path}")


@app.command("verify-pr")
def verify_pr_command(
    base: Annotated[str, typer.Option("--base", help="Base ref for the PR diff.")] = "main",
    head: Annotated[str, typer.Option("--head", help="Head ref for the PR diff.")] = "HEAD",
    repo_root: Annotated[Path, typer.Option(help="Repository root.")] = Path("."),
    out_dir: Annotated[Path, typer.Option(help="Root directory for PR proof runs.")] = Path(
        ".murmur/pr-runs"
    ),
    cmd: Annotated[
        list[str] | None,
        typer.Option("--cmd", help="Optional objective command to run on the head ref."),
    ] = None,
    budget: Annotated[float, typer.Option(help="Maximum verification budget in USD.")] = 0.10,
    run_id: Annotated[str, typer.Option(help="Optional stable run id.")] = "",
) -> None:
    """Verify a PR-style diff and emit a trust proof."""

    try:
        proof = verify_pr(
            repo_root=repo_root,
            base=base,
            head=head,
            out_root=out_dir,
            commands=tuple(cmd or ()),
            budget_usd=budget,
            run_id=run_id,
        )
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    run_path = out_dir / proof.run_id
    typer.echo(
        json.dumps(
            {
                "run_id": proof.run_id,
                "status": proof.verdict,
                "run_dir": str(run_path),
                "trust_score": proof.trust_score.score if proof.trust_score else None,
                "changed_files": proof.verification.changed_files,
                "failures": proof.verification.failures,
            },
            indent=2,
        )
    )
    raise typer.Exit(0 if proof.verdict == "pass" else 1)


@tools_app.command("list")
def tools_list() -> None:
    """List registered built-in tool adapters."""

    typer.echo(json.dumps(default_tool_metadata(), indent=2))


@proof_app.command("inspect")
def proof_inspect(
    run_dir: Annotated[Path, typer.Argument(help="Run directory to inspect.")],
) -> None:
    """Print the high-signal proof summary for one run directory."""

    proof_path = run_dir / "proof.json"
    if not proof_path.is_file():
        proof_path = run_dir / "summary.json"
    if not proof_path.is_file():
        typer.echo(f"error: no proof.json or summary.json in {run_dir}", err=True)
        raise typer.Exit(2)
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    verification = dict(proof.get("verification", {}))
    budget = dict(proof.get("budget", {}))
    trust = proof.get("trust_score") or {}
    payload = {
        "run_id": proof.get("run_id"),
        "verdict": proof.get("verdict", proof.get("status")),
        "trust_score": trust,
        "changed_files": verification.get("changed_files", ()),
        "failures": verification.get("failures", ()),
        "tool_calls": proof.get("tool_calls", budget.get("tool_calls", 0)),
        "model_calls": proof.get("model_calls", budget.get("model_calls", 0)),
        "artifacts": proof.get("artifact_index", ()),
    }
    typer.echo(json.dumps(payload, indent=2, default=str))


@workflow_app.command("check")
def workflow_check(
    path: Annotated[Path, typer.Argument(help="Murmur workflow YAML path.")],
    contract_path: Annotated[
        Path | None,
        typer.Option("--contract", help="Optional contract YAML for fixed fix-test validation."),
    ] = None,
) -> None:
    """Validate a Murmur workflow YAML file."""

    workflow = WorkflowPlan.read(path)
    issues = workflow.validate()
    if issues:
        for issue in issues:
            typer.echo(f"error: {issue}", err=True)
        raise typer.Exit(1)
    if contract_path is not None:
        contract = Contract.read(contract_path)
        try:
            validate_fix_test_workflow(workflow, contract=contract)
        except RuntimeError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from exc
    typer.echo(f"Workflow OK: {path}")


@workflow_app.command("explain")
def workflow_explain(
    path: Annotated[Path, typer.Argument(help="Murmur workflow YAML path.")],
) -> None:
    """Print deterministic execution order, dependencies, and budgets."""

    workflow = WorkflowPlan.read(path)
    issues = workflow.validate()
    if issues:
        for issue in issues:
            typer.echo(f"error: {issue}", err=True)
        raise typer.Exit(1)
    typer.echo(explain_workflow(workflow))


@workflow_app.command("run")
def workflow_run(
    path: Annotated[Path, typer.Argument(help="Murmur workflow YAML path.")],
    repo_root: Annotated[Path, typer.Option(help="Repository root to execute in.")] = Path("."),
    out_dir: Annotated[Path, typer.Option(help="Root directory for workflow runs.")] = Path(
        ".murmur/runs"
    ),
    contract_path: Annotated[
        Path | None,
        typer.Option("--contract", help="Optional contract YAML for policy-controlled exec nodes."),
    ] = None,
    resume: Annotated[bool, typer.Option(help="Reuse matching completed node evidence.")] = False,
    concurrency: Annotated[int, typer.Option(min=1, help="Maximum ready nodes to schedule.")] = 1,
    agent: Annotated[
        str, typer.Option(help="Contract agent for coding_fix_test: scripted | murmur-lite.")
    ] = "scripted",
    attempt_concurrency: Annotated[
        int,
        typer.Option(min=1, help="Maximum isolated coding attempts to run at once."),
    ] = 1,
    provider: Annotated[
        str,
        typer.Option(help="Provider for legacy PatchModel-backed generate/map nodes."),
    ] = "",
    model: Annotated[str, typer.Option(help="Model id for model-backed workflow nodes.")] = "",
    model_provider: Annotated[
        str,
        typer.Option(
            help="ModelPort provider for map fan-out: none | fake | ollama | openai | deepseek."
        ),
    ] = "",
    run_id: Annotated[
        str,
        typer.Option(help="Optional stable run id for deterministic resume."),
    ] = "",
) -> None:
    """Execute a validated Murmur workflow plan."""

    workflow = WorkflowPlan.read(path)
    issues = workflow.validate()
    if issues:
        for issue in issues:
            typer.echo(f"error: {issue}", err=True)
        raise typer.Exit(1)
    contract = Contract.read(contract_path) if contract_path is not None else None
    if contract is not None:
        contract_issues = contract.validate()
        if contract_issues:
            for issue in contract_issues:
                typer.echo(f"error: {issue}", err=True)
            raise typer.Exit(1)
    if _is_coding_fix_test_workflow(workflow):
        try:
            proof = run_fix_test_workflow(
                workflow=workflow,
                repo_root=repo_root,
                out_root=out_dir,
                contract=contract,
                agent_name=agent,
                provider=provider,
                model=model,
                attempt_concurrency=attempt_concurrency,
                run_id=run_id,
            )
        except (KeyError, RuntimeError) as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(2) from exc
        run_path = out_dir / proof.run_id
        typer.echo(
            json.dumps(
                {
                    "run_id": proof.run_id,
                    "status": proof.verdict,
                    "run_dir": str(run_path),
                    "attempts": len(proof.attempts),
                    "tool_calls": proof.tool_calls,
                    "model_calls": proof.model_calls,
                },
                indent=2,
            )
        )
        raise typer.Exit(0 if proof.verdict == "pass" else 1)
    workflow_model = None
    if (provider or model) and not model_provider.strip():
        from murmur.benchmarks.swe.providers import create_patch_model, default_model

        workflow_model = create_patch_model(
            provider=provider or None,
            model=model or default_model(provider),
        )
    try:
        model_port, default_model_id = _build_model_port(model_provider, model)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    runtime = WorkflowRuntime(
        repo_root=repo_root,
        out_root=out_dir,
        contract=contract,
        model=workflow_model,
        concurrency=concurrency,
        resume=resume,
        model_port=model_port,
        default_model=default_model_id,
    )
    try:
        result = runtime.run(workflow, run_id=run_id or None)
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "run_dir": str(result.run_dir),
                "nodes": len(result.node_results),
            },
            indent=2,
        )
    )
    raise typer.Exit(0 if result.passed else 1)


def _build_model_port(provider: str, model: str):
    """Map a --model-provider value onto a ModelPort adapter, or return none."""

    from murmur.adapters.models import FakeModel, OllamaModel, OpenAICompatibleModel

    normalized = provider.strip().lower()
    if normalized in {"", "none"}:
        return None, model
    if normalized == "fake":
        return FakeModel(), model or "fake-model"
    if normalized == "ollama":
        if not model:
            raise RuntimeError("--model is required with --model-provider ollama")
        return OllamaModel(), model
    if normalized == "openai":
        return OpenAICompatibleModel(), model or "gpt-4o-mini"
    if normalized == "deepseek":
        return (
            OpenAICompatibleModel(
                base_url="https://api.deepseek.com", api_key_env="DEEPSEEK_API_KEY"
            ),
            model or "deepseek-chat",
        )
    raise RuntimeError(f"unknown model provider: {provider}")


@app.command("plan")
def plan_workflow(
    task: Annotated[str, typer.Option("--task", help="Natural-language task for Murmur.")],
    out: Annotated[Path, typer.Option(help="Workflow YAML output path.")] = Path(
        ".murmur/workflows/murmur.yaml"
    ),
    template: Annotated[
        str,
        typer.Option(help=f"Workflow template: auto | {' | '.join(TEMPLATES)}."),
    ] = "auto",
    cmd: Annotated[
        str,
        typer.Option("--cmd", help="Optional objective command/test/backtest."),
    ] = "",
    n: Annotated[int, typer.Option(min=1, help="Number of candidates for coding templates.")] = 1,
    max_repairs: Annotated[int, typer.Option(min=0, help="Repair loop budget.")] = 0,
    auto_size: Annotated[
        bool,
        typer.Option(
            "--auto-size",
            help="Choose candidate count and repair budget from task difficulty.",
        ),
    ] = False,
    budget: Annotated[float, typer.Option(help="Budget used by --auto-size.")] = 0.50,
    self_write: Annotated[
        bool,
        typer.Option(
            "--self-write",
            help="Ask the configured model to write the workflow IR, then validate it.",
        ),
    ] = False,
    provider: Annotated[str, typer.Option(help="Provider for --self-write.")] = "",
    model: Annotated[str, typer.Option(help="Model id for --self-write.")] = "",
) -> None:
    """Create a validated Murmur workflow plan."""

    try:
        size_reason = ""
        if auto_size:
            size = choose_workflow_size(task=task, command=cmd, budget_usd=budget)
            n = size.attempts
            max_repairs = size.max_repairs
            size_reason = size.reason
        # --self-write requests model-authored planning (the same engine the workbench
        # uses by default); plan_task validates the model's plan and falls back to a
        # deterministic template if the model is unavailable or its plan is invalid.
        # The scripted CLI stays deterministic by default to avoid surprise API calls.
        planner_model = None
        effective_template = template
        if self_write:
            from murmur.benchmarks.swe.providers import create_patch_model, default_model

            planner_model = create_patch_model(
                provider=provider or None,
                model=model or default_model(provider),
            )
            effective_template = "auto"
        outcome = plan_task(
            task=task,
            model=planner_model,
            command=cmd,
            budget_usd=budget,
            template=effective_template,
            attempts=n,
            max_repairs=max_repairs,
        )
        workflow = outcome.workflow
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    issues = workflow.validate()
    if issues:
        for issue in issues:
            typer.echo(f"error: {issue}", err=True)
        raise typer.Exit(1)
    workflow.write(out)
    typer.echo(f"Workflow written to {out}")
    planner_line = f"Planner: {outcome.mode}"
    if outcome.reason:
        planner_line += f" ({outcome.reason})"
    typer.echo(planner_line)
    typer.echo(f"Template: {workflow.name}")
    if auto_size:
        typer.echo(f"Auto-size: n={n}, max_repairs={max_repairs} ({size_reason})")


def _is_coding_fix_test_workflow(workflow: WorkflowPlan) -> bool:
    if workflow.name == "coding_fix_test":
        return True
    node_ops = {node.id: node.op for node in workflow.nodes}
    return node_ops == {
        "reproduce": "exec",
        "generate": "generate",
        "run_tests": "exec",
        "repair": "loop",
        "rank": "rank",
        "verify": "verify",
        "report": "report",
    }


@app.command("run-contract")
def run_contract_command(
    path: Annotated[Path, typer.Argument(help="Contract YAML path.")],
    agent: Annotated[
        str, typer.Option(help="Contract agent: scripted | murmur-lite | murmur.")
    ] = "scripted",
    out_dir: Annotated[Path, typer.Option(help="Root directory for proof runs.")] = Path(
        ".murmur/runs"
    ),
    provider: Annotated[str, typer.Option(help="Provider for murmur/murmur-lite.")] = "",
    model: Annotated[str, typer.Option(help="Model id for murmur/murmur-lite.")] = "",
    judge_provider: Annotated[
        str,
        typer.Option(help="LLM judge for rank ties: fake | ollama | openai | deepseek."),
    ] = "",
    judge_model: Annotated[str, typer.Option(help="Model id for the tie-break judge.")] = "",
) -> None:
    """Execute an existing Murmur contract."""

    contract = Contract.read(path)
    issues = contract.validate()
    if issues:
        for issue in issues:
            typer.echo(f"error: {issue}", err=True)
        raise typer.Exit(1)
    proof = run_contract(
        contract=contract,
        out_root=out_dir,
        agent_name=agent,
        provider=provider,
        model=model,
        judge_provider=judge_provider,
        judge_model=judge_model,
    )
    run_path = out_dir / proof.run_id
    typer.echo(f"# Murmur proof - {proof.run_id}\n")
    typer.echo(proof_console_summary(proof, run_path))
    raise typer.Exit(0 if proof.verdict == "pass" else 1)


@app.command()
def demo(
    n: Annotated[int, typer.Option(min=1, help="Number of trajectories to run.")] = 3,
    task: Annotated[str, typer.Option(help="Task: demo | hard (or MURMUR_TASK).")] = "",
    event_log: Annotated[Path, typer.Option(help="JSONL event log path.")] = Path(
        ".murmur/demo.jsonl"
    ),
) -> None:
    """Record a deterministic fake-agent run."""

    spec = load_agent_task(task or None)
    store = JsonlEventStore(event_log, reset=True)
    conductor = RunConductor(agent=FakeAgent(), storage=store, tools=fake_tools())
    result = asyncio.run(conductor.run(spec, n=n))
    typer.echo(render_run_report(result))
    typer.echo(f"\nEvents written to {event_log}")


@app.command()
def run(
    n: Annotated[int, typer.Option(min=1, help="Number of trajectories to fan out.")] = 30,
    task: Annotated[str, typer.Option(help="Task: demo | hard (or MURMUR_TASK).")] = "",
    success_rate: Annotated[
        float, typer.Option(min=0.0, max=1.0, help="Per-run success probability of the agent.")
    ] = 0.7,
    error_rate: Annotated[
        float, typer.Option(min=0.0, max=1.0, help="Probability a run hits a flaky tool (errors).")
    ] = 0.1,
    seed: Annotated[int, typer.Option(help="Base seed; run is fully reproducible.")] = 7,
    event_log: Annotated[Path, typer.Option(help="JSONL event log path.")] = Path(
        ".murmur/run.jsonl"
    ),
    html: Annotated[
        Path | None, typer.Option(help="Write a standalone HTML/SVG trajectory fan here.")
    ] = Path(".murmur/fan.html"),
    no_color: Annotated[bool, typer.Option("--no-color", help="Disable ANSI color.")] = False,
) -> None:
    """Fan out a stochastic agent ×N and show the reliability distribution."""

    store = JsonlEventStore(event_log, reset=True)
    factory = stochastic_agent_factory(
        success_rate=success_rate, error_rate=error_rate, base_seed=seed
    )
    spec = load_agent_task(task or None)
    conductor = RunConductor(agent_factory=factory, storage=store, tools=stochastic_tools())
    result = asyncio.run(conductor.run(spec, n=n))
    events = list(asyncio.run(store.read_events()))

    typer.echo(render_run_report(result))
    typer.echo(f"task: {spec.task_id}")
    typer.echo("")
    typer.echo(render_fan(result, color=not no_color))
    typer.echo(f"\nEvents written to {event_log}")
    if html is not None:
        out = write_fan_html(result, html, events=events)
        typer.echo(f"Trajectory fan written to {out}  (open in a browser)")


def _verify_replay(conductor: RunConductor, events: list[Event]) -> int:
    """Re-execute each recorded trajectory through the replay gateway."""

    started = [
        (event.trajectory_id, int(event.payload.get("index", 0)))
        for event in events
        if event.type == EventType.TRAJECTORY_STARTED and event.trajectory_id is not None
    ]
    verified = 0
    for trajectory_id, index in started:
        try:
            asyncio.run(
                conductor.replay(
                    events=events,
                    task=demo_task(),
                    trajectory_id=trajectory_id,
                    index=index,
                )
            )
            verified += 1
        except ReplayDivergenceError as exc:
            typer.echo(f"  divergence in {trajectory_id}: {exc}", err=True)
    return verified


@app.command()
def trace(
    n: Annotated[int, typer.Option(min=1, help="Number of trajectories to trace.")] = 30,
    task: Annotated[str, typer.Option(help="Task: demo | hard (or MURMUR_TASK).")] = "",
    success_rate: Annotated[float, typer.Option(min=0.0, max=1.0)] = 0.7,
    error_rate: Annotated[float, typer.Option(min=0.0, max=1.0)] = 0.1,
    seed: Annotated[int, typer.Option(help="Base seed; run is fully reproducible.")] = 7,
    event_log: Annotated[Path, typer.Option(help="JSONL event log path.")] = Path(
        ".murmur/trace.jsonl"
    ),
    html: Annotated[Path, typer.Option(help="Trace viewer output path.")] = Path(
        ".murmur/trace.html"
    ),
    replay: Annotated[
        bool, typer.Option(help="Verify replay and mark spans murmur.replay=true.")
    ] = False,
    capture_content: Annotated[
        bool, typer.Option(help="Include prompt/arg content in spans (off by default).")
    ] = False,
    otlp: Annotated[bool, typer.Option(help="Also export spans over OTLP.")] = False,
    backend: Annotated[str, typer.Option(help="OTLP backend: phoenix | langsmith.")] = "phoenix",
    endpoint: Annotated[str | None, typer.Option(help="Override the OTLP endpoint URL.")] = None,
    project: Annotated[
        str, typer.Option(help="LangSmith project name (sets LANGSMITH_PROJECT).")
    ] = "",
) -> None:
    """Record a run, project it into gen_ai.* spans, and write the trace viewer."""

    store = JsonlEventStore(event_log, reset=True)
    factory = stochastic_agent_factory(
        success_rate=success_rate, error_rate=error_rate, base_seed=seed
    )
    conductor = RunConductor(
        agent_factory=factory,
        storage=store,
        tools=stochastic_tools(),
        capture_content=capture_content,
    )
    spec = load_agent_task(task or None)
    result = asyncio.run(conductor.run(spec, n=n))
    events = list(asyncio.run(store.read_events()))

    if replay:
        verified = _verify_replay(conductor, events)
        typer.echo(f"Replay: {verified}/{n} trajectories reproduced exactly from the log.")

    traces = events_to_traces(events, capture_content=capture_content, replay=replay)

    counts = {"pass": 0, "fail": 0, "error": 0}
    for t in traces:
        counts[t.outcome] = counts.get(t.outcome, 0) + 1
    total_tokens = sum(t.total_tokens for t in traces)
    total_cost = sum(t.total_cost_usd for t in traces)

    typer.echo(f"# Murmur trace {result.run_id}")
    typer.echo(
        f"- trajectories: {len(traces)}  "
        f"(pass {counts['pass']} / fail {counts['fail']} / error {counts['error']})"
    )
    typer.echo(f"- spans: {sum(len(t.spans) for t in traces)}")
    typer.echo(f"- tokens: {total_tokens / 1000:.1f}k   cost: ${total_cost:.4f}")
    typer.echo(f"- content capture: {'on' if capture_content else 'off (structural only)'}")

    out = write_traces_html(traces, html, run_id=result.run_id)
    typer.echo(f"\nTrace viewer written to {out}  (open in a browser)")

    if otlp:
        from murmur.adapters.trace.otlp import (
            OtelNotInstalled,
            build_otlp_trace_port,
            langsmith_project_url,
        )
        from murmur.trace.emit import emit_traces

        if backend == "langsmith":
            if project:
                os.environ["LANGSMITH_PROJECT"] = project
            if not os.environ.get("LANGSMITH_API_KEY"):
                typer.echo(
                    "LANGSMITH_API_KEY is not set; the LangSmith export will be rejected (401). "
                    "Set it and re-run.",
                    err=True,
                )
        try:
            port = build_otlp_trace_port(backend=backend, endpoint=endpoint)
            emit_traces(traces, port)
        except OtelNotInstalled as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc

        stats = port.export_stats()
        if not stats.ok:
            hint = (
                " (check LANGSMITH_API_KEY)"
                if backend == "langsmith"
                else " (is the collector running?)"
            )
            typer.echo(
                f"Export FAILED: {backend} rejected {stats.failed_batches} span batch(es); "
                f"only {stats.ok_spans} spans accepted{hint}.",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo(
            f"Exported {stats.ok_spans} spans ({len(traces)} traces) over OTLP to {backend}."
        )
        if backend == "langsmith":
            resolved_project = os.environ.get("LANGSMITH_PROJECT", "murmur")
            typer.echo(
                f"Open LangSmith project {resolved_project!r}: "
                f"{langsmith_project_url(resolved_project)}"
            )


@app.command()
def replay(
    event_log: Annotated[Path, typer.Option(help="JSONL event log path.")] = Path(
        ".murmur/demo.jsonl"
    ),
    mutate: Annotated[
        bool, typer.Option(help="Intentionally change the prompt to prove divergence.")
    ] = False,
) -> None:
    """Replay the first recorded trajectory from an event log."""

    store = JsonlEventStore(event_log)
    events = list(asyncio.run(store.read_events()))
    conductor = RunConductor(agent=FakeAgent(), storage=store, tools=fake_tools())
    try:
        output = asyncio.run(conductor.replay(events=events, task=demo_task(mutate=mutate)))
    except ReplayDivergenceError as exc:
        typer.echo(f"Replay diverged: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Replay matched recorded output: {output}")


def _git(*args: str, default: str = "") -> str:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=5, check=False)
        return out.stdout.strip() or default
    except (OSError, subprocess.SubprocessError):
        return default


def _detect_branch() -> str:
    # In a GitHub PR the candidate's target branch is what we compare against.
    return (
        os.environ.get("GITHUB_BASE_REF")
        or os.environ.get("GITHUB_REF_NAME")
        or _git("rev-parse", "--abbrev-ref", "HEAD", default="local")
    )


def _detect_commit() -> str:
    return os.environ.get("GITHUB_SHA", "")[:7] or _git("rev-parse", "--short", "HEAD", default="")


async def _run_real_suite(
    tasks,
    *,
    suite: str,
    scaffold: str,
    model: str,
    provider: str,
    n: int,
    seed: int,
    branch: str,
    trace_html: Path | None = None,
):
    """Build a real SWE-bench AgentPort + JudgePort and run the batched judged suite.

    Uses the two-phase batched runner: every agent runs through the conductor (so
    the run is recorded and traceable), then each attempt's patches are evaluated
    across all instances in one harness run. Preflights the model and evaluator
    dependencies so a missing API key or Docker fails fast with a clear error
    instead of silently turning every trajectory into an ``error`` outcome (the
    conductor swallows agent faults by design).
    """

    from murmur.adapters.agents.swe import SwePatchAgent
    from murmur.benchmarks.scaffold import run_judged_suite_batched
    from murmur.benchmarks.swe.evaluator import SubprocessSweEvaluator
    from murmur.benchmarks.swe.judge import SweBenchJudge
    from murmur.benchmarks.swe.providers import create_patch_model, default_model

    patch_model = create_patch_model(
        provider=provider or None,
        model=model or default_model(provider),
    )
    evaluator = SubprocessSweEvaluator()
    patch_model.ensure_ready()
    evaluator.ensure_ready()

    repair = scaffold == "self-repair"

    def agent_factory(lane_seed: int) -> SwePatchAgent:
        return SwePatchAgent(patch_model, repair=repair, seed=lane_seed)

    run = await run_judged_suite_batched(
        tasks,
        agent_factory=agent_factory,
        judge=SweBenchJudge(evaluator),
        n=n,
        seed=seed,
        branch=branch,
        suite_version=suite_version_for(suite),
        scaffold="self-repair" if repair else "single-shot",
        commit=_detect_commit(),
    )
    if trace_html is not None:
        _write_swe_trace(run, trace_html)
    return run.suite


def _write_swe_trace(run, path: Path) -> None:
    """Project the recorded SWE-bench run into the gen_ai.* trace viewer."""

    from murmur.report.trace_html import write_traces_html
    from murmur.trace.mapper import events_to_traces

    traces = []
    for events in run.events.values():
        traces.extend(events_to_traces(events))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_traces_html(traces, path, run_id=f"swe::{run.suite.scaffold}")
    typer.echo(f"Trace viewer written to {path}  (open in a browser)")


@app.command()
def gate(
    suite: Annotated[str, typer.Option(help="Benchmark suite to run.")] = "synthetic",
    n: Annotated[int, typer.Option(min=1, help="Trajectories per task.")] = 20,
    seed: Annotated[int, typer.Option(help="Per-lane seed base; run is reproducible.")] = 7,
    k: Annotated[int, typer.Option(min=1, help="Horizon for the pass^k delta.")] = 5,
    scaffold: Annotated[
        str, typer.Option(help="Candidate scaffold name (label only).")
    ] = "baseline",
    success_delta: Annotated[
        float, typer.Option(help="Shift applied to every task's base difficulty.")
    ] = 0.0,
    error_rate: Annotated[float, typer.Option(min=0.0, max=1.0)] = 0.08,
    branch: Annotated[
        str | None, typer.Option(help="Baseline branch; auto-detected if unset.")
    ] = None,
    baseline_dir: Annotated[Path, typer.Option(help="Baseline store directory.")] = Path(
        ".murmur/baselines"
    ),
    update_baseline: Annotated[
        bool,
        typer.Option(help="Persist this run as the baseline (use on the base branch / merge)."),
    ] = False,
    comment_out: Annotated[Path, typer.Option(help="Write the PR comment markdown here.")] = Path(
        ".murmur/gate.md"
    ),
    boot_seed: Annotated[int, typer.Option(help="Bootstrap seed; keeps the verdict stable.")] = 0,
    real_agent: Annotated[
        bool,
        typer.Option(
            help="For a real suite: run a real AgentPort + SWE-bench JudgePort (needs deps)."
        ),
    ] = False,
    model: Annotated[str, typer.Option(help="Model id for --real-agent.")] = "",
    provider: Annotated[
        str,
        typer.Option(
            help="LLM provider: deepseek | anthropic (default from MURMUR_MODEL_PROVIDER)."
        ),
    ] = "",
    trace_html: Annotated[
        Path, typer.Option(help="With --real-agent: write the SWE-bench trace viewer here.")
    ] = Path(".murmur/swe-trace.html"),
) -> None:
    """Run the suite and gate on a *statistical* regression vs the stored baseline."""

    try:
        tasks = load_suite(suite)
        resolved_branch = branch or _detect_branch()
        if suite == "synthetic":
            scaffold_spec = Scaffold(
                name=scaffold, success_delta=success_delta, error_rate=error_rate
            )
            candidate = asyncio.run(
                run_suite(
                    tasks,
                    scaffold=scaffold_spec,
                    n=n,
                    seed=seed,
                    branch=resolved_branch,
                    commit=_detect_commit(),
                    suite_version=suite_version_for(suite),
                )
            )
        elif real_agent:
            candidate = asyncio.run(
                _run_real_suite(
                    tasks,
                    suite=suite,
                    scaffold=scaffold,
                    model=model,
                    provider=provider,
                    n=n,
                    seed=seed,
                    branch=resolved_branch,
                    trace_html=trace_html,
                )
            )
        else:
            # The synthetic scaffold cannot *solve* a real benchmark; emitting a
            # pass^k for it would be the fabricated number the locked decisions
            # forbid. Pass --real-agent to run a real AgentPort + SWE-bench judge.
            typer.echo(
                f"Suite {suite!r} loaded {len(tasks)} real tasks. Re-run with --real-agent to "
                "evaluate them with a real AgentPort + SWE-bench JudgePort (needs "
                "DEEPSEEK_API_KEY or ANTHROPIC_API_KEY + Docker + the 'bench' extra). "
                "Refusing to emit a synthetic "
                "pass^k for a real benchmark.",
                err=True,
            )
            raise typer.Exit(2)
    except (BenchmarkDataUnavailable, BenchDependencyMissing) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    store = BaselineStore(baseline_dir)
    baseline = store.load(resolved_branch, candidate.suite_version, n)

    if baseline is None:
        report = baseline_set_report(candidate, k=k)
        store.save(candidate)
    else:
        report = regression_verdict(
            baseline,
            candidate,
            k=k,
            seed=boot_seed,
            baseline_ref=f"{baseline.branch}@{baseline.commit or 'baseline'}",
        )
        if update_baseline and not report.blocks:
            store.save(candidate)

    comment = render_regression_comment(report, suite_version=candidate.suite_version)
    typer.echo(comment)
    comment_out.parent.mkdir(parents=True, exist_ok=True)
    comment_out.write_text(comment + "\n", encoding="utf-8")
    typer.echo(f"\nComment written to {comment_out}")
    raise typer.Exit(1 if report.blocks else 0)


@app.command()
def bench(
    subset: Annotated[
        int, typer.Option(help="SWE-bench Verified subset size (0 = full set).")
    ] = 50,
    n: Annotated[int, typer.Option(min=1, help="Attempts per task (use >= k).")] = 10,
    k: Annotated[int, typer.Option(min=1, help="Horizon for the pass^k headline.")] = 5,
    model: Annotated[str, typer.Option(help="Model id; held fixed across scaffolds.")] = "",
    provider: Annotated[
        str,
        typer.Option(
            help="LLM provider: deepseek | anthropic (default from MURMUR_MODEL_PROVIDER)."
        ),
    ] = "",
    scaffold_a: Annotated[str, typer.Option(help="Reference scaffold.")] = "single-shot",
    scaffold_b: Annotated[str, typer.Option(help="Candidate scaffold (the harness change).")] = (
        "self-repair"
    ),
    max_workers: Annotated[int, typer.Option(help="SWE-bench evaluator Docker workers.")] = 4,
    seed: Annotated[int, typer.Option(help="Base seed for attempt fan-out.")] = 0,
    out_dir: Annotated[Path, typer.Option(help="Where to write the report + suite JSON.")] = Path(
        ".murmur/bench"
    ),
    html: Annotated[
        Path | None,
        typer.Option(
            help="Write a standalone benchmark HTML report. Defaults to OUT_DIR/report.html."
        ),
    ] = None,
) -> None:
    """Deprecated legacy SWE-bench patch-only path; use fix-test / run-contract."""

    typer.echo(
        "`murmur bench` is deprecated as a public proof path. Use "
        "`murmur fix-test --cmd \"pytest ...\"` for contract-first execution, or "
        "`murmur run-contract .murmur/contracts/task.yaml` for an existing contract.",
        err=True,
    )
    raise typer.Exit(2)

    from murmur.benchmarks.swe.evaluator import SubprocessSweEvaluator
    from murmur.benchmarks.swe.providers import create_patch_model, default_model
    from murmur.benchmarks.swe.runner import compare_scaffolds, run_scaffold
    from murmur.benchmarks.swe.scaffold import BUILTIN_SCAFFOLDS
    from murmur.report.swe_md import render_benchmark_report

    for name in (scaffold_a, scaffold_b):
        if name not in BUILTIN_SCAFFOLDS:
            typer.echo(
                f"unknown scaffold {name!r}; built-ins: {', '.join(BUILTIN_SCAFFOLDS)}", err=True
            )
            raise typer.Exit(2)

    try:
        tasks = load_suite("swe-bench-verified", subset_size=subset or 0)
        patch_model = create_patch_model(
            provider=provider or None,
            model=model or default_model(provider),
        )
        evaluator = SubprocessSweEvaluator(run_dir=out_dir / "swebench", max_workers=max_workers)
        version = suite_version_for("swe-bench-verified", subset_size=subset or 0)
        ref = run_scaffold(
            tasks,
            scaffold=BUILTIN_SCAFFOLDS[scaffold_a](),
            model=patch_model,
            evaluator=evaluator,
            n=n,
            seed=seed,
            branch="bench",
            suite_version=version,
        )
        cand = run_scaffold(
            tasks,
            scaffold=BUILTIN_SCAFFOLDS[scaffold_b](),
            model=patch_model,
            evaluator=evaluator,
            n=n,
            seed=seed,
            branch="bench",
            suite_version=version,
        )
    except (BenchmarkDataUnavailable, BenchDependencyMissing) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    comparison = compare_scaffolds(ref, cand, k=k)
    report = render_benchmark_report(ref, cand, comparison, k=k, subset_label=f"{version}")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{scaffold_a}.json").write_text(
        json.dumps(ref.to_dict(), indent=2), encoding="utf-8"
    )
    (out_dir / f"{scaffold_b}.json").write_text(
        json.dumps(cand.to_dict(), indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(report + "\n", encoding="utf-8")
    html_out = write_benchmark_html(
        ref,
        cand,
        comparison,
        html or out_dir / "report.html",
        k=k,
        subset_label=f"{version}",
        markdown_report=report,
    )
    index_out = _write_local_preview_index(out_dir.parent)

    typer.echo(report)
    typer.echo(f"\nWritten to {out_dir}")
    typer.echo(f"Benchmark HTML written to {html_out}")
    typer.echo(f"Local index updated at {index_out}")


@app.command()
def doctor(
    provider: Annotated[
        str,
        typer.Option(
            help="Provider to verify: deepseek | anthropic (default: MURMUR_MODEL_PROVIDER)."
        ),
    ] = "",
    model: Annotated[str, typer.Option(help="Model id override.")] = "",
    ping: Annotated[
        bool, typer.Option(help="Send one minimal completion to verify the API key.")
    ] = True,
) -> None:
    """Show model-provider configuration and optionally ping the live API."""

    from murmur.benchmarks.swe.model import DeepSeekPatchModel
    from murmur.benchmarks.swe.providers import create_patch_model, default_model, provider_status
    from murmur.benchmarks.swe.types import BenchDependencyMissing

    status = provider_status(provider or None)
    typer.echo("Murmur model provider")
    for key, value in status.items():
        typer.echo(f"  {key}: {value}")
    if status.get("provider") == "deepseek":
        typer.echo(f"  reasoning_effort: {os.environ.get('DEEPSEEK_REASONING_EFFORT', 'high')}")
        typer.echo(f"  thinking: {os.environ.get('DEEPSEEK_THINKING', 'enabled')}")
        typer.echo(f"  model_default: {DeepSeekPatchModel.DEFAULT_MODEL}")
    env_path = os.environ.get("MURMUR_ENV_LOADED")
    if env_path:
        typer.echo(f"  env_file: {env_path}")

    if not ping:
        return

    try:
        patch_model = create_patch_model(
            provider=provider or None, model=model or default_model(provider)
        )
        patch_model.ensure_ready()
        response = patch_model.complete(
            system="You are a connectivity check.",
            user="Reply with exactly: ok",
            seed=0,
            max_tokens=32,
        )
    except BenchDependencyMissing as exc:
        typer.echo(f"\nNot ready: {exc}", err=True)
        raise typer.Exit(2) from exc

    snippet = (response.text or "").strip().replace("\n", " ")[:120]
    typer.echo(
        f"\nAPI ok — model={patch_model.model}  "
        f"tokens in/out={response.input_tokens}/{response.output_tokens}  "
        f"cost≈${response.cost_usd:.4f}  reply={snippet!r}"
    )


@agents_app.command("test-all")
def agents_test_all(
    provider: Annotated[str, typer.Option(help="deepseek | anthropic")] = "",
    model: Annotated[str, typer.Option(help="Model id override.")] = "",
    n: Annotated[int, typer.Option(min=1, help="Trajectories per agent.")] = 1,
    seed: Annotated[int, typer.Option(help="Per-lane seed base.")] = 7,
    skip_real: Annotated[
        bool, typer.Option(help="Only run free simulated agents (no API/Docker).")
    ] = False,
) -> None:
    """Run every registered agent module (simulated always; real unless --skip-real)."""

    from murmur.adapters.agents.registry import available, get
    from murmur.benchmarks.swe.types import BenchDependencyMissing

    failures: list[str] = []
    for name in available():
        module = get(name)
        if skip_real and not module.simulated:
            typer.echo(f"— skip {name} (real agent)")
            continue
        typer.echo(f"\n=== {name} ===")
        try:
            agents_run(
                name=name,
                n=n,
                seed=seed,
                provider=provider,
                model=model,
                event_log=Path(f".murmur/agents-{name}.jsonl"),
                html=None,
            )
        except (BenchDependencyMissing, typer.Exit) as exc:
            failures.append(name)
            typer.echo(f"failed: {exc}", err=True)
    if failures:
        typer.echo(f"\n{len(failures)} agent(s) failed: {', '.join(failures)}", err=True)
        raise typer.Exit(1)
    typer.echo("\nAll exercised agents completed.")


@agents_app.command("list")
def agents_list() -> None:
    """List registered agent modules and whether they need a live model."""

    from murmur.adapters.agents.registry import available, get

    for name in available():
        module = get(name)
        kind = "simulated" if module.simulated else "real (API + bench extra)"
        caps = ",".join(module.capabilities.labels()) or "none"
        typer.echo(f"{name:18}  {kind:28}  {caps:36}  {module.description}")


@agents_app.command("run")
def agents_run(
    name: Annotated[str, typer.Argument(help="Agent module name (see `murmur agents list`).")],
    n: Annotated[int, typer.Option(min=1, help="Trajectories to fan out.")] = 1,
    seed: Annotated[int, typer.Option(help="Per-lane seed base.")] = 7,
    task: Annotated[str, typer.Option(help="Task: demo | hard (or MURMUR_TASK).")] = "",
    provider: Annotated[str, typer.Option(help="deepseek | anthropic")] = "",
    model: Annotated[str, typer.Option(help="Model id override.")] = "",
    event_log: Annotated[Path, typer.Option(help="JSONL event log path.")] = Path(
        ".murmur/agents-run.jsonl"
    ),
    html: Annotated[
        Path | None, typer.Option(help="Write reliability fan HTML here.")
    ] = Path(".murmur/agents-fan.html"),
) -> None:
    """Run one registered agent module through the conductor (smoke / integration test)."""

    from murmur.adapters.agents.registry import get
    from murmur.benchmarks.swe.types import BenchDependencyMissing

    try:
        module = get(name)
        built = module.build(model=model, provider=provider)
    except KeyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except BenchDependencyMissing as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    store = JsonlEventStore(event_log, reset=True)
    conductor = RunConductor(
        agent_factory=built.agent_factory,
        storage=store,
        tools=built.tools,
        judge=built.judge,
    )
    spec = load_agent_task(task or None)
    result = asyncio.run(conductor.run(spec, n=n))
    events = list(asyncio.run(store.read_events()))

    typer.echo(render_run_report(result))
    typer.echo(f"task: {spec.task_id}")
    typer.echo("")
    typer.echo(render_fan(result, color=True))
    typer.echo(f"\nAgent {name!r} ({built.label}) — events written to {event_log}")
    if html is not None:
        out = write_fan_html(result, html, events=events)
        typer.echo(f"Fan report written to {out}")


def _example_plan_path() -> Path:
    return Path(__file__).resolve().parent / "flock" / "examples" / "resumes.yaml"


@flock_app.command("plan")
def flock_plan(
    task: Annotated[str, typer.Argument(help="Natural-language task to compile into a plan.")],
    source: Annotated[
        list[str] | None, typer.Option(help="A source name available at run start (repeatable).")
    ] = None,
    budget: Annotated[int, typer.Option(help="Token budget for the whole run.")] = 200_000,
    live: Annotated[
        bool, typer.Option(help="Use a real thinking model (deepseek-v4-pro) to write the plan.")
    ] = False,
    out: Annotated[Path | None, typer.Option(help="Write the plan YAML to this path.")] = None,
) -> None:
    """Compile a natural-language task into a validated Workflow IR plan.

    Offline (default) the planner model is a deterministic fake, so it falls back to a
    template plan with no API keys. ``--live`` uses DeepSeek to write a task-specific plan.
    """

    from murmur.flock.adapters.fake import FakeModel
    from murmur.flock.ir import dump_plan_yaml
    from murmur.flock.models import build_model
    from murmur.flock.planner import plan_workflow

    sources = tuple(source or [])
    model = build_model("deepseek-v4-pro") if live else FakeModel()
    try:
        plan = asyncio.run(
            plan_workflow(task, model=model, budget_tokens=budget, sources=sources)
        )
    except Exception as exc:  # noqa: BLE001 - surface any planning failure to the user
        typer.echo(f"planning failed: {exc}", err=True)
        raise typer.Exit(2) from exc

    yaml_text = dump_plan_yaml(plan)
    typer.echo(yaml_text)
    if out is not None:
        out.write_text(yaml_text, encoding="utf-8")
        typer.echo(f"plan written to {out}")


def _echo_flock_summary(plan, report) -> None:
    """Print a per-node summary, the cost totals, and the final output for a run."""

    for nid, result in report.results.items():
        status = "ok" if result.ok else f"ERROR {result.error}"
        typer.echo(
            f"  [{result.op:<10}] {nid:<12} -> "
            f"{len(result.output):>3} artifact(s), {result.calls:>3} call(s)   {status}"
        )
    typer.echo("")
    typer.echo(
        f"model calls: {report.model_calls}   "
        f"tokens: {report.spent_tokens}/{plan.budget_tokens}   "
        f"cost: ${report.spent_cost_usd:.4f}"
    )
    typer.echo("\nfinal:")
    for artifact in report.final:
        typer.echo(f"--- {artifact.id} ---")
        typer.echo(artifact.content[:1000])


def _load_items(items_file: Path | None) -> list[str]:
    if items_file is not None:
        return [ln for ln in items_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [f"Sample item {i}" for i in range(1, 7)]


@flock_app.command("run")
def flock_run(
    plan_file: Annotated[
        Path | None, typer.Argument(help="Plan YAML (defaults to the bundled resume example).")
    ] = None,
    items_file: Annotated[
        Path | None, typer.Option(help="Source items, one per line.")
    ] = None,
    source: Annotated[
        str, typer.Option(help="Name of the source the items feed (default: the plan's first).")
    ] = "",
    max_parallel: Annotated[int, typer.Option(help="Max concurrent subagent calls.")] = 8,
    live: Annotated[
        bool, typer.Option(help="Use real model adapters instead of offline fakes.")
    ] = False,
    untrusted: Annotated[
        bool, typer.Option(help="Mark the source as untrusted (taint quarantine applies).")
    ] = False,
    event_log: Annotated[
        Path | None, typer.Option(help="JSONL event log; reuse the same path to resume a run.")
    ] = None,
    trace: Annotated[
        Path | None, typer.Option(help="Write a markdown trace (DAG + per-node table) here.")
    ] = None,
) -> None:
    """Execute a Workflow IR plan and print per-node results, cost, and the final output."""

    from murmur.flock.eventlog import JsonlFlockLog
    from murmur.flock.ir import load_plan_yaml
    from murmur.flock.models import default_resolver, offline_resolver
    from murmur.flock.report import render_run_report
    from murmur.flock.scheduler import execute_plan

    path = plan_file or _example_plan_path()
    try:
        plan = load_plan_yaml(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - surface load/parse/validation errors
        typer.echo(f"failed to load plan {path}: {exc}", err=True)
        raise typer.Exit(2) from exc

    src_name = source or (plan.sources[0] if plan.sources else "input")
    items = _load_items(items_file)
    resolver = default_resolver() if live else offline_resolver()
    log = JsonlFlockLog(event_log) if event_log is not None else None

    report = asyncio.run(
        execute_plan(
            plan,
            sources={src_name: items},
            resolver=resolver,
            max_parallel=max_parallel,
            event_log=log,
            untrusted_sources=[src_name] if untrusted else None,
        )
    )

    mode = "live" if live else "offline (deterministic fakes)"
    typer.echo(f"goal: {report.goal}")
    typer.echo(f"mode: {mode}   source: {src_name} ({len(items)} items)")
    typer.echo("")
    _echo_flock_summary(plan, report)

    if trace is not None:
        trace.write_text(render_run_report(report, plan=plan), encoding="utf-8")
        typer.echo(f"\ntrace written to {trace}")

    if not report.ok:
        raise typer.Exit(1)


@flock_app.command("improve")
def flock_improve(
    task: Annotated[str, typer.Argument(help="Natural-language task to plan and run.")],
    source: Annotated[
        str, typer.Option(help="Name of the source the items feed.")
    ] = "items",
    items_file: Annotated[
        Path | None, typer.Option(help="Source items, one per line.")
    ] = None,
    k: Annotated[int, typer.Option(help="Number of candidate plans to generate and race.")] = 3,
    library: Annotated[
        Path, typer.Option(help="Template library directory (mined winners are saved here).")
    ] = Path(".murmur/flock/templates"),
    budget: Annotated[int, typer.Option(help="Token budget per candidate run.")] = 200_000,
    live: Annotated[
        bool, typer.Option(help="Use real models (deepseek-v4-pro planner + live adapters).")
    ] = False,
    trace: Annotated[
        Path | None, typer.Option(help="Write a markdown trace of the chosen run here.")
    ] = None,
) -> None:
    """Self-improving plan: reuse a proven template for this task, or race K candidates.

    On a library miss it generates K candidate workflows, runs them all, keeps the best,
    and distills the winner into the library so the next similar task is cheap.
    """

    from murmur.flock.adapters.fake import FakeModel
    from murmur.flock.improve import self_improving_plan
    from murmur.flock.library import TemplateLibrary
    from murmur.flock.models import build_model, default_resolver, offline_resolver
    from murmur.flock.report import render_run_report
    from murmur.flock.scheduler import execute_plan

    items = _load_items(items_file)
    model = build_model("deepseek-v4-pro") if live else FakeModel()
    resolver = default_resolver() if live else offline_resolver()
    lib = TemplateLibrary(library)

    try:
        decision = asyncio.run(
            self_improving_plan(
                task,
                model=model,
                library=lib,
                sources=[source],
                source_values={source: items},
                k=k,
                resolver=resolver,
                budget_tokens=budget,
            )
        )
    except Exception as exc:  # noqa: BLE001 - surface planning/run failures to the user
        typer.echo(f"improve failed: {exc}", err=True)
        raise typer.Exit(2) from exc

    if decision.origin == "mined" and decision.tournament is not None:
        report = decision.tournament.winner.report
        cands = decision.tournament.candidates
        scores = ", ".join(f"{c.score:.1f}" for c in cands)
        typer.echo(f"origin: mined (raced {len(cands)} candidates; scores: {scores})")
    else:
        report = asyncio.run(
            execute_plan(decision.plan, sources={source: items}, resolver=resolver)
        )
        typer.echo("origin: reused (matched a previously mined template)")
    if decision.template is not None:
        typer.echo(f"template: {decision.template.name}   library: {library}")
    typer.echo(f"goal: {task}   source: {source} ({len(items)} items)")
    typer.echo("")
    _echo_flock_summary(decision.plan, report)

    if trace is not None:
        trace.write_text(render_run_report(report, plan=decision.plan), encoding="utf-8")
        typer.echo(f"\ntrace written to {trace}")

    if not report.ok:
        raise typer.Exit(1)
