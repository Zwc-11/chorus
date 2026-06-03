"""Local isolated worktree/copy sandbox."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    timeout: bool = False

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


class LocalWorktreeSandbox:
    def __init__(self, source_root: Path, worktree_root: Path) -> None:
        self.source_root = source_root.resolve()
        self.root = worktree_root.resolve()
        self._snapshot: dict[str, str] = {}

    @classmethod
    def create(cls, source_root: Path, run_dir: Path) -> LocalWorktreeSandbox:
        source = source_root.resolve()
        worktree = (run_dir / "worktree").resolve()
        if worktree.exists():
            shutil.rmtree(worktree)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        sandbox = cls(source, worktree)
        if not sandbox._try_git_worktree():
            _copy_tree(source, worktree)
        sandbox._snapshot = sandbox._read_snapshot()
        return sandbox

    def run(self, command: str, *, timeout_s: int = 600) -> CommandResult:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.root) + os.pathsep + env.get("PYTHONPATH", "")
        try:
            proc = subprocess.run(
                command,
                cwd=self.root,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
                check=False,
            )
            return CommandResult(command, proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command,
                124,
                exc.stdout or "",
                exc.stderr or "",
                timeout=True,
            )

    def list_files(self, pattern: str = "**/*") -> list[str]:
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.glob(pattern)
            if path.is_file() and not _ignored(path.relative_to(self.root))
        )

    def read_file(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")

    def search(self, query: str, pattern: str = "**/*") -> list[str]:
        results: list[str] = []
        for rel in self.list_files(pattern):
            path = self.root / rel
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    results.append(f"{rel}:{number}:{line.strip()}")
        return results[:100]

    def apply_patch(self, patch: str) -> CommandResult:
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=self.root,
            input=patch,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return CommandResult("git apply", proc.returncode, proc.stdout, proc.stderr)

    def git_diff(self) -> str:
        if (self.root / ".git").exists():
            proc = subprocess.run(
                ["git", "diff", "--"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if proc.stdout:
                return proc.stdout
        return self._snapshot_diff()

    def changed_files(self) -> tuple[str, ...]:
        diff = self.git_diff()
        files: list[str] = []
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                files.append(line.removeprefix("+++ b/"))
        return tuple(dict.fromkeys(files))

    def _try_git_worktree(self) -> bool:
        if not (self.source_root / ".git").exists():
            return False
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.source_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if status.returncode != 0 or status.stdout.strip():
            return False
        proc = subprocess.run(
            ["git", "worktree", "add", "--detach", str(self.root), "HEAD"],
            cwd=self.source_root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return proc.returncode == 0

    def _read_snapshot(self) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for rel in self.list_files("**/*"):
            try:
                snapshot[rel] = (self.root / rel).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
        return snapshot

    def _snapshot_diff(self) -> str:
        current = self._read_snapshot()
        paths = sorted(set(self._snapshot) | set(current))
        chunks: list[str] = []
        for rel in paths:
            before = self._snapshot.get(rel, "").splitlines(keepends=True)
            after = current.get(rel, "").splitlines(keepends=True)
            if before == after:
                continue
            chunks.extend(
                unified_diff(before, after, fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="")
            )
        return "\n".join(chunks)


def _copy_tree(source: Path, dest: Path) -> None:
    def ignore(_dir: str, names: list[str]) -> set[str]:
        blocked = {".git", ".chorus", ".venv", ".venv-linux", "__pycache__", ".pytest_cache"}
        return {name for name in names if name in blocked or name.endswith(".pyc")}

    shutil.copytree(source, dest, ignore=ignore)


def _ignored(path: Path) -> bool:
    parts = set(path.parts)
    return bool(
        parts
        & {
            ".git",
            ".chorus",
            ".venv",
            ".venv-linux",
            "__pycache__",
            ".pytest_cache",
        }
    )
