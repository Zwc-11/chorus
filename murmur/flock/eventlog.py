"""Event sourcing for resumable runs.

Every node start/finish/failure is appended to an event log. Kill the process and
re-run with the same log and the executor replays it: nodes that already *finished*
are restored from their recorded output and skipped, so a resumed run only does the
work that did not complete — and finished work costs nothing the second time.

Two stores implement the same tiny interface: an in-memory log (tests, single
process) and a JSONL file log (survives a kill). The log is append-only — the
Event-Sourcing + Memento pattern from the design catalog.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from murmur.flock.artifact import Artifact

# Event kinds.
RUN_STARTED = "run_started"
NODE_FINISHED = "node_finished"
NODE_FAILED = "node_failed"
RUN_FINISHED = "run_finished"


@dataclass(frozen=True, slots=True)
class FlockEvent:
    """One append-only record in a run's history."""

    kind: str
    node_id: str = ""
    op: str = ""
    calls: int = 0
    artifacts: tuple[dict[str, Any], ...] = ()
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "node_id": self.node_id,
            "op": self.op,
            "calls": self.calls,
            "artifacts": list(self.artifacts),
            "error": self.error,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlockEvent:
        return cls(
            kind=str(data["kind"]),
            node_id=str(data.get("node_id", "")),
            op=str(data.get("op", "")),
            calls=int(data.get("calls", 0) or 0),
            artifacts=tuple(data.get("artifacts", ()) or ()),
            error=data.get("error"),
            meta=dict(data.get("meta", {}) or {}),
        )

    @classmethod
    def node_finished(
        cls, node_id: str, op: str, output: tuple[Artifact, ...], calls: int
    ) -> FlockEvent:
        return cls(
            kind=NODE_FINISHED,
            node_id=node_id,
            op=op,
            calls=calls,
            artifacts=tuple(a.to_dict() for a in output),
        )


class FlockLog(Protocol):
    """Append-only sink + reader for :class:`FlockEvent`."""

    def append(self, event: FlockEvent) -> None: ...

    def read(self) -> list[FlockEvent]: ...


class InMemoryFlockLog:
    """An in-process append-only log (no persistence)."""

    def __init__(self) -> None:
        self._events: list[FlockEvent] = []

    def append(self, event: FlockEvent) -> None:
        self._events.append(event)

    def read(self) -> list[FlockEvent]:
        return list(self._events)


class JsonlFlockLog:
    """A JSONL file log that survives a process kill (one event per line)."""

    def __init__(self, path: str | Path, *, reset: bool = False) -> None:
        self._path = Path(path)
        if reset and self._path.exists():
            self._path.unlink()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: FlockEvent) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def read(self) -> list[FlockEvent]:
        if not self._path.exists():
            return []
        events: list[FlockEvent] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(FlockEvent.from_dict(json.loads(line)))
        return events


@dataclass(frozen=True, slots=True)
class CompletedNode:
    """A node restored from the log: its recorded output and call count."""

    op: str
    output: tuple[Artifact, ...]
    calls: int


def completed_nodes(events: list[FlockEvent]) -> dict[str, CompletedNode]:
    """Map node id → its finished output, replaying the log.

    Only ``node_finished`` events count; a later finish for the same node overrides an
    earlier one, and a node that only ever ``node_failed`` is absent (so it re-runs).
    """

    done: dict[str, CompletedNode] = {}
    for e in events:
        if e.kind == NODE_FINISHED:
            done[e.node_id] = CompletedNode(
                op=e.op,
                output=tuple(Artifact.from_dict(a) for a in e.artifacts),
                calls=e.calls,
            )
    return done
