"""Speech-to-text adapter with an explicit failure taxonomy.

One place turns microphone audio into text, so the CLI loop and the wake-word listener
share the same behaviour (locale and error handling). ``speech_recognition`` and a
microphone are optional: when they are missing the transcriber reports ``unavailable``
instead of raising, and no raw exception text is ever printed or returned - only a typed reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ultron.voice.languages import DEFAULT_LANGUAGE, normalise_mode, profile_for

#: Failure reasons. ``None`` means the transcript is usable.
UNAVAILABLE = "unavailable"   # speech_recognition extra or microphone missing
TIMEOUT = "timeout"           # nothing was said within the timeout
NO_MATCH = "no_match"         # audio captured but not understood
ERROR = "error"               # anything else; details are redacted, never surfaced

INSTALL_HINT = "Voice input needs the optional 'voice' extra: uv sync --extra voice (or pip install -e '.[voice]')."

#: The recogniser locale used when no language is configured. Derived from the language
#: table rather than restated, so the reported default and the tag the recogniser is
#: actually given can never drift apart. English (India) is the historical behaviour
#: shared by the CLI loop, the wake-word listener and the desktop session, and it is a
#: real BCP-47 tag - unlike the internal codes ``"en"``, ``"hinglish"`` and ``"auto"``.
DEFAULT_LOCALE = profile_for(DEFAULT_LANGUAGE).stt_locale


def recogniser_locale(language: str | None) -> str:
    """The BCP-47 tag to hand the recogniser for a configured language mode.

    :mod:`ultron.voice.languages` owns the code -> tag table, so there is exactly one
    mapping in the codebase. An unknown, unset or ``auto`` mode resolves to the default
    profile rather than being passed through: those are internal codes, not tags
    Google's recogniser understands.
    """
    return profile_for(normalise_mode(language)).stt_locale


#: Sentinel for "no module was injected". Deliberately distinct from ``None``, which is
#: a real value meaning "the optional dependency is absent" - the way a caller (or a
#: test) states that this machine has no voice extra. Without the sentinel the two are
#: indistinguishable and an explicit ``None`` gets read as "go and load the real one",
#: which silently turns `unavailable` into a live microphone attempt on any machine
#: that HAS the extra installed.
_UNSET: Any = object()


@dataclass(frozen=True)
class Transcription:
    """The outcome of one listening attempt."""

    text: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


def _sr_module():
    try:
        import speech_recognition as sr
    except ImportError:
        return None
    return sr


class GoogleTranscriber:
    """Capture one utterance from the microphone and recognise it with Google.

    ``sr_module`` is injectable so tests never touch hardware or the network. Omit it -
    the production default used by ``main.py`` and the wake-word listener - and the
    optional ``speech_recognition`` module is loaded lazily. Pass ``None`` to state
    explicitly that the module is absent; the transcriber then reports ``unavailable``
    even on a machine that has the extra installed.

    ``language`` selects the recogniser locale used when a caller does not pass one
    explicitly. It is resolved through the shared language table, so :attr:`locale` is
    a real BCP-47 tag and a diagnostic that reports it is reporting what the recogniser
    is actually given - never a guess.
    """

    def __init__(self, *, sr_module: Any = _UNSET, ambient_duration: float = 0.4,
                 language: str = DEFAULT_LANGUAGE) -> None:
        self._sr = sr_module
        self._ambient_duration = ambient_duration
        #: The configured language mode ("en", "hi", "hinglish" or "auto").
        self.language = language

    @property
    def locale(self) -> str:
        """The BCP-47 tag handed to ``recognize_google`` when no locale is passed.

        Read-only and derived from :attr:`language`, so it cannot go stale or disagree
        with the tag actually used.
        """
        return recogniser_locale(self.language)

    def locale_for(self, language: str | None) -> str:
        """The tag to use for one turn, so a session can change language mid-run."""
        return recogniser_locale(language)

    def _resolve(self) -> Any:
        """The module to use: the injected one, or the optional dependency.

        ``_UNSET`` means "nothing was injected, load the real thing". An explicit
        ``None`` is a value rather than an absence, so it is returned as-is and the
        caller's "there is no voice extra here" is honoured.
        """
        return _sr_module() if self._sr is _UNSET else self._sr

    def available(self) -> bool:
        return self._resolve() is not None

    def transcribe(self, *, locale: str | None = None, timeout: float = 6.0,
                   phrase_time_limit: float = 8.0) -> Transcription:
        """Recognise one utterance. ``locale`` defaults to :attr:`locale`.

        Omit ``locale`` and the tag resolved from :attr:`language` is used, which is
        what a diagnostic reports - so the two can never disagree.
        """
        sr = self._resolve()
        if sr is None:
            return Transcription(error=UNAVAILABLE)
        tag = locale or self.locale
        recognizer = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                if self._ambient_duration:
                    recognizer.adjust_for_ambient_noise(source, duration=self._ambient_duration)
                audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        except Exception as exc:  # noqa: BLE001 - mapped to a typed reason, details redacted
            reason = TIMEOUT if type(exc).__name__ == "WaitTimeoutError" else ERROR
            return Transcription(error=reason)
        try:
            text = recognizer.recognize_google(audio, language=tag)
        except Exception as exc:  # noqa: BLE001 - no raw recogniser text reaches the caller
            reason = NO_MATCH if type(exc).__name__ == "UnknownValueError" else ERROR
            return Transcription(error=reason)
        if not isinstance(text, str) or not text.strip():
            return Transcription(error=NO_MATCH)
        return Transcription(text=text.strip())
