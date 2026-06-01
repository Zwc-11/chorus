"""Patch models: the seam to an LLM.

:class:`AnthropicPatchModel` is the real implementation -- it calls the Anthropic
Messages API with prompt caching on the (static) system block, so every call after
the first reads the system prompt from cache instead of re-paying for it. It is the
only place this package spends money, and it imports ``anthropic`` lazily so the
rest of the package runs without it.

The real API has no seed parameter; independent ``pass^k`` samples come from
sampling at ``temperature=1.0``, not from the seed (which the fake uses for
deterministic tests). Costs are computed from a list-price table; the defaults are
*estimates* -- override ``prices`` for an exact figure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from chorus.benchmarks.swe.types import BenchDependencyMissing, ModelResponse

DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens. Cache write is ~1.25x input, cache read ~0.1x."""

    input: float
    output: float

    def cost(
        self, *, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int
    ) -> float:
        return (
            (input_tokens * self.input)
            + (cache_write * self.input * 1.25)
            + (cache_read * self.input * 0.10)
            + (output_tokens * self.output)
        ) / 1_000_000


# List-price estimates (USD / Mtok). Override for accuracy; only the relative cost
# matters for the harness-only comparison, and it is labelled an estimate.
DEFAULT_PRICES: dict[str, Price] = {
    "claude-opus-4-8": Price(input=15.0, output=75.0),
    "claude-sonnet-4-6": Price(input=3.0, output=15.0),
    "claude-haiku-4-5": Price(input=1.0, output=5.0),
}


class AnthropicPatchModel:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        api_key: str | None = None,
        prices: dict[str, Price] | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._prices = prices or DEFAULT_PRICES
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

    def ensure_ready(self) -> None:
        """Preflight: raise BenchDependencyMissing now if the key/SDK are absent."""

        self._ensure_client()

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise BenchDependencyMissing(
                "ANTHROPIC_API_KEY is not set; the benchmark needs a real model to run."
            )
        try:
            import anthropic
        except ImportError as exc:
            raise BenchDependencyMissing(
                "anthropic is not installed; `pip install 'chorus-harness[bench]'`."
            ) from exc
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def complete(
        self, *, system: str, user: str, seed: int, max_tokens: int | None = None
    ) -> ModelResponse:
        del seed  # the API has no seed; sampling provides independent draws
        client = self._ensure_client()
        resp = client.messages.create(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            temperature=self.temperature,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        usage = resp.usage
        return ModelResponse(
            text=text,
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cost_usd=self._cost(usage),
        )

    def _cost(self, usage) -> float:
        price = self._prices.get(self.model)
        if price is None:
            return 0.0
        return price.cost(
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )
