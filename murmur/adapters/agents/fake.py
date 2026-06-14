"""Fake agent adapter for Phase 0.

This file gives Chorus a tiny deterministic agent so we can test the harness
without paying for a model or depending on an external agent framework.
"""

from __future__ import annotations

from typing import Any

from murmur.core.ports import ToolGatewayPort
from murmur.core.types import TaskSpec


class FakeAgent:
    """Deterministic agent for local architecture tests before real model adapters exist."""

    async def run(self, task: TaskSpec, gateway: ToolGatewayPort) -> str:
        echoed = await gateway.call("echo", {"text": task.prompt})
        return await gateway.call("uppercase", {"text": echoed})


def fake_tools() -> dict[str, Any]:
    return {
        "echo": lambda args: args["text"],
        "uppercase": lambda args: args["text"].upper(),
    }
