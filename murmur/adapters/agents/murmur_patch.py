"""Murmur patch agent: a structured coding-repair pipeline on the ModelPort seam.

Unlike the free-form JSON action loop in :class:`MurmurLiteAgent`, this agent
walks a fixed pipeline that plays to cheap models' strengths — each model call
has one narrow job with a strict output format:

    reproduce failure -> localize files -> read them -> propose unified diff
    -> apply (retry on apply error) -> run test -> git diff -> finish

Every tool call goes through the policy-controlled :class:`ContractToolProxy`;
the agent never touches the filesystem or shell directly.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from murmur.adapters.tools.contract_proxy import ContractToolProxy
from murmur.core.model_port import ModelPort, ModelResponse
from murmur.domain.contract import Contract

_LOCALIZE_SYSTEM = (
    "You are Murmur's file localizer. Given a failing-test output and a repository "
    "file list, identify the source files most likely to contain the bug. "
    "Return ONLY a JSON array of up to {max_files} repo-relative file paths from "
    "the provided list. No prose, no markdown."
)

_PATCH_SYSTEM = (
    "You are Murmur's patch writer. Fix the failing test with the smallest correct "
    "change. Return ONLY a unified diff that `git apply` accepts: paths relative to "
    "the repository root with a/ and b/ prefixes, correct hunk headers, no prose, "
    "no markdown fences. Do not modify the tests."
)


class MurmurPatchAgent:
    """ModelPort-backed contract agent for failing-test repair."""

    def __init__(
        self,
        *,
        model_port: ModelPort,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        max_files: int = 3,
        max_patch_rounds: int = 2,
    ) -> None:
        self.model_port = model_port
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_files = max(1, max_files)
        self.max_patch_rounds = max(1, max_patch_rounds)

    def run(self, *, contract: Contract, tools: ContractToolProxy, feedback: str = "") -> str:
        failure = feedback.strip() or self._reproduce(contract, tools)
        listing = self._list_files(tools)
        paths = self._localize(tools, failure=failure, listing=listing)
        contents = self._read_files(tools, paths)

        apply_error = ""
        applied = False
        for _ in range(self.max_patch_rounds):
            patch = self._propose_patch(
                tools,
                goal=contract.task.title,
                failure=failure,
                contents=contents,
                apply_error=apply_error,
            )
            if not patch:
                apply_error = (
                    "Your previous reply contained no unified diff. "
                    "Reply with only the diff."
                )
                continue
            result = tools.call("apply_patch", {"patch": patch})
            if result.ok:
                applied = True
                break
            apply_error = f"`git apply` rejected the previous diff:\n{result.error}"
        if not applied:
            summary = "no applicable patch produced"
            tools.call("finish", {"summary": summary})
            return summary

        test = tools.call("run_test", {"command": contract.task.command})
        passed = bool(test.ok and isinstance(test.result, dict) and test.result.get("passed"))
        touched = ", ".join(paths) or "unknown files"
        summary = (
            f"patched {touched}; target test passed"
            if passed
            else f"patched {touched}; target test still failing"
        )
        tools.call("git_diff", {})
        tools.call("finish", {"summary": summary})
        return summary

    # --- pipeline steps -------------------------------------------------------

    def _reproduce(self, contract: Contract, tools: ContractToolProxy) -> str:
        result = tools.call("run_test", {"command": contract.task.command})
        if result.ok and isinstance(result.result, dict):
            data = result.result
            return f"{data.get('stdout', '')}\n{data.get('stderr', '')}".strip()[:8000]
        return str(result.error)[:8000]

    def _list_files(self, tools: ContractToolProxy) -> list[str]:
        result = tools.call("list_files", {"glob": "**/*.py"})
        if result.ok and isinstance(result.result, list):
            return [str(path) for path in result.result][:200]
        return []

    def _localize(
        self, tools: ContractToolProxy, *, failure: str, listing: list[str]
    ) -> list[str]:
        if not listing:
            return []
        response = self._complete(
            tools,
            [
                {
                    "role": "system",
                    "content": _LOCALIZE_SYSTEM.format(max_files=self.max_files),
                },
                {
                    "role": "user",
                    "content": (
                        f"## Failing test output\n{failure or '(no output captured)'}\n\n"
                        f"## Repository files\n" + "\n".join(listing)
                    ),
                },
            ],
        )
        paths = _extract_paths(response.text, known=listing)
        if not paths:
            paths = [path for path in listing if path in failure]
        return paths[: self.max_files]

    def _read_files(self, tools: ContractToolProxy, paths: list[str]) -> dict[str, str]:
        contents: dict[str, str] = {}
        for path in paths:
            result = tools.call("read_file", {"path": path})
            if result.ok:
                contents[path] = str(result.result)[:6000]
        return contents

    def _propose_patch(
        self,
        tools: ContractToolProxy,
        *,
        goal: str,
        failure: str,
        contents: dict[str, str],
        apply_error: str,
    ) -> str:
        sections = [f"## Task\n{goal}", f"## Failing test output\n{failure}"]
        for path, text in contents.items():
            sections.append(f"## File: {path}\n```\n{text}\n```")
        if apply_error:
            sections.append(f"## Previous attempt failed\n{apply_error}")
        response = self._complete(
            tools,
            [
                {"role": "system", "content": _PATCH_SYSTEM},
                {"role": "user", "content": "\n\n".join(sections)},
            ],
        )
        return _extract_patch(response.text)

    def _complete(
        self, tools: ContractToolProxy, messages: list[dict[str, str]]
    ) -> ModelResponse:
        response = asyncio.run(
            self.model_port.complete(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        )
        tools.budget.model_calls += 1
        tools.budget.cost_usd += response.cost_usd
        tools.events.emit(
            "model_call_finished",
            {
                "model": response.model or self.model,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
            },
        )
        return response


def port_for_provider(provider: str, model: str = "") -> tuple[ModelPort, str]:
    """Map a provider name onto a ModelPort adapter and a default model id."""

    from murmur.adapters.models import FakeModel, OllamaModel, OpenAICompatibleModel

    normalized = (provider or "deepseek").strip().lower()
    if normalized == "fake":
        return FakeModel(), model or "fake-model"
    if normalized == "ollama":
        if not model:
            raise KeyError("a model id is required for provider 'ollama'")
        return OllamaModel(), model
    if normalized == "openai":
        return OpenAICompatibleModel(), model or "gpt-4o-mini"
    if normalized == "deepseek":
        return (
            OpenAICompatibleModel(
                base_url="https://api.deepseek.com", api_key_env="DEEPSEEK_API_KEY"
            ),
            model or "deepseek-chat",
        )
    raise KeyError(f"unknown model provider: {provider!r}")


def _extract_paths(text: str, *, known: list[str]) -> list[str]:
    """Parse a JSON array of paths, tolerating fences and prose around it."""

    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", stripped, re.DOTALL)
    raw = fenced.group(1) if fenced else stripped
    parsed: Any = None
    if not fenced:
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        raw = match.group(0) if match else raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    known_set = set(known)
    if isinstance(parsed, list):
        ordered = [str(item) for item in parsed if str(item) in known_set]
        if ordered:
            return ordered
    # Fallback: any known path the model mentioned, in listing order.
    return [path for path in known if path in text]


def _extract_patch(text: str) -> str:
    """Pull a git-applyable unified diff out of a model reply."""

    fenced = re.search(r"```(?:diff|patch)?\n(.*?)```", text, re.DOTALL)
    body = fenced.group(1) if fenced else text
    start = body.find("--- ")
    if start == -1:
        return ""
    patch = body[start:].strip("\n") + "\n"
    return patch
