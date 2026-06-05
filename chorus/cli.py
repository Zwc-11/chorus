"""Command-line interface for Chorus.

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

from chorus.adapters.agents.fake import FakeAgent, fake_tools
from chorus.adapters.agents.stochastic import stochastic_agent_factory, stochastic_tools
from chorus.adapters.storage.baseline import BaselineStore
from chorus.adapters.storage.jsonl import JsonlEventStore
from chorus.application.contract_compiler import compile_fix_test_contract
from chorus.application.fix_test import (
    proof_summary,
    run_contract,
    run_fix_test,
    validate_fix_test_workflow,
)
from chorus.application.workflow_planner import TEMPLATES, plan_from_task
from chorus.application.workflow_runtime import WorkflowRuntime, explain_workflow
from chorus.benchmarks.loader import load_suite, suite_version_for
from chorus.benchmarks.scaffold import Scaffold, run_suite
from chorus.benchmarks.swe.types import BenchDependencyMissing
from chorus.benchmarks.swebench import BenchmarkDataUnavailable
from chorus.config import load_project_env
from chorus.core.agent_tasks import demo_task, load_agent_task
from chorus.core.conductor import RunConductor
from chorus.core.events import Event, EventType
from chorus.core.regression import baseline_set_report, regression_verdict
from chorus.domain.contract import Contract
from chorus.domain.workflow import WorkflowPlan
from chorus.gateway.tool_gateway import ReplayDivergenceError
from chorus.report.fan import render_fan
from chorus.report.fan_html import write_fan_html
from chorus.report.markdown import render_run_report
from chorus.report.murmur_workflow_html import write_murmur_workflow_html
from chorus.report.regression_md import render_regression_comment
from chorus.report.swe_html import write_benchmark_html
from chorus.report.trace_html import write_traces_html
from chorus.trace.mapper import events_to_traces

app = typer.Typer(no_args_is_help=True)
agents_app = typer.Typer(help="List and exercise registered agent modules.")
contract_app = typer.Typer(help="Create and validate Chorus engineering contracts.")
workflow_app = typer.Typer(help="Create and validate Murmur workflow plans.")
app.add_typer(agents_app, name="agents")
app.add_typer(contract_app, name="contract")
app.add_typer(workflow_app, name="workflow")


@app.callback()
def _main() -> None:
    """Chorus — contract and proof layer for AI-generated code changes."""

    loaded = load_project_env()
    if loaded is not None:
        os.environ.setdefault("CHORUS_ENV_LOADED", str(loaded))

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
    """Create a minimal Chorus starter setup for an agent repository."""

    targets = {
        root / "tasks" / "chorus-smoke.yaml": _starter_task(),
        root / ".github" / "workflows" / "chorus.yml": _starter_workflow(),
        root / ".chorus" / "README.md": _starter_notes(),
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
    typer.echo("\nNext: run `chorus run --n 5` or wire your agent through AgentPort.")


def _starter_task() -> str:
    return """# Minimal Chorus task. Replace this with a repo-specific agent task.
task_id: chorus.smoke
expected_output: HELLO CHORUS
metadata:
  kind: smoke
prompt: |
  Reply with exactly: HELLO CHORUS
"""


def _starter_workflow() -> str:
    return """name: Chorus reliability gate

on:
  pull_request:
  workflow_dispatch:

jobs:
  chorus:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install -e ".[dev]"
      - run: chorus gate --suite synthetic --n 20 --k 5
"""


def _starter_notes() -> str:
    return """# Chorus local notes

This directory is for event logs, fan reports, traces, and baseline files.

Useful commands:

- `chorus agents list`
- `chorus run --n 30`
- `chorus trace --n 12 --replay`
- `chorus gate --suite synthetic --n 20 --k 5`
- `chorus fix-test --cmd "python -m pytest tests/test_example.py -q"`
"""


def _write_local_preview_index(root: Path) -> Path:
    """Write the small static launcher for generated local reports."""

    write_murmur_workflow_html(root / "murmur.html")

    links = [
        (
            "murmur",
            "Workflow tree",
            "murmur.html",
            "self-writing plan · agent fan-out · growing DAG visualization",
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
<title>Chorus local preview</title>
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
  <h1>chorus</h1>
  <p>
    Local HUD preview generated from Chorus reliability runs, traces, and
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


@app.command("murmur-preview")
def murmur_preview(
    out_dir: Annotated[
        Path, typer.Option(help="Directory for murmur.html and index.html.")
    ] = Path(".chorus/preview"),
) -> None:
    """Write the Murmur workflow tree demo and refresh the local preview index."""

    out_dir.mkdir(parents=True, exist_ok=True)
    html_out = write_murmur_workflow_html(out_dir / "murmur.html")
    index_out = _write_local_preview_index(out_dir)
    typer.echo(f"Murmur workflow UI written to {html_out}")
    typer.echo(f"Preview index written to {index_out}")


@app.command("fix-test")
def fix_test(
    cmd: Annotated[str, typer.Option("--cmd", help="Failing test command to reproduce/fix.")],
    budget: Annotated[float, typer.Option(help="Maximum model/tool budget in USD.")] = 0.50,
    agent: Annotated[
        str, typer.Option(help="Contract agent: scripted | chorus-lite.")
    ] = "scripted",
    repo_root: Annotated[Path, typer.Option(help="Repository root to execute in.")] = Path("."),
    out_dir: Annotated[Path, typer.Option(help="Root directory for proof runs.")] = Path(
        ".chorus/runs"
    ),
    provider: Annotated[str, typer.Option(help="Provider for chorus-lite.")] = "",
    model: Annotated[str, typer.Option(help="Model id for chorus-lite.")] = "",
    n: Annotated[int, typer.Option(min=1, help="Number of isolated repair attempts.")] = 1,
    max_repairs: Annotated[
        int, typer.Option(min=0, help="Maximum repair iterations per failed attempt.")
    ] = 0,
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
        )
    except (KeyError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    run_path = out_dir / proof.run_id
    typer.echo("# Chorus contract proof")
    typer.echo(proof_summary(proof))
    typer.echo(f"\nProof written to {run_path / 'proof.md'}")
    typer.echo(f"HTML report written to {run_path / 'report.html'}")
    raise typer.Exit(0 if proof.verdict == "pass" else 1)


@contract_app.command("create")
def contract_create(
    from_test: Annotated[
        str, typer.Option("--from-test", help="Failing test command to compile into a contract.")
    ],
    repo_root: Annotated[Path, typer.Option(help="Repository root.")] = Path("."),
    budget: Annotated[float, typer.Option(help="Maximum budget in USD.")] = 0.50,
    out: Annotated[Path, typer.Option(help="Contract YAML output path.")] = Path(
        ".chorus/contracts/fix-test.yaml"
    ),
) -> None:
    """Compile a failing-test command into a Chorus contract YAML file."""

    contract = compile_fix_test_contract(
        command=from_test,
        repo_root=repo_root,
        budget_usd=budget,
    )
    contract.write(out)
    typer.echo(f"Contract written to {out}")


@contract_app.command("check")
def contract_check(path: Annotated[Path, typer.Argument(help="Contract YAML path.")]) -> None:
    """Validate a Chorus contract YAML file."""

    contract = Contract.read(path)
    issues = contract.validate()
    if issues:
        for issue in issues:
            typer.echo(f"error: {issue}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Contract OK: {path}")


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
        ".chorus/runs"
    ),
    contract_path: Annotated[
        Path | None,
        typer.Option("--contract", help="Optional contract YAML for policy-controlled exec nodes."),
    ] = None,
    resume: Annotated[bool, typer.Option(help="Reuse matching completed node evidence.")] = False,
    concurrency: Annotated[int, typer.Option(min=1, help="Maximum ready nodes to schedule.")] = 1,
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
    runtime = WorkflowRuntime(
        repo_root=repo_root,
        out_root=out_dir,
        contract=contract,
        concurrency=concurrency,
        resume=resume,
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


@app.command("plan")
def plan_workflow(
    task: Annotated[str, typer.Option("--task", help="Natural-language task for Murmur.")],
    out: Annotated[Path, typer.Option(help="Workflow YAML output path.")] = Path(
        ".chorus/workflows/murmur.yaml"
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
) -> None:
    """Create a validated Murmur workflow from an approved template."""

    try:
        workflow = plan_from_task(
            task=task,
            template=template,
            command=cmd,
            attempts=n,
            max_repairs=max_repairs,
        )
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
    typer.echo(f"Template: {workflow.name}")


@app.command("run-contract")
def run_contract_command(
    path: Annotated[Path, typer.Argument(help="Contract YAML path.")],
    agent: Annotated[
        str, typer.Option(help="Contract agent: scripted | chorus-lite.")
    ] = "scripted",
    out_dir: Annotated[Path, typer.Option(help="Root directory for proof runs.")] = Path(
        ".chorus/runs"
    ),
    provider: Annotated[str, typer.Option(help="Provider for chorus-lite.")] = "",
    model: Annotated[str, typer.Option(help="Model id for chorus-lite.")] = "",
) -> None:
    """Execute an existing Chorus contract."""

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
    )
    run_path = out_dir / proof.run_id
    typer.echo("# Chorus contract proof")
    typer.echo(proof_summary(proof))
    typer.echo(f"\nProof written to {run_path / 'proof.md'}")
    typer.echo(f"HTML report written to {run_path / 'report.html'}")
    raise typer.Exit(0 if proof.verdict == "pass" else 1)


@app.command()
def demo(
    n: Annotated[int, typer.Option(min=1, help="Number of trajectories to run.")] = 3,
    task: Annotated[str, typer.Option(help="Task: demo | hard (or CHORUS_TASK).")] = "",
    event_log: Annotated[Path, typer.Option(help="JSONL event log path.")] = Path(
        ".chorus/demo.jsonl"
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
    task: Annotated[str, typer.Option(help="Task: demo | hard (or CHORUS_TASK).")] = "",
    success_rate: Annotated[
        float, typer.Option(min=0.0, max=1.0, help="Per-run success probability of the agent.")
    ] = 0.7,
    error_rate: Annotated[
        float, typer.Option(min=0.0, max=1.0, help="Probability a run hits a flaky tool (errors).")
    ] = 0.1,
    seed: Annotated[int, typer.Option(help="Base seed; run is fully reproducible.")] = 7,
    event_log: Annotated[Path, typer.Option(help="JSONL event log path.")] = Path(
        ".chorus/run.jsonl"
    ),
    html: Annotated[
        Path | None, typer.Option(help="Write a standalone HTML/SVG trajectory fan here.")
    ] = Path(".chorus/fan.html"),
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
    task: Annotated[str, typer.Option(help="Task: demo | hard (or CHORUS_TASK).")] = "",
    success_rate: Annotated[float, typer.Option(min=0.0, max=1.0)] = 0.7,
    error_rate: Annotated[float, typer.Option(min=0.0, max=1.0)] = 0.1,
    seed: Annotated[int, typer.Option(help="Base seed; run is fully reproducible.")] = 7,
    event_log: Annotated[Path, typer.Option(help="JSONL event log path.")] = Path(
        ".chorus/trace.jsonl"
    ),
    html: Annotated[Path, typer.Option(help="Trace viewer output path.")] = Path(
        ".chorus/trace.html"
    ),
    replay: Annotated[
        bool, typer.Option(help="Verify replay and mark spans chorus.replay=true.")
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

    typer.echo(f"# Chorus trace {result.run_id}")
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
        from chorus.adapters.trace.otlp import (
            OtelNotInstalled,
            build_otlp_trace_port,
            langsmith_project_url,
        )
        from chorus.trace.emit import emit_traces

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
            resolved_project = os.environ.get("LANGSMITH_PROJECT", "chorus")
            typer.echo(
                f"Open LangSmith project {resolved_project!r}: "
                f"{langsmith_project_url(resolved_project)}"
            )


@app.command()
def replay(
    event_log: Annotated[Path, typer.Option(help="JSONL event log path.")] = Path(
        ".chorus/demo.jsonl"
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

    from chorus.adapters.agents.swe import SwePatchAgent
    from chorus.benchmarks.scaffold import run_judged_suite_batched
    from chorus.benchmarks.swe.evaluator import SubprocessSweEvaluator
    from chorus.benchmarks.swe.judge import SweBenchJudge
    from chorus.benchmarks.swe.providers import create_patch_model, default_model

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

    from chorus.report.trace_html import write_traces_html
    from chorus.trace.mapper import events_to_traces

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
        ".chorus/baselines"
    ),
    update_baseline: Annotated[
        bool,
        typer.Option(help="Persist this run as the baseline (use on the base branch / merge)."),
    ] = False,
    comment_out: Annotated[Path, typer.Option(help="Write the PR comment markdown here.")] = Path(
        ".chorus/gate.md"
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
            help="LLM provider: deepseek | anthropic (default from CHORUS_MODEL_PROVIDER)."
        ),
    ] = "",
    trace_html: Annotated[
        Path, typer.Option(help="With --real-agent: write the SWE-bench trace viewer here.")
    ] = Path(".chorus/swe-trace.html"),
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
            help="LLM provider: deepseek | anthropic (default from CHORUS_MODEL_PROVIDER)."
        ),
    ] = "",
    scaffold_a: Annotated[str, typer.Option(help="Reference scaffold.")] = "single-shot",
    scaffold_b: Annotated[str, typer.Option(help="Candidate scaffold (the harness change).")] = (
        "self-repair"
    ),
    max_workers: Annotated[int, typer.Option(help="SWE-bench evaluator Docker workers.")] = 4,
    seed: Annotated[int, typer.Option(help="Base seed for attempt fan-out.")] = 0,
    out_dir: Annotated[Path, typer.Option(help="Where to write the report + suite JSON.")] = Path(
        ".chorus/bench"
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
        "`chorus bench` is deprecated as a public proof path. Use "
        "`chorus fix-test --cmd \"pytest ...\"` for contract-first execution, or "
        "`chorus run-contract .chorus/contracts/task.yaml` for an existing contract.",
        err=True,
    )
    raise typer.Exit(2)

    from chorus.benchmarks.swe.evaluator import SubprocessSweEvaluator
    from chorus.benchmarks.swe.providers import create_patch_model, default_model
    from chorus.benchmarks.swe.runner import compare_scaffolds, run_scaffold
    from chorus.benchmarks.swe.scaffold import BUILTIN_SCAFFOLDS
    from chorus.report.swe_md import render_benchmark_report

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
            help="Provider to verify: deepseek | anthropic (default: CHORUS_MODEL_PROVIDER)."
        ),
    ] = "",
    model: Annotated[str, typer.Option(help="Model id override.")] = "",
    ping: Annotated[
        bool, typer.Option(help="Send one minimal completion to verify the API key.")
    ] = True,
) -> None:
    """Show model-provider configuration and optionally ping the live API."""

    from chorus.benchmarks.swe.model import DeepSeekPatchModel
    from chorus.benchmarks.swe.providers import create_patch_model, default_model, provider_status
    from chorus.benchmarks.swe.types import BenchDependencyMissing

    status = provider_status(provider or None)
    typer.echo("Chorus model provider")
    for key, value in status.items():
        typer.echo(f"  {key}: {value}")
    if status.get("provider") == "deepseek":
        typer.echo(f"  reasoning_effort: {os.environ.get('DEEPSEEK_REASONING_EFFORT', 'high')}")
        typer.echo(f"  thinking: {os.environ.get('DEEPSEEK_THINKING', 'enabled')}")
        typer.echo(f"  model_default: {DeepSeekPatchModel.DEFAULT_MODEL}")
    env_path = os.environ.get("CHORUS_ENV_LOADED")
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

    from chorus.adapters.agents.registry import available, get
    from chorus.benchmarks.swe.types import BenchDependencyMissing

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
                event_log=Path(f".chorus/agents-{name}.jsonl"),
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

    from chorus.adapters.agents.registry import available, get

    for name in available():
        module = get(name)
        kind = "simulated" if module.simulated else "real (API + bench extra)"
        caps = ",".join(module.capabilities.labels()) or "none"
        typer.echo(f"{name:18}  {kind:28}  {caps:36}  {module.description}")


@agents_app.command("run")
def agents_run(
    name: Annotated[str, typer.Argument(help="Agent module name (see `chorus agents list`).")],
    n: Annotated[int, typer.Option(min=1, help="Trajectories to fan out.")] = 1,
    seed: Annotated[int, typer.Option(help="Per-lane seed base.")] = 7,
    task: Annotated[str, typer.Option(help="Task: demo | hard (or CHORUS_TASK).")] = "",
    provider: Annotated[str, typer.Option(help="deepseek | anthropic")] = "",
    model: Annotated[str, typer.Option(help="Model id override.")] = "",
    event_log: Annotated[Path, typer.Option(help="JSONL event log path.")] = Path(
        ".chorus/agents-run.jsonl"
    ),
    html: Annotated[
        Path | None, typer.Option(help="Write reliability fan HTML here.")
    ] = Path(".chorus/agents-fan.html"),
) -> None:
    """Run one registered agent module through the conductor (smoke / integration test)."""

    from chorus.adapters.agents.registry import get
    from chorus.benchmarks.swe.types import BenchDependencyMissing

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
