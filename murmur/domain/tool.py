"""Typed tool request/result records for the contract harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True, slots=True)
class ToolRequest:
    tool_name: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_name: str
    ok: bool
    result: Any = None
    error: str = ""
    latency_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class ExecResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    passed: bool
    timeout: bool = False
    latency_ms: float = 0.0
    summary: str = ""
    failing_tests: tuple[str, ...] = ()

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "passed": self.passed,
            "timeout": self.timeout,
            "latency_ms": self.latency_ms,
            "summary": self.summary,
            "failing_tests": list(self.failing_tests),
            "output": self.output,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            command=str(data.get("command", "")),
            returncode=int(data.get("returncode", 1)),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            passed=bool(data.get("passed", False)),
            timeout=bool(data.get("timeout", False)),
            latency_ms=float(data.get("latency_ms", 0.0)),
            summary=str(data.get("summary", "")),
            failing_tests=tuple(str(item) for item in data.get("failing_tests", ())),
        )
