"""macOS system tools that were previously reachable without safeguards.

- Power (restart / shut down): fixed AppleScript, argv-only subprocess, DESTRUCTIVE tier.
- User cache cleanup: narrow Python deletion of entries *directly inside*
  ``~/Library/Caches`` — no shell, no globbing, symlinks are removed as links
  and never followed. DESTRUCTIVE tier (irreversible).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ultron.security.policy import RiskTier
from ultron.tools.base import ToolResult, ToolSpec

Runner = Callable[..., Any]
MACOS = frozenset({"darwin"})
OSASCRIPT = "/usr/bin/osascript"


class PowerTool:
    def __init__(self, action: str, runner: Runner = subprocess.run) -> None:
        if action not in ("restart", "shut down"):
            raise ValueError(action)
        self._action = action
        self._runner = runner
        name = "system.restart" if action == "restart" else "system.shutdown"
        self.spec = ToolSpec(
            name=name,
            description=f"{action.capitalize()} this Mac.",
            risk=RiskTier.DESTRUCTIVE,
            side_effects=frozenset({"system"}),
            platforms=MACOS,
        )

    def preview(self, arguments: Mapping[str, Any]) -> str:
        return f"This will {self._action} this Mac. Unsaved work in open apps may be lost."

    def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        script = f'tell application "System Events" to {self._action}'
        completed = self._runner([OSASCRIPT, "-e", script], check=False, capture_output=True, timeout=30)
        if getattr(completed, "returncode", 1) == 0:
            verb = "Restarting" if self._action == "restart" else "Shutting down"
            return ToolResult(True, f"{verb} now, sir.")
        return ToolResult(False, f"macOS did not allow me to {self._action}. Check Automation permissions.")


class ClearUserCachesTool:
    spec = ToolSpec(
        name="system.clear_user_caches",
        description="Delete the contents of ~/Library/Caches.",
        risk=RiskTier.DESTRUCTIVE,
        side_effects=frozenset({"local_fs"}),
        platforms=MACOS,
    )

    def __init__(self, cache_dir: Path | None = None, preview_limit: int = 100_000) -> None:
        self._cache_dir = cache_dir
        self._preview_limit = preview_limit

    def _target(self) -> Path:
        path = self._cache_dir or Path.home() / "Library" / "Caches"
        if path.is_symlink() or not path.is_dir():
            raise FileNotFoundError("cache directory is missing or is a symlink")
        resolved = path.resolve(strict=True)
        if resolved != path.absolute():
            raise PermissionError("cache directory path is not canonical")
        return resolved

    def preview(self, arguments: Mapping[str, Any]) -> str:
        target = self._target()
        count = 0
        with os.scandir(target) as entries:
            for _ in entries:
                count += 1
                if count >= self._preview_limit:
                    break
        return (
            f"This will permanently delete {count} item{'s' if count != 1 else ''} inside your user cache folder "
            f"({target}). Apps rebuild caches, but they may be slower or need you to sign in again."
        )

    def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        target = self._target()
        removed = failed = 0
        with os.scandir(target) as entries:
            for entry in entries:
                path = Path(entry.path)
                try:
                    if path.parent != target:  # defensive: never act outside the target
                        failed += 1
                        continue
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        path.unlink()  # removes the link itself, never its target
                    else:
                        shutil.rmtree(path)
                    removed += 1
                except OSError:
                    failed += 1  # protected or in-use entries are skipped
        message = f"Cleanup complete. I removed {removed} cache item{'s' if removed != 1 else ''}, sir."
        if failed:
            message += f" {failed} item{'s were' if failed != 1 else ' was'} protected or in use and skipped."
        return ToolResult(True, message, {"removed": removed, "failed": failed})
