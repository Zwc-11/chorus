"""Fake ModelPort adapters for tests, dry runs, and budget estimation.

:class:`FakeModel` replays scripted responses in order and records every call so
tests can assert on prompts, temperatures, and fan-out counts without a network.
:class:`StochasticFakeModel` is seeded: the same seed yields the same sequence,
different seeds diverge — the cheap way to simulate a distribution of attempts.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from murmur.core.model_port import ModelResponse, ModelUnavailable


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """Structural record of one ``complete`` call, for test assertions."""

    model: str
    messages: tuple[dict[str, str], ...]
    temperature: float
    max_tokens: int
    response_format: dict[str, Any] | None


@dataclass
class FakeModel:
    """Replays ``responses`` in order (cycling), recording every call.

    Entries may be plain strings or full :class:`ModelResponse` objects. With no
    responses configured, each call returns ``fake:{model}:{index}`` so fan-out
    candidates stay distinguishable.
    """

    responses: Sequence[str | ModelResponse] = ()
    input_tokens: int = 12
    output_tokens: int = 24
    cost_per_call_usd: float = 0.0
    calls: list[RecordedCall] = field(default_factory=list)

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> ModelResponse:
        index = len(self.calls)
        self.calls.append(
            RecordedCall(
                model=model,
                messages=tuple(dict(m) for m in messages),
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        )
        if self.responses:
            entry = self.responses[index % len(self.responses)]
            if isinstance(entry, ModelResponse):
                return entry
            text = entry
        else:
            text = f"fake:{model}:{index}"
        return ModelResponse(
            text=text,
            model=model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=self.cost_per_call_usd,
        )


class StochasticFakeModel:
    """Seeded fake: deterministic per seed, divergent across seeds.

    Each call draws from ``candidates``; with probability ``error_rate`` the call
    raises :class:`ModelUnavailable` instead, simulating a flaky provider.
    """

    def __init__(
        self,
        *,
        seed: int = 0,
        candidates: Sequence[str] = ("candidate-a", "candidate-b", "candidate-c"),
        error_rate: float = 0.0,
    ) -> None:
        if not candidates:
            raise ValueError("candidates must be non-empty")
        self._rng = random.Random(seed)
        self._candidates = tuple(candidates)
        self._error_rate = error_rate
        self.calls: list[RecordedCall] = []

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> ModelResponse:
        self.calls.append(
            RecordedCall(
                model=model,
                messages=tuple(dict(m) for m in messages),
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        )
        if self._rng.random() < self._error_rate:
            raise ModelUnavailable(f"simulated outage for {model}")
        text = self._rng.choice(self._candidates)
        return ModelResponse(
            text=text,
            model=model,
            input_tokens=10,
            output_tokens=len(text),
        )
