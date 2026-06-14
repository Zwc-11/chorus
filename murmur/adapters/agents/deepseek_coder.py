"""Real coding agent driven by DeepSeek v4 Pro (reasoning + thinking enabled)."""

from __future__ import annotations

from murmur.benchmarks.swe.providers import create_patch_model, default_model
from murmur.benchmarks.swe.types import PatchModel
from murmur.core.ports import ToolGatewayPort
from murmur.core.types import TaskSpec

_SYSTEM = """\
You are an expert full-stack engineer. Follow the user task exactly. Use tools to \
write files into the workspace, then call submit when the site is complete. Think \
step-by-step before each tool call.
"""


class DeepSeekCodingAgent:
    """Multi-step agent: plan → generate → write files → submit artifact."""

    def __init__(
        self,
        model: PatchModel | None = None,
        *,
        provider: str = "",
        model_id: str = "",
        seed: int = 0,
        max_steps: int = 4,
    ) -> None:
        self._model = model or create_patch_model(
            provider=provider or None, model=model_id or default_model(provider)
        )
        self._seed = seed
        self._max_steps = max(1, max_steps)

    @property
    def name(self) -> str:
        return "deepseek-v4-pro"

    async def run(self, task: TaskSpec, gateway: ToolGatewayPort) -> str:
        workspace: dict[str, str] = {}
        transcript = f"Task:\n{task.prompt}\n"

        for index in range(self._max_steps):
            phase = ("plan", "implement", "refine", "finalize")[min(index, 3)]
            await gateway.step(index=index, phase=phase)
            response = self._model.complete(
                system=_SYSTEM,
                user=transcript + _step_instruction(index, workspace),
                seed=self._seed + index,
            )
            await gateway.model(
                model=getattr(self._model, "model", "deepseek-v4-pro"),
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                finish_reason="stop",
                content=response.text[:500],
            )
            transcript += f"\n\n--- assistant ({phase}) ---\n{response.text}\n"
            applied = await self._apply_tool_calls(gateway, response.text, workspace)
            if applied.get("submitted"):
                return applied["submitted"]
            if index == self._max_steps - 1:
                bundle = _bundle_workspace(workspace, response.text)
                return await gateway.call("submit", {"artifact": bundle})

        return await gateway.call("submit", {"artifact": _bundle_workspace(workspace, "")})

    async def _apply_tool_calls(
        self, gateway: ToolGatewayPort, text: str, workspace: dict[str, str]
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for path, content in _extract_file_blocks(text).items():
            workspace[path] = content
            await gateway.call("write_file", {"path": path, "content": content})
        if "submit" in text.lower() and workspace:
            artifact = _bundle_workspace(workspace, text)
            result["submitted"] = await gateway.call("submit", {"artifact": artifact})
        return result


def _step_instruction(index: int, workspace: dict[str, str]) -> str:
    files = ", ".join(workspace) or "(none yet)"
    if index == 0:
        return (
            "\nStep 1: Outline the site structure, then emit ```html and ```css fenced "
            f"blocks OR call write_file for index.html and styles.css. Workspace: {files}"
        )
    if index == 1:
        return (
            "\nStep 2: Implement index.html and styles.css with the tech-noir palette. "
            f"Workspace so far: {files}"
        )
    return (
        "\nStep 3: Polish copy, verify id=metrics and accent #e8192a, then submit the "
        f"full artifact. Workspace: {files}"
    )


def _extract_file_blocks(text: str) -> dict[str, str]:
    import re

    out: dict[str, str] = {}
    pattern = re.compile(r"```(?:html|css|HTML|CSS)?\s*\n(.*?)```", re.DOTALL)
    blocks = pattern.findall(text)
    for block in blocks:
        body = block.strip()
        if body.lower().startswith("<!doctype") or body.lower().startswith("<html"):
            out.setdefault("index.html", body)
        elif "{" in body and ("--" in body or "body" in body or ".hero" in body):
            out.setdefault("styles.css", body)
    return out


def _bundle_workspace(workspace: dict[str, str], fallback: str) -> str:
    if workspace:
        parts = [f"=== {path} ===\n{content}" for path, content in sorted(workspace.items())]
        return "\n\n".join(parts)
    return fallback


def coding_tools() -> dict:
    """Stateless tools — file state lives on :class:`DeepSeekCodingAgent` per trajectory."""

    def write_file(args: dict) -> str:
        return f"wrote {args['path']} ({len(str(args['content']))} bytes)"

    def read_file(args: dict) -> str:
        return str(args.get("content", ""))

    def submit(args: dict) -> str:
        return str(args.get("artifact", ""))

    return {"write_file": write_file, "read_file": read_file, "submit": submit}
