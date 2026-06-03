"""Agent registry: a name maps to a pluggable workflow module.

Chorus's hexagonal core already makes the agent a swappable module -- anything that
implements ``AgentPort`` drops into the conductor, and the ``JudgePort`` is
injectable. This registry surfaces that as a *user-facing* choice: ``--agent
<name>`` disconnects one workflow and plugs in another with no code change. Each
module also declares whether it is ``simulated`` (free, deterministic, no deps) or
real (needs a key / Docker / an extra), so a command can fail fast with a clear
message instead of silently producing a fake number.

Register your own workflow with :func:`register` -- that is the "replace with a new
workflow" seam, exercised the same way the built-ins are.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from chorus.core.ports import AgentPort, JudgePort
from chorus.core.types import AgentAdapterCapabilities
from chorus.gateway.tool_gateway import ToolCallable


@dataclass(frozen=True, slots=True)
class BuiltAgent:
    """A ready-to-run workflow: the agent factory plus its tools and judge.

    ``agent_factory(lane)`` builds one ``AgentPort`` per trajectory (``lane`` is the
    conductor's index / a per-lane seed). ``judge`` of ``None`` means "use the
    conductor default" (the deterministic contract check).
    """

    agent_factory: Callable[[int], AgentPort]
    tools: dict[str, ToolCallable] = field(default_factory=dict)
    judge: JudgePort | None = None
    label: str = ""


@dataclass(frozen=True, slots=True)
class AgentModule:
    name: str
    description: str
    build: Callable[..., BuiltAgent]
    simulated: bool = True  # True = free/no deps; False = needs key/Docker/extra
    capabilities: AgentAdapterCapabilities = field(default_factory=AgentAdapterCapabilities)


_REGISTRY: dict[str, AgentModule] = {}


def register(module: AgentModule) -> None:
    _REGISTRY[module.name] = module


def available(*, simulated: bool | None = None) -> list[str]:
    names = _REGISTRY.values()
    if simulated is not None:
        names = [m for m in names if m.simulated is simulated]  # type: ignore[assignment]
    return sorted(m.name for m in names)


def get(name: str) -> AgentModule:
    if name not in _REGISTRY:
        raise KeyError(f"unknown agent {name!r}; available: {', '.join(available())}")
    return _REGISTRY[name]


# --- built-in modules --------------------------------------------------------


def _build_stochastic(
    *, success_rate: float = 0.7, error_rate: float = 0.1, base_seed: int = 7, **_: object
) -> BuiltAgent:
    from chorus.adapters.agents.stochastic import stochastic_agent_factory, stochastic_tools

    return BuiltAgent(
        agent_factory=stochastic_agent_factory(
            success_rate=success_rate, error_rate=error_rate, base_seed=base_seed
        ),
        tools=stochastic_tools(),
        judge=None,
        label="stochastic",
    )


def _swe_builder(repair: bool) -> Callable[..., BuiltAgent]:
    def build(*, model: str = "", provider: str = "", **_: object) -> BuiltAgent:
        from chorus.adapters.agents.swe import SwePatchAgent
        from chorus.benchmarks.swe.evaluator import SubprocessSweEvaluator
        from chorus.benchmarks.swe.judge import SweBenchJudge
        from chorus.benchmarks.swe.providers import create_patch_model, default_model

        patch_model = create_patch_model(
            provider=provider or None,
            model=model or default_model(provider),
        )
        evaluator = SubprocessSweEvaluator()
        patch_model.ensure_ready()
        evaluator.ensure_ready()
        return BuiltAgent(
            agent_factory=lambda lane: SwePatchAgent(patch_model, repair=repair, seed=lane),
            tools={},
            judge=SweBenchJudge(evaluator),
            label="self-repair" if repair else "single-shot",
        )

    return build


register(
    AgentModule(
        "stochastic",
        "Seeded simulated coding agent -- free, deterministic, no model call.",
        _build_stochastic,
        simulated=True,
        capabilities=AgentAdapterCapabilities(
            record=True,
            replay=True,
            live_execution=True,
            tool_interception=True,
        ),
    )
)
register(
    AgentModule(
        "swe-single-shot",
        (
            "[legacy] Patch-only SWE-bench baseline; use contract-first fix-test for "
            "public proof runs."
        ),
        _swe_builder(repair=False),
        simulated=False,
        capabilities=AgentAdapterCapabilities(
            record=True,
            live_execution=True,
            sandbox=True,
        ),
    )
)


def _build_deepseek_website(*, model: str = "", provider: str = "", **_: object) -> BuiltAgent:
    from chorus.adapters.agents.deepseek_coder import DeepSeekCodingAgent, coding_tools
    from chorus.benchmarks.swe.providers import create_patch_model, default_model

    patch_model = create_patch_model(
        provider=provider or None,
        model=model or default_model(provider),
    )
    patch_model.ensure_ready()
    return BuiltAgent(
        agent_factory=lambda lane: DeepSeekCodingAgent(patch_model, seed=lane),
        tools=coding_tools(),
        judge=None,
        label="deepseek-v4-pro",
    )


register(
    AgentModule(
        "deepseek-website",
        (
            "DeepSeek v4 Pro + high reasoning -- builds the hard landing-site task "
            "(DEEPSEEK_API_KEY)."
        ),
        _build_deepseek_website,
        simulated=False,
        capabilities=AgentAdapterCapabilities(
            record=True,
            live_execution=True,
            tool_interception=True,
        ),
    )
)
register(
    AgentModule(
        "swe-self-repair",
        (
            "[legacy] Patch-only SWE-bench self-review baseline; use contract-first "
            "fix-test for public proof runs."
        ),
        _swe_builder(repair=True),
        simulated=False,
        capabilities=AgentAdapterCapabilities(
            record=True,
            live_execution=True,
            sandbox=True,
        ),
    )
)
