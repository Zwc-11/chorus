"""Model provider factory — one entry point for Anthropic and DeepSeek patch models."""

from __future__ import annotations

import os
from typing import Literal

from chorus.benchmarks.swe.model import (
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicPatchModel,
    DeepSeekPatchModel,
)
from chorus.benchmarks.swe.types import BenchDependencyMissing, PatchModel

ProviderName = Literal["anthropic", "deepseek"]

PROVIDERS: tuple[ProviderName, ...] = ("anthropic", "deepseek")
DEFAULT_PROVIDER: ProviderName = "deepseek"

DEFAULT_MODEL_BY_PROVIDER: dict[ProviderName, str] = {
    "anthropic": DEFAULT_ANTHROPIC_MODEL,
    "deepseek": DeepSeekPatchModel.DEFAULT_MODEL,
}


def normalize_provider(name: str | None) -> ProviderName:
    raw = (name or os.environ.get("CHORUS_MODEL_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if raw in PROVIDERS:
        return raw  # type: ignore[return-value]
    raise BenchDependencyMissing(
        f"Unknown model provider {raw!r}; use one of: {', '.join(PROVIDERS)}. "
        "Set CHORUS_MODEL_PROVIDER or pass --provider."
    )


def default_model(provider: str | None = None) -> str:
    return DEFAULT_MODEL_BY_PROVIDER[normalize_provider(provider)]


def create_patch_model(
    *,
    provider: str | None = None,
    model: str = "",
    api_key: str | None = None,
    **kwargs: object,
) -> PatchModel:
    """Build the active :class:`PatchModel` for SWE agents, bench, and gate."""

    resolved = normalize_provider(provider)
    model_id = model or default_model(resolved)
    if resolved == "deepseek":
        return DeepSeekPatchModel(model=model_id, api_key=api_key, **kwargs)  # type: ignore[arg-type]
    return AnthropicPatchModel(model=model_id, api_key=api_key, **kwargs)  # type: ignore[arg-type]


def provider_status(provider: str | None = None) -> dict[str, str]:
    """Summarize whether credentials for *provider* appear configured."""

    resolved = normalize_provider(provider)
    if resolved == "deepseek":
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        return {
            "provider": resolved,
            "model_default": default_model(resolved),
            "api_key": "set" if key else "missing (DEEPSEEK_API_KEY)",
            "base_url": DeepSeekPatchModel.DEFAULT_BASE_URL,
            "reasoning_effort": os.environ.get("DEEPSEEK_REASONING_EFFORT", "high"),
            "thinking": os.environ.get("DEEPSEEK_THINKING", "enabled"),
        }
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    return {
        "provider": resolved,
        "model_default": default_model(resolved),
        "api_key": "set" if key else "missing (ANTHROPIC_API_KEY)",
        "base_url": "https://api.anthropic.com",
    }
