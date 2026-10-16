"""Desktop front-end controller for the Ultron voice assistant.

This module owns the *lifecycle* of the voice session - starting it, stopping it,
refusing to start it twice, and releasing the microphone and wake-word engine on quit.
It contains no GUI code at all, which is deliberate: the lifecycle rules (no duplicate
loops, no leaked microphone, a Quit that cannot hang) are the parts that must be
tested, and they are testable without a Mac.

``macos/ultron_menubar.py`` is a thin AppKit shell that drives this controller, so the
native app and the terminal loop share one implementation rather than two.
"""

from __future__ import annotations

import threading

from ultron.voice.diagnostics import VoiceLogger
from ultron.voice.session import VoiceSession
from ultron.voice.states import VoiceState
from ultron.voice.transcribe import GoogleTranscriber
from ultron.voice.wakeword import WAKE_WORD, build_engine

#: How long Quit waits for the voice thread to let go of the microphone before giving
#: up on it. Bounded on purpose: a stuck audio device must not make the app unquittable.
JOIN_TIMEOUT_SECONDS = 5.0


class DesktopController:
    """Owns one voice session and the thread that runs it.

    The controller is the single owner of the microphone. Nothing else in the process
    opens a capture stream, which is what keeps the wake-word listener and the speech
    recogniser from fighting over the same device.
    """

    def __init__(
        self,
        *,
        session_factory=None,
        on_transition=None,
        logger=None,
        join_timeout: float = JOIN_TIMEOUT_SECONDS,
    ) -> None:
        self._session_factory = session_factory or self._default_session
        self._on_transition = on_transition
        self._logger = logger or VoiceLogger()
        self._join_timeout = join_timeout
        self._session: VoiceSession | None = None
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._started = False
        self._stopping = False

    # ------------------------------------------------------------------ the session
    def _default_session(self) -> VoiceSession:
        session = VoiceSession(
            wake_engine=build_engine(),
            transcriber=GoogleTranscriber(),
            responder=None,
            speaker=None,
            logger=self._logger,
            on_transition=self._handle_transition,
        )
        return session

    def _handle_transition(self, previous: VoiceState, current: VoiceState) -> None:
        """Mirror session transitions to whoever wants them (a menu, a log, a test)."""
        if self._on_transition is not None:
            self._on_transition(previous, current)

    # ------------------------------------------------------------------ public API
    @property
    def session(self) -> VoiceSession | None:
        return self._session

    @property
    def listening(self) -> bool:
        """True while the voice thread is alive. Never true because a flag was set."""
        return self._worker is not None and self._worker.is_alive()

    def start(self) -> bool:
        """Start listening. Returns False, and starts nothing, if already running.

        Refusing a second start is what prevents duplicate loops and two threads
        capturing the same microphone - the original defect in this repository.
        """
        with self._lock:
            if self._started and self.listening:
                return False
            self._stopping = False
            session = self._session_factory()
            self._session = session
            worker = threading.Thread(target=self._run, args=(session,), name="ultron-voice")
            worker.daemon = True
            self._worker = worker
            self._started = True
            worker.start()
            return True

    def stop(self) -> None:
        """Ask the session to stop. Safe to call when it is not running."""
        session = self._session
        if session is not None:
            session.request_stop()

    def quit(self) -> None:
        """Stop the voice thread and wait for it to release the microphone.

        The join is bounded by ``join_timeout`` so a wedged audio device cannot make
        the application impossible to quit.
        """
        self._stopping = True
        self.stop()
        worker = self._worker
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=self._join_timeout)
        self._worker = None
        self._session = None
        self._started = False

    # ------------------------------------------------------------------ internals
    def _run(self, session: VoiceSession) -> None:
        """Worker body. Always releases the audio devices, even on failure."""
        try:
            session.run()
        except Exception:  # noqa: BLE001 - a dead voice thread must not kill the UI
            self._logger.note("internal", "voice worker failed")
        finally:
            session._release_everything()
            with self._lock:
                self._started = False

    # ------------------------------------------------------------------ diagnostics
    def describe(self) -> str:
        """One-line status for a menu or a log. Never contains a transcript."""
        state = self._session.state if self._session is not None else VoiceState.IDLE
        running = "listening" if self.listening else "stopped"
        return f"Ultron ({WAKE_WORD}): {running}, state {state.name}"


__all__ = ["DesktopController", "JOIN_TIMEOUT_SECONDS"]
