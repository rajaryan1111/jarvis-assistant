"""Regression tests for `ultron voice-status`.

The requirement is a diagnostic that never claims microphone or wake-word support
works merely because a package is installed, and that clearly separates a static
configuration check from a real microphone test. These tests run it with every
dependency absent and present to check both halves of that promise.

The "present" cases deliberately exercise the **real** ``GoogleTranscriber``. An earlier
version of the all-green test replaced it with a stub that hardcoded ``locale =
"en-IN"``, which is precisely how ``AttributeError: 'GoogleTranscriber' object has no
attribute 'locale'`` reached a real Mac: the stub had an attribute the real class did
not. No test here may substitute the transcriber again.
"""
import io
import sys
import types
from contextlib import redirect_stdout


def _fake_speech_recognition(monkeypatch):
    """Make the optional voice extra look installed, without importing anything.

    ``voice-status`` only asks whether the module is importable, so a bare module
    object is enough. No microphone is opened and no network call is made.
    """
    monkeypatch.setitem(sys.modules, "speech_recognition", types.ModuleType("speech_recognition"))


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

    # The voice extra looks installed, so the REAL GoogleTranscriber is constructed and
    # its own default locale is reported. Substituting the transcriber here is what let
    # the missing-attribute crash through in the first place.
    _fake_speech_recognition(monkeypatch)

    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main()
    text = out.getvalue()
    assert "1 input device(s)" in text, text
    assert "Fake Mic" in text
    # the honest caveat must survive the all-green case
    assert "still needs the smoke test" in text
    assert code == 0


# ============================================== the macOS AttributeError regression
def test_voice_status_does_not_crash_when_speech_recognition_is_available(monkeypatch):
    """The reported failure: `uv run ultron voice-status` died with
    ``AttributeError: 'GoogleTranscriber' object has no attribute 'locale'``.

    It only happened on a machine where the ``voice`` extra is installed, because only
    then does the CLI take the branch that reads ``transcriber.locale``. So the
    regression test has to put the extra there - and must use the real transcriber.
    """
    _fake_speech_recognition(monkeypatch)

    code, out = run_voice_status(monkeypatch, "empty")

    assert code == 1, "nothing else is configured here, so 'not ready' is the right exit"
    assert "AttributeError" not in out
    assert "Traceback" not in out
    assert "Google speech recognition is available" in out, out


def test_the_reported_locale_is_a_real_recogniser_tag(monkeypatch):
    """What voice-status prints must be a tag the recogniser accepts, not a guess."""
    _fake_speech_recognition(monkeypatch)

    from ultron.voice.transcribe import DEFAULT_LOCALE, GoogleTranscriber

    _code, out = run_voice_status(monkeypatch, "empty")

    assert f"Default recogniser locale: {DEFAULT_LOCALE}" in out, out
    assert "-" in DEFAULT_LOCALE, "a BCP-47 tag has a region subtag; a bare code does not"
    # and the value really is the one the real object reports
    assert GoogleTranscriber().locale == DEFAULT_LOCALE


class _FakeRecognizer:
    """Records the tag handed to ``recognize_google``; never touches hardware."""

    def __init__(self, handed):
        self._handed = handed

    def adjust_for_ambient_noise(self, source, duration=0.0):
        return None

    def listen(self, source, timeout=0.0, phrase_time_limit=0.0):
        return b"audio"

    def recognize_google(self, audio, language=""):
        self._handed.append(language)
        return "hello"


class _FakeMicrophone:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _recording_sr(handed):
    return types.SimpleNamespace(
        Recognizer=lambda: _FakeRecognizer(handed), Microphone=_FakeMicrophone)


def test_the_default_locale_is_the_tag_the_recogniser_actually_receives(monkeypatch):
    """The report and the recogniser must not be able to disagree.

    Call ``transcribe`` with no locale and capture what is handed to
    ``recognize_google``: it has to be the same tag the command prints.
    """
    _fake_speech_recognition(monkeypatch)

    from ultron.voice.transcribe import GoogleTranscriber

    handed = []
    transcriber = GoogleTranscriber(sr_module=_recording_sr(handed))

    assert transcriber.available()
    result = transcriber.transcribe(timeout=0.0, phrase_time_limit=0.0)  # no locale passed

    assert result.ok, result
    assert handed == [transcriber.locale], (handed, transcriber.locale)


def test_an_explicit_locale_still_wins_over_the_default(monkeypatch):
    """Backwards compatibility: every existing caller passes a locale explicitly."""
    _fake_speech_recognition(monkeypatch)

    from ultron.voice.transcribe import GoogleTranscriber

    handed = []
    transcriber = GoogleTranscriber(sr_module=_recording_sr(handed), language="hi")

    transcriber.transcribe(locale="hi-IN", timeout=0.0, phrase_time_limit=0.0)
    assert handed == ["hi-IN"]


def test_the_language_mode_selects_the_reported_locale(monkeypatch):
    """A configured language must change what the command prints."""
    _fake_speech_recognition(monkeypatch)

    from ultron.voice.transcribe import GoogleTranscriber

    assert GoogleTranscriber(language="hi").locale == "hi-IN"
    assert GoogleTranscriber(language="en").locale == "en-IN"
    assert GoogleTranscriber(language="hinglish").locale == "en-IN"  # no Hinglish locale


def test_internal_language_codes_are_never_handed_to_the_recogniser(monkeypatch):
    """``auto`` and ``hinglish`` are modes, not tags; they must resolve, not pass through."""
    _fake_speech_recognition(monkeypatch)

    from ultron.voice.transcribe import recogniser_locale

    for code in ("auto", "hinglish", "en", "hi", None, "", "no-such-language"):
        tag = recogniser_locale(code)
        assert tag in ("en-IN", "hi-IN"), (code, tag)
    assert recogniser_locale("auto") == "en-IN"
    assert recogniser_locale("hi") == "hi-IN"


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
