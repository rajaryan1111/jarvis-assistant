"""Speech-to-text adapter with an explicit failure taxonomy.

One place turns microphone audio into text, so the CLI loop and the wake-word listener
share the same behaviour (locale and error handling). ``speech_recognition`` and a
microphone are optional: when they are missing the transcriber reports ``unavailable``
instead of raising, and no raw exception text is ever printed or returned - only a typed reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Failure reasons. ``None`` means the transcript is usable.
UNAVAILABLE = "unavailable"   # speech_recognition extra or microphone missing
TIMEOUT = "timeout"           # nothing was said within the timeout
NO_MATCH = "no_match"         # audio captured but not understood
ERROR = "error"               # anything else; details are redacted, never surfaced

INSTALL_HINT = "Voice input needs the optional 'voice' extra: uv sync --extra voice (or pip install -e '.[voice]')."

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
    """

    def __init__(self, *, sr_module: Any = _UNSET, ambient_duration: float = 0.4) -> None:
        self._sr = sr_module
        self._ambient_duration = ambient_duration

    def _resolve(self) -> Any:
        """The module to use: the injected one, or the optional dependency.

        ``_UNSET`` means "nothing was injected, load the real thing". An explicit
        ``None`` is a value rather than an absence, so it is returned as-is and the
        caller's "there is no voice extra here" is honoured.
        """
        return _sr_module() if self._sr is _UNSET else self._sr

    def available(self) -> bool:
        return self._resolve() is not None

    def transcribe(self, *, locale: str, timeout: float = 6.0, phrase_time_limit: float = 8.0) -> Transcription:
        sr = self._resolve()
        if sr is None:
            return Transcription(error=UNAVAILABLE)
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
            text = recognizer.recognize_google(audio, language=locale)
        except Exception as exc:  # noqa: BLE001 - no raw recogniser text reaches the caller
            reason = NO_MATCH if type(exc).__name__ == "UnknownValueError" else ERROR
            return Transcription(error=reason)
        if not isinstance(text, str) or not text.strip():
            return Transcription(error=NO_MATCH)
        return Transcription(text=text.strip())
