"""A real ``AgentPort`` that emits a patch for a SWE-bench task.

This is the integrated counterpart to the batch :mod:`murmur.benchmarks.swe`
scaffolds: it implements the *existing* ``AgentPort`` (``run(task, gateway) ->
str``) and records each model call through the gateway, so a real SWE-bench run
flows through the conductor and inherits Chorus's tracing, replay, divergence, and
diagnosis -- the whole point of routing it through the ports instead of a side
channel.

The output string is the unified diff. Acceptance is decided downstream by a
``JudgePort`` that runs the tests (see :class:`murmur.benchmarks.swe.judge.SweBenchJudge`),
not by the agent. ``repair=True`` adds one self-review turn -- the same
harness-only diff the batch scaffolds compare, expressed against the same ports.
"""

from __future__ import annotations

from murmur.benchmarks.swe.scaffold import (
    _REPAIR_SYSTEM,
    _SYSTEM,
    _build_user,
    extract_patch,
)
from murmur.benchmarks.swe.types import PatchModel
from murmur.core.ports import ToolGatewayPort
from murmur.core.types import TaskSpec


class SwePatchAgent:
    """Emit a patch from the problem statement + repo/base_commit via ``model``."""

    def __init__(self, model: PatchModel, *, repair: bool = False, seed: int = 0) -> None:
        self._model = model
        self._repair = repair
        self._seed = seed

    @property
    def name(self) -> str:
        return "self-repair" if self._repair else "single-shot"

    async def run(self, task: TaskSpec, gateway: ToolGatewayPort) -> str:
        user = _build_user(task)
        patch = await self._turn(gateway, step=0, system=_SYSTEM, user=user, seed=self._seed)
        if not self._repair:
            return patch
        review = f"{user}\nProposed patch:\n```diff\n{patch}\n```\n"
        return await self._turn(
            gateway, step=1, system=_REPAIR_SYSTEM, user=review, seed=self._seed + 1
        )

    async def _turn(
        self, gateway: ToolGatewayPort, *, step: int, system: str, user: str, seed: int
    ) -> str:
        await gateway.step(index=step, phase="repair" if step else "generate")
        response = self._model.complete(system=system, user=user, seed=seed)
        await gateway.model(
            model=getattr(self._model, "model", "patch-model"),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            finish_reason="stop",
        )
        return extract_patch(response.text)
