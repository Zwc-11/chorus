"""Model port: the single seam between workflow operators and LLM providers.

Every model call in Murmur goes through a :class:`ModelPort`. Concrete adapters
(OpenAI-compatible HTTP, Ollama, fakes for tests) live in
``murmur.adapters.models``; the core never imports a vendor SDK. This mirrors
the :class:`~murmur.core.ports.ToolGatewayPort` philosophy: one choke point so
calls can be budgeted, recorded, and replayed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ModelUnavailable(RuntimeError):
    """The provider cannot serve a call (missing key, unreachable host, bad config)."""


class ModelOutputError(RuntimeError):
    """The provider responded, but with output the harness cannot use."""


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD per million tokens for one model id. List-price estimates by default."""

    input: float
    output: float

    def cost(self, *, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input + output_tokens * self.output) / 1_000_000


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """One completion plus the structural usage the reliability layer needs."""

    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    finish_reason: str = ""
    latency_ms: float = 0.0


class ModelPort(Protocol):
    """The only seam to an LLM for workflow operators.

    Adapters must be safe to call concurrently: ``map`` nodes fan out N
    completions at once and await them together.
    """

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = ...,
        max_tokens: int = ...,
        response_format: dict[str, Any] | None = ...,
    ) -> ModelResponse:
        """Return the model's completion plus usage/cost for one call."""
