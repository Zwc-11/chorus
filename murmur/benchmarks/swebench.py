"""SWE-bench Verified task loader.

This is the real-benchmark drop-in promised by the ``load_suite`` seam. It loads
the public `princeton-nlp/SWE-bench_Verified` instances and maps each one onto a
``TaskSpec`` behind the existing interface: the problem statement becomes the
prompt and the test-based acceptance contract (``FAIL_TO_PASS`` /
``PASS_TO_PASS`` plus the repo coordinates needed to reproduce it) travels in
metadata. A real coding agent behind ``AgentPort`` plus a test evaluator turns
these specs into the headline ``pass^k`` number; that run is intentionally out of
this module's scope (it needs a real model and the SWE-bench execution harness).

Data source resolution, in order:

1. An explicit path argument, or the ``CHORUS_SWEBENCH_PATH`` env var, pointing at
   a ``.json`` / ``.jsonl`` dump of instances. This keeps the loader testable and
   runnable offline.
2. The ``datasets`` library, if installed: ``load_dataset(..., split="test")``.
3. Otherwise a clear, actionable error -- never a silent fall back to synthetic
   data, which would quietly corrupt the headline number.

Subset selection is deterministic so iteration is reproducible (the doc's "fixed
subset for iteration, one full run for the headline"): instances are sorted by
``instance_id`` and the first ``subset_size`` are taken. ``subset_size=None`` (or
``0``) loads the full set for the headline run.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from murmur.core.types import TaskSpec

# Sorting by instance_id and slicing gives a stable, documented subset without
# hard-coding IDs that might drift; bump the version string if this policy changes.
SUITE_NAME = "swe-bench-verified"
DATASET_ID = "princeton-nlp/SWE-bench_Verified"
DEFAULT_SUBSET_SIZE = 50
PATH_ENV = "CHORUS_SWEBENCH_PATH"
SUBSET_ENV = "CHORUS_SWEBENCH_SUBSET"


class BenchmarkDataUnavailable(RuntimeError):
    """Raised when no SWE-bench Verified data source can be resolved."""


def suite_version(subset_size: int | None = DEFAULT_SUBSET_SIZE) -> str:
    """Baseline key for this suite: the subset size is part of the conditions."""

    if not subset_size:
        return f"{SUITE_NAME}-full"
    return f"{SUITE_NAME}-subset{subset_size}"


def load_swebench_verified(
    *,
    path: str | os.PathLike[str] | None = None,
    subset_size: int | None = DEFAULT_SUBSET_SIZE,
) -> list[TaskSpec]:
    """Load SWE-bench Verified as ``TaskSpec``s (a deterministic subset by default)."""

    instances = _read_instances(path)
    ordered = sorted(instances, key=lambda inst: str(inst.get("instance_id", "")))
    if subset_size:
        ordered = ordered[:subset_size]
    return [_instance_to_task(inst) for inst in ordered]


def resolve_subset_size(subset_size: int | None) -> int | None:
    """Apply the ``CHORUS_SWEBENCH_SUBSET`` override; ``0`` means the full set."""

    if subset_size is not None:
        return subset_size
    raw = os.environ.get(SUBSET_ENV)
    if raw is None:
        return DEFAULT_SUBSET_SIZE
    value = int(raw)
    return value if value > 0 else None


def _read_instances(path: str | os.PathLike[str] | None) -> list[dict[str, Any]]:
    resolved = path or os.environ.get(PATH_ENV)
    if resolved:
        return _read_file(Path(resolved))
    return _read_from_datasets()


def _read_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise BenchmarkDataUnavailable(
            f"SWE-bench data file not found: {path}. Point {PATH_ENV} at a .json/.jsonl "
            f"dump of {DATASET_ID} instances, or install `datasets`."
        )
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    return list(data) if isinstance(data, list) else list(data.get("instances", []))


def _read_from_datasets() -> list[dict[str, Any]]:
    try:
        from datasets import load_dataset  # type: ignore import-not-found
    except ImportError as exc:  # pragma: no cover - exercised via the path branch
        raise BenchmarkDataUnavailable(
            f"No SWE-bench data source. Either set {PATH_ENV} to a local .json/.jsonl dump "
            f"of {DATASET_ID}, or `pip install datasets` to fetch it from the Hub."
        ) from exc
    dataset = load_dataset(DATASET_ID, split="test")
    return [dict(row) for row in dataset]


def _instance_to_task(instance: dict[str, Any]) -> TaskSpec:
    instance_id = str(instance["instance_id"])
    return TaskSpec(
        task_id=instance_id,
        prompt=str(instance.get("problem_statement", "")),
        expected_output=None,  # acceptance is test-based, not a string match
        metadata={
            "suite": SUITE_NAME,
            "repo": instance.get("repo", ""),
            "base_commit": instance.get("base_commit", ""),
            "environment_setup_commit": instance.get("environment_setup_commit", ""),
            "version": instance.get("version", ""),
            "fail_to_pass": _coerce_test_list(instance.get("FAIL_TO_PASS")),
            "pass_to_pass": _coerce_test_list(instance.get("PASS_TO_PASS")),
        },
    )


def _coerce_test_list(value: Any) -> tuple[str, ...]:
    """SWE-bench stores test lists as JSON-encoded strings; accept either form."""

    if value is None:
        return ()
    if isinstance(value, str):
        parsed = json.loads(value) if value.strip() else []
        return tuple(str(item) for item in parsed)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return ()
