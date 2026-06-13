"""Contract-first agents for the MVP execution harness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import unified_diff
from time import sleep
from typing import Any, Protocol

from murmur.adapters.tools.contract_proxy import ContractToolProxy
from murmur.benchmarks.swe.providers import create_patch_model, default_model
from murmur.benchmarks.swe.types import PatchModel
from murmur.domain.contract import Contract


class ContractAgent(Protocol):
    def run(self, *, contract: Contract, tools: ContractToolProxy, feedback: str = "") -> str:
        """Run through typed tools and return a human review summary."""


@dataclass(slots=True)
class ScriptedFixAgent:
    """Free deterministic demo agent for smoke tests and the first local demo."""

    def run(self, *, contract: Contract, tools: ContractToolProxy, feedback: str = "") -> str:
        del feedback
        files = tools.call("list_files", {"glob": "**/*.py"})
        if not files.ok:
            return files.error
        for path in files.result:
            content = tools.call("read_file", {"path": path})
            if not content.ok:
                continue
            patched = _scripted_patch_text(str(content.result))
            if patched == content.result:
                continue
            patch = _unified(path, str(content.result), patched)
            applied = tools.call("apply_patch", {"patch": patch})
            if not applied.ok:
                return f"scripted patch failed for {path}: {applied.error}"
            tools.call("finish", {"summary": f"patched {path} with scripted failing-test fix"})
            return f"patched {path}"
        tools.call("finish", {"summary": "no scripted patch pattern matched"})
        return "no scripted patch pattern matched"


class MurmurLiteAgent:
    """Structured-action model loop; every action must be a JSON tool request."""

    def __init__(
        self,
        model: PatchModel | None = None,
        *,
        provider: str = "",
        model_id: str = "",
        max_steps: int = 12,
        seed_offset: int = 0,
    ) -> None:
        self.model = model or create_patch_model(
            provider=provider or None,
            model=model_id or default_model(provider),
        )
        self.max_steps = max_steps
        self.seed_offset = seed_offset

    def run(self, *, contract: Contract, tools: ContractToolProxy, feedback: str = "") -> str:
        transcript = _initial_observation(contract, feedback=feedback)
        for step in range(self.max_steps):
            response = _complete_with_retries(
                model=self.model,
                system=_SYSTEM,
                user=transcript,
                seed=self.seed_offset + step,
                tools=tools,
            )
            tools.events.emit(
                "model_call_finished",
                {
                    "model": getattr(self.model, "model", "model"),
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                },
            )
            tools.budget.model_calls += 1
            tools.budget.cost_usd += response.cost_usd
            if not response.text.strip():
                tools.events.emit("model_empty_response", {"step": step})
                return "model returned empty content"
            action = _parse_action(response.text)
            if action is None:
                tools.events.emit("model_invalid_action", {"text": response.text[:1000]})
                transcript += "\nInvalid action. Return one JSON object with action.type and args."
                continue
            tool_name = str(action.get("type", ""))
            args = dict(action.get("args", {}))
            if tool_name == "finish":
                tools.call("finish", {"summary": str(action.get("summary", ""))})
                return str(action.get("summary", "finished"))
            result = tools.call(tool_name, args)
            transcript += f"\nAction: {tool_name} {args}\nResult: {result}"
            if result.tool_name == "apply_patch" and result.ok:
                verify = tools.call("run_test", {"command": contract.task.command})
                transcript += f"\nVerification: {verify.result}"
        tools.call("finish", {"summary": "max steps reached"})
        return "max steps reached"


_SYSTEM = """\
You are Murmur Lite, a coding agent inside a contract-first harness.
Return exactly one JSON object per turn. Valid actions:
{"type":"list_files","args":{"glob":"**/*.py"}}
{"type":"search","args":{"query":"text","glob":"**/*.py"}}
{"type":"read_file","args":{"path":"src/file.py"}}
{"type":"apply_patch","args":{"patch":"unified diff"}}
{"type":"run_test","args":{"command":"pytest ..."}}
{"type":"git_diff","args":{}}
{"type":"finish","summary":"what changed"}
Do not ask for tools outside the contract.
"""


def build_contract_agent(
    *,
    agent: str,
    provider: str = "",
    model: str = "",
    seed: int = 0,
) -> ContractAgent:
    if agent == "scripted":
        return ScriptedFixAgent()
    if agent in {"murmur-lite", "lite"}:
        return MurmurLiteAgent(provider=provider, model_id=model, seed_offset=seed)
    if agent == "murmur":
        from murmur.adapters.agents.murmur_patch import MurmurPatchAgent, port_for_provider

        port, model_id = port_for_provider(provider, model)
        return MurmurPatchAgent(model_port=port, model=model_id)
    raise KeyError("unknown contract agent; use 'scripted', 'murmur-lite', or 'murmur'")


def _complete_with_retries(
    *,
    model: PatchModel,
    system: str,
    user: str,
    seed: int,
    tools: ContractToolProxy,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return model.complete(system=system, user=user, seed=seed)
        except Exception as exc:  # noqa: BLE001 - provider faults are run evidence
            last_error = exc
            tools.events.emit(
                "model_call_retry",
                {"attempt": attempt + 1, "error": str(exc), "metadata": tools.metadata},
            )
            sleep(0.25 * (attempt + 1))
    raise RuntimeError(f"model call failed after retries: {last_error}")


def _scripted_patch_text(text: str) -> str:
    replacements = {
        "return price - discount": "return price * (1 - discount)",
        "return total - discount": "return total * (1 - discount)",
        "return a - b": "return a + b",
    }
    for old, new in replacements.items():
        if old in text:
            return text.replace(old, new)
    return text


def _unified(path: str, before: str, after: str) -> str:
    return "".join(
        unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _initial_observation(contract: Contract, *, feedback: str = "") -> str:
    observation = (
        f"Contract task: {contract.task.title}\n"
        f"Target command: {contract.task.command}\n"
        f"Allowed read: {contract.files.allow_read}\n"
        f"Allowed edit: {contract.files.allow_edit}\n"
    )
    if feedback:
        observation += f"\nPrevious test feedback:\n{feedback[:4000]}\n"
    return observation


def _parse_action(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        parts = stripped.split("```")
        stripped = parts[1] if len(parts) > 1 else stripped
        stripped = stripped.removeprefix("json").strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "type" not in data:
        return None
    return data
