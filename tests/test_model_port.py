"""ModelPort and its adapters: fakes, OpenAI-compatible HTTP, and Ollama.

The HTTP adapters take an injected ``transport`` so no test touches the network;
we assert on the exact request they build and on how they parse responses.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chorus.adapters.models.fake import FakeModel, StochasticFakeModel
from chorus.adapters.models.ollama import OllamaModel
from chorus.adapters.models.openai_compatible import OpenAICompatibleModel
from chorus.core.model_port import (
    ModelOutputError,
    ModelPort,
    ModelPrice,
    ModelResponse,
    ModelUnavailable,
)

MESSAGES = [{"role": "user", "content": "fix the bug"}]


def _complete(port: ModelPort, **kwargs: Any) -> ModelResponse:
    """Drive any adapter through the ModelPort-typed seam."""

    return asyncio.run(port.complete(messages=MESSAGES, **kwargs))


class CapturingTransport:
    """Records the request and returns a canned OpenAI-style body."""

    def __init__(self, body: dict[str, Any] | None = None) -> None:
        self.body = body or {
            "model": "deepseek-chat-0125",
            "choices": [
                {"message": {"role": "assistant", "content": "patched"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
        }
        self.requests: list[tuple[str, dict[str, str], dict[str, Any], float]] = []

    def __call__(
        self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        self.requests.append((url, headers, payload, timeout))
        return self.body


# --- FakeModel ---------------------------------------------------------------


def test_fake_model_replays_and_records() -> None:
    fake = FakeModel(responses=["one", "two"])
    first = _complete(fake, model="m", temperature=0.7, max_tokens=64)
    second = _complete(fake, model="m")
    third = _complete(fake, model="m")  # cycles back

    assert (first.text, second.text, third.text) == ("one", "two", "one")
    assert len(fake.calls) == 3
    assert fake.calls[0].temperature == 0.7
    assert fake.calls[0].max_tokens == 64
    assert fake.calls[0].messages == (MESSAGES[0],)


def test_fake_model_default_texts_distinguish_fanout() -> None:
    fake = FakeModel()
    texts = [_complete(fake, model="qwen").text for _ in range(3)]
    assert texts == ["fake:qwen:0", "fake:qwen:1", "fake:qwen:2"]


def test_fake_model_passes_through_full_responses() -> None:
    canned = ModelResponse(text="diff", model="x", cost_usd=0.05)
    fake = FakeModel(responses=[canned])
    assert _complete(fake, model="ignored") is canned


def test_stochastic_fake_is_deterministic_per_seed() -> None:
    run1 = StochasticFakeModel(seed=7)
    run2 = StochasticFakeModel(seed=7)
    run3 = StochasticFakeModel(seed=8)
    seq1 = [_complete(run1, model="m").text for _ in range(8)]
    seq2 = [_complete(run2, model="m").text for _ in range(8)]
    seq3 = [_complete(run3, model="m").text for _ in range(8)]
    assert seq1 == seq2
    assert seq1 != seq3  # different seeds should diverge over 8 draws


def test_stochastic_fake_simulates_outages() -> None:
    flaky = StochasticFakeModel(seed=0, error_rate=1.0)
    with pytest.raises(ModelUnavailable, match="simulated outage"):
        _complete(flaky, model="m")


# --- OpenAICompatibleModel ----------------------------------------------------


def test_openai_compatible_builds_request_and_parses_response() -> None:
    transport = CapturingTransport()
    port = OpenAICompatibleModel(
        base_url="https://api.deepseek.com/",
        api_key="sk-test",
        prices={"deepseek-chat": ModelPrice(input=0.27, output=1.10)},
        transport=transport,
    )
    response = _complete(
        port,
        model="deepseek-chat",
        temperature=0.5,
        max_tokens=256,
        response_format={"type": "json_object"},
    )

    url, headers, payload, timeout = transport.requests[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test"
    assert payload["model"] == "deepseek-chat"
    assert payload["temperature"] == 0.5
    assert payload["max_tokens"] == 256
    assert payload["response_format"] == {"type": "json_object"}
    assert timeout == 120.0

    assert response.text == "patched"
    assert response.model == "deepseek-chat-0125"  # server-reported id wins
    assert response.finish_reason == "stop"
    assert response.input_tokens == 1_000_000
    assert response.output_tokens == 1_000_000
    assert response.cost_usd == pytest.approx(0.27 + 1.10)
    assert response.latency_ms >= 0.0


def test_openai_compatible_omits_response_format_by_default() -> None:
    transport = CapturingTransport()
    port = OpenAICompatibleModel(api_key="k", transport=transport)
    _complete(port, model="gpt-4o-mini")
    payload = transport.requests[0][2]
    assert "response_format" not in payload


def test_openai_compatible_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    port = OpenAICompatibleModel(transport=CapturingTransport())
    with pytest.raises(ModelUnavailable, match="OPENAI_API_KEY"):
        _complete(port, model="gpt-4o-mini")


def test_openai_compatible_reads_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACME_KEY", "from-env")
    transport = CapturingTransport()
    port = OpenAICompatibleModel(api_key_env="ACME_KEY", transport=transport)
    _complete(port, model="m")
    assert transport.requests[0][1]["Authorization"] == "Bearer from-env"


def test_openai_compatible_rejects_empty_choices() -> None:
    transport = CapturingTransport(body={"choices": [], "usage": {}})
    port = OpenAICompatibleModel(api_key="k", transport=transport)
    with pytest.raises(ModelOutputError, match="no choices"):
        _complete(port, model="m")


def test_unknown_model_costs_zero() -> None:
    transport = CapturingTransport()
    port = OpenAICompatibleModel(api_key="k", transport=transport)
    response = _complete(port, model="some-local-finetune")
    assert response.cost_usd == 0.0


# --- OllamaModel ----------------------------------------------------------------


def test_ollama_defaults_local_no_key_zero_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    transport = CapturingTransport()
    port = OllamaModel(transport=transport)
    response = _complete(port, model="qwen2.5-coder")

    url, headers, _, _ = transport.requests[0]
    assert url == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in headers
    assert response.cost_usd == 0.0


def test_ollama_honors_env_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-box:11434/v1")
    transport = CapturingTransport()
    _complete(OllamaModel(transport=transport), model="m")
    assert transport.requests[0][0] == "http://gpu-box:11434/v1/chat/completions"
