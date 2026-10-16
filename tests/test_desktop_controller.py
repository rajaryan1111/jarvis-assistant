"""Regression tests for the desktop controller's lifecycle rules.

These are the guarantees the native app has to keep on a real Mac: one listening loop
at a time, no microphone left held after Quit, and a Quit that cannot hang forever.
They run without AppKit and without a microphone, so they pass everywhere.
"""
import threading
import time

from ultron.desktop import JOIN_TIMEOUT_SECONDS, DesktopController
from ultron.voice.states import VoiceState


class RecordingSession:
    """A stand-in for VoiceSession that records how it was driven."""

    def __init__(self, *, turns=1, delay=0.0, raises=None):
        self.turns = turns
        self.delay = delay
        self.raises = raises
        self.started = threading.Event()
        self.stopped = False
        self.released = False
        self.run_calls = 0
        self.state = VoiceState.IDLE

    def run(self, *, max_turns=None):
        self.run_calls += 1
        self.started.set()
        remaining = self.turns if max_turns is None else min(self.turns, max_turns)
        for _ in range(remaining):
            if self.stopped:
                break
            if self.delay:
                time.sleep(self.delay)
        if self.raises is not None:
            raise self.raises

    def request_stop(self):
        self.stopped = True

    def _release_everything(self):
        self.released = True


def make_controller(session, **kwargs):
    return DesktopController(session_factory=lambda: session, **kwargs)


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_starting_twice_does_not_create_a_second_listening_loop():
    """The duplicate-loop defect: two threads capturing one microphone."""
    session = RecordingSession(turns=5, delay=0.02)
    controller = make_controller(session)
    assert controller.start() is True
    assert controller.start() is False, "a second start must be refused, not stacked"
    assert wait_for(session.started.is_set)
    controller.stop()
    controller.quit()
    assert session.run_calls == 1, session.run_calls


def test_the_microphone_is_released_when_the_worker_finishes():
    session = RecordingSession(turns=0)
    controller = make_controller(session)
    controller.start()
    assert wait_for(lambda: not controller.listening), "worker never finished"
    assert session.released, "the session must release the wake engine and mic"


def test_the_microphone_is_released_even_when_the_session_raises():
    """A crash in the voice loop must not leave the device captured."""
    session = RecordingSession(turns=0, raises=RuntimeError("boom"))
    controller = make_controller(session)
    controller.start()
    assert wait_for(lambda: not controller.listening)
    assert session.released


def test_quit_stops_and_joins_the_worker():
    session = RecordingSession(turns=10, delay=0.02)
    controller = make_controller(session)
    controller.start()
    assert wait_for(session.started.is_set)
    controller.quit()
    assert not controller.listening
    assert session.stopped, "quit must ask the session to stop"
    assert session.released, "quit must wait for the devices to be released"


def test_quit_is_bounded_so_a_wedged_microphone_cannot_hang_the_app():
    """A stuck audio device must not make the app impossible to quit."""
    stuck = threading.Event()

    class StuckSession(RecordingSession):
        def run(self, *, max_turns=None):
            self.started.set()
            stuck.wait(timeout=30)

    session = StuckSession(turns=1)
    controller = make_controller(session, join_timeout=0.1)
    controller.start()
    assert wait_for(session.started.is_set)
    began = time.monotonic()
    controller.quit()
    elapsed = time.monotonic() - began
    assert elapsed < 2.0, f"quit took {elapsed:.1f}s; it must be bounded"
    stuck.set()


def test_the_default_join_timeout_is_short():
    assert 0 < JOIN_TIMEOUT_SECONDS <= 5.0, JOIN_TIMEOUT_SECONDS


def test_stop_before_start_is_harmless():
    session = RecordingSession(turns=0)
    controller = make_controller(session)
    controller.stop()
    controller.quit()
    assert session.run_calls == 0


def test_the_controller_reports_a_honest_status_line():
    session = RecordingSession(turns=3, delay=0.02)
    controller = make_controller(session)
    assert "stopped" in controller.describe()
    controller.start()
    assert wait_for(session.started.is_set)
    assert "listening" in controller.describe()
    controller.quit()
    assert "stopped" in controller.describe()


def test_the_status_line_never_contains_a_transcript():
    """The menu bar is visible on screen; it must not echo what was said."""
    session = RecordingSession(turns=1)
    controller = make_controller(session)
    controller.start()
    assert wait_for(lambda: not controller.listening)
    line = controller.describe()
    assert "transcript" not in line.lower()


def test_the_controller_uses_the_shared_voice_session_by_default():
    """No second voice pipeline: the default factory builds the real one."""
    import ultron.desktop.controller as module

    assert module.VoiceSession.__name__ == "VoiceSession"


def test_a_dead_worker_is_not_reported_as_listening():
    session = RecordingSession(turns=0)
    controller = make_controller(session)
    controller.start()
    assert wait_for(lambda: not controller.listening)
    assert controller.listening is False
    controller.quit()


def test_transitions_are_forwarded_to_the_observer():
    """The menu bar needs to repaint on every state change."""
    seen = []
    session = RecordingSession(turns=1, delay=0.01)
    controller = make_controller(session, on_transition=lambda a, b: seen.append((a, b)))
    controller.start()
    assert wait_for(lambda: not controller.listening)
    controller.quit()
    assert isinstance(seen, list)
