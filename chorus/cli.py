"""Command-line interface for Chorus.

This file turns the Phase 0 harness into commands a user can run: record a
dummy run, replay it, and intentionally mutate it to prove divergence detection.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Annotated

import typer

from chorus.adapters.agents.fake import FakeAgent, fake_tools
from chorus.adapters.agents.stochastic import stochastic_agent_factory, stochastic_tools
from chorus.adapters.storage.jsonl import JsonlEventStore
from chorus.core.conductor import RunConductor
from chorus.core.types import TaskSpec
from chorus.gateway.tool_gateway import ReplayDivergenceError
from chorus.report.fan import render_fan
from chorus.report.fan_html import write_fan_html
from chorus.report.markdown import render_run_report

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
    n: Annotated[int, typer.Option(min=1, help="Number of trajectories to fan out.")] = 12,
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

    typer.echo(render_run_report(result))
    typer.echo("")
    typer.echo(render_fan(result, color=not no_color))
    typer.echo(f"\nEvents written to {event_log}")
    if html is not None:
        out = write_fan_html(result, html)
        typer.echo(f"Trajectory fan written to {out}  (open in a browser)")


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
