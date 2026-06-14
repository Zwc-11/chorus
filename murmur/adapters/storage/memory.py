"""In-memory event store.

This file keeps events in a Python list. It is meant for tests and fast local
checks where writing to disk would add noise.
"""

from __future__ import annotations

from collections.abc import Sequence

from murmur.core.events import Event


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: list[Event] = []

    async def append(self, event: Event) -> None:
        self._events.append(event)

    async def read_events(self) -> Sequence[Event]:
        return tuple(self._events)
