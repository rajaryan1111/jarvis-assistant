"""Routine macOS actions that used to run straight from ``main.py`` helpers.

Each one is a narrow tool with a declared risk tier, validated arguments, an
absolute executable path and a timeout (via :func:`ultron.tools.process.run_command`).
None of them accepts a shell string. The legacy helpers in ``main.py`` now call
these only through the :class:`~ultron.tools.registry.ToolRegistry`, so the
same policy applies to voice, CLI, HUD, API and background callers.

Platform behaviour (osascript, screencapture, say, open) is exercised here with
fake runners only; see docs/M1_1_TOOL_SECURITY_BOUNDARY.md for the manual
checks that still need a real Mac.
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ultron.security.paths import PathPolicyError, resolve_directory, unique_child
from ultron.security.policy import RiskTier
from ultron.tools.base import ToolResult, ToolSpec
from ultron.tools.process import Runner, run_command

MACOS = frozenset({"darwin"})
OSASCRIPT = "/usr/bin/osascript"
OPEN = "/usr/bin/open"
SAY = "/usr/bin/say"
SCREENCAPTURE = "/usr/sbin/screencapture"
CPU_TEMP_CANDIDATES = ("/opt/homebrew/bin/osx-cpu-temp", "/usr/local/bin/osx-cpu-temp")

# App names as `open -a` expects them ("Google Chrome", "Visual Studio Code"). No path
# separators, nothing that `open` could read as an option, bounded length.
_APP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .&+'()_-]{0,63}$")
_VOICE_RE = re.compile(r"^[A-Za-z][A-Za-z ()-]{0,40}$")
MAX_URL_CHARS = 2048


def _fail(message: str, reason: str) -> ToolResult:
    return ToolResult(False, message, {"reason": reason})


class SpeakTool:
    spec = ToolSpec(
        name="audio.speak",
        description="Speak text aloud with the macOS 'say' command.",
        risk=RiskTier.ROUTINE,
        side_effects=frozenset({"audio"}),
        platforms=MACOS,
        internal_ok=True,  # reminder and intruder watchers announce things
    )

    def __init__(self, runner: Runner = subprocess.run, voice: str = "Daniel", max_chars: int = 4000,
                 timeout: float = 180.0) -> None:
        if not _VOICE_RE.match(voice):
            raise ValueError("invalid voice name")
        self._runner, self._voice, self._max_chars, self._timeout = runner, voice, max_chars, timeout

    def preview(self, arguments: Mapping[str, Any]) -> str:
        return "Speak a message aloud."

    def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip():
            return _fail("There is nothing to say.", "invalid_argument")
        text = text[: self._max_chars]
        if text.startswith("-"):  # `say` would parse a leading "-" as an option (e.g. "-o file")
            text = " " + text
        result = run_command([SAY, "-v", self._voice, text], runner=self._runner, timeout=self._timeout)
        if result.ok:
            return ToolResult(True, "Spoken.")
        return _fail("I could not speak that message.", result.error or "failed")


class OpenAppTool:
    spec = ToolSpec(
        name="apps.open",
        description="Open a macOS application by name.",
        risk=RiskTier.ROUTINE,
        side_effects=frozenset({"launch_app"}),
        platforms=MACOS,
    )

    def __init__(self, runner: Runner = subprocess.run, timeout: float = 15.0) -> None:
        self._runner, self._timeout = runner, timeout

    def preview(self, arguments: Mapping[str, Any]) -> str:
        return f"Open the app {arguments.get('app')!s}."

    def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        app = arguments.get("app")
        if not isinstance(app, str) or not _APP_NAME_RE.match(app) or ".." in app:
            return _fail("That is not a valid application name.", "invalid_argument")
        result = run_command([OPEN, "-a", app], runner=self._runner, timeout=self._timeout)
        if result.ok:
            return ToolResult(True, f"Launching {app}.")
        return _fail(f"I could not find an app called {app}.", result.error or "not_found")


def validate_web_url(url: Any) -> str:
    """Return ``url`` if it is a plain http(s) URL with a host; raise ``ValueError`` otherwise."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_CHARS:
        raise ValueError("invalid URL")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise ValueError("URL contains whitespace or control characters")
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http"):
        raise ValueError("only http and https URLs can be opened")
    if not parts.hostname or "@" in parts.netloc:
        raise ValueError("URL must have a host and no embedded credentials")
    return url


class OpenUrlTool:
    spec = ToolSpec(
        name="web.open_url",
        description="Open an http(s) URL in the default browser.",
        risk=RiskTier.ROUTINE,
        side_effects=frozenset({"browser"}),
    )

    def __init__(self, opener: Callable[[str], Any] | None = None) -> None:
        self._opener = opener

    def preview(self, arguments: Mapping[str, Any]) -> str:
        return f"Open {arguments.get('url')!s} in your browser."

    def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        try:
            url = validate_web_url(arguments.get("url"))
        except ValueError:
            return _fail("I can only open normal web addresses.", "invalid_argument")
        opener = self._opener or webbrowser.open
        if opener(url) is False:
            return _fail("I could not open a browser.", "no_browser")
        return ToolResult(True, f"Opened {urlsplit(url).hostname} in your browser.")


class VolumeTool:
    spec = ToolSpec(
        name="system.set_volume",
        description="Set the output volume (0-100).",
        risk=RiskTier.ROUTINE,
        side_effects=frozenset({"system_setting"}),
        platforms=MACOS,
    )

    def __init__(self, runner: Runner = subprocess.run, timeout: float = 10.0) -> None:
        self._runner, self._timeout = runner, timeout

    def preview(self, arguments: Mapping[str, Any]) -> str:
        return f"Set the volume to {arguments.get('level')!s} percent."

    def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        level = arguments.get("level")
        if isinstance(level, bool) or not isinstance(level, int):
            return _fail("Tell me the volume level as a number, like volume 50.", "invalid_argument")
        level = max(0, min(level, 100))
        result = run_command([OSASCRIPT, "-e", f"set volume output volume {level}"],
                             runner=self._runner, timeout=self._timeout)
        if result.ok:
            return ToolResult(True, f"Volume set to {level} percent.")
        return _fail("I could not change the volume.", result.error or "failed")


class BrightnessTool:
    spec = ToolSpec(
        name="system.brightness",
        description="Nudge the display brightness up or down.",
        risk=RiskTier.ROUTINE,
        side_effects=frozenset({"system_setting"}),
        platforms=MACOS,
    )

    def __init__(self, runner: Runner = subprocess.run, timeout: float = 10.0) -> None:
        self._runner, self._timeout = runner, timeout

    def preview(self, arguments: Mapping[str, Any]) -> str:
        return f"Turn the brightness {arguments.get('direction')!s}."

    def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        direction = arguments.get("direction")
        steps = arguments.get("steps", 4)
        if direction not in ("up", "down") or isinstance(steps, bool) or not isinstance(steps, int):
            return _fail("I can only turn the brightness up or down.", "invalid_argument")
        steps = max(1, min(steps, 16))
        key_code = 107 if direction == "up" else 113  # unchanged legacy key codes (verify on a Mac)
        script = f'tell application "System Events"\nrepeat {steps} times\nkey code {key_code}\nend repeat\nend tell'
        result = run_command([OSASCRIPT, "-e", script], runner=self._runner, timeout=self._timeout)
        if result.ok:
            return ToolResult(True, "Brightness adjusted.")
        return _fail("I tried, but could not change the brightness.", result.error or "failed")


def default_screenshot_dir() -> str:
    return os.environ.get("ULTRON_SCREENSHOT_DIR") or str(Path.home() / "Desktop")


class ScreenshotTool:
    spec = ToolSpec(
        name="system.screenshot",
        description="Save a screenshot of the screen into the screenshot folder (default ~/Desktop).",
        risk=RiskTier.ROUTINE,
        side_effects=frozenset({"screen_capture", "local_fs_create"}),
        platforms=MACOS,
    )

    def __init__(self, runner: Runner = subprocess.run, directory: str | os.PathLike[str] | None = None,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now, timeout: float = 20.0) -> None:
        self._runner, self._directory, self._clock, self._timeout = runner, directory, clock, timeout

    def preview(self, arguments: Mapping[str, Any]) -> str:
        return "Save a screenshot of your screen."

    def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        try:
            # The user's own (configurable) folder; a symlinked Desktop is the user's choice.
            directory = resolve_directory(self._directory or default_screenshot_dir(), allow_symlink=True)
            stem = "jarvis_screenshot_" + self._clock().strftime("%Y-%m-%d_%H-%M-%S")
            target = unique_child(directory, stem, ".png")  # never overwrites, never writes via a link
        except PathPolicyError as exc:
            return _fail(f"I can't save a screenshot there: {exc}.", "invalid_path")
        result = run_command([SCREENCAPTURE, "-x", str(target)], runner=self._runner, timeout=self._timeout)
        if result.ok:
            return ToolResult(True, f"Screenshot saved to {directory.name} as {target.name}.", {"path": str(target)})
        return _fail("I tried to take a screenshot but something went wrong.", result.error or "failed")


class CpuTemperatureTool:
    spec = ToolSpec(
        name="system.cpu_temperature",
        description="Read the CPU temperature with osx-cpu-temp (Homebrew).",
        risk=RiskTier.OBSERVE,
        platforms=MACOS,
    )

    def __init__(self, runner: Runner = subprocess.run, candidates: Sequence[str] = CPU_TEMP_CANDIDATES,
                 timeout: float = 5.0) -> None:
        self._runner, self._candidates, self._timeout = runner, tuple(candidates), timeout

    def preview(self, arguments: Mapping[str, Any]) -> str:
        return "Read the CPU temperature."

    def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        # Fixed absolute locations only: no PATH lookup.
        exe = next((c for c in self._candidates if os.path.isfile(c) and os.access(c, os.X_OK)), None)
        if exe is None:
            return _fail("osx-cpu-temp is not installed.", "not_found")
        result = run_command([exe], runner=self._runner, timeout=self._timeout, max_output=64)
        reading = result.stdout.strip().splitlines()[0][:32] if result.ok and result.stdout.strip() else ""
        if not reading:
            return _fail("The temperature sensor did not answer.", result.error or "failed")
        return ToolResult(True, reading, {"temperature": reading})
