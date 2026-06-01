"""Baseline store: persist and load a suite result per (branch, suite, N).

The candidate of a PR is compared against the baseline recorded for its target
branch, on the same suite at the same N. JSON on disk keeps it zero-config and
inspectable; a CI job commits or caches the baseline directory between runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from chorus.core.suite import SuiteResult


class BaselineStore:
    def __init__(self, root: Path | str = ".chorus/baselines") -> None:
        self.root = Path(root)

    def _path(self, branch: str, suite_version: str, n: int) -> Path:
        safe_branch = branch.replace("/", "_").replace("\\", "_")
        return self.root / f"{safe_branch}__{suite_version}__N{n}.json"

    def load(self, branch: str, suite_version: str, n: int) -> SuiteResult | None:
        path = self._path(branch, suite_version, n)
        if not path.exists():
            return None
        return SuiteResult.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, result: SuiteResult) -> Path:
        path = self._path(result.branch, result.suite_version, result.n)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        return path
