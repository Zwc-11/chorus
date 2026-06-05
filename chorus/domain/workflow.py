"""Typed Murmur workflow plan records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

SUPPORTED_OPS = frozenset(
    {
        "classify",
        "map",
        "generate",
        "exec",
        "loop",
        "filter",
        "rank",
        "tournament",
        "verify",
        "reduce",
        "report",
    }
)


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    id: str
    op: str
    inputs: tuple[str, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    budget: dict[str, Any] = field(default_factory=dict)
    policy: str = ""
    outputs: tuple[str, ...] = ()
    role: str = ""
    model: str = ""
    effort: str = ""
    temperature: float | None = None
    seed: int | None = None
    result_ref: str = ""
    artifact_refs: tuple[str, ...] = ()
    taint: str = ""
    quarantined: bool = False

    @property
    def dependencies(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.inputs, *self.depends_on)))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["inputs"] = list(self.inputs)
        data["depends_on"] = list(self.depends_on)
        data["outputs"] = list(self.outputs)
        data["artifact_refs"] = list(self.artifact_refs)
        if self.temperature is None:
            data.pop("temperature")
        if self.seed is None:
            data.pop("seed")
        return {key: value for key, value in data.items() if value not in ("", (), {}, [])}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowNode:
        temperature = data.get("temperature")
        seed = data.get("seed")
        return cls(
            id=str(data["id"]),
            op=str(data["op"]),
            inputs=tuple(str(item) for item in data.get("inputs", ())),
            params=dict(data.get("params", {})),
            depends_on=tuple(str(item) for item in data.get("depends_on", ())),
            budget=dict(data.get("budget", {})),
            policy=str(data.get("policy", "")),
            outputs=tuple(str(item) for item in data.get("outputs", ())),
            role=str(data.get("role", "")),
            model=str(data.get("model", "")),
            effort=str(data.get("effort", "")),
            temperature=None if temperature is None else float(temperature),
            seed=None if seed is None else int(seed),
            result_ref=str(data.get("result_ref", "")),
            artifact_refs=tuple(str(item) for item in data.get("artifact_refs", ())),
            taint=str(data.get("taint", "")),
            quarantined=bool(data.get("quarantined", False)),
        )


@dataclass(frozen=True, slots=True)
class WorkflowPlan:
    version: int
    goal: str
    budget: dict[str, Any]
    nodes: tuple[WorkflowNode, ...]
    schema_version: int = 1
    name: str = ""
    description: str = ""
    budgets: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()

    def validate(self) -> list[str]:
        issues: list[str] = []
        if self.version != 1 or self.schema_version != 1:
            issues.append("schema_version/version must be 1")
        if not self.goal:
            issues.append("goal is required")
        issues.extend(_budget_issues("budget", self.budget))
        issues.extend(_budget_issues("budgets", self.budgets))
        seen: set[str] = set()
        for node in self.nodes:
            if not node.id:
                issues.append("node.id is required")
            if node.id in seen:
                issues.append(f"duplicate node id: {node.id}")
            seen.add(node.id)
            if node.op not in SUPPORTED_OPS:
                issues.append(f"unsupported op for node {node.id}: {node.op}")
            issues.extend(_budget_issues(f"node {node.id} budget", node.budget))
            if node.policy and node.policy not in {"default", "allow_tainted_inputs"}:
                issues.append(f"unsupported policy for node {node.id}: {node.policy}")
        for node in self.nodes:
            for dependency in node.dependencies:
                if dependency not in seen:
                    issues.append(f"node {node.id} references missing dependency {dependency}")
        issues.extend(_cycle_issues(self.nodes))
        return issues

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "version": self.version,
            "name": self.name,
            "goal": self.goal,
            "description": self.description,
            "budget": self.budget,
            "budgets": self.budgets,
            "artifacts": list(self.artifacts),
            "nodes": [node.to_dict() for node in self.nodes],
        }
        return {key: value for key, value in data.items() if value not in ("", (), {}, [])}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowPlan:
        version = int(data.get("version", data.get("schema_version", 1)))
        return cls(
            version=version,
            goal=str(data.get("goal", "")),
            budget=dict(data.get("budget", {})),
            nodes=tuple(WorkflowNode.from_dict(dict(node)) for node in data.get("nodes", ())),
            schema_version=int(data.get("schema_version", version)),
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            budgets=dict(data.get("budgets", {})),
            artifacts=tuple(str(item) for item in data.get("artifacts", ())),
        )

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False)

    @classmethod
    def from_yaml(cls, text: str) -> WorkflowPlan:
        data = yaml.safe_load(text) or {}
        return cls.from_dict(dict(data))

    @classmethod
    def read(cls, path: Path) -> WorkflowPlan:
        return cls.from_yaml(path.read_text(encoding="utf-8"))

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_yaml(), encoding="utf-8")
        return path


def _budget_issues(label: str, budget: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key, value in budget.items():
        if key in {"currency", "unit", "note"}:
            continue
        if isinstance(value, bool):
            issues.append(f"{label}.{key} must be numeric")
            continue
        if isinstance(value, int | float):
            if value < 0:
                issues.append(f"{label}.{key} must be non-negative")
            continue
        if key.startswith("max_") or key.endswith(("_ms", "_seconds", "_usd", "_calls")):
            issues.append(f"{label}.{key} must be numeric")
    return issues


def _cycle_issues(nodes: tuple[WorkflowNode, ...]) -> list[str]:
    by_id = {node.id: node for node in nodes}
    visiting: set[str] = set()
    visited: set[str] = set()
    issues: list[str] = []

    def visit(node_id: str, path: tuple[str, ...]) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            cycle = " -> ".join((*path, node_id))
            issues.append(f"workflow contains dependency cycle: {cycle}")
            return
        node = by_id.get(node_id)
        if node is None:
            return
        visiting.add(node_id)
        for dependency in node.dependencies:
            visit(dependency, (*path, node_id))
        visiting.remove(node_id)
        visited.add(node_id)

    for node in nodes:
        visit(node.id, ())
    return issues
