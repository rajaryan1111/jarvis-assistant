"""Subprocess runner, filesystem safety and macOS tool argument validation.

Processes are fake runners except one harmless real child (the current Python
interpreter sleeping) used to prove that timeouts really kill. All files live in tmp_path.
"""

from __future__ import annotations

import datetime
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FakeOpener, FakeRunner

from ultron.security.paths import (
    PathPolicyError,
    atomic_write_text,
    child_path,
    is_within,
    resolve_directory,
    unique_child,
)
from ultron.tools.macos import (
    OPEN,
    OSASCRIPT,
    SAY,
    BrightnessTool,
    CpuTemperatureTool,
    OpenAppTool,
    OpenUrlTool,
    ScreenshotTool,
    SpeakTool,
    VolumeTool,
)
from ultron.tools.paths import read_confined_text
from ultron.tools.process import run_command

FAKE_SECRET = "sk-" + "Zq7" * 8  # credential-shaped, built at runtime


# ============================================================ run_command
@pytest.mark.parametrize("argv", ["say hello", b"say", [], ["say", "hi"], ["relative/bin"], [OSASCRIPT, 3],
                                  [OSASCRIPT, "a\x00b"]])
def test_run_command_rejects_shell_strings_relative_and_malformed_argv(argv):
    runner = FakeRunner()
    with pytest.raises(ValueError):
        run_command(argv, runner=runner)
    assert runner.calls == []


def test_run_command_never_uses_a_shell_and_always_has_a_timeout():
    runner = FakeRunner()
    result = run_command([SAY, "hi"], runner=runner, timeout=3)
    assert result.ok and runner.calls == [[SAY, "hi"]]
    kwargs = runner.kwargs[0]
    assert kwargs["shell"] is False and kwargs["timeout"] == 3 and kwargs["capture_output"] is True
    assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["check"] is False


def test_run_command_reports_timeouts_without_raising():
    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=b"partial " + FAKE_SECRET.encode())

    result = run_command([SAY, "x"], runner=slow, timeout=1)
    assert not result.ok and result.timed_out and result.error == "timeout"
    assert FAKE_SECRET not in result.stdout


def test_run_command_really_kills_a_child_that_overruns():
    result = run_command([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert result.timed_out and not result.ok


@pytest.mark.parametrize("exc,error", [(FileNotFoundError(), "not_found"), (PermissionError(), "os_error")])
def test_run_command_missing_or_forbidden_executable(exc, error):
    def broken(argv, **kwargs):
        raise exc

    result = run_command([SAY, "x"], runner=broken)
    assert not result.ok and result.error == error


def test_run_command_bounds_and_redacts_output():
    runner = FakeRunner(returncode=2, stdout=(FAKE_SECRET + " ") + "A" * 10_000)
    result = run_command([SAY, "x"], runner=runner, max_output=100)
    assert not result.ok and result.returncode == 2
    assert FAKE_SECRET not in result.stdout and len(result.stdout) < 200 and result.stdout.endswith("[truncated]")


def test_run_command_passes_input_explicitly():
    runner = FakeRunner()
    run_command(["/usr/bin/pbcopy"], runner=runner, input_text="hello")
    assert runner.kwargs[0]["input"] == b"hello" and "stdin" not in runner.kwargs[0]


# ============================================================ paths
@pytest.mark.parametrize("name", ["", ".", "..", "../x", "a/b", "/etc/passwd", ".hidden", "-o", "a\x00b"])
def test_child_path_rejects_traversal_and_odd_names(tmp_path, name):
    with pytest.raises(PathPolicyError):
        child_path(tmp_path, name)


def test_child_path_refuses_to_write_through_a_symlink(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "shot.png").symlink_to(victim)
    with pytest.raises(PathPolicyError):
        child_path(tmp_path / "out", "shot.png")


def test_unique_child_never_reuses_existing_names_or_dangling_links(tmp_path):
    (tmp_path / "a.png").write_text("x")
    (tmp_path / "a-2.png").symlink_to(tmp_path / "missing")
    assert unique_child(tmp_path, "a", ".png") == tmp_path / "a-3.png"


def test_resolve_directory_rules(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(PathPolicyError):
        resolve_directory(link)
    assert resolve_directory(link, allow_symlink=True) == real.resolve()
    with pytest.raises(PathPolicyError):
        resolve_directory("relative/dir")
    with pytest.raises(PathPolicyError):
        resolve_directory(tmp_path / "missing")
    created = resolve_directory(tmp_path / "new" / "dir", create=True)
    assert created.is_dir() and stat.S_IMODE(created.stat().st_mode) == 0o700
    (tmp_path / "file").write_text("x")
    with pytest.raises(PathPolicyError):
        resolve_directory(tmp_path / "file")


def test_is_within_resolves_symlinks(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    assert is_within(root, root / "a" / "b")
    assert not is_within(root, root / "escape" / "x")
    assert not is_within(root, root / ".." / "x")


def test_atomic_write_is_private_and_failure_keeps_the_old_file(tmp_path):
    target = tmp_path / "data.json"
    atomic_write_text(target, "v1")
    assert target.read_text() == "v1" and stat.S_IMODE(target.stat().st_mode) == 0o600

    def explode(_tmp):
        raise RuntimeError("disk full")

    from ultron.security.paths import atomic_replace

    with pytest.raises(RuntimeError):
        atomic_replace(target, explode)
    assert target.read_text() == "v1" and sorted(p.name for p in tmp_path.iterdir()) == ["data.json"]


def test_memory_save_does_not_follow_a_planted_temp_symlink(monkeypatch, tmp_path):
    """Regression: save_memory wrote through a fixed 'memory.json.tmp' path with O_TRUNC."""
    import main

    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    memory_file = tmp_path / "memory.json"
    (tmp_path / "memory.json.tmp").symlink_to(victim)
    monkeypatch.setattr(main, "MEMORY_FILE", str(memory_file))
    main.save_memory({"notes": ["x"]})
    assert victim.read_text() == "precious"
    assert json.loads(memory_file.read_text()) == {"notes": ["x"]}


def test_token_file_write_does_not_follow_a_planted_temp_symlink(tmp_path):
    from ultron.security.tokens import generate_token, write_token_file

    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    target = tmp_path / "api_token"
    (tmp_path / "api_token.tmp").symlink_to(victim)
    write_token_file(target, generate_token())
    assert victim.read_text() == "precious" and stat.S_IMODE(target.stat().st_mode) == 0o600


# ============================================================ macOS tools (fake runners)
def test_speak_tool_neutralises_option_injection_and_bounds_text():
    runner = FakeRunner()
    tool = SpeakTool(runner=runner, max_chars=10)
    assert tool.run({"text": "-o /tmp/evil.aiff hi"}).ok
    assert runner.calls[-1] == [SAY, "-v", "Daniel", " -o /tmp/ev"]
    assert not tool.run({"text": "   "}).ok and not tool.run({"text": 5}).ok
    with pytest.raises(ValueError):
        SpeakTool(voice="-o x")


@pytest.mark.parametrize("app", ["-n", "-a Terminal", "../../bin/sh", "/Applications/Evil.app", "a" * 65,
                                 "Safari; rm -rf ~", "Safari\nTerminal", "", None, 7, "..app"])
def test_open_app_rejects_paths_options_and_metacharacters(app):
    runner = FakeRunner()
    result = OpenAppTool(runner=runner).run({"app": app})
    assert not result.ok and result.data["reason"] == "invalid_argument" and runner.calls == []


def test_open_app_reports_a_missing_app_as_a_failure():
    runner = FakeRunner(returncode=1)
    result = OpenAppTool(runner=runner).run({"app": "Google Chrome"})
    assert runner.calls == [[OPEN, "-a", "Google Chrome"]] and not result.ok


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", "ftp://x.com",
                                 "https://user:pw@example.com", "https://exa mple.com", "https://", "x.com",
                                 "https://example.com/\x07", "https://example.com/" + "a" * 3000, None])
def test_open_url_only_accepts_plain_web_addresses(url):
    opener = FakeOpener()
    assert not OpenUrlTool(opener=opener).run({"url": url}).ok and opener.urls == []


def test_open_url_opens_https():
    opener = FakeOpener()
    assert OpenUrlTool(opener=opener).run({"url": "https://www.youtube.com/watch?v=abc"}).ok
    assert opener.urls == ["https://www.youtube.com/watch?v=abc"]


def test_volume_and_brightness_validate_arguments():
    runner = FakeRunner()
    assert VolumeTool(runner=runner).run({"level": 250}).message == "Volume set to 100 percent."
    assert runner.calls[-1] == [OSASCRIPT, "-e", "set volume output volume 100"]
    for bad in (True, "50; do shell script \"x\"", None, 5.5):
        assert not VolumeTool(runner=runner).run({"level": bad}).ok
    assert not BrightnessTool(runner=runner).run({"direction": "sideways"}).ok
    assert not BrightnessTool(runner=runner).run({"direction": "up", "steps": "4 times\ndo shell script"}).ok
    assert len(runner.calls) == 1


def test_failing_osascript_is_reported():
    result = VolumeTool(runner=FakeRunner(returncode=1)).run({"level": 10})
    assert not result.ok and result.message == "I could not change the volume."


def test_screenshot_never_overwrites_and_refuses_bad_folders(tmp_path):
    fixed = datetime.datetime(2026, 10, 13, 9, 30, 0)
    runner = FakeRunner()
    shots = tmp_path / "shots"
    shots.mkdir()
    (shots / "jarvis_screenshot_2026-10-13_09-30-00.png").symlink_to(tmp_path / "victim")
    tool = ScreenshotTool(runner=runner, directory=shots, clock=lambda: fixed)
    assert tool.run({}).ok
    assert runner.calls[-1][-1] == str(shots.resolve() / "jarvis_screenshot_2026-10-13_09-30-00-2.png")
    assert not (tmp_path / "victim").exists()

    missing = ScreenshotTool(runner=runner, directory=tmp_path / "nope").run({})
    assert not missing.ok and missing.data["reason"] == "invalid_path"
    failing = ScreenshotTool(runner=FakeRunner(returncode=1), directory=shots, clock=lambda: fixed).run({})
    assert not failing.ok


def test_cpu_temperature_uses_fixed_paths_not_path_lookup(tmp_path, monkeypatch):
    planted = tmp_path / "osx-cpu-temp"
    planted.write_text("#!/bin/sh\necho 99")
    planted.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    runner = FakeRunner(stdout=b"46.5\xc2\xb0C\n")
    assert CpuTemperatureTool(runner=runner, candidates=("/nonexistent/osx-cpu-temp",)).run({}).ok is False
    assert runner.calls == []
    result = CpuTemperatureTool(runner=runner, candidates=(str(planted),)).run({})
    assert result.ok and result.data["temperature"] == "46.5°C" and runner.calls == [[str(planted)]]


# ============================================================ face storage (fake OpenCV)
class FakeCV2:
    def __init__(self, fail_write: bool = False) -> None:
        self.fail_write = fail_write

    def resize(self, img, size):
        return img

    def imwrite(self, path, img):
        if self.fail_write:
            return False
        Path(path).write_bytes(b"JPEG" + img)
        return True


@pytest.fixture
def face_env(monkeypatch, tmp_path):
    import vision

    faces = tmp_path / "faces"
    monkeypatch.setattr(vision, "FACE_DIR", str(faces))
    monkeypatch.setattr(vision, "cv2", FakeCV2())
    monkeypatch.setattr(vision, "_capture_frame_from_camera", lambda: b"new")
    monkeypatch.setattr(vision, "_crop_face", lambda frame: frame)
    return vision, faces


def test_register_face_writes_atomically_inside_the_face_dir(face_env):
    vision, faces = face_env
    assert vision.register_face("raj") is True
    assert (faces / "raj.jpg").read_bytes() == b"JPEGnew"
    assert sorted(p.name for p in faces.iterdir()) == ["raj.jpg"]


def test_register_face_failure_keeps_the_previous_face(face_env, monkeypatch):
    vision, faces = face_env
    vision.register_face("raj")
    monkeypatch.setattr(vision, "cv2", FakeCV2(fail_write=True))
    assert vision.register_face("raj") is False
    assert (faces / "raj.jpg").read_bytes() == b"JPEGnew"
    assert sorted(p.name for p in faces.iterdir()) == ["raj.jpg"]


@pytest.mark.parametrize("name", ["../raj", "raj/../../x", "Raj Smith", "", "x" * 40])
def test_register_face_rejects_unsafe_names(face_env, name):
    vision, faces = face_env
    assert vision.register_face(name) is False
    assert not faces.exists() or list(faces.iterdir()) == []


def test_register_face_refuses_symlinks(face_env, tmp_path, monkeypatch):
    vision, faces = face_env
    victim = tmp_path / "victim.jpg"
    victim.write_bytes(b"precious")
    faces.mkdir()
    (faces / "raj.jpg").symlink_to(victim)
    assert vision.register_face("raj") is False and victim.read_bytes() == b"precious"
    assert vision.recognize_face("raj") == (False, None)  # a linked reference face is never used

    other = tmp_path / "elsewhere"
    other.mkdir()
    linked_dir = tmp_path / "linked_faces"
    linked_dir.symlink_to(other, target_is_directory=True)
    monkeypatch.setattr(vision, "FACE_DIR", str(linked_dir))
    assert vision.register_face("raj") is False and list(other.iterdir()) == []


# ============================================================ owner recovery CLI
def _memory_file(tmp_path, enabled: bool) -> Path:
    path = tmp_path / "memory.json"
    path.write_text(json.dumps({"security": {"enabled": enabled, "last_auth_time": 0, "auth_timeout_sec": 60}}))
    return path


def test_cli_security_disable_requires_explicit_confirmation(monkeypatch, tmp_path, capsys):
    import main
    from ultron import cli

    path = _memory_file(tmp_path, enabled=True)
    monkeypatch.setattr(main, "MEMORY_FILE", str(path))
    monkeypatch.setattr(main, "memory", main.load_memory())
    monkeypatch.setattr(sys, "stdin", open(os.devnull))  # not a TTY
    assert cli.main(["security", "disable"]) == 1
    assert json.loads(path.read_text())["security"]["enabled"] is True

    assert cli.main(["security", "status"]) == 0
    assert "enabled" in capsys.readouterr().out

    assert cli.main(["security", "disable", "--yes"]) == 0
    assert json.loads(path.read_text())["security"]["enabled"] is False
    assert "restart" in capsys.readouterr().out.lower()


# ============================================================ read_confined_text
def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root


def test_read_confined_text_returns_redacted_text_inside_root(tmp_path):
    root = _repo(tmp_path)
    target = root / "notes.md"
    target.write_text(f"token {FAKE_SECRET}\nplain line\n", encoding="utf-8")
    text, err = read_confined_text(root, target)
    assert err is None
    assert "plain line" in text and FAKE_SECRET not in text and "[REDACTED]" in text


def test_read_confined_text_refuses_symlinks(tmp_path):
    root = _repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE SECRET", encoding="utf-8")
    (root / "link.md").symlink_to(outside)
    assert read_confined_text(root, root / "link.md") == (None, "symlink")


def test_read_confined_text_refuses_paths_outside_root(tmp_path):
    root = _repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE SECRET", encoding="utf-8")
    text, err = read_confined_text(root, outside)
    assert (text, err) == (None, "outside_root")


def test_read_confined_text_refuses_sensitive_names(tmp_path):
    root = _repo(tmp_path)
    for name in (".env", "secrets.json", "id_rsa"):
        target = root / name
        target.write_text("SENSITIVE", encoding="utf-8")
        assert read_confined_text(root, target) == (None, "sensitive"), name


def test_read_confined_text_refuses_directories_and_oversized_files(tmp_path):
    root = _repo(tmp_path)
    (root / "sub").mkdir()
    assert read_confined_text(root, root / "sub") == (None, "not_a_file")

    big = root / "big.txt"
    big.write_text("x", encoding="utf-8")
    assert read_confined_text(root, big, max_bytes=0) == (None, "oversized")


def test_read_confined_text_refuses_an_unreadable_file(tmp_path):
    root = _repo(tmp_path)
    target = root / "locked.md"
    target.write_text("secret", encoding="utf-8")
    os.chmod(target, 0)
    try:
        if os.getuid() == 0:  # root ignores the mode bits; the refusal is untestable here
            pytest.skip("running as root: permission bits are not enforced")
        assert read_confined_text(root, target) == (None, "unreadable")
    finally:
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)


def test_read_confined_text_refuses_an_invalid_root(tmp_path):
    root = _repo(tmp_path)
    target = root / "notes.md"
    target.write_text("ok", encoding="utf-8")
    missing = tmp_path / "no-such-root"
    assert read_confined_text(missing, target) == (None, "invalid_root")
