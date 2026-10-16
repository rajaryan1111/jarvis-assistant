"""Regression tests for `ultron voice-status`.

The requirement is a diagnostic that never claims microphone or wake-word support
works merely because a package is installed, and that clearly separates a static
configuration check from a real microphone test. These tests run it with every
dependency absent and present to check both halves of that promise.
"""
import io
import sys
from contextlib import redirect_stdout


def run_voice_status(monkeypatch, report=None):
    """Run the command with a clean environment, optionally forcing a setup report."""
    from ultron import cli
    from ultron.voice import wakeword

    monkeypatch.setattr(sys, "argv", ["ultron", "voice-status"])
    if report is not None:
        # capture the real function before replacing it, or the lambda recurses
        real = wakeword.setup_report
        monkeypatch.setattr(wakeword, "setup_report", lambda env=None: real({}) if report == "empty" else report)
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main()
    return code, out.getvalue()


def test_it_says_it_did_not_test_the_microphone(monkeypatch):
    """The single most important honesty property of the command."""
    code, out = run_voice_status(monkeypatch, "empty")
    assert "no microphone" in out.lower()
    assert "did NOT test the microphone" in out
    assert "cannot confirm the wake word was heard" in out


def test_it_names_the_missing_pieces_when_nothing_is_installed(monkeypatch):
    code, out = run_voice_status(monkeypatch, "empty")
    assert code == 1, "an unconfigured voice pipeline must be reported as not ready"
    for needle in ("PyAudio", "PICOVOICE_ACCESS_KEY", ".ppn", "STT"):
        assert needle in out, needle


def test_it_points_at_the_picovoice_console_for_the_model(monkeypatch):
    """Ultron is not a built-in keyword, so the fix is a custom model, not a flag."""
    _code, out = run_voice_status(monkeypatch, "empty")
    assert "console.picovoice.ai" in out


def test_it_reports_ready_when_everything_is_present(monkeypatch):
    """And even then it must not claim hardware was verified."""
    import types

    fake_pa = types.ModuleType("pyaudio")

    class FakePyAudio:
        def get_device_count(self):
            return 1

        def get_device_info_by_index(self, index):
            return {"name": "Fake Mic", "maxInputChannels": 1}

        def terminate(self):
            return None

    fake_pa.PyAudio = FakePyAudio
    monkeypatch.setitem(sys.modules, "pyaudio", fake_pa)

    from ultron import cli
    from ultron.voice import wakeword

    monkeypatch.setattr(
        wakeword, "setup_report",
        lambda env=None: wakeword.WakeWordSetup(
            wake_word=wakeword.WAKE_WORD, extra_installed=True, access_key_present=True,
            model_path=__import__("pathlib").Path("/tmp/ultron.ppn"), missing=(), steps=(),
        ),
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/say" if name == "say" else None)
    monkeypatch.setattr(sys, "argv", ["ultron", "voice-status"])

    # speech recognition is genuinely absent on this machine, so stand in for it:
    # the point of this test is the all-green reporting path, not the library itself.
    class FakeTranscriber:
        locale = "en-IN"

        def available(self):
            return True

    from ultron.voice import transcribe as transcribe_module

    monkeypatch.setattr(transcribe_module, "GoogleTranscriber", FakeTranscriber)

    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main()
    text = out.getvalue()
    assert "1 input device(s)" in text, text
    assert "Fake Mic" in text
    # the honest caveat must survive the all-green case
    assert "still needs the smoke test" in text
    assert code == 0


def test_it_does_not_print_any_key_value(monkeypatch):
    """A diagnostic must not become a credential leak."""
    import os

    from ultron import cli

    monkeypatch.setenv("PICOVOICE_ACCESS_KEY", "sk-" + "x" * 40)
    monkeypatch.setattr(sys, "argv", ["ultron", "voice-status"])
    out = io.StringIO()
    with redirect_stdout(out):
        cli.main()
    assert "sk-" + "x" * 40 not in out.getvalue()
    del os.environ["PICOVOICE_ACCESS_KEY"]
