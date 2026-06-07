"""The model gateway — Murmur's single seam to an LLM.

Everything the runtime needs from a model is the :class:`ModelPort` protocol: one
async ``complete`` call. The domain never knows whether it is talking to DeepSeek,
a local Ollama model, or a deterministic fake — swapping one for another is a
config change, not a code change (Ports & Adapters).

Two cross-cutting concerns wrap every call as decorators, exactly as the design
catalog prescribes:

- :class:`BudgetLedger` — a token/cost cap for the whole run. The global cap is a
  circuit breaker: once tripped, further calls raise :class:`BudgetExceeded`.
- :class:`MeteredModel` — wraps any ``ModelPort`` so each call debits the ledger
  and is appended to a call log (the raw material for traces and resume).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from murmur.flock.ir import Effort


class ModelUnavailable(RuntimeError):
    """A model adapter cannot run: missing SDK, missing API key, or unreachable host.

    Kept local to the flock subpackage so model adapters do not couple to the
    benchmark harness's dependency-missing type.
    """


class BudgetExceeded(RuntimeError):
    """A model call would push the run past its token budget; the run halts."""


@dataclass(frozen=True, slots=True)
class ModelReply:
    """One model call's result plus the usage needed to bill and trace it."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    finish_reason: str = "stop"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@runtime_checkable
class ModelPort(Protocol):
    """The only seam to an LLM. Adapters implement this; the runtime depends on it."""

    name: str

    async def complete(
        self,
        *,
        system: str,
        user: str,
        effort: Effort = "low",
        max_tokens: int | None = None,
    ) -> ModelReply:
        """Return the model's completion for one prompt, with usage and cost.

        ``effort`` routes the call: ``"low"`` for cheap fan-out work, ``"high"`` to
        engage a thinking/reasoning model where the plan asks for it.
        """


@dataclass(frozen=True, slots=True)
class ModelCall:
    """An append-only record of one completed model call (for traces / resume)."""

    model: str
    effort: Effort
    input_tokens: int
    output_tokens: int
    cost_usd: float
    node_id: str = ""


class BudgetLedger:
    """Tracks tokens and cost against a hard token cap for the whole run.

    Thread-safe so concurrent fan-out tasks debit it correctly. ``reserve`` is the
    pre-call admission check (a circuit breaker); ``debit`` records actual usage
    after a call returns. We admit on a conservative estimate, then reconcile.
    """

    def __init__(self, *, budget_tokens: int) -> None:
        if budget_tokens <= 0:
            raise ValueError(f"budget_tokens must be > 0, got {budget_tokens}")
        self._budget = budget_tokens
        self._spent_tokens = 0
        self._spent_cost = 0.0
        self._calls = 0
        self._lock = threading.Lock()

    @property
    def budget_tokens(self) -> int:
        return self._budget

    @property
    def spent_tokens(self) -> int:
        with self._lock:
            return self._spent_tokens

    @property
    def spent_cost_usd(self) -> float:
        with self._lock:
            return self._spent_cost

    @property
    def remaining_tokens(self) -> int:
        with self._lock:
            return max(0, self._budget - self._spent_tokens)

    @property
    def calls(self) -> int:
        with self._lock:
            return self._calls

    def reserve(self, estimated_tokens: int) -> None:
        """Admit a call that would cost ~``estimated_tokens``, or trip the breaker."""

        with self._lock:
            if self._spent_tokens + max(0, estimated_tokens) > self._budget:
                raise BudgetExceeded(
                    f"run budget exhausted: spent {self._spent_tokens}/{self._budget} tokens, "
                    f"next call estimated at ~{estimated_tokens}"
                )

    def debit(self, *, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        """Record actual usage after a call returns."""

        with self._lock:
            self._spent_tokens += max(0, input_tokens) + max(0, output_tokens)
            self._spent_cost += max(0.0, cost_usd)
            self._calls += 1


def estimate_tokens(text: str) -> int:
    """Cheap pre-call token estimate (~4 chars/token) for budget admission."""

    return max(1, len(text) // 4)


class MeteredModel:
    """Decorator over any :class:`ModelPort`: budget admission + a call log.

    Wrapping is transparent — it satisfies :class:`ModelPort` itself, so the runtime
    cannot tell a metered model from a bare one. ``node_id`` tags every recorded
    call so a trace can attribute cost to the plan node that spent it.
    """

    def __init__(
        self,
        inner: ModelPort,
        *,
        ledger: BudgetLedger | None = None,
        node_id: str = "",
        on_call: Callable[[ModelCall], None] | None = None,
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._node_id = node_id
        self._on_call = on_call

    @property
    def name(self) -> str:
        return self._inner.name

    def for_node(self, node_id: str) -> MeteredModel:
        """Return a sibling wrapper that tags calls with *node_id* (shared ledger)."""

        return MeteredModel(
            self._inner, ledger=self._ledger, node_id=node_id, on_call=self._on_call
        )

    async def complete(
        self,
        *,
        system: str,
        user: str,
        effort: Effort = "low",
        max_tokens: int | None = None,
    ) -> ModelReply:
        if self._ledger is not None:
            self._ledger.reserve(estimate_tokens(system) + estimate_tokens(user))
        reply = await self._inner.complete(
            system=system, user=user, effort=effort, max_tokens=max_tokens
        )
        if self._ledger is not None:
            self._ledger.debit(
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cost_usd=reply.cost_usd,
            )
        if self._on_call is not None:
            self._on_call(
                ModelCall(
                    model=reply.model or self._inner.name,
                    effort=effort,
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    cost_usd=reply.cost_usd,
                    node_id=self._node_id,
                )
            )
        return reply


@dataclass
class CallLog:
    """An in-memory append-only sink for :class:`ModelCall` records."""

    calls: list[ModelCall] = field(default_factory=list)

    def record(self, call: ModelCall) -> None:
        self.calls.append(call)

    @property
    def total_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return sum(c.input_tokens + c.output_tokens for c in self.calls)
