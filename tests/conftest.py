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
for _name in (
    "ULTRON_API_TOKEN", "ULTRON_API_TOKEN_FILE", "ULTRON_HOST", "ULTRON_PORT", "ULTRON_DEBUG",
    "ULTRON_ALLOW_NON_LOOPBACK", "ULTRON_ALLOWED_HOSTS", "ULTRON_ALLOWED_ORIGINS",
    "OPENAI_API_KEY", "WEATHER_API_KEY", "NEWS_API_KEY", "CRICKET_API_KEY", "PICOVOICE_ACCESS_KEY",
):
    os.environ.pop(_name, None)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeRunner:
    """Stands in for subprocess.run: records argv, never executes anything."""

    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return type("Completed", (), {"returncode": self.returncode})()


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def legacy(monkeypatch, tmp_path, fake_runner):
    """``main`` with a darwin-flavoured tool registry backed by fakes, plus muted speech.

    Replacing ``main.TOOLS`` means shutdown/restart only reach ``fake_runner`` and the
    cache tool only sees ``tmp_path / "Caches"``. ``main.subprocess.run`` is also
    replaced so no legacy branch can launch a real process during a test.
    """
    import main

    cache_dir = tmp_path / "Caches"
    cache_dir.mkdir()
    monkeypatch.setattr(main, "TOOLS", main.build_tool_registry(platform="darwin", runner=fake_runner,
                                                                cache_dir=cache_dir))
    monkeypatch.setattr(main.subprocess, "run", fake_runner)
    monkeypatch.setattr(main, "memory", {"reminders": []})
    monkeypatch.setattr(main, "save_memory", lambda mem: None)
    monkeypatch.setattr(main, "is_security_enabled", lambda: False)
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
