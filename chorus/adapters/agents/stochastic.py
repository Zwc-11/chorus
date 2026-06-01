"""Stochastic agent adapter.

This file gives Chorus a *flaky* agent so the harness has a real distribution to
measure. A real coding agent's output is a distribution, not a single
trajectory; this adapter simulates one cheaply and deterministically.

Each instance is seeded, so a run is fully reproducible: the same seed yields the
same number of steps, the same pass/fail/error outcome, and the same simulated
cost and latency. Different seeds across the ``N`` trajectories of a run produce
the spread that ``pass^k``, variance, and the Wilson interval are computed over.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from random import Random
from typing import Any

from chorus.core.ports import AgentPort, ToolGatewayPort
from chorus.core.types import TaskSpec


class FlakyToolError(RuntimeError):
    """Raised by a simulated tool to model a transient infrastructure failure."""


class StochasticAgent:
    """A seedable, flaky agent used to exercise the reliability machinery.

    It "solves" the demo task (echo then uppercase the prompt) with probability
    ``success_rate``. On the unlucky branch it either returns a wrong answer
    (a graded ``fail``) or hits a flaky tool (an uncaught ``error``).
    """

    def __init__(
        self,
        *,
        success_rate: float = 0.7,
        error_rate: float = 0.0,
        seed: int = 0,
        min_steps: int = 2,
        max_steps: int = 5,
        latency_ms: tuple[float, float] = (1.0, 12.0),
    ) -> None:
        if not 0.0 <= success_rate <= 1.0:
            raise ValueError("success_rate must be in [0, 1]")
        if not 0.0 <= error_rate <= 1.0:
            raise ValueError("error_rate must be in [0, 1]")
        self._success_rate = success_rate
        self._error_rate = error_rate
        self._rng = Random(seed)
        self._min_steps = min_steps
        self._max_steps = max_steps
        self._latency_ms = latency_ms

    async def run(self, task: TaskSpec, gateway: ToolGatewayPort) -> str:
        text = task.prompt
        steps = self._rng.randint(self._min_steps, self._max_steps)
        for index in range(steps):
            text = await gateway.call("think", {"step": index, "text": text})
            await asyncio.sleep(self._rng.uniform(*self._latency_ms) / 1000.0)

        # The flaky branch is evaluated before success so seeds stay stable when
        # error_rate changes from zero.
        if self._rng.random() < self._error_rate:
            # Raises FlakyToolError inside the gateway -> recorded, then surfaces
            # as an uncaught error outcome the failure classifier can label.
            return await gateway.call("flaky_io", {"text": text})

        if self._rng.random() < self._success_rate:
            return await gateway.call("transform", {"text": text})
        return await gateway.call("emit", {"text": text})


def stochastic_tools() -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Tools the stochastic agent drives, all pure and deterministic."""

    def _flaky_io(_: dict[str, Any]) -> Any:
        raise FlakyToolError("upstream tool connection reset")

    return {
        "think": lambda args: args["text"],
        "transform": lambda args: args["text"].upper(),
        "emit": lambda args: args["text"],
        "flaky_io": _flaky_io,
    }


def stochastic_agent_factory(
    *,
    success_rate: float = 0.7,
    error_rate: float = 0.0,
    base_seed: int = 7,
) -> Callable[[int], AgentPort]:
    """Build a per-trajectory agent factory with independent, reproducible seeds."""

    def factory(index: int) -> AgentPort:
        return StochasticAgent(
            success_rate=success_rate,
            error_rate=error_rate,
            seed=base_seed * 1_000 + index,
        )

    return factory
