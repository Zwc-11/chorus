"""OpenAI-compatible chat-completions adapter.

One adapter covers every provider that speaks ``POST {base_url}/chat/completions``:
OpenAI, DeepSeek, vLLM, LM Studio, OpenRouter, and (via the subclass in
``ollama.py``) local Ollama. It uses only the standard library — ``urllib`` in a
worker thread — so the base install stays dependency-light, and tests inject a
fake ``transport`` instead of touching the network.

Examples::

    OpenAICompatibleModel(api_key_env="OPENAI_API_KEY")
    OpenAICompatibleModel(base_url="https://api.deepseek.com",
                          api_key_env="DEEPSEEK_API_KEY")
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from chorus.core.model_port import ModelOutputError, ModelPrice, ModelResponse, ModelUnavailable

# transport(url, headers, payload, timeout_seconds) -> decoded JSON body.
Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]

# List-price estimates (USD / Mtok) for common cheap models. Unknown models cost
# 0.0 — pass ``prices`` for accuracy; only relative cost matters to the harness.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "deepseek-chat": ModelPrice(input=0.27, output=1.10),
    "deepseek-reasoner": ModelPrice(input=0.55, output=2.19),
    "gpt-4o-mini": ModelPrice(input=0.15, output=0.60),
    "gpt-4o": ModelPrice(input=2.50, output=10.00),
}


def urllib_transport(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout_seconds: float
) -> dict[str, Any]:
    """Default transport: blocking stdlib POST, mapped to ModelPort errors."""

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise ModelUnavailable(f"HTTP {exc.code} from {url}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ModelUnavailable(f"cannot reach {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ModelOutputError(f"non-JSON response from {url}: {exc}") from exc


class OpenAICompatibleModel:
    """ModelPort adapter for any ``/chat/completions`` endpoint."""

    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    requires_api_key = True

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_seconds: float = 120.0,
        prices: Mapping[str, ModelPrice] | None = None,
        extra_headers: Mapping[str, str] | None = None,
        transport: Transport | None = None,
    ) -> None:
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._api_key_env = api_key_env
        self._api_key = api_key or (os.environ.get(api_key_env, "") if api_key_env else "")
        self._timeout_seconds = timeout_seconds
        self._prices = dict(DEFAULT_PRICES if prices is None else prices)
        self._extra_headers = dict(extra_headers or {})
        self._transport: Transport = transport or urllib_transport

    def ensure_ready(self) -> None:
        """Preflight: raise :class:`ModelUnavailable` now if the key is absent."""

        if self.requires_api_key and not self._api_key:
            hint = f"set {self._api_key_env} " if self._api_key_env else ""
            raise ModelUnavailable(
                f"no API key for {self._base_url}; {hint}or pass api_key=."
            )

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> ModelResponse:
        self.ensure_ready()
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        headers = {"Content-Type": "application/json", **self._extra_headers}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._base_url}/chat/completions"
        start = time.perf_counter()
        raw = await asyncio.to_thread(
            self._transport, url, headers, payload, self._timeout_seconds
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        return self._parse(raw, requested_model=model, latency_ms=latency_ms)

    def _parse(
        self, raw: dict[str, Any], *, requested_model: str, latency_ms: float
    ) -> ModelResponse:
        choices: Sequence[Any] = raw.get("choices") or []
        if not choices:
            raise ModelOutputError(
                f"no choices in response from {requested_model} at {self._base_url}"
            )
        first = choices[0] or {}
        message = first.get("message") or {}
        text = message.get("content") or ""
        usage = raw.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        price = self._prices.get(requested_model)
        cost = (
            price.cost(input_tokens=input_tokens, output_tokens=output_tokens) if price else 0.0
        )
        return ModelResponse(
            text=text,
            model=str(raw.get("model") or requested_model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            finish_reason=str(first.get("finish_reason") or ""),
            latency_ms=latency_ms,
        )
