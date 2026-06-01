"""Evaluators: the seam to the SWE-bench test harness (Docker).

:class:`SubprocessSweEvaluator` writes predictions in the format the official
``swebench`` harness expects, shells out to ``python -m
swebench.harness.run_evaluation``, and parses the run report into per-instance
outcomes. Docker, disk, and the ``swebench`` package are required only at call
time; :func:`parse_report` is pure so the parsing is unit-tested without any of
them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from chorus.benchmarks.swe.types import (
    APPLY_FAILED,
    EMPTY_PATCH,
    EVAL_ERROR,
    RESOLVED,
    TESTS_FAILED,
    BenchDependencyMissing,
    SweOutcome,
    SwePrediction,
)

DATASET_NAME = "princeton-nlp/SWE-bench_Verified"
MODEL_TAG = "chorus"


def parse_report(report: dict, submitted: list[str]) -> dict[str, SweOutcome]:
    """Map a SWE-bench run report + the submitted ids onto per-instance outcomes.

    The summary report does not separate "patch did not apply" from "tests failed",
    so both land in ``tests_failed`` unless the caller inspects per-instance logs;
    ``empty_patch`` and harness ``error`` are distinguished because the report lists
    them explicitly.
    """

    def ids(key: str) -> set[str]:
        return {str(x) for x in report.get(key, [])}

    resolved = ids("resolved_ids")
    empty = ids("empty_patch_ids")
    errored = ids("error_ids")

    outcomes: dict[str, SweOutcome] = {}
    for instance_id in submitted:
        if instance_id in resolved:
            outcomes[instance_id] = SweOutcome(instance_id, True, RESOLVED)
        elif instance_id in empty:
            outcomes[instance_id] = SweOutcome(instance_id, False, EMPTY_PATCH)
        elif instance_id in errored:
            outcomes[instance_id] = SweOutcome(instance_id, False, EVAL_ERROR)
        else:
            outcomes[instance_id] = SweOutcome(instance_id, False, TESTS_FAILED)
    return outcomes


def write_predictions(predictions: list[SwePrediction], path: Path, *, model_tag: str) -> Path:
    """Serialize predictions to the JSONL shape the harness reads."""

    lines = [
        json.dumps(
            {
                "instance_id": p.instance_id,
                "model_name_or_path": model_tag,
                "model_patch": p.model_patch,
            }
        )
        for p in predictions
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class SubprocessSweEvaluator:
    def __init__(
        self,
        *,
        run_dir: Path | str = ".chorus/swebench",
        dataset_name: str = DATASET_NAME,
        model_tag: str = MODEL_TAG,
        max_workers: int = 4,
        timeout_s: int = 7200,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.dataset_name = dataset_name
        self.model_tag = model_tag
        self.max_workers = max_workers
        self.timeout_s = timeout_s

    def evaluate(self, predictions: list[SwePrediction], *, run_id: str) -> dict[str, SweOutcome]:
        self._ensure_swebench()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        preds_path = write_predictions(
            predictions, self.run_dir / f"preds__{run_id}.jsonl", model_tag=self.model_tag
        )
        cmd = [
            sys.executable,
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            self.dataset_name,
            "--predictions_path",
            str(preds_path),
            "--max_workers",
            str(self.max_workers),
            "--run_id",
            run_id,
        ]
        subprocess.run(cmd, cwd=self.run_dir, check=True, timeout=self.timeout_s)  # noqa: S603
        report = self._load_report(run_id)
        return parse_report(report, [p.instance_id for p in predictions])

    def ensure_ready(self) -> None:
        """Preflight: raise BenchDependencyMissing now if swebench is absent."""

        self._ensure_swebench()

    def _ensure_swebench(self) -> None:
        try:
            import swebench  # noqa: F401
        except ImportError as exc:
            raise BenchDependencyMissing(
                "swebench is not installed; `pip install 'chorus-harness[bench]'` and ensure "
                "Docker is running. See https://github.com/princeton-nlp/SWE-bench."
            ) from exc

    def _load_report(self, run_id: str) -> dict:
        # The harness names the report after the model tag and run id; locate it
        # robustly since the exact filename has varied across harness versions.
        candidates = sorted(self.run_dir.glob(f"*{run_id}*.json"))
        candidates += sorted(self.run_dir.glob(f"{self.model_tag}.*.json"))
        for path in candidates:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and "resolved_ids" in data:
                return data
        raise BenchDependencyMissing(
            f"No SWE-bench run report with 'resolved_ids' found under {self.run_dir} for run "
            f"{run_id!r}. Check the harness output and adjust run_dir if your swebench version "
            "writes the report elsewhere."
        )


# Re-exported so callers can build a richer breakdown if they parse logs themselves.
__all__ = [
    "APPLY_FAILED",
    "SubprocessSweEvaluator",
    "parse_report",
    "write_predictions",
]
