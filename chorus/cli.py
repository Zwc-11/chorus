"""Command-line interface for Chorus.

This file turns the Phase 0 harness into commands a user can run: record a
dummy run, replay it, and intentionally mutate it to prove divergence detection.
"""

from __future__ import annotations

import asyncio
import contextlib
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
from chorus.benchmarks.loader import load_suite
from chorus.benchmarks.scaffold import Scaffold, run_suite
from chorus.core.conductor import RunConductor
from chorus.core.events import Event, EventType
from chorus.core.regression import baseline_set_report, regression_verdict
from chorus.core.types import TaskSpec
from chorus.gateway.tool_gateway import ReplayDivergenceError
from chorus.report.fan import render_fan
from chorus.report.fan_html import write_fan_html
from chorus.report.markdown import render_run_report
from chorus.report.regression_md import render_regression_comment
from chorus.report.trace_html import write_traces_html
from chorus.trace.mapper import events_to_traces

app = typer.Typer(no_args_is_help=True)


@app.callback()
def _main() -> None:
    """Chorus — a reliability and cost harness for coding agents."""

    # Prefer UTF-8 so the trajectory-fan glyphs render on modern terminals
    # (Windows consoles default to a legacy code page). The fan renderer falls
    # back to ASCII if this is not possible.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(encoding="utf-8")


def demo_task(*, mutate: bool = False) -> TaskSpec:
    prompt = "hello chorus"
    if mutate:
        prompt = "hello mutated chorus"
    return TaskSpec(
        task_id="demo.echo_uppercase",
        prompt=prompt,
        expected_output="HELLO CHORUS",
    )


@app.command()
def demo(
    n: Annotated[int, typer.Option(min=1, help="Number of trajectories to run.")] = 3,
    event_log: Annotated[Path, typer.Option(help="JSONL event log path.")] = Path(
        ".chorus/demo.jsonl"
    ),
) -> None:
    """Record a deterministic fake-agent run."""

    store = JsonlEventStore(event_log, reset=True)
    conductor = RunConductor(agent=FakeAgent(), storage=store, tools=fake_tools())
    result = asyncio.run(conductor.run(demo_task(), n=n))
    typer.echo(render_run_report(result))
    typer.echo(f"\nEvents written to {event_log}")


@app.command()
def run(
    n: Annotated[int, typer.Option(min=1, help="Number of trajectories to fan out.")] = 30,
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
    conductor = RunConductor(agent_factory=factory, storage=store, tools=stochastic_tools())
    result = asyncio.run(conductor.run(demo_task(), n=n))
    events = list(asyncio.run(store.read_events()))

    typer.echo(render_run_report(result))
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
    result = asyncio.run(conductor.run(demo_task(), n=n))
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
        from chorus.adapters.trace.otlp import OtelNotInstalled, build_otlp_trace_port
        from chorus.trace.emit import emit_traces

        try:
            port = build_otlp_trace_port(backend=backend, endpoint=endpoint)
            emit_traces(traces, port)
            typer.echo(f"Exported {len(traces)} traces over OTLP to {backend}.")
        except OtelNotInstalled as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc


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
) -> None:
    """Run the suite and gate on a *statistical* regression vs the stored baseline."""

    tasks = load_suite(suite)
    resolved_branch = branch or _detect_branch()
    scaffold_spec = Scaffold(name=scaffold, success_delta=success_delta, error_rate=error_rate)
    candidate = asyncio.run(
        run_suite(
            tasks,
            scaffold=scaffold_spec,
            n=n,
            seed=seed,
            branch=resolved_branch,
            commit=_detect_commit(),
        )
    )
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
