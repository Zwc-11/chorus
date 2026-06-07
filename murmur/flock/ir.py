"""The Workflow IR — Murmur's typed plan.

Claude Code executes LLM-generated JavaScript. Murmur's planner emits a *structured
plan* instead, and the runtime interprets it. No arbitrary code execution: a plan is
plain data — a DAG of operator nodes — which is safer, simpler to build, and trivially
validatable with a schema.

A :class:`WorkflowPlan` is the single source of truth the executor walks. Each
:class:`Node` is one operator (see the ``Op`` literal). Edges are dependencies: a
node's ``inputs`` name the nodes (or the plan-level ``sources``) it consumes. The
graph must be a DAG; :func:`validate_plan` enforces that plus the structural and
information-flow rules below before the executor ever runs a node.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

# The seven operators. Each maps to one reusable execution pattern in the runtime.
Op = Literal["classify", "map", "reduce", "tournament", "verify", "filter", "loop"]
Effort = Literal["low", "high"]  # routes a node to a cheap vs. a thinking model call
Trust = Literal["trusted", "untrusted"]  # quarantine flag for information-flow control

OPS: frozenset[str] = frozenset(
    ("classify", "map", "reduce", "tournament", "verify", "filter", "loop")
)
EFFORTS: frozenset[str] = frozenset(("low", "high"))
TRUST_LEVELS: frozenset[str] = frozenset(("trusted", "untrusted"))


class PlanValidationError(ValueError):
    """A plan violates the IR schema, the DAG rule, or the taint (IFC) rule.

    Raised with a human-readable message so a planner can be re-prompted with the
    exact reason its plan was rejected.
    """


@dataclass(frozen=True, slots=True)
class Node:
    """One operator in the plan — an executable, loggable, replayable command.

    ``role`` is the subagent's instruction (the prompt the operator fills its
    template with). ``model``/``effort`` pick the strategy and route cheap vs.
    thinking calls. ``trust`` marks whether this node reads untrusted content.
    """

    id: str
    op: Op
    role: str = ""
    inputs: tuple[str, ...] = ()
    model: str = "fake"
    effort: Effort = "low"
    trust: Trust = "trusted"
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "op": self.op,
            "role": self.role,
            "inputs": list(self.inputs),
            "model": self.model,
            "effort": self.effort,
            "trust": self.trust,
            "params": dict(self.params),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Node:
        if "id" not in data or "op" not in data:
            raise PlanValidationError(f"node is missing required 'id'/'op': {data!r}")
        return cls(
            id=str(data["id"]),
            op=str(data["op"]),  # type: ignore[arg-type]
            role=str(data.get("role", "")),
            inputs=tuple(str(x) for x in data.get("inputs", ()) or ()),
            model=str(data.get("model", "fake")),
            effort=str(data.get("effort", "low")),  # type: ignore[arg-type]
            trust=str(data.get("trust", "trusted")),  # type: ignore[arg-type]
            params=dict(data.get("params", {}) or {}),
        )


@dataclass(frozen=True, slots=True)
class WorkflowPlan:
    """A DAG of operator nodes the executor interprets to produce a result.

    ``sources`` names the external inputs available when the run starts (e.g.
    ``("resumes",)``); a node may list a source or another node's id in ``inputs``.
    ``budget_tokens`` is the hard cap for the whole run — the global circuit breaker.
    """

    goal: str
    budget_tokens: int
    nodes: tuple[Node, ...]
    sources: tuple[str, ...] = ()

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(n.id for n in self.nodes)

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def topological_order(self) -> tuple[Node, ...]:
        """Return nodes in a dependency-respecting order (Kahn's algorithm).

        Raises :class:`PlanValidationError` if the graph contains a cycle. Edges
        from plan-level ``sources`` are ignored — only node→node edges order work.
        """

        ids = set(self.node_ids)
        indegree = {n.id: 0 for n in self.nodes}
        dependents: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for n in self.nodes:
            for dep in n.inputs:
                if dep in ids:  # a source is not a scheduling dependency
                    indegree[n.id] += 1
                    dependents[dep].append(n.id)

        ready: deque[str] = deque(sorted(nid for nid, d in indegree.items() if d == 0))
        ordered: list[str] = []
        while ready:
            nid = ready.popleft()
            ordered.append(nid)
            for child in sorted(dependents[nid]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)

        if len(ordered) != len(self.nodes):
            cyclic = sorted(set(self.node_ids) - set(ordered))
            raise PlanValidationError(f"plan has a dependency cycle among nodes: {cyclic}")
        return tuple(self.node(nid) for nid in ordered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "budget_tokens": self.budget_tokens,
            "sources": list(self.sources),
            "nodes": [n.to_dict() for n in self.nodes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowPlan:
        if not isinstance(data, dict):
            raise PlanValidationError(f"plan must be a mapping, got {type(data).__name__}")
        raw_nodes = data.get("nodes")
        if not isinstance(raw_nodes, list):
            raise PlanValidationError("plan 'nodes' must be a list")
        try:
            budget = int(data.get("budget_tokens", 0))
        except (TypeError, ValueError) as exc:
            raise PlanValidationError("plan 'budget_tokens' must be an integer") from exc
        return cls(
            goal=str(data.get("goal", "")),
            budget_tokens=budget,
            nodes=tuple(Node.from_dict(n) for n in raw_nodes),
            sources=tuple(str(s) for s in data.get("sources", ()) or ()),
        )


def validate_plan(plan: WorkflowPlan) -> None:
    """Raise :class:`PlanValidationError` if *plan* is not safe to execute.

    Enforced, in order: a positive budget; at least one node; unique ids; known
    operators / effort / trust values; every input resolves to a node or a source;
    the graph is acyclic; and the taint rule — an untrusted node's output may not
    flow into a trusted node (no taint laundering). The planner is constrained to
    emit plans that pass this, so it can never hand the runtime garbage.
    """

    if plan.budget_tokens <= 0:
        raise PlanValidationError(f"budget_tokens must be > 0, got {plan.budget_tokens}")
    if not plan.nodes:
        raise PlanValidationError("plan has no nodes")

    seen: set[str] = set()
    for n in plan.nodes:
        if not n.id:
            raise PlanValidationError("every node needs a non-empty id")
        if n.id in seen:
            raise PlanValidationError(f"duplicate node id: {n.id!r}")
        seen.add(n.id)

    sources = set(plan.sources)
    for n in plan.nodes:
        if n.op not in OPS:
            raise PlanValidationError(
                f"node {n.id!r} has unknown op {n.op!r}; valid: {sorted(OPS)}"
            )
        if n.effort not in EFFORTS:
            raise PlanValidationError(f"node {n.id!r} has unknown effort {n.effort!r}")
        if n.trust not in TRUST_LEVELS:
            raise PlanValidationError(f"node {n.id!r} has unknown trust {n.trust!r}")
        for dep in n.inputs:
            if dep == n.id:
                raise PlanValidationError(f"node {n.id!r} depends on itself")
            if dep not in seen and dep not in sources:
                raise PlanValidationError(
                    f"node {n.id!r} input {dep!r} is not a known node id or source"
                )

    # DAG check (raises on cycle).
    plan.topological_order()

    # Information-flow control: taint must propagate, never be laundered.
    trust_of = {n.id: n.trust for n in plan.nodes}
    for n in plan.nodes:
        if n.trust == "trusted":
            tainted = [d for d in n.inputs if trust_of.get(d) == "untrusted"]
            if tainted:
                raise PlanValidationError(
                    f"node {n.id!r} is trusted but reads untrusted input(s) {tainted}; "
                    "mark it untrusted or insert a verify node"
                )


def parse_plan(data: dict[str, Any], *, validate: bool = True) -> WorkflowPlan:
    """Build a :class:`WorkflowPlan` from a dict and (by default) validate it."""

    plan = WorkflowPlan.from_dict(data)
    if validate:
        validate_plan(plan)
    return plan


def load_plan_yaml(text: str, *, validate: bool = True) -> WorkflowPlan:
    """Parse a YAML plan document into a validated :class:`WorkflowPlan`."""

    import yaml

    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise PlanValidationError("YAML plan must be a mapping at the top level")
    return parse_plan(data, validate=validate)


def dump_plan_yaml(plan: WorkflowPlan) -> str:
    """Serialize a plan back to a stable YAML document."""

    import yaml

    return yaml.safe_dump(plan.to_dict(), sort_keys=False, default_flow_style=False)
