"""OpenAI-compatible model adapter — serves both DeepSeek and local Ollama.

DeepSeek's API and Ollama's ``/v1`` endpoint both speak the OpenAI chat-completions
protocol, so a single adapter covers both: only the base URL, the API key, and the
default model differ. The runtime is async; the OpenAI SDK call is synchronous, so we
run it in a worker thread (``asyncio.to_thread``) to keep fan-out concurrent without
pulling in a second async client.

The SDK is imported lazily and the API key is read at call time, so importing this
module never requires the ``openai`` package or any credentials — exactly the pattern
the benchmark's :class:`DeepSeekPatchModel` already uses.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from murmur.flock.gateway import ModelReply, ModelUnavailable
from murmur.flock.ir import Effort


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens, for cost accounting on the ledger."""

    input: float
    output: float

    def cost(self, *, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input + output_tokens * self.output) / 1_000_000


# List-price estimates (USD / Mtok). Only relative cost matters for the harness, and
# local Ollama models are free.
DEFAULT_PRICES: dict[str, Price] = {
    "deepseek-chat": Price(input=0.27, output=1.10),
    "deepseek-reasoner": Price(input=0.55, output=2.19),
    "deepseek-v4-flash": Price(input=0.10, output=0.40),
    "deepseek-v4-pro": Price(input=0.80, output=3.20),
}


class OpenAICompatModel:
    """An async :class:`~murmur.flock.gateway.ModelPort` over any OpenAI-compatible API.

    ``effort="high"`` engages reasoning/thinking on DeepSeek's advanced models; for
    everything else it sends a plain chat completion at ``temperature``.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None,
        name: str | None = None,
        api_key_env: str = "",
        temperature: float = 1.0,
        max_tokens: int = 4096,
        prices: dict[str, Price] | None = None,
        reasoning_effort: str = "high",
        thinking_enabled: bool = True,
    ) -> None:
        self.name = name or model
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._prices = prices or DEFAULT_PRICES
        self._reasoning_effort = reasoning_effort
        self._thinking_enabled = thinking_enabled
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        key = self._api_key or (os.environ.get(self._api_key_env) if self._api_key_env else None)
        if not key:
            hint = f" Set {self._api_key_env}." if self._api_key_env else ""
            raise ModelUnavailable(f"no API key for model {self.name!r}.{hint}")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - exercised only without the SDK
            raise ModelUnavailable(
                "openai is not installed; `pip install 'murmur-harness[bench]'`."
            ) from exc
        self._client = OpenAI(api_key=key, base_url=self._base_url)
        return self._client

    def _uses_reasoning(self) -> bool:
        name = self._model.lower()
        return "v4" in name or "reasoner" in name

    def _kwargs(self, *, system: str, user: str, effort: Effort, max_tokens: int | None) -> dict:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens or self._max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if effort == "high" and self._uses_reasoning():
            if self._reasoning_effort:
                kwargs["reasoning_effort"] = self._reasoning_effort
            if self._thinking_enabled:
                kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            kwargs["temperature"] = self._temperature
        return kwargs

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        price = self._prices.get(self._model)
        if price is None:
            return 0.0
        return price.cost(input_tokens=input_tokens, output_tokens=output_tokens)

    def _complete_sync(self, kwargs: dict) -> ModelReply:
        client = self._ensure_client()
        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        text = choice.message.content or ""
        usage = resp.usage
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
        return ModelReply(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=self._cost(in_tok, out_tok),
            model=self._model,
            finish_reason=str(getattr(choice, "finish_reason", "stop") or "stop"),
        )

    async def complete(
        self,
        *,
        system: str,
        user: str,
        effort: Effort = "low",
        max_tokens: int | None = None,
    ) -> ModelReply:
        kwargs = self._kwargs(system=system, user=user, effort=effort, max_tokens=max_tokens)
        return await asyncio.to_thread(self._complete_sync, kwargs)


# --- Factory helpers for the two adapters Phase 0 ships ----------------------------

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
OLLAMA_BASE_URL = "http://localhost:11434/v1"


def deepseek_model(
    *, model: str = "deepseek-v4-pro", api_key: str | None = None
) -> OpenAICompatModel:
    """A DeepSeek adapter; reads ``DEEPSEEK_API_KEY`` lazily if no key is passed."""

    return OpenAICompatModel(
        model=model,
        base_url=os.environ.get("DEEPSEEK_BASE_URL") or DEEPSEEK_BASE_URL,
        api_key=api_key,
        api_key_env="DEEPSEEK_API_KEY",
        name=model,
    )


def ollama_model(*, model: str, base_url: str | None = None) -> OpenAICompatModel:
    """A local-Ollama adapter — free, no key needed (Ollama ignores the token)."""

    return OpenAICompatModel(
        model=model,
        base_url=base_url or os.environ.get("OLLAMA_BASE_URL") or OLLAMA_BASE_URL,
        api_key=os.environ.get("OLLAMA_API_KEY") or "ollama",
        name=f"ollama:{model}",
        prices={},  # local inference is free
        thinking_enabled=False,
    )
