"""Load a local ``.env`` into ``os.environ`` without extra dependencies."""

from __future__ import annotations

import os
from pathlib import Path


def load_project_env(*, start: Path | None = None) -> Path | None:
    """Apply the first ``.env`` found walking up from *start* (default: cwd).

    Existing environment variables are never overwritten.
  Returns the path loaded, or ``None`` if no file was found.
    """

    root = (start or Path.cwd()).resolve()
    for directory in (root, *root.parents):
        path = directory / ".env"
        if path.is_file():
            _apply_env_file(path)
            return path
    return None


def _apply_env_file(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
