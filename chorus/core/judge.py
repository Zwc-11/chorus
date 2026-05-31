from __future__ import annotations

from chorus.core.types import Outcome, TaskSpec


class DeterministicJudge:
    """Tier 0 judge: use the task contract before any expensive evaluator."""

    async def judge(self, task: TaskSpec, output: str) -> Outcome:
        return "pass" if task.accepts(output) else "fail"

