from __future__ import annotations

from collections.abc import Sequence

from chorus.core.events import Event


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: list[Event] = []

    async def append(self, event: Event) -> None:
        self._events.append(event)

    async def read_events(self) -> Sequence[Event]:
        return tuple(self._events)
