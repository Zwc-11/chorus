"""Typed tool request/result records for the contract harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolRequest:
    tool_name: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_name: str
    ok: bool
    result: Any = None
    error: str = ""
    latency_ms: float = 0.0
