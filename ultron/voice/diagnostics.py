"""Structured, secret-free diagnostics for the voice session.

Every pipeline transition is recorded with a timestamp and a *category*, never with the
content that produced it. What may be logged:

* the state and the transition,
* how long a stage took,
* an error *category* (timeout, no speech, permission denied, device missing, ...),
* counts and lengths - never the transcript, never the reply, never raw audio.

That is enough to answer "where did it stop?" without turning a log file into a
transcript of the owner's conversations or a place a credential could land.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ultron.voice.states import VoiceState

#: Error categories. Deliberately coarse: they name *what kind* of thing happened so a
#: failure can be reported usefully without quoting a message that might carry a secret.
CAT_DEVICE_MISSING = "device_missing"        # no microphone / no audio device
CAT_PERMISSION_DENIED = "permission_denied"  # macOS refused the microphone
CAT_NO_SPEECH = "no_speech"                  # silence or a timeout
CAT_RECOGNITION = "recognition"              # speech-to-text failed
CAT_PROVIDER = "provider"                    # the model/provider failed
CAT_SPEECH_OUTPUT = "speech_output"          # TTS failed
CAT_WAKE_ENGINE = "wake_engine"              # wake-word engine failed
CAT_INTERNAL = "internal"                    # anything unexpected


@dataclass(frozen=True)
class VoiceEvent:
    """One recorded transition. ``detail`` is already safe to print."""

    state: VoiceState
    category: str | None = None
    detail: str = ""
    elapsed_ms: int | None = None
    at: float = 0.0

    def render(self) -> str:
        """A single log line. Never contains a transcript, a reply or a secret."""
        parts = [f"state={self.state.value}"]
        if self.category:
            parts.append(f"category={self.category}")
        if self.elapsed_ms is not None:
            parts.append(f"took={self.elapsed_ms}ms")
        if self.detail:
            parts.append(self.detail)
        return " ".join(parts)


class VoiceLogger:
    """Records transitions with timestamps and keeps a bounded, printable history."""

    def __init__(self, *, clock=time.monotonic, sink=None, keep: int = 200) -> None:
        self._clock = clock
        self._sink = sink          # optional callable(line: str) -> None
        self._keep = keep
        self._events: list[VoiceEvent] = []
        self._stage_started: float | None = None

    # -- recording ---------------------------------------------------------
    def _emit(self, event: VoiceEvent) -> VoiceEvent:
        self._events.append(event)
        if len(self._events) > self._keep:
            del self._events[: len(self._events) - self._keep]
        if self._sink is not None:
            try:
                self._sink(event.render())
            except Exception:  # noqa: BLE001, S110 - a broken sink must never stop the session
                return event
        return event

    def enter(self, state: VoiceState, *, category: str | None = None, detail: str = "") -> VoiceEvent:
        """Record entering ``state`` and start timing the stage that begins there."""
        elapsed = None
        if self._stage_started is not None:
            elapsed = int((self._clock() - self._stage_started) * 1000)
        self._stage_started = self._clock()
        return self._emit(VoiceEvent(state=state, category=category, detail=detail,
                                     elapsed_ms=elapsed, at=time.time()))

    def note(self, category: str, detail: str = "") -> VoiceEvent:
        """Record something that happened *inside* the current state, without moving."""
        return self._emit(VoiceEvent(state=self.current, category=category, detail=detail,
                                     at=time.time()))

    # -- reading -----------------------------------------------------------
    @property
    def current(self) -> VoiceState:
        for event in reversed(self._events):
            return event.state
        return VoiceState.IDLE

    @property
    def events(self) -> tuple[VoiceEvent, ...]:
        return tuple(self._events)

    def render(self) -> str:
        """The full history as printable lines."""
        return "\n".join(e.render() for e in self._events)

    def last_error(self) -> VoiceEvent | None:
        for event in reversed(self._events):
            if event.category is not None:
                return event
        return None
