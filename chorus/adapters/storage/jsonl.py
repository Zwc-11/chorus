from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from chorus.core.events import Event


class JsonlEventStore:
    """Zero-config append-only event store."""

    def __init__(self, path: Path | str, *, reset: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if reset and self.path.exists():
            self.path.unlink()

    async def append(self, event: Event) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")

    async def read_events(self) -> Sequence[Event]:
        if not self.path.exists():
            return []
        events: list[Event] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    events.append(Event.from_dict(json.loads(line)))
        return events
