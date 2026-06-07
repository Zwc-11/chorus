"""A deterministic, offline model adapter.

:class:`FakeModel` implements :class:`~murmur.flock.gateway.ModelPort` without any
network or SDK. It is the workhorse for tests and for running whole plans offline:
given the same prompt it always returns the same reply, so a fan-out of N "agents"
is reproducible. Scripted replies let a test pin exact outputs per prompt.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping

from murmur.flock.gateway import ModelReply
from murmur.flock.ir import Effort


def _word_tokens(text: str) -> int:
    return max(1, len(text.split()))


class FakeModel:
    """A deterministic stand-in for a real model.

    ``responder`` maps ``(system, user, effort)`` to the reply text. The default
    echoes a short, stable digest of the prompt so different prompts get different
    (but reproducible) answers. ``scripted`` short-circuits with an exact reply when
    the user prompt contains one of its keys — handy for asserting on specific picks.
    ``cost_per_call`` lets a test exercise the budget ledger.
    """

    def __init__(
        self,
        *,
        name: str = "fake",
        responder: Callable[[str, str, Effort], str] | None = None,
        scripted: Mapping[str, str] | None = None,
        cost_per_call: float = 0.0,
    ) -> None:
        self.name = name
        self._responder = responder
        self._scripted = dict(scripted or {})
        self._cost_per_call = cost_per_call
        self.call_count = 0

    async def complete(
        self,
        *,
        system: str,
        user: str,
        effort: Effort = "low",
        max_tokens: int | None = None,
    ) -> ModelReply:
        self.call_count += 1
        text = self._reply_text(system=system, user=user, effort=effort)
        if max_tokens is not None and max_tokens > 0:
            text = text[: max_tokens * 4]
        return ModelReply(
            text=text,
            input_tokens=_word_tokens(system) + _word_tokens(user),
            output_tokens=_word_tokens(text),
            cost_usd=self._cost_per_call,
            model=self.name,
            finish_reason="stop",
        )

    def _reply_text(self, *, system: str, user: str, effort: Effort) -> str:
        for needle, reply in self._scripted.items():
            if needle in user:
                return reply
        if self._responder is not None:
            return self._responder(system, user, effort)
        digest = hashlib.sha256(f"{effort}\x00{system}\x00{user}".encode()).hexdigest()[:8]
        return f"[fake:{effort}] {user.strip()[:80]} -> {digest}"
