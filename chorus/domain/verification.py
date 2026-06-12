"""Verification result for a contract-first run."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class VerificationResult:
    passed: bool
    failure_reproduced: bool
    target_test_passed: bool
    related_tests_passed: bool
    static_checks_passed: bool
    forbidden_files_touched: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    diff_lines: int = 0
    failures: tuple[str, ...] = ()
    target_output: str = ""
    related_outputs: dict[str, str] = field(default_factory=dict)
    static_outputs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
