"""Event model and recorder.

This file defines the append-only events that are the source of truth for
Chorus. Runs, tool calls, verdicts, and replay checks are all derived from
these recorded events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import uuid4

from murmur.core.ports import StoragePort


class EventType(StrEnum):
    RUN_STARTED = "run_started"
    TRAJECTORY_STARTED = "trajectory_started"
    STEP_STARTED = "step_started"
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONTRACT_CHECK = "contract_check"
    VERDICT = "verdict"
    TRAJECTORY_FINISHED = "trajectory_finished"
    RUN_FINISHED = "run_finished"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def hash_payload(value: Any) -> str:
    return sha256(stable_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Event:
    run_id: str
    trajectory_id: str | None
    seq: int
    type: EventType
    ts: str
    payload: dict[str, Any]
    hash: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        trajectory_id: str | None,
        seq: int,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> Event:
        event_hash = hash_payload(
            {
                "run_id": run_id,
                "trajectory_id": trajectory_id,
                "seq": seq,
                "type": event_type.value,
                "payload": payload,
            }
        )
        return cls(
            run_id=run_id,
            trajectory_id=trajectory_id,
            seq=seq,
            type=event_type,
            ts=utc_now(),
            payload=payload,
            hash=event_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trajectory_id": self.trajectory_id,
            "seq": self.seq,
            "type": self.type.value,
            "ts": self.ts,
            "payload": self.payload,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        return cls(
            run_id=str(data["run_id"]),
            trajectory_id=data.get("trajectory_id"),
            seq=int(data["seq"]),
            type=EventType(data["type"]),
            ts=str(data["ts"]),
            payload=dict(data.get("payload", {})),
            hash=str(data["hash"]),
        )


class EventRecorder:
    """Append-only event writer for one run or trajectory stream."""

    def __init__(self, storage: StoragePort, run_id: str, trajectory_id: str | None = None) -> None:
        self._storage = storage
        self._run_id = run_id
        self._trajectory_id = trajectory_id
        self._seq = 0

    async def emit(self, event_type: EventType, payload: dict[str, Any]) -> Event:
        self._seq += 1
        event = Event.create(
            run_id=self._run_id,
            trajectory_id=self._trajectory_id,
            seq=self._seq,
            event_type=event_type,
            payload=payload,
        )
        await self._storage.append(event)
        return event
