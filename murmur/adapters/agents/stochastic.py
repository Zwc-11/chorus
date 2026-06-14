"""Stochastic agent adapter.

This file gives Chorus a *flaky* agent so the harness has a real distribution to
measure and a realistic trace to render. A real coding agent's output is a
distribution, not a single trajectory; this adapter simulates one cheaply and
deterministically.

Each instance is seeded, so a run is fully reproducible: the same seed yields the
same step/model/tool sequence, the same token usage, the same pass/fail/error
outcome, and the same simulated latency. Different seeds across the ``N``
trajectories of a run produce the spread that ``pass^k``, variance, and the
Wilson interval are computed over.

The agent talks only to the gateway (the single record/replay choke point): it
marks steps, makes model calls, and calls tools. Every step is one
``model -> tool`` turn, the structure the Phase 1 trace projects into
``gen_ai.*`` spans.
"""

from __future__ import annotations

from collections.abc import Callable
from random import Random
from typing import Any

from murmur.core.ports import AgentPort, ToolGatewayPort
from murmur.core.types import TaskSpec

MODEL_NAME = "claude-sim-4"
_PHASES = ("plan", "act", "reflect", "verify")

# A fixed opening plan every trajectory follows identically. Because all
# trajectories take the same actions here, the divergence overlay shows them
# *converged* through the prefix and only splitting once seed-driven choices
# begin -- which is the whole point of the overlay: locating the step where runs
# stop agreeing. (``None`` args mean "edit the current working text".)
_PREFIX_PLAN: tuple[tuple[str, dict[str, Any] | None], ...] = (
    ("read_file", {"path": "src/main.py"}),
    ("bash", {"command": "pytest -q"}),
    ("read_file", {"path": "tests/test_main.py"}),
    ("edit", None),
)


class FlakyToolError(RuntimeError):
    """Raised by a simulated tool to model a transient infrastructure failure."""


class StochasticAgent:
    """A seedable, flaky agent used to exercise the reliability + tracing machinery.

    It "solves" the demo task (echo then uppercase the prompt) with probability
    ``success_rate``. On the unlucky branch it either returns a wrong answer
    (a graded ``fail``) or a tool blows up (an uncaught ``error``).
    """

    def __init__(
        self,
        *,
        success_rate: float = 0.7,
        error_rate: float = 0.0,
        seed: int = 0,
        converge_steps: int = 4,
        diverge_steps: int = 4,
    ) -> None:
        if not 0.0 <= success_rate <= 1.0:
            raise ValueError("success_rate must be in [0, 1]")
        if not 0.0 <= error_rate <= 1.0:
            raise ValueError("error_rate must be in [0, 1]")
        self._success_rate = success_rate
        self._error_rate = error_rate
        self._rng = Random(seed)
        self._converge_steps = converge_steps
        self._diverge_steps = diverge_steps

    async def run(self, task: TaskSpec, gateway: ToolGatewayPort) -> str:
        text = task.prompt

        # Decide the outcome up front, but spend the steps first so even failed
        # and errored runs produce a realistic, costed trajectory. The error roll
        # precedes the success roll so seeds stay stable as rates change.
        will_error = self._rng.random() < self._error_rate
        will_pass = self._rng.random() < self._success_rate
        steps = self._converge_steps + self._rng.randint(0, self._diverge_steps)

        for index in range(steps):
            phase = _PHASES[index % len(_PHASES)]
            await gateway.step(index=index, phase=phase)
            await gateway.model(
                model=MODEL_NAME,
                input_tokens=self._rng.randint(400, 1400),
                output_tokens=self._rng.randint(80, 420),
                finish_reason="tool_call",
                latency_ms=self._rng.uniform(280.0, 1600.0),
                content=f"({phase}) working on: {text}",
            )
            if index < self._converge_steps:
                tool, args = self._planned_tool(index, text)
            else:
                tool, args = self._pick_tool(text)
            text = await gateway.call(tool, args)

        await gateway.step(index=steps, phase="verify")
        await gateway.model(
            model=MODEL_NAME,
            input_tokens=self._rng.randint(300, 900),
            output_tokens=self._rng.randint(40, 160),
            finish_reason="stop",
            latency_ms=self._rng.uniform(200.0, 900.0),
            content="finalizing",
        )

        if will_error:
            # A failing shell command: recorded as an execute_tool span that errors.
            return await gateway.call("bash", {"command": "pytest -q", "fail": True})
        if will_pass:
            if task.metadata.get("acceptance"):
                return await gateway.call("submit", {"artifact": _simulated_hard_pass(task)})
            return await gateway.call("transform", {"text": task.prompt})
        return await gateway.call("emit", {"text": task.prompt})

    def _planned_tool(self, index: int, text: str) -> tuple[str, dict[str, Any]]:
        tool, args = _PREFIX_PLAN[index % len(_PREFIX_PLAN)]
        if args is None:
            return tool, {"text": text}
        return tool, dict(args)

    def _pick_tool(self, text: str) -> tuple[str, dict[str, Any]]:
        choice = self._rng.choice(("read_file", "bash", "edit"))
        if choice == "read_file":
            return "read_file", {"path": "src/main.py"}
        if choice == "bash":
            return "bash", {"command": "pytest -q"}
        return "edit", {"text": text}


def stochastic_tools() -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Tools the stochastic agent drives, all pure and deterministic."""

    def _bash(args: dict[str, Any]) -> Any:
        if args.get("fail"):
            raise FlakyToolError(f"command failed: {args.get('command', '')} (exit 1)")
        return f"$ {args.get('command', '')}\nok"

    return {
        "read_file": lambda args: f"<contents of {args['path']}>",
        "bash": _bash,
        "edit": lambda args: args["text"],
        "transform": lambda args: args["text"].upper(),
        "emit": lambda args: args["text"],
        "submit": lambda args: args["artifact"],
        "write_file": lambda args: f"wrote {args['path']}",
    }


def _simulated_hard_pass(task: TaskSpec) -> str:
    """Minimal artifact that satisfies :func:`murmur.core.acceptance.hard_website_accepts`."""

    del task
    return """=== index.html ===
<!DOCTYPE html>
<html lang="en"><head><link rel="stylesheet" href="styles.css"></head>
<body><header class="hero"><h1>chorus reliability harness</h1></header>
<section id="metrics">
  <span>pass@1 0.82</span><span>pass^k 0.41</span><span>variance 0.19</span>
</section>
</body></html>
=== styles.css ===
:root { --bg: #e4e4e0; --accent: #e8192a; }
body { background: var(--bg); color: #0a0a0a; letter-spacing: 0.08em; }
#metrics { font-family: monospace; border: 1px solid var(--accent); }
"""


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
