from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from chorus.adapters.agents.fake import FakeAgent, fake_tools
from chorus.adapters.storage.jsonl import JsonlEventStore
from chorus.core.conductor import RunConductor
from chorus.core.types import TaskSpec
from chorus.gateway.tool_gateway import ReplayDivergenceError
from chorus.report.markdown import render_run_report

app = typer.Typer(no_args_is_help=True)


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
    event_log: Annotated[
        Path, typer.Option(help="JSONL event log path.")
    ] = Path(".chorus/demo.jsonl"),
) -> None:
    """Record a deterministic fake-agent run."""

    store = JsonlEventStore(event_log, reset=True)
    conductor = RunConductor(agent=FakeAgent(), storage=store, tools=fake_tools())
    result = asyncio.run(conductor.run(demo_task(), n=n))
    typer.echo(render_run_report(result))
    typer.echo(f"\nEvents written to {event_log}")


@app.command()
def replay(
    event_log: Annotated[
        Path, typer.Option(help="JSONL event log path.")
    ] = Path(".chorus/demo.jsonl"),
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
