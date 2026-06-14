"""Patch models: the seam to an LLM.

:class:`AnthropicPatchModel` calls the Anthropic Messages API (prompt caching on the
system block). :class:`DeepSeekPatchModel` uses the OpenAI-compatible DeepSeek API
(``deepseek-reasoner`` by default). Use :func:`murmur.benchmarks.swe.providers.create_patch_model`
to pick the provider via ``CHORUS_MODEL_PROVIDER`` or ``--provider``.

Heavy SDKs import lazily so the rest of the package runs without them. The real APIs
have no seed parameter; independent ``pass^k`` samples come from sampling at
``temperature=1.0``. Costs use list-price tables — estimates unless you override
``prices``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from murmur.benchmarks.swe.types import BenchDependencyMissing, BenchModelOutputError, ModelResponse

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_MODEL = DEFAULT_ANTHROPIC_MODEL  # backward-compatible alias


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
        model: str = DEFAULT_ANTHROPIC_MODEL,
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
                "anthropic is not installed; `pip install 'murmur-ai-harness[bench]'`."
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


# DeepSeek — OpenAI-compatible chat completions (https://api.deepseek.com)
DEEPSEEK_DEFAULT_PRICES: dict[str, Price] = {
    "deepseek-chat": Price(input=0.27, output=1.10),
    "deepseek-reasoner": Price(input=0.55, output=2.19),
    "deepseek-v4-pro": Price(input=0.80, output=3.20),
}


class DeepSeekPatchModel:
    """DeepSeek v4 Pro with reasoning + thinking (override via ``CHORUS_MODEL``)."""

    DEFAULT_MODEL = "deepseek-v4-pro"
    DEFAULT_BASE_URL = "https://api.deepseek.com"

    def __init__(
        self,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 1.0,
        api_key: str | None = None,
        base_url: str | None = None,
        prices: dict[str, Price] | None = None,
        reasoning_effort: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> None:
        self.model = model or os.environ.get("CHORUS_MODEL") or self.DEFAULT_MODEL
        default_max = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "8192"))
        self.max_tokens = max_tokens if max_tokens is not None else default_max
        self.temperature = temperature
        self._base_url = base_url or os.environ.get("DEEPSEEK_BASE_URL") or self.DEFAULT_BASE_URL
        self._prices = prices or DEEPSEEK_DEFAULT_PRICES
        self._api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        self._reasoning_effort = reasoning_effort or os.environ.get(
            "DEEPSEEK_REASONING_EFFORT", "high"
        )
        thinking_raw = os.environ.get("DEEPSEEK_THINKING", "enabled")
        self._thinking_enabled = (
            thinking_enabled
            if thinking_enabled is not None
            else thinking_raw.strip().lower() in ("1", "true", "yes", "enabled", "on")
        )
        self._client: Any = None

    def ensure_ready(self) -> None:
        self._ensure_client()

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise BenchDependencyMissing(
                "DEEPSEEK_API_KEY is not set; add it to .env or export it before "
                "running a real agent."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise BenchDependencyMissing(
                "openai is not installed; `pip install 'murmur-ai-harness[bench]'`."
            ) from exc
        self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    def _uses_advanced_reasoning(self) -> bool:
        name = self.model.lower()
        return "v4" in name or "reasoner" in name

    def _completion_kwargs(
        self, *, system: str, user: str, max_tokens: int | None
    ) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "messages": messages,
        }
        if self._uses_advanced_reasoning():
            if self._reasoning_effort:
                kwargs["reasoning_effort"] = self._reasoning_effort
            if self._thinking_enabled:
                kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            kwargs["temperature"] = self.temperature
        return kwargs

    def complete(
        self, *, system: str, user: str, seed: int, max_tokens: int | None = None
    ) -> ModelResponse:
        del seed
        client = self._ensure_client()
        resp = client.chat.completions.create(
            **self._completion_kwargs(system=system, user=user, max_tokens=max_tokens)
        )
        choice = resp.choices[0]
        message = choice.message
        text = message.content or ""
        usage = resp.usage
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
        self._raise_if_reasoning_starved(choice=choice, text=text, output_tokens=out_tok)
        return ModelResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=self._cost(in_tok, out_tok),
        )

    def _raise_if_reasoning_starved(self, *, choice: Any, text: str, output_tokens: int) -> None:
        if text.strip():
            return
        message = choice.message
        extra = getattr(message, "model_extra", None) or {}
        reasoning = getattr(message, "reasoning_content", None) or extra.get("reasoning_content")
        if not reasoning:
            return
        finish_reason = getattr(choice, "finish_reason", "")
        raise BenchModelOutputError(
            "DeepSeek returned reasoning tokens but no final patch content "
            f"(finish_reason={finish_reason!r}, output_tokens={output_tokens}). "
            "For SWE-bench, use `--model deepseek-chat` or increase DEEPSEEK_MAX_TOKENS "
            "substantially; otherwise Chorus would submit an empty patch."
        )

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        price = self._prices.get(self.model)
        if price is None:
            return 0.0
        return price.cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read=0,
            cache_write=0,
        )
