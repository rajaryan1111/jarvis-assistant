"""Test isolation.

Runs before any test module imports ``main``: points every data path at a throwaway
directory, disables ``.env`` loading and removes real credentials from the
environment, so tests can never read the owner's keys, memory or token files.
Destructive tools are always exercised with fake runners and temporary directories.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_SANDBOX = Path(tempfile.mkdtemp(prefix="ultron-tests-"))
os.environ["ULTRON_SKIP_DOTENV"] = "1"
os.environ["ULTRON_DATA_DIR"] = str(_SANDBOX / "data")
os.environ["ULTRON_MEMORY_FILE"] = str(_SANDBOX / "memory.json")
os.environ["ULTRON_KNOWLEDGE_DIR"] = str(_SANDBOX / "knowledge_docs")
os.environ["ULTRON_KNOWLEDGE_INDEX"] = str(_SANDBOX / "knowledge_index.json")
os.environ["ULTRON_FACE_DIR"] = str(_SANDBOX / "faces")
os.environ["ULTRON_SCREENSHOT_DIR"] = str(_SANDBOX / "screenshots")
for _name in (
    "ULTRON_API_TOKEN", "ULTRON_API_TOKEN_FILE", "ULTRON_CLIENT_TOKEN", "ULTRON_CLIENT_TOKEN_FILE",
    "ULTRON_VOICE_TRUST", "ULTRON_SESSION_HOURS", "ULTRON_HOST", "ULTRON_PORT", "ULTRON_DEBUG",
    "ULTRON_ALLOW_NON_LOOPBACK", "ULTRON_ALLOWED_HOSTS", "ULTRON_ALLOWED_ORIGINS",
    "OPENAI_API_KEY", "WEATHER_API_KEY", "NEWS_API_KEY", "CRICKET_API_KEY", "PICOVOICE_ACCESS_KEY",
):
    os.environ.pop(_name, None)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeRunner:
    """Stands in for subprocess.run: records argv (and kwargs), never executes anything."""

    def __init__(self, returncode: int = 0, stdout: bytes = b"") -> None:
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.returncode = returncode
        self.stdout = stdout

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.kwargs.append(kwargs)
        return type("Completed", (), {"returncode": self.returncode, "stdout": self.stdout, "stderr": b""})()


class FakeOpener:
    """Stands in for webbrowser.open: records URLs, opens nothing."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url):
        self.urls.append(url)
        return True


def owner_hud(name: str = "owner"):
    """A signed-in HUD session typing (or clicking): the only context that can approve."""
    from ultron.security.policy import Channel, CommandContext

    return CommandContext(f"session:{name}", Channel.HUD, authenticated=True)


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def fake_opener() -> FakeOpener:
    return FakeOpener()


def fresh_memory() -> dict:
    return {"notes": [], "profile": {}, "favorites": {}, "smarthome": {}, "reminders": [],
            "security": {"enabled": False, "last_auth_time": 0, "auth_timeout_sec": 60}, "plans": []}


@pytest.fixture
def legacy(monkeypatch, tmp_path, fake_runner, fake_opener):
    """``main`` with a darwin-flavoured tool registry backed by fakes.

    Replacing ``main.TOOLS`` means every tool only reaches ``fake_runner`` (processes) or
    ``fake_opener`` (browser); the cache tool only sees ``tmp_path / "Caches"`` and
    screenshots go to ``tmp_path / "Shots"``. ``main.subprocess.run`` is also replaced so
    no legacy branch can launch a real process during a test.
    """
    import main

    cache_dir = tmp_path / "Caches"
    cache_dir.mkdir()
    shots = tmp_path / "Shots"
    shots.mkdir()
    monkeypatch.setattr(main, "TOOLS", main.build_tool_registry(
        platform="darwin", runner=fake_runner, cache_dir=cache_dir, opener=fake_opener,
        screenshot_dir=shots, temperature_candidates=()))
    monkeypatch.setattr(main.subprocess, "run", fake_runner)
    monkeypatch.setattr(main.webbrowser, "open", fake_opener)
    monkeypatch.setattr(main, "jarvis_sleep", False)
    monkeypatch.setattr(main, "memory", fresh_memory())
    monkeypatch.setattr(main, "save_memory", lambda mem: None)
    monkeypatch.setattr(main, "cache_dir_for_tests", cache_dir, raising=False)
    return main


@pytest.fixture(autouse=True)
def _no_real_power_commands(monkeypatch):
    """Belt and braces: fail loudly if anything tries to run osascript for real."""
    import subprocess

    real_run = subprocess.run

    def guarded(argv, *args, **kwargs):
        text = " ".join(map(str, argv)) if isinstance(argv, (list, tuple)) else str(argv)
        if "osascript" in text or "shut down" in text or "rm -rf" in text:
            raise AssertionError(f"test attempted a dangerous command: {text[:80]}")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)
