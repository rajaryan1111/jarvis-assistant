"""Modular voice pipeline: recognition, language handling, response generation, output.

The stages are separate and injectable, so the whole path is testable without a
microphone, a network recogniser or a TTS binary:

* ``transcriber`` - audio -> :class:`~ultron.voice.transcribe.Transcription`
* ``responder``   - transcript -> reply text (``main.handle_command`` in production)
* ``speaker``     - reply -> status string (``main.speak`` in production)

Every stage reports a typed outcome; the pipeline returns a :class:`VoiceTurn` instead of
raising or silently returning an empty string. Language handling:

* the mode is ``en`` (historical behaviour), ``hi``, ``hinglish`` or ``auto``;
* a spoken switch ("switch to hindi") changes the mode and never reaches the responder;
* in ``auto`` mode the transcript is classified and the recogniser locale follows it from
  the next turn - detection is transcript-based, so there is a one-turn lag, and an
  explicit switch always wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ultron.voice.languages import (
    AUTO,
    DEFAULT_LANGUAGE,
    ENGLISH,
    HINDI,
    HINGLISH,
    detect_language,
    normalise_mode,
    parse_language_command,
    profile_for,
)
from ultron.voice.transcribe import NO_MATCH, TIMEOUT, UNAVAILABLE, Transcription

#: What the user hears when a stage fails. Kept short and free of technical detail.
FAILURE_MESSAGES = {
    UNAVAILABLE: "Voice input is not available on this machine, sir.",
    TIMEOUT: "I did not hear anything, sir.",
    NO_MATCH: "I could not make that out, sir.",
}
DEFAULT_FAILURE_MESSAGE = "I could not understand that, sir."
EMPTY_REPLY_MESSAGE = "I do not have anything to say about that yet, sir."
SWITCH_CONFIRMATIONS = {
    ENGLISH: "English it is, sir.",
    HINDI: "ठीक है sir, Hindi mein baat karte hain.",
    HINGLISH: "Hinglish mein baat karte hain, sir.",
}


@dataclass
class VoiceTurn:
    """What one spoken turn did, in a form tests and callers can assert on."""

    transcript: str = ""
    reply: str = ""
    language: str = DEFAULT_LANGUAGE
    mode: str = DEFAULT_LANGUAGE
    speak_status: str = ""
    error: str | None = None
    switched_to: str | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.error is None


class VoicePipeline:
    """Wires the four stages together and owns the language mode."""

    def __init__(self, *, transcriber, responder, speaker, language: str = DEFAULT_LANGUAGE,
                 detector=detect_language) -> None:
        self._transcriber = transcriber
        self._responder = responder
        self._speaker = speaker
        self._mode = normalise_mode(language)
        self._detector = detector
        self._active = DEFAULT_LANGUAGE

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def language(self) -> str:
        """The language the next utterance will be recognised in."""
        return self._mode if self._mode != AUTO else self._active

    def set_language(self, mode: str) -> str:
        """Explicitly select a language (or ``auto``). Returns the resolved mode."""
        self._mode = normalise_mode(mode)
        return self._mode

    def run(self) -> VoiceTurn:
        locale = profile_for(self.language).stt_locale
        result: Transcription = self._transcriber.transcribe(locale=locale)
        if not result.ok:
            # A failed stage is still a turn: the owner hears why, instead of silence.
            reply = FAILURE_MESSAGES.get(result.error or "", DEFAULT_FAILURE_MESSAGE)
            return VoiceTurn(language=self.language, mode=self._mode, error=result.error,
                             reply=reply, speak_status=self._speaker(reply))

        transcript = result.text.strip()
        switched = parse_language_command(transcript)
        if switched is not None:
            self._mode = switched
            self._active = switched
            reply = SWITCH_CONFIRMATIONS[switched]
            return VoiceTurn(transcript=transcript, reply=reply, language=switched, mode=switched,
                             speak_status=self._speaker(reply), switched_to=switched)

        detected = self._detector(transcript) if self._mode == AUTO else self._mode
        if self._mode == AUTO and detected != self._active:
            # Locale follows detection from the next turn; this turn used the old locale.
            self._active = detected

        reply = self._responder(transcript)
        if not isinstance(reply, str) or not reply.strip():
            reply = EMPTY_REPLY_MESSAGE
        status = self._speaker(reply)
        return VoiceTurn(transcript=transcript, reply=reply, language=detected, mode=self._mode,
                         speak_status=status if isinstance(status, str) else "")
