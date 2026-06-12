"""Ollama adapter: local models through Ollama's OpenAI-compatible endpoint.

Ollama serves ``/v1/chat/completions`` on ``http://localhost:11434`` with no API
key, so this is a thin subclass: local-first defaults, no key requirement, and
every call costs $0.00 — the whole point of the cheap-model harness. Override
the host with ``OLLAMA_BASE_URL`` or ``base_url=``.
"""

from __future__ import annotations

import os

from chorus.adapters.models.openai_compatible import OpenAICompatibleModel, Transport


class OllamaModel(OpenAICompatibleModel):
    """ModelPort adapter for a local Ollama server."""

    DEFAULT_BASE_URL = "http://localhost:11434/v1"
    requires_api_key = False

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float = 300.0,
        transport: Transport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url or os.environ.get("OLLAMA_BASE_URL") or self.DEFAULT_BASE_URL,
            api_key="",
            api_key_env="",
            timeout_seconds=timeout_seconds,
            prices={},  # local models are free; cost stays 0.0
            transport=transport,
        )
