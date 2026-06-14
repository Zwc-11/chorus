"""Phase 0 record/replay tests.

These tests prove the current harness can record a deterministic run, replay it,
and reject a replay when the task prompt changes.
"""

from __future__ import annotations

import asyncio

import pytest

from murmur.adapters.agents.fake import FakeAgent, fake_tools
from murmur.adapters.storage.memory import InMemoryEventStore
from murmur.core.conductor import RunConductor
from murmur.core.events import EventType
from murmur.core.types import TaskSpec
from murmur.gateway.tool_gateway import ReplayDivergenceError


def run_async(value):
    return asyncio.run(value)


def build_conductor(store: InMemoryEventStore) -> RunConductor:
    return RunConductor(agent=FakeAgent(), storage=store, tools=fake_tools())


def test_dummy_run_records_and_replays() -> None:
    store = InMemoryEventStore()
    conductor = build_conductor(store)
    task = TaskSpec(
        task_id="demo.echo_uppercase",
        prompt="hello chorus",
        expected_output="HELLO CHORUS",
    )

    result = run_async(conductor.run(task, n=2))
    events = list(run_async(store.read_events()))
    replayed = run_async(conductor.replay(events=events, task=task))

    assert result.verdict == "pass"
    assert result.metrics.pass_at_k == 1.0
    assert replayed == "HELLO CHORUS"
    assert EventType.RUN_STARTED in {event.type for event in events}
    assert EventType.TOOL_CALL in {event.type for event in events}
    assert EventType.TOOL_RESULT in {event.type for event in events}
    assert EventType.VERDICT in {event.type for event in events}


def test_replay_detects_divergence_when_step_changes() -> None:
    store = InMemoryEventStore()
    conductor = build_conductor(store)
    recorded_task = TaskSpec(
        task_id="demo.echo_uppercase",
        prompt="hello chorus",
        expected_output="HELLO CHORUS",
    )
    mutated_task = TaskSpec(
        task_id="demo.echo_uppercase",
        prompt="hello mutated chorus",
        expected_output="HELLO CHORUS",
    )

    run_async(conductor.run(recorded_task, n=1))
    events = list(run_async(store.read_events()))

    with pytest.raises(ReplayDivergenceError):
        run_async(conductor.replay(events=events, task=mutated_task))
