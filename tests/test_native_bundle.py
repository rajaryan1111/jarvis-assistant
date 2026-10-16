"""Regression tests for the native macOS application bundle.

These check the things that can be verified without a Mac: that the bundle metadata
carries a microphone usage description (without which macOS kills the app the first
time it touches the mic), that the build script exists and is runnable, that the menu
shell never opens a browser, and that the extra is declared in the packaging.
"""
import plistlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MACOS = REPO_ROOT / "macos"


@pytest.fixture(scope="module")
def info_plist() -> dict:
    with (MACOS / "Info.plist").open("rb") as handle:
        return plistlib.load(handle)


def test_the_bundle_declares_a_microphone_usage_description(info_plist):
    """Without this key macOS terminates the app on the first mic access."""
    description = info_plist.get("NSMicrophoneUsageDescription", "")
    assert description, "NSMicrophoneUsageDescription is missing from Info.plist"
    assert len(description) > 20, "the permission prompt would be too vague to be useful"
    assert "Ultron" in description


def test_the_bundle_is_a_menu_bar_accessory(info_plist):
    """LSUIElement keeps it out of the dock, which is what a status-bar app wants."""
    assert info_plist.get("LSUIElement") is True


def test_the_bundle_names_the_executable_it_builds(info_plist):
    """Must match the file macos/build_app.sh writes into Contents/MacOS."""
    executable = info_plist["CFBundleExecutable"]
    build_script = (MACOS / "build_app.sh").read_text(encoding="utf-8")
    assert f'"$APP/Contents/MacOS/{executable}"' in build_script, (
        f"Info.plist names {executable} but the build script writes a different name"
    )


def test_the_bundle_targets_apple_silicon_and_a_recent_macos(info_plist):
    assert "arm64" in info_plist.get("LSArchitecturePriority", [])
    assert float(info_plist["LSMinimumSystemVersion"]) >= 12.0


def test_the_build_script_exists_and_is_executable():
    script = MACOS / "build_app.sh"
    assert script.exists()
    assert script.stat().st_mode & 0o111, "build_app.sh is not executable"


def test_the_menu_shell_exists_and_names_the_controller():
    source = (MACOS / "menubar.py").read_text(encoding="utf-8")
    assert "DesktopController" in source, "the shell must drive the shared controller"
    assert "ultron.desktop" in source


def test_the_menu_shell_never_opens_a_browser_automatically():
    """Requirement: wake-word activation and startup must not launch a browser."""
    source = (MACOS / "menubar.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        if "open-hud" in line and not line.lstrip().startswith("#"):
            assert line.lstrip().startswith("subprocess.Popen"), (
                f"the HUD is opened outside an explicit menu action: {line.strip()!r}"
            )


def test_the_hud_menu_item_is_labelled_as_optional():
    source = (MACOS / "menubar.py").read_text(encoding="utf-8")
    assert "Open HUD (optional)" in source


def test_the_shell_quits_by_releasing_the_microphone():
    """Quit must go through controller.quit(), which stops and joins the worker."""
    source = (MACOS / "menubar.py").read_text(encoding="utf-8")
    assert "controller.quit()" in source
    assert "applicationWillTerminate_" in source


def test_the_native_extra_is_declared():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^macos\s*=\s*\["pyobjc', text, re.M), (
        "the macos extra is not declared in pyproject.toml"
    )


def test_the_macos_module_is_packaged():
    """`python -m macos` is what the built app's stub execs, so it must be installed."""
    import tomllib

    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    assert "macos" in data["tool"]["setuptools"]["py-modules"]


def test_the_menu_shell_is_importable_without_appkit():
    """On a machine without PyObjC the module must fail with a clear message, not a
    traceback from deep inside an import."""
    source = (MACOS / "menubar.py").read_text(encoding="utf-8")
    assert "_require_appkit" in source
    assert "uv sync --extra macos" in source


def test_the_build_script_checks_for_the_optional_dependency():
    script = (MACOS / "build_app.sh").read_text(encoding="utf-8")
    assert 'import AppKit' in script, "the build must fail early if PyObjC is missing"
    assert "--extra macos" in script


def test_the_build_script_never_signs_with_a_committed_identity():
    """No signing credentials in the repository."""
    script = (MACOS / "build_app.sh").read_text(encoding="utf-8")
    assert 'ULTRON_CODESIGN_IDENTITY:--' in script, "the identity must come from the environment"
    assert "Developer ID" in script  # documented, not hardcoded
