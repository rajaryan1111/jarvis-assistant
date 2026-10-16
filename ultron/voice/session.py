"""The voice session: one microphone owner, explicit states, no stuck listener.

This is the fix for the "it doesn't hear me" failure. The old code had two disjoint
programs: ``main.py`` looped a command listener with no wake word at all, and
``wakeword_listener.py`` detected the wake word in a separate process - and while doing
so it kept its own PyAudio capture stream *open* and then opened a second one inside the
speech recogniser, so the two competed for the same device.

``VoiceSession`` replaces both with one state machine that owns the microphone
explicitly:

    IDLE (wake engine owns the mic)
      -> WAKE_WORD_DETECTED   the engine fires; its stream is released *here*
      -> LISTENING_FOR_COMMAND  the transcriber opens the only capture stream
      -> TRANSCRIBING           the result is classified
      -> PROCESSING_REQUEST     the responder runs (no microphone held)
      -> SPEAKING_RESPONSE      the reply is spoken (no microphone held)
      -> IDLE                   the wake engine re-acquires the microphone

Guarantees the tests pin down:

* **one microphone owner at a time** - the wake stream is released before the command
  stream opens, and re-acquired only after speaking finishes;
* **no duplicate loops** - :meth:`run` refuses to start twice, and a second
  :meth:`run_once` cannot interleave with a running loop;
* **no stuck listener** - every failure goes through ``ERROR`` and recovers to ``IDLE``
  (or ``STOPPED`` when shutting down), so the microphone is always released;
* **observable transitions** - every state change is reported to the logger and to an
  optional listener, so "where did it stop?" has an answer.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ultron.voice.diagnostics import (
    CAT_DEVICE_MISSING,
    CAT_INTERNAL,
    CAT_NO_SPEECH,
    CAT_PERMISSION_DENIED,
    CAT_PROVIDER,
    CAT_RECOGNITION,
    CAT_SPEECH_OUTPUT,
    CAT_WAKE_ENGINE,
    VoiceLogger,
)
from ultron.voice.states import VoiceState, transition
from ultron.voice.transcribe import ERROR, NO_MATCH, TIMEOUT, UNAVAILABLE, Transcription

#: How a reply is spoken. Returns a status string ("" when there is nothing to say).
Speaker = Callable[[str], str]
#: Turns a transcript into a reply.
Responder = Callable[[str], str]

#: Maps a transcriber failure reason onto a diagnostic category.
_REASON_CATEGORY = {
    UNAVAILABLE: CAT_DEVICE_MISSING,
    TIMEOUT: CAT_NO_SPEECH,
    NO_MATCH: CAT_NO_SPEECH,
    ERROR: CAT_RECOGNITION,
}

#: What the owner hears for each failure category. Short, useful, no technical detail.
_CATEGORY_REPLY = {
    CAT_DEVICE_MISSING: "I cannot reach the microphone, sir.",
    CAT_NO_SPEECH: "I did not hear anything, sir.",
    CAT_RECOGNITION: "I could not make that out, sir.",
    CAT_PERMISSION_DENIED: "I do not have microphone permission, sir.",
    CAT_WAKE_ENGINE: "My wake-word listener is not available, sir.",
}


def _category_of_exception(exc: BaseException) -> str:
    """Classify an exception without quoting it (its text can carry a secret)."""
    name = type(exc).__name__.lower()
    if "permission" in name or "denied" in name:
        return CAT_PERMISSION_DENIED
    if "device" in name or "audio" in name or "stream" in name:
        return CAT_DEVICE_MISSING
    if "timeout" in name:
        return CAT_NO_SPEECH
    return CAT_INTERNAL


@dataclass
class SessionTurn:
    """The outcome of one activation, in a form tests and a UI can assert on.

    Named ``SessionTurn`` rather than ``VoiceTurn`` so it never collides with
    :class:`ultron.voice.pipeline.VoiceTurn`, which is the established public name.
    """

    state: VoiceState = VoiceState.IDLE
    transcript: str = ""
    reply: str = ""
    category: str | None = None
    activated: bool = False
    speak_status: str = ""

    @property
    def ok(self) -> bool:
        return self.category is None


@dataclass
class VoiceSession:
    """One assistant voice session. See the module docstring for the guarantees."""

    wake_engine: object
    transcriber: object
    responder: Responder
    speaker: Speaker
    logger: VoiceLogger = field(default_factory=VoiceLogger)
    language: str = "en"
    command_timeout: float = 6.0
    phrase_time_limit: float = 8.0
    wake_timeout: float | None = None
    on_transition: Callable[[VoiceState, VoiceState], None] | None = None
    #: Seconds to wait before retrying when this machine has no usable voice input.
    #: Without it a missing microphone or a missing voice extra spins the CPU (measured
    #: at ~39,000 turns a second). A normal silence/timeout retries immediately.
    unavailable_backoff: float = 5.0
    #: Injectable so tests never actually sleep.
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self._running = False
        self._stop_requested = False
        self._busy = False
        self._wake_started = False

    # -- state helpers -----------------------------------------------------
    @property
    def state(self) -> VoiceState:
        return self.logger.current

    def _go(self, target: VoiceState, *, category: str | None = None, detail: str = "") -> VoiceState:
        """Move to ``target``, recording it. Illegal moves are reported, not hidden."""
        current = self.logger.current
        try:
            transition(current, target)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed silently
            self.logger.note(CAT_INTERNAL, f"refused {current.value}->{target.value}: {exc}")
            raise
        self.logger.enter(target, category=category, detail=detail)
        if self.on_transition is not None:
            try:
                self.on_transition(current, target)
            except Exception:  # noqa: BLE001, S110 - a UI callback must not break the session
                pass
        return target

    def request_stop(self) -> None:
        """Ask the loop to finish after the current turn. Safe from another thread."""
        self._stop_requested = True

    # -- microphone ownership ----------------------------------------------
    def _ensure_wake_engine(self) -> None:
        """Make sure the wake engine holds the microphone. Idempotent.

        The session - not the engine - owns this lifecycle, so "is the engine usable"
        and "does the engine currently hold the device" never get confused.
        """
        if not self._wake_started:
            self.wake_engine.start()
            self._wake_started = True

    def _release_wake_engine(self) -> None:
        """Hand the microphone away from the wake engine, before capturing a command."""
        if self._wake_started:
            self.wake_engine.stop()
            self._wake_started = False

    # -- the machine -------------------------------------------------------
    def run_once(self) -> SessionTurn:
        """One full activation cycle. Never raises; failures become a spoken reason."""
        if self._busy:
            # A second entry would mean two microphone owners. Refuse instead.
            self.logger.note(CAT_INTERNAL, "run_once re-entry refused (one microphone owner)")
            return SessionTurn(state=self.logger.current, category=CAT_INTERNAL,
                             reply="I am already listening, sir.")
        self._busy = True
        try:
            return self._cycle()
        except Exception as exc:  # noqa: BLE001 - the session survives any stage failure
            return self._recover(exc)
        finally:
            self._busy = False

    def _cycle(self) -> SessionTurn:
        # ---- IDLE: the wake engine owns the microphone -------------------
        self._ensure_wake_engine()
        self.logger.enter(VoiceState.IDLE, detail="waiting for wake word")
        if not self.wake_engine.wait(self.wake_timeout):
            # A timeout is a normal "nothing happened", not an error.
            self._maybe_backoff(None)
            return SessionTurn(state=VoiceState.IDLE)

        # ---- WAKE WORD DETECTED: release the wake stream BEFORE capturing --
        self._go(VoiceState.WAKE_WORD_DETECTED, detail="wake word heard")
        self._release_wake_engine()

        # ---- LISTENING FOR COMMAND: the only capture stream is open here --
        self._go(VoiceState.LISTENING_FOR_COMMAND, detail="capturing command")
        try:
            result: Transcription = self.transcriber.transcribe(
                locale=self._locale(), timeout=self.command_timeout,
                phrase_time_limit=self.phrase_time_limit)
        except Exception as exc:  # noqa: BLE001
            category = _category_of_exception(exc)
            self.logger.enter(VoiceState.ERROR, category=category, detail="capture failed")
            raise _StageFailure(category, exc) from exc

        # ---- TRANSCRIBING: classify the outcome --------------------------
        self._go(VoiceState.TRANSCRIBING,
                 category=None if result.ok else _REASON_CATEGORY.get(result.error or "", CAT_RECOGNITION),
                 detail="ok" if result.ok else f"reason={result.error}")
        if not result.ok:
            # Silence, a timeout or a recognition failure: say so and go back to waiting.
            category = _REASON_CATEGORY.get(result.error or "", CAT_RECOGNITION)
            reply = _CATEGORY_REPLY.get(category, "I could not understand that, sir.")
            self._speak(reply)
            # Nothing usable was heard: say so and go back to waiting for the wake word.
            self._go(VoiceState.IDLE, category=category, detail="no command; waiting again")
            self._maybe_backoff(category)
            return SessionTurn(state=VoiceState.IDLE, category=category, reply=reply)

        # ---- PROCESSING REQUEST: no microphone is held here ---------------
        transcript = result.text.strip()
        self._go(VoiceState.PROCESSING_REQUEST, detail=f"transcript_chars={len(transcript)}")
        try:
            reply = self.responder(transcript)
        except Exception as exc:  # noqa: BLE001
            self.logger.enter(VoiceState.ERROR, category=CAT_PROVIDER, detail="responder failed")
            raise _StageFailure(CAT_PROVIDER, exc) from exc
        if not isinstance(reply, str) or not reply.strip():
            reply = "I do not have anything to say about that yet, sir."

        # ---- SPEAKING RESPONSE: still no microphone ----------------------
        self._go(VoiceState.SPEAKING_RESPONSE, detail=f"reply_chars={len(reply)}")
        status = self._speak(reply)

        # ---- back to IDLE: the wake engine re-acquires the microphone -----
        self._ensure_wake_engine()
        self._go(VoiceState.IDLE, detail="resuming wake-word listening")
        return SessionTurn(state=VoiceState.IDLE, transcript=transcript, reply=reply,
                         activated=True, speak_status=status)

    def _maybe_backoff(self, category: str | None) -> None:
        """Wait before retrying, but only when voice input is genuinely unavailable.

        Mirrors the policy the old CLI loop had: a missing device or a missing optional
        extra backs off, while an ordinary silence or timeout retries at once so the
        assistant still feels responsive.
        """
        if category == CAT_DEVICE_MISSING and self.unavailable_backoff > 0:
            self.sleep(self.unavailable_backoff)

    def _speak(self, reply: str) -> str:
        try:
            status = self.speaker(reply)
        except Exception:  # noqa: BLE001, S110 - TTS failure must not stop the session
            self.logger.note(CAT_SPEECH_OUTPUT, "speaker failed")
            return ""
        return status if isinstance(status, str) else ""

    def _locale(self) -> str:
        getter = getattr(self.transcriber, "locale_for", None)
        if callable(getter):
            try:
                return str(getter(self.language))
            except Exception:  # noqa: BLE001, S110 - fall back to the raw language code
                return self.language
        return self.language

    def _recover(self, exc: BaseException) -> SessionTurn:
        """A stage failed. Say why, release the microphone, and go back to IDLE."""
        category = getattr(exc, "category", None) or _category_of_exception(exc)
        reply = _CATEGORY_REPLY.get(category, "Something went wrong, sir.")
        self._speak(reply)
        target = VoiceState.STOPPED if self._stop_requested else VoiceState.IDLE
        if self.logger.current is VoiceState.IDLE:
            # The wake stage itself failed while already idle: record the error
            # explicitly, because idle -> idle is not a legal transition.
            self.logger.enter(VoiceState.ERROR, category=category, detail="stage failed")
        try:
            self._go(target, category=category,
                     detail="stopping" if target is VoiceState.STOPPED else "recovered")
        except Exception:  # noqa: BLE001, S110 - never let recovery itself raise
            self.logger.note(CAT_INTERNAL, "recovery transition refused")
        return SessionTurn(state=self.logger.current, category=category, reply=reply)

    # -- the loop ----------------------------------------------------------
    def run(self, *, max_turns: int | None = None) -> int:
        """Run activation cycles until stopped. Returns how many completed.

        Refuses to start a second concurrent loop: two loops would mean two microphone
        owners, which is precisely the failure this module exists to prevent.
        """
        if self._running:
            self.logger.note(CAT_INTERNAL, "run re-entry refused (one loop only)")
            return 0
        self._running = True
        completed = 0
        try:
            while not self._stop_requested:
                turn = self.run_once()
                if turn.activated:
                    completed += 1
                if max_turns is not None and completed >= max_turns:
                    break
                if self.logger.current is VoiceState.STOPPED:
                    break
        finally:
            self._running = False
            self._release_everything()
        return completed

    def _release_everything(self) -> None:
        """Release the microphone whatever happened. Idempotent."""
        try:
            self._release_wake_engine()
            self.wake_engine.stop()
        except Exception:  # noqa: BLE001, S110 - cleanup must not raise
            pass
        if self.logger.current not in (VoiceState.STOPPED,):
            self.logger.enter(VoiceState.STOPPED, detail="microphone released")


class _StageFailure(Exception):
    """Internal: a stage failed and carries its diagnostic category."""

    def __init__(self, category: str, cause: BaseException) -> None:
        super().__init__(category)
        self.category = category
        self.__cause__ = cause


__all__ = [
    "Responder",
    "SessionTurn",
    "Speaker",
    "VoiceSession",
    "VoiceState",
]
