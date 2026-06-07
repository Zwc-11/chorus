"""Model routing — turn an IR ``model`` string into a built :class:`ModelPort`.

A plan node names its model as a string (``"deepseek-v4-flash"``, ``"deepseek-v4-pro"``,
``"ollama:qwen2.5"``, or ``"fake"`` / ``"fake:<label>"``). :func:`build_model` maps
that string to a concrete adapter — the one place model-selection strategy lives.

A *resolver* is just ``Callable[[str], ModelPort]``. The runtime takes one so the
same plan can run live (:func:`default_resolver`) or entirely on deterministic fakes
(:func:`offline_resolver`) without changing the plan — useful for tests, demos, and
dry runs with no API keys.
"""

from __future__ import annotations

from collections.abc import Callable

from murmur.flock.adapters.fake import FakeModel
from murmur.flock.adapters.openai_compat import deepseek_model, ollama_model
from murmur.flock.gateway import ModelPort, ModelUnavailable

ModelResolver = Callable[[str], ModelPort]


def build_model(spec: str) -> ModelPort:
    """Build the live adapter named by *spec*, or raise :class:`ModelUnavailable`."""

    s = (spec or "").strip()
    if s == "fake" or s.startswith("fake:"):
        return FakeModel(name=s or "fake")
    if s.startswith("ollama:"):
        return ollama_model(model=s.split(":", 1)[1])
    if s.startswith("deepseek"):
        return deepseek_model(model=s)
    raise ModelUnavailable(
        f"unknown model spec {spec!r}; expected 'fake', 'fake:<label>', "
        "'deepseek-<name>', or 'ollama:<name>'"
    )


def default_resolver() -> ModelResolver:
    """Resolve specs to their real adapters (DeepSeek / Ollama / fake)."""

    return build_model


def offline_resolver(*, cost_per_call: float = 0.0) -> ModelResolver:
    """Resolve *every* spec to a deterministic :class:`FakeModel`.

    The fake keeps the requested spec as its ``name``, so traces still show which
    model a plan *intended* to use while nothing leaves the machine.
    """

    return lambda spec: FakeModel(name=spec or "fake", cost_per_call=cost_per_call)
