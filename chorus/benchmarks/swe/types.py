"""Shared types and ports for the SWE-bench evaluation harness.

The seam to the outside world is two small protocols: a :class:`PatchModel` (the
single choke point for LLM calls) and a :class:`SweEvaluator` (the single choke
point for running tests in Docker). Everything else -- scaffolds, the runner -- is
pure and testable with fakes that implement these protocols.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from chorus.core.types import TaskSpec

# Outcome categories for an unresolved attempt, ordered roughly by how early the
# attempt failed. These become the per-task failure breakdown in the SuiteResult,
# so a benchmark regression reads the same way as a gate regression.
EMPTY_PATCH = "empty_patch"
APPLY_FAILED = "apply_failed"
TESTS_FAILED = "tests_failed"
EVAL_ERROR = "eval_error"
RESOLVED = "resolved"


class BenchDependencyMissing(RuntimeError):
    """A heavy optional dependency (anthropic / swebench / docker) is not available."""


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class ScaffoldOutput:
    """What a scaffold produced for one attempt: the patch and what it cost."""

    patch: str
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class SwePrediction:
    instance_id: str
    model_patch: str
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class SweOutcome:
    instance_id: str
    resolved: bool
    category: str  # RESOLVED when resolved, else one of the failure categories


class PatchModel(Protocol):
    """The only seam to an LLM. A scaffold turns a task into one or more calls."""

    def complete(
        self, *, system: str, user: str, seed: int, max_tokens: int = ...
    ) -> ModelResponse:
        """Return the model's text completion plus usage/cost for one call."""


class SweEvaluator(Protocol):
    """The only seam to the SWE-bench test harness (Docker)."""

    def evaluate(self, predictions: list[SwePrediction], *, run_id: str) -> dict[str, SweOutcome]:
        """Apply each patch, run the tests, and report resolved/not per instance."""


class SweScaffold(Protocol):
    """A harness strategy: turn one task into one attempt's patch."""

    name: str

    def run(self, task: TaskSpec, model: PatchModel, *, seed: int) -> ScaffoldOutput:
        """Produce a patch (and its cost) for one attempt at ``task``."""
