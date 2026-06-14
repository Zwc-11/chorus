"""A ``JudgePort`` backed by the SWE-bench test harness.

``judge(task, patch) -> "pass" | "fail"`` is the seam the conductor calls per
trajectory. Acceptance is real: the patch is applied and the instance's tests are
run via the official harness. Because that harness is fundamentally a *batch*
operation, this judge supports two modes:

* **per-call** (default): each ``judge`` runs the harness for one instance. Simple
  and ports-clean; relies on the harness caching Docker images across calls.
  Right for small / debug N.
* **primed**: call :meth:`prime` with all predictions for an attempt to evaluate
  them in one parallel harness run; ``judge`` then becomes a cache lookup. Right
  when a runner can collect patches before judging.

Results are cached by ``(instance_id, patch)`` so re-judging an identical patch
never re-runs Docker, and so a primed batch and a later per-call agree.
"""

from __future__ import annotations

import hashlib

from murmur.benchmarks.swe.types import SweEvaluator, SweOutcome, SwePrediction
from murmur.core.types import TaskSpec


def _patch_key(instance_id: str, patch: str) -> str:
    digest = hashlib.sha1(patch.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return f"{instance_id}:{digest}"


class SweBenchJudge:
    def __init__(self, evaluator: SweEvaluator) -> None:
        self._evaluator = evaluator
        self._cache: dict[str, SweOutcome] = {}
        self._run_counter = 0

    def prime(self, predictions: list[SwePrediction], *, run_id: str) -> dict[str, SweOutcome]:
        """Batch-evaluate predictions in one harness run and cache the outcomes."""

        outcomes = self._evaluator.evaluate(predictions, run_id=run_id)
        for prediction in predictions:
            outcome = outcomes.get(prediction.instance_id)
            if outcome is not None:
                self._cache[_patch_key(prediction.instance_id, prediction.model_patch)] = outcome
        return outcomes

    async def judge(self, task: TaskSpec, output: str) -> str:
        key = _patch_key(task.task_id, output)
        outcome = self._cache.get(key)
        if outcome is None:
            outcome = self._evaluate_one(task.task_id, output)
            self._cache[key] = outcome
        return "pass" if outcome.resolved else "fail"

    def _evaluate_one(self, instance_id: str, patch: str) -> SweOutcome:
        self._run_counter += 1
        prediction = SwePrediction(instance_id=instance_id, model_patch=patch)
        run_id = f"judge-{instance_id}-{self._run_counter}"
        outcomes = self._evaluator.evaluate([prediction], run_id=run_id)
        return outcomes.get(instance_id) or SweOutcome(instance_id, False, "eval_error")
