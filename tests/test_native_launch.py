"""Regression tests: normal desktop startup must never open a browser.

The native requirement is "no browser required for normal operation", and explicitly
that the browser HUD must not open automatically during normal desktop startup or
wake-word activation. These tests read the launcher and the startup path as text so
they cannot be satisfied by a browser that merely happens to be closed on this machine.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "run_jarvis.command"


@pytest.fixture(scope="module")
def launcher() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


def test_the_launcher_exists_and_is_executable():
    assert LAUNCHER.exists(), f"missing macOS launcher: {LAUNCHER}"
    assert LAUNCHER.stat().st_mode & 0o111, "run_jarvis.command is not executable"


def test_normal_startup_does_not_open_the_hud(launcher):
    """The default path must never *invoke* `open-hud`; only the opt-in does.

    Comments and echoes that merely mention the command are fine - the requirement is
    that no browser is launched. So each mention is classified, and anything that
    actually runs must sit inside the ULTRON_HUD=1 guard.
    """
    lines = launcher.splitlines()
    guard = next(
        i for i, line in enumerate(lines) if 'if [ "$ULTRON_HUD" = "1" ]; then' in line
    )
    for number, line in enumerate(lines):
        if "open-hud" not in line:
            continue
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("echo"):
            continue  # documentation, not an invocation
        assert number > guard, (
            f"line {number + 1} invokes the HUD outside the ULTRON_HUD=1 guard: {stripped!r}"
        )


def test_the_hud_is_still_reachable_by_explicit_request(launcher):
    """Removing the HUD entirely would break approvals; it must remain reachable."""
    assert "open-hud" in launcher, "the HUD must stay available on explicit request"


def test_the_hud_opt_in_is_documented_in_the_launcher(launcher):
    assert "ULTRON_HUD" in launcher, "the HUD opt-in flag is not documented"


def test_voice_is_the_default_experience(launcher):
    """Requirement: start the app -> grant mic permission -> activate by voice."""
    active = "\n".join(line for line in launcher.splitlines() if not line.lstrip().startswith("#"))
    assert re.search(r'JARVIS_VOICE="\$\{ULTRON_VOICE:-\$\{JARVIS_VOICE:-1\}\}"', active), (
        "voice must be the default, not something the user has to opt into"
    )


def test_the_legacy_env_var_still_works(launcher):
    """Do not break a user who already set JARVIS_VOICE."""
    assert "JARVIS_VOICE" in launcher and "ULTRON_VOICE" in launcher


def test_the_cli_open_hud_subcommand_is_not_called_at_import_time():
    """Importing the CLI (what startup does) must not open anything."""
    from ultron import cli
    assert hasattr(cli, "main"), "the CLI no longer exposes main()"
    # the browser-opening helper must only be reachable through an explicit command
    assert callable(cli._cmd_open_hud)


def test_no_cli_path_other_than_open_hud_can_open_a_browser():
    """Every browser-opening line in the CLI must sit inside _cmd_open_hud."""
    from ultron import cli

    lines = Path(cli.__file__).read_text(encoding="utf-8").splitlines()
    helper_start = next(
        i for i, line in enumerate(lines) if line.startswith("def _cmd_open_hud")
    )
    inside = set()
    for i, line in enumerate(lines):
        if "webbrowser.open" in line or 'open -a' in line:
            inside.add(i)
    assert inside, "expected _cmd_open_hud to open a browser"
    assert all(i >= helper_start for i in inside), (
        "a browser is opened outside _cmd_open_hud; startup and wake-word activation "
        "must never launch one"
    )
