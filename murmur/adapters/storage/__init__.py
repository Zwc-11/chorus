"""Storage adapter exports.

Storage adapters persist the append-only event log. The core only depends on
the StoragePort shape, while this package exposes concrete implementations.
"""

from murmur.adapters.storage.jsonl import JsonlEventStore
from murmur.adapters.storage.memory import InMemoryEventStore

__all__ = ["InMemoryEventStore", "JsonlEventStore"]
