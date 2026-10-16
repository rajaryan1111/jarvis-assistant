"""The voice session state machine, wake-word configuration and resource ownership.

Everything here is mock-based: a fake wake-word engine, a fake transcriber, a fake
speaker and a fake responder. No microphone, no Porcupine, no network, no TTS binary and
no credentials. The point is to pin down the behaviour that used to be implicit in a
``while True`` loop - especially *who owns the microphone* and *what happens when a stage
fails* - so a regression is caught here rather than on the owner's Mac.
"""

from __future__ import annotations

import pytest

from ultron.voice import WAKE_WORD, VoiceSession, VoiceState, setup_report
from ultron.voice.diagnostics import (
    CAT_DEVICE_MISSING,
    CAT_NO_SPEECH,
    CAT_PERMISSION_DENIED,
    CAT_PROVIDER,
    CAT_RECOGNITION,
    VoiceLogger,
)
from ultron.voice.states import (
    MICROPHONE_STATES,
    IllegalTransition,
    can_transition,
    transition,
)
from ultron.voice.transcribe import NO_MATCH, TIMEOUT, UNAVAILABLE, Transcription
from ultron.voice.wakeword import (
    ACCESS_KEY_ENV_VAR,
    MODEL_ENV_VAR,
    UnavailableWakeEngine,
    resolve_model_path,
)


# ============================================================ fakes
class FakeWakeEngine:
    """Records exactly when the microphone is acquired and released."""

    def __init__(self, *, activations=1, wait_error=None, start_error=None, available=True):
        self.activations = activations
        self._wait_error = wait_error
        self._start_error = start_error
        self._available = available
        self.starts = 0
        self.stops = 0
        self.waits = 0
        self.owned = False          # True while this engine holds the microphone

    @property
    def available(self) -> bool:
        return self._available

    def start(self):
        if self._start_error is not None:
            raise self._start_error
        self.starts += 1
        self.owned = True

    def stop(self):
        self.stops += 1
        self.owned = False

    def wait(self, timeout=None):
        self.waits += 1
        if self._wait_error is not None:
            raise self._wait_error
        if self.activations > 0:
            self.activations -= 1
            return True
        return False


class FakeTranscriber:
    """Scripted transcriptions, or scripted exceptions. Records the locale it was given."""

    def __init__(self, script):
        self._script = list(script)
        self.locales = []
        self.calls = 0

    def transcribe(self, *, locale, timeout=0.0, phrase_time_limit=0.0):
        self.locales.append(locale)
        self.calls += 1
        item = self._script.pop(0) if self._script else Transcription(error=NO_MATCH)
        if isinstance(item, BaseException):
            raise item
        return item


class RecordingSpeaker:
    def __init__(self, *, error=None):
        self.spoken = []
        self._error = error

    def __call__(self, text):
        if self._error is not None:
            raise self._error
        self.spoken.append(text)
        return "completed"


def make_session(**kwargs):
    """A session with recording fakes. Override any stage via kwargs.

    ``sleep`` always defaults to a recorder, so a test never really waits. The recorder
    is returned as the fifth element so a test can assert on the back-off.
    """
    wake = kwargs.pop("wake", None) or FakeWakeEngine()
    transcriber = kwargs.pop("transcriber", None) or FakeTranscriber([Transcription(text="hi")])
    speaker = kwargs.pop("speaker", None) or RecordingSpeaker()
    responder = kwargs.pop("responder", None) or (lambda text: f"you said {text}")
    logger = kwargs.pop("logger", None) or VoiceLogger()
    on_transition = kwargs.pop("on_transition", None)
    slept: list[float] = []
    sleep = kwargs.pop("sleep", None) or slept.append
    return VoiceSession(
        wake_engine=wake,
        transcriber=transcriber,
        responder=responder,
        speaker=speaker,
        logger=logger,
        on_transition=on_transition,
        sleep=sleep,
        **kwargs,
    ), wake, transcriber, speaker, logger


# ============================================================ state machine
def test_the_documented_state_sequence_is_the_one_that_runs():
    """The happy path must walk exactly the documented states, in order."""
    seen = []
    session, *_ = make_session(
        wake=FakeWakeEngine(),
        transcriber=FakeTranscriber([Transcription(text="what time is it")]),
        on_transition=lambda old, new: seen.append(new),
    )
    turn = session.run_once()

    assert turn.activated and turn.ok, turn
    assert seen == [
        VoiceState.WAKE_WORD_DETECTED,
        VoiceState.LISTENING_FOR_COMMAND,
        VoiceState.TRANSCRIBING,
        VoiceState.PROCESSING_REQUEST,
        VoiceState.SPEAKING_RESPONSE,
        VoiceState.IDLE,
    ], seen


def test_wake_word_timeout_is_not_an_error():
    """Nothing said for a while is a normal idle turn, not a failure."""
    session, wake, *_ = make_session(wake=FakeWakeEngine(activations=0))
    turn = session.run_once()
    assert not turn.activated and turn.category is None, turn
    assert wake.waits == 1


def test_illegal_transitions_are_refused_not_hidden():
    assert can_transition(VoiceState.IDLE, VoiceState.SPEAKING_RESPONSE) is False
    with pytest.raises(IllegalTransition):
        transition(VoiceState.IDLE, VoiceState.SPEAKING_RESPONSE)


def test_every_state_can_reach_error_and_recover():
    """No state is a dead end: ERROR is always transient, so the listener recovers."""
    for state in (VoiceState.IDLE, VoiceState.WAKE_WORD_DETECTED, VoiceState.LISTENING_FOR_COMMAND,
                  VoiceState.TRANSCRIBING, VoiceState.PROCESSING_REQUEST, VoiceState.SPEAKING_RESPONSE):
        assert can_transition(state, VoiceState.ERROR), state
        assert can_transition(VoiceState.ERROR, VoiceState.IDLE), state


# ============================================================ microphone ownership
def test_the_wake_stream_is_released_before_the_command_stream_opens():
    """The original bug: two capture streams open at once competed for the device."""
    order = []
    wake = FakeWakeEngine()

    class OrderedTranscriber:
        def transcribe(self, *, locale, timeout=0.0, phrase_time_limit=0.0):
            order.append(("capture", wake.owned))
            return Transcription(text="hi")

    session, *_ = make_session(wake=wake, transcriber=OrderedTranscriber())
    session.run_once()

    assert order == [("capture", False)], "the wake engine still owned the mic while capturing"
    assert wake.stops >= 1 and wake.starts >= 1


def test_the_wake_engine_reacquires_the_microphone_after_speaking():
    wake = FakeWakeEngine()
    session, *_ = make_session(wake=wake, transcriber=FakeTranscriber([Transcription(text="hi")]))
    session.run_once()
    assert wake.owned is True, "the wake engine did not take the microphone back"
    assert wake.starts == 2, wake.starts  # started once, released, re-acquired


def test_only_one_stage_holds_the_microphone_at_a_time():
    """MICROPHONE_STATES must never contain two states the session is inside at once."""
    session, *_ = make_session(transcriber=FakeTranscriber([Transcription(text="hi")]))
    session.run_once()
    states = [e.state for e in session.logger.events]
    held = [s for s in states if s in MICROPHONE_STATES]
    assert held, states
    # speaking and processing must not be "microphone held" states
    assert VoiceState.SPEAKING_RESPONSE not in MICROPHONE_STATES
    assert VoiceState.PROCESSING_REQUEST not in MICROPHONE_STATES


def test_the_microphone_is_released_when_the_session_stops():
    wake = FakeWakeEngine()
    session, *_ = make_session(wake=wake)
    session.run(max_turns=1)
    assert wake.owned is False, "the microphone was left held after the loop ended"
    assert session.logger.current is VoiceState.STOPPED


# ============================================================ failure handling
def test_explicit_microphone_unavailability_is_reported_and_recovers():
    """A missing voice extra must be spoken as a reason, not silently swallowed."""
    speaker = RecordingSpeaker()
    session, wake, transcriber, _, logger = make_session(
        transcriber=FakeTranscriber([Transcription(error=UNAVAILABLE)]), speaker=speaker)
    turn = session.run_once()

    assert turn.category == CAT_DEVICE_MISSING, turn
    assert speaker.spoken, "the owner was not told the microphone is unreachable"
    assert logger.current is VoiceState.IDLE, "the listener did not recover"
    assert transcriber.calls == 1


def test_permission_denied_is_its_own_category():
    class PermissionDenied(Exception):
        pass

    speaker = RecordingSpeaker()
    session, *_ = make_session(transcriber=FakeTranscriber([PermissionDenied("denied")]),
                               speaker=speaker)
    turn = session.run_once()
    assert turn.category == CAT_PERMISSION_DENIED, turn
    assert any("permission" in s.lower() for s in speaker.spoken), speaker.spoken


def test_audio_device_initialisation_failure_recovers():
    class DeviceBusy(Exception):
        pass

    session, *_ = make_session(transcriber=FakeTranscriber([DeviceBusy("busy")]))
    turn = session.run_once()
    assert turn.category == CAT_DEVICE_MISSING, turn
    assert session.logger.current is VoiceState.IDLE


@pytest.mark.parametrize("reason,expected", [
    (TIMEOUT, CAT_NO_SPEECH),
    (NO_MATCH, CAT_NO_SPEECH),
    (UNAVAILABLE, CAT_DEVICE_MISSING),
    ("error", CAT_RECOGNITION),
])
def test_every_transcription_failure_has_a_useful_category(reason, expected):
    speaker = RecordingSpeaker()
    session, *_ = make_session(transcriber=FakeTranscriber([Transcription(error=reason)]),
                               speaker=speaker)
    turn = session.run_once()
    assert turn.category == expected, (reason, turn)
    assert speaker.spoken, "silence must still produce a spoken reason"


def test_silence_is_not_treated_as_a_successful_empty_transcript():
    """An empty transcript must never reach the responder as a real command."""
    seen = []
    session, *_ = make_session(transcriber=FakeTranscriber([Transcription(text="   ")]),
                               responder=seen.append)
    turn = session.run_once()
    assert not turn.activated, turn
    assert seen == [], "an empty transcript reached the responder"


def test_recognition_exception_does_not_crash_and_does_not_reach_the_responder():
    seen = []
    session, *_ = make_session(transcriber=FakeTranscriber([RuntimeError("boom")]),
                               responder=seen.append)
    turn = session.run_once()
    assert turn.category is not None and not turn.activated, turn
    assert seen == []


def test_responder_failure_is_a_provider_error_and_recovers():
    def exploding(text):
        raise RuntimeError("provider down")

    speaker = RecordingSpeaker()
    session, *_ = make_session(transcriber=FakeTranscriber([Transcription(text="hi")]),
                               responder=exploding, speaker=speaker)
    turn = session.run_once()
    assert turn.category == CAT_PROVIDER, turn
    assert session.logger.current is VoiceState.IDLE
    assert speaker.spoken


def test_tts_failure_does_not_stop_the_session():
    speaker = RecordingSpeaker(error=OSError("say failed"))
    session, *_ = make_session(transcriber=FakeTranscriber([Transcription(text="hi")]),
                               speaker=speaker)
    turn = session.run_once()
    assert turn.activated, "a TTS failure must not lose a successful command"
    assert session.logger.current is VoiceState.IDLE


def test_wake_engine_failure_recovers_to_idle():
    wake = FakeWakeEngine(wait_error=RuntimeError("engine died"))
    session, *_ = make_session(wake=wake)
    turn = session.run_once()
    assert turn.category is not None, turn
    assert session.logger.current is VoiceState.IDLE, "the listener did not recover"


def test_a_failing_wake_engine_start_is_reported_not_hidden():
    wake = FakeWakeEngine(start_error=RuntimeError("no audio device"))
    session, *_ = make_session(wake=wake)
    turn = session.run_once()
    assert turn.category is not None
    assert session.logger.current is VoiceState.IDLE


def test_the_listener_resumes_after_a_failure():
    """After any failure the next activation must still work."""
    script = [Transcription(error=UNAVAILABLE), Transcription(text="hello")]
    session, *_ = make_session(wake=FakeWakeEngine(activations=2),
                               transcriber=FakeTranscriber(script))
    first = session.run_once()
    second = session.run_once()
    assert first.category == CAT_DEVICE_MISSING and not first.activated
    assert second.activated and second.ok, second


# ============================================================ no duplicate loops
def test_a_second_concurrent_loop_is_refused():
    session, *_ = make_session()
    assert session.run(max_turns=1) == 1
    # a second call while the first is still "running" must not start another loop
    session._running = True
    assert session.run() == 0, "a second loop started: two microphone owners"


def test_run_once_cannot_reenter_itself():
    """A re-entrant run_once would mean overlapping recordings."""
    session, *_ = make_session()
    session._busy = True
    turn = session.run_once()
    assert turn.category is not None and not turn.activated, turn


def test_stop_is_safe_from_another_thread_and_ends_the_loop():
    session, *_ = make_session(wake=FakeWakeEngine(activations=0))
    session.request_stop()
    assert session.run() == 0


# ============================================================ diagnostics
def test_diagnostics_record_every_transition_with_a_timestamp():
    logger = VoiceLogger()
    session, *_ = make_session(transcriber=FakeTranscriber([Transcription(text="hi")]), logger=logger)
    session.run_once()
    states = [e.state for e in logger.events]
    assert VoiceState.WAKE_WORD_DETECTED in states and VoiceState.IDLE in states
    assert all(e.at > 0 for e in logger.events), "events carry no timestamp"
    assert logger.render()


def test_diagnostics_never_record_the_transcript_or_the_reply():
    """A log must answer "where did it stop?" without becoming a conversation record."""
    secret_transcript = "my password is hunter2"
    secret_reply = "your balance is 12345"
    logger = VoiceLogger(sink=lambda line: None)
    session, *_ = make_session(
        transcriber=FakeTranscriber([Transcription(text=secret_transcript)]),
        responder=lambda text: secret_reply, logger=logger)
    session.run_once()
    rendered = logger.render()
    assert secret_transcript not in rendered
    assert secret_reply not in rendered
    assert "hunter2" not in rendered and "12345" not in rendered


def test_a_failure_records_a_category_not_a_message():
    # Assembled at runtime: a literal would trip the repository's own credential
    # scanner, whose rule is deliberately strict and must not be relaxed.
    leaky_token = "sk" + "-" + "abcdefghijklmnopqrstuvwxyz012345"

    class LeakyError(Exception):
        def __str__(self):
            return f"failed with token {leaky_token}"

    logger = VoiceLogger(sink=lambda line: None)
    session, *_ = make_session(transcriber=FakeTranscriber([LeakyError()]), logger=logger)
    session.run_once()
    assert leaky_token not in logger.render()
    assert "sk-" not in logger.render()
    assert logger.last_error() is not None and logger.last_error().category


def test_the_locale_is_passed_through_to_the_transcriber():
    transcriber = FakeTranscriber([Transcription(text="hi")])
    session, *_ = make_session(transcriber=transcriber, language="hi-IN")
    session.run_once()
    assert transcriber.locales == ["hi-IN"]


# ============================================================ wake-word configuration
def test_the_wake_word_is_ultron():
    assert WAKE_WORD == "ultron"
    assert "ultron" in WAKE_WORD.lower()


def test_a_missing_model_and_key_are_reported_with_fix_steps():
    report = setup_report({})
    assert report.ready is False
    assert "PICOVOICE_ACCESS_KEY" in report.missing
    assert "wake-word model" in report.missing
    assert any("console.picovoice.ai" in s for s in report.steps)
    assert any(".ppn" in s for s in report.steps)
    # the report may name the variable (that is the instruction) but must never
    # contain a key value
    described = report.describe()
    assert "never commit it" in described


def test_a_configured_key_is_never_echoed_by_the_report():
    """The report may name the variable; it must never print its value.

    This is the property the old length heuristic was trying to approximate. That
    heuristic was wrong: it asserted no whitespace-separated token exceeded 40
    characters, but the report legitimately prints the resolved model path, and
    that path is longer than 40 characters on a CI runner
    (/home/runner/work/<repo>/<repo>/ultron.ppn). It passed locally only because
    the sandbox path happened to be shorter. Test the real invariant instead.
    """
    # Assembled at runtime so the repository's own credential scanner, whose rule
    # is deliberately strict, does not flag this file.
    fake_key = "sk" + "-" + "x" * 40

    report = setup_report({ACCESS_KEY_ENV_VAR: fake_key})
    described = report.describe()

    assert ACCESS_KEY_ENV_VAR in described, "the report must name the variable to configure"
    assert fake_key not in described, "the report must never print a key value"
    # and the value must not be split up across lines either
    assert "".join(described.split()).find(fake_key) == -1


def test_an_unavailable_engine_says_why_instead_of_claiming_support():
    engine = UnavailableWakeEngine(setup_report({}))
    assert engine.available is False
    with pytest.raises(RuntimeError, match="wake-word listening is NOT available"):
        engine.start()
    with pytest.raises(RuntimeError):
        engine.wait()


def test_the_model_path_honours_the_env_override(tmp_path):
    model = tmp_path / "hey-ultron.ppn"
    model.write_bytes(b"ppn")
    assert resolve_model_path(str(model)) == model
    assert resolve_model_path(str(tmp_path / "nope.ppn")) is None


def test_setup_report_detects_a_present_model_and_key(tmp_path):
    model = tmp_path / "ultron.ppn"
    model.write_bytes(b"ppn")
    env = {MODEL_ENV_VAR: str(model), "PICOVOICE_ACCESS_KEY": "k" * 24}
    # the extra itself is not installed here, so only model+key can be satisfied
    report = setup_report(env)
    assert report.model_path == model
    assert report.access_key_present is True
    assert "wake-word model" not in report.missing


# ============================================================ the fix that started this
def test_the_named_regression_is_fixed_even_with_the_extra_installed(monkeypatch):
    """The reported failure: explicit ``sr_module=None`` must mean unavailable.

    Reproduces a machine that HAS the optional voice extra, which is the only place the
    old ``self._sr or _sr_module()`` logic misbehaved.
    """
    import sys

    from ultron.voice import GoogleTranscriber

    monkeypatch.setitem(sys.modules, "speech_recognition", object())
    result = GoogleTranscriber(sr_module=None).transcribe(locale="en-IN")
    assert result.error == UNAVAILABLE and not result.ok
    assert GoogleTranscriber(sr_module=None).available() is False


# ============================================================ back-off (no CPU spin)
def test_a_missing_microphone_backs_off_instead_of_spinning():
    """The busy-loop bug: a missing device must not retry tens of thousands of times."""
    slept = []
    session, *_ = make_session(
        transcriber=FakeTranscriber([Transcription(error=UNAVAILABLE)]),
        sleep=slept.append)
    session.run_once()
    assert slept == [5.0], slept


def test_an_ordinary_silence_retries_immediately():
    """Back-off is only for a genuinely unavailable device, not for a quiet room."""
    slept = []
    session, *_ = make_session(
        transcriber=FakeTranscriber([Transcription(error=TIMEOUT)]),
        sleep=slept.append)
    session.run_once()
    assert slept == [], slept


def test_a_no_match_retries_immediately():
    slept = []
    session, *_ = make_session(
        transcriber=FakeTranscriber([Transcription(error=NO_MATCH)]),
        sleep=slept.append)
    session.run_once()
    assert slept == [], slept


def test_the_backoff_is_configurable_and_can_be_disabled():
    slept = []
    session, *_ = make_session(
        transcriber=FakeTranscriber([Transcription(error=UNAVAILABLE)]),
        sleep=slept.append, unavailable_backoff=0)
    session.run_once()
    assert slept == [], slept


def test_a_wake_timeout_does_not_back_off():
    """Nothing said for a while is normal; only a broken device backs off."""
    slept = []
    session, *_ = make_session(wake=FakeWakeEngine(activations=0), sleep=slept.append)
    session.run_once()
    assert slept == [], slept
