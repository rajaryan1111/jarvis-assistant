"""The voice pipeline: language handling, stage separation and failure taxonomy.

Everything here is mock-based: no microphone, no network recogniser, no TTS binary and no
credentials. The transcriber, responder and speaker are injected, so the tests exercise
the real pipeline logic rather than a provider.
"""

from __future__ import annotations

import pytest

from ultron.voice import (
    AUTO,
    ENGLISH,
    HINDI,
    HINGLISH,
    GoogleTranscriber,
    Transcription,
    VoicePipeline,
    detect_language,
    normalise_mode,
    parse_language_command,
    profile_for,
)
from ultron.voice.transcribe import NO_MATCH, TIMEOUT, UNAVAILABLE


class FakeRecognizer:
    def __init__(self, behaviour):
        self._behaviour = behaviour
        self.locales = []

    def adjust_for_ambient_noise(self, source, duration=0.0):
        return None

    def listen(self, source, timeout=0.0, phrase_time_limit=0.0):
        if self._behaviour.listen_error is not None:
            raise self._behaviour.listen_error
        return self._behaviour.audio

    def recognize_google(self, audio, language=""):
        self.locales.append(language)
        if self._behaviour.recognize_error is not None:
            raise self._behaviour.recognize_error
        return self._behaviour.text


class FakeMicrophone:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSR:
    """A stand-in for the optional speech_recognition module."""

    def __init__(self, behaviour):
        self._behaviour = behaviour
        self.recognizers = []

    def Recognizer(self):
        recognizer = FakeRecognizer(self._behaviour)
        self.recognizers.append(recognizer)
        return recognizer

    def Microphone(self):
        return FakeMicrophone()


class Behaviour:
    def __init__(self, text="", audio=b"audio", listen_error=None, recognize_error=None):
        self.text, self.audio = text, audio
        self.listen_error, self.recognize_error = listen_error, recognize_error


class FakeTranscriber:
    """Returns a scripted Transcription and records the locale it was asked for."""

    def __init__(self, script):
        self._script = list(script)
        self.locales = []

    def transcribe(self, *, locale, timeout=6.0, phrase_time_limit=8.0):
        self.locales.append(locale)
        item = self._script.pop(0) if self._script else Transcription(error=NO_MATCH)
        return item if isinstance(item, Transcription) else Transcription(text=item)


def pipeline(script, *, language=ENGLISH, responder=None, speaker=None):
    spoken = []
    turns = []

    def default_responder(text):
        turns.append(text)
        return f"reply to {text}"

    def default_speaker(text):
        spoken.append(text)
        return "completed"

    pipe = VoicePipeline(transcriber=FakeTranscriber(script), responder=responder or default_responder,
                         speaker=speaker or default_speaker, language=language)
    return pipe, turns, spoken


# ============================================================ language classification
@pytest.mark.parametrize("text,expected", [
    ("", ENGLISH),
    ("   ", ENGLISH),
    ("what is the weather today", ENGLISH),
    ("open my notes please", ENGLISH),
    ("मौसम कैसा है", HINDI),
    ("यह एक परीक्षण है", HINDI),
    ("kya haal hai bhai", HINGLISH),
    ("mera naam kya hai", HINGLISH),
    ("aap kaise hain", HINGLISH),
])
def test_detect_language_classifies_transcripts(text, expected):
    assert detect_language(text) == expected


def test_detect_language_needs_two_markers_for_hinglish():
    """A single ambiguous romanised word must not flip the language."""
    assert detect_language("bhai open the file") == ENGLISH
    assert detect_language("kya kar raha hai") == HINGLISH


@pytest.mark.parametrize("text,expected", [
    ("switch to hindi", HINDI),
    ("hindi mein bolo", HINDI),
    ("jarvis please speak hindi", HINDI),
    ("switch to english", ENGLISH),
    ("english mode", ENGLISH),
    ("hinglish mode", HINGLISH),
    ("use hinglish", HINGLISH),
])
def test_parse_language_command_recognises_switches(text, expected):
    assert parse_language_command(text) == expected


@pytest.mark.parametrize("text", [
    "",
    "what is the weather",
    "how do I say water in hindi",
    "translate this sentence into hindi for me please",
    "please switch to something else entirely now",
])
def test_parse_language_command_ignores_everything_else(text):
    assert parse_language_command(text) is None


def test_language_modes_and_profiles():
    assert normalise_mode("hi") == HINDI
    assert normalise_mode("AUTO") == AUTO
    assert normalise_mode("klingon") == ENGLISH          # unknown falls back to English
    assert normalise_mode(None, {"ULTRON_VOICE_LANGUAGE": "hinglish"}) == HINGLISH
    assert normalise_mode(None, {"ULTRON_VOICE_LANGUAGE": "nonsense"}) == ENGLISH
    assert profile_for(HINDI).stt_locale == "hi-IN"
    assert profile_for("no-such-language").code == ENGLISH
    assert profile_for(HINGLISH).tts_voice is None       # no voice is auto-selected


# ============================================================ transcriber
def test_transcriber_reports_unavailable_without_the_voice_extra():
    result = GoogleTranscriber(sr_module=None).transcribe(locale="en-IN")
    assert result.error == UNAVAILABLE and not result.ok


def test_transcriber_returns_text_and_passes_the_locale():
    sr = FakeSR(Behaviour(text="  what is the weather  "))
    result = GoogleTranscriber(sr_module=sr).transcribe(locale="hi-IN")
    assert result.ok and result.text == "what is the weather"
    assert sr.recognizers[0].locales == ["hi-IN"]


def test_transcriber_maps_failures_to_typed_reasons():
    class WaitTimeoutError(Exception):
        pass

    class UnknownValueError(Exception):
        pass

    timeout = GoogleTranscriber(sr_module=FakeSR(Behaviour(listen_error=WaitTimeoutError())))
    assert timeout.transcribe(locale="en-IN").error == TIMEOUT

    no_match = GoogleTranscriber(sr_module=FakeSR(Behaviour(recognize_error=UnknownValueError())))
    assert no_match.transcribe(locale="en-IN").error == NO_MATCH

    other = GoogleTranscriber(sr_module=FakeSR(Behaviour(recognize_error=RuntimeError("leak?"))))
    assert other.transcribe(locale="en-IN").error == "error"

    empty = GoogleTranscriber(sr_module=FakeSR(Behaviour(text="   ")))
    assert empty.transcribe(locale="en-IN").error == NO_MATCH


def test_transcriber_never_surfaces_exception_details():
    class UnknownValueError(Exception):
        pass

    secret = "sk-" + "supersecretvalue" * 2
    transcriber = GoogleTranscriber(sr_module=FakeSR(Behaviour(recognize_error=UnknownValueError(secret))))
    result = transcriber.transcribe(locale="en-IN")
    assert secret not in repr(result)


# ============================================================ pipeline
def test_pipeline_runs_all_four_stages_in_order():
    pipe, turns, spoken = pipeline(["what is the weather"])
    turn = pipe.run()
    assert turn.ok and turn.transcript == "what is the weather"
    assert turn.reply == "reply to what is the weather"
    assert turn.language == ENGLISH and turn.speak_status == "completed"
    assert turns == ["what is the weather"] and spoken == ["reply to what is the weather"]


def test_pipeline_uses_the_selected_language_locale():
    pipe, _turns, _spoken = pipeline(["मौज कैसा है"], language=HINDI)
    turn = pipe.run()
    assert turn.ok and turn.language == HINDI
    assert pipe._transcriber.locales == ["hi-IN"]


def test_spoken_language_switch_never_reaches_the_responder():
    pipe, turns, spoken = pipeline(["switch to hindi"])
    turn = pipe.run()
    assert turn.switched_to == HINDI and pipe.mode == HINDI
    assert turns == []                                   # the command handler was not called
    assert "hindi" in turn.reply.lower()
    assert spoken == [turn.reply]

    following, _t, _s = pipeline(["मौज कैसा है"])
    following.set_language(HINDI)
    following.run()
    assert following._transcriber.locales == ["hi-IN"]


def test_auto_mode_follows_detection_from_the_next_turn():
    pipe, _turns, _spoken = pipeline(["what is the weather", "kya haal hai bhai"], language=AUTO)
    first = pipe.run()
    assert first.language == ENGLISH                     # first turn uses the default locale
    second = pipe.run()
    assert second.language == HINGLISH                   # detection applied to the next turn
    assert pipe._transcriber.locales == ["en-IN", "en-IN"]


def test_auto_mode_detects_hindi_and_switches_locale():
    pipe, _turns, _spoken = pipeline(["मौज कैसा है"], language=AUTO)
    assert pipe.run().language == HINDI
    pipe.run()                                           # no more scripted audio -> no_match
    assert pipe._transcriber.locales[-1] == "hi-IN"


@pytest.mark.parametrize("error,message", [
    (UNAVAILABLE, "not available"),
    (TIMEOUT, "did not hear"),
    (NO_MATCH, "make that out"),
    ("error", "could not understand"),
])
def test_pipeline_reports_each_failure_without_calling_the_responder(error, message):
    pipe, turns, spoken = pipeline([Transcription(error=error)])
    turn = pipe.run()
    assert not turn.ok and turn.error == error
    assert message in turn.reply.lower()
    assert turns == [] and spoken == [turn.reply]        # the owner still hears the reason


def test_pipeline_replaces_an_empty_or_malformed_reply():
    for bad in ("", "   ", None, 42):
        pipe, _turns, spoken = pipeline(["hello"], responder=lambda text, reply=bad: reply)
        turn = pipe.run()
        assert "do not have anything to say" in turn.reply
        assert spoken[-1] == turn.reply


def test_pipeline_survives_a_speaker_that_reports_unavailable():
    pipe, _turns, _spoken = pipeline(["hello"], speaker=lambda text: "unavailable")
    turn = pipe.run()
    assert turn.ok and turn.speak_status == "unavailable" and turn.reply


def test_a_stage_that_raises_becomes_a_spoken_reason_not_a_crash():
    """The voice loop must survive a stage that raises.

    ``VoicePipeline.run()`` is called from a ``while True`` loop in ``main.main()`` and
    from the wake-word listener. Its own docstring promises it returns a ``VoiceTurn``
    "instead of raising" - but only the *returned* failures were handled. A stage that
    raised (``responder`` is ``main.handle_command``, a very large function; ``speaker``
    shells out to ``say`` on macOS) killed the assistant mid-conversation instead of the
    owner hearing why.
    """
    from ultron.voice.pipeline import VoicePipeline

    # Assembled at runtime so this file never contains a literal that the repository's
    # own credential scanner would flag (the scanner rule is deliberately strict).
    secret = "sk" + "-" + "abcdefghijklmnopqrstuvwxyz012345"

    class Explodes:
        def transcribe(self, **kwargs):
            raise RuntimeError(f"leak {secret}")

    spoken = []
    pipeline = VoicePipeline(transcriber=Explodes(), responder=lambda t: "x", speaker=spoken.append)
    turn = pipeline.run()
    assert turn.error == "error", turn
    assert turn.reply, turn
    assert spoken, "the owner must hear why the turn failed"
    assert secret not in "".join(spoken), spoken
    assert "sk-" not in "".join(spoken), spoken


def test_a_responder_that_raises_does_not_kill_the_loop():
    """``responder`` is ``main.handle_command`` in production: it must not be able to
    take the whole voice loop down."""
    from ultron.voice.pipeline import VoicePipeline
    from ultron.voice.transcribe import Transcription

    class OkTranscriber:
        def transcribe(self, **kwargs):
            return Transcription(text="do something complicated")

    def exploding_responder(text):
        raise ValueError("unexpected internal failure")

    spoken = []
    pipeline = VoicePipeline(transcriber=OkTranscriber(), responder=exploding_responder,
                             speaker=spoken.append)
    turn = pipeline.run()
    assert turn.error == "error", turn
    assert turn.reply, turn
    assert spoken


def test_a_speaker_that_raises_does_not_kill_the_loop():
    """``speaker`` shells out to ``say`` on macOS; even its failure must be survivable."""
    from ultron.voice.pipeline import VoicePipeline
    from ultron.voice.transcribe import Transcription

    class OkTranscriber:
        def transcribe(self, **kwargs):
            return Transcription(text="hello")

    def exploding_speaker(text):
        raise OSError("say failed")

    pipeline = VoicePipeline(transcriber=OkTranscriber(), responder=lambda t: "Hello, sir.",
                             speaker=exploding_speaker)
    turn = pipeline.run()  # must not raise
    assert turn.transcript == "hello", turn
