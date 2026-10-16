"""The voice session state machine: explicit, observable transitions.

The assistant's voice behaviour is a small state machine. Making the states and the
legal transitions explicit - rather than leaving them implicit in a ``while True`` loop -
is what lets a failure be diagnosed ("it stopped in TRANSCRIBING") instead of guessed at,
and what lets the whole flow be tested without a microphone.

    IDLE / WAITING_FOR_WAKE_WORD
        -> WAKE_WORD_DETECTED        the wake-word engine fired
        -> STOPPED                   the session was asked to stop

    WAKE_WORD_DETECTED
        -> LISTENING_FOR_COMMAND     the microphone was handed over successfully

    LISTENING_FOR_COMMAND
        -> TRANSCRIBING              audio was captured

    TRANSCRIBING
        -> PROCESSING_REQUEST        a transcript came back
        -> IDLE                      nothing was heard; back to waiting for the wake word

    PROCESSING_REQUEST
        -> SPEAKING_RESPONSE         the responder produced a reply

    SPEAKING_RESPONSE
        -> IDLE                      the reply was spoken; back to waiting

Every state can also go to ``ERROR``, and ``ERROR`` always returns to ``IDLE`` (or
``STOPPED`` when the session is shutting down), so a failure never leaves the microphone
locked or the listener permanently stopped.
"""

from __future__ import annotations

from enum import Enum


class VoiceState(str, Enum):
    """One observable state of the voice session."""

    # Waiting for the wake word. The wake-word engine owns the microphone here.
    IDLE = "idle"
    # The wake word fired; the microphone is being handed to the command listener.
    WAKE_WORD_DETECTED = "wake_word_detected"
    # Capturing the spoken command.
    LISTENING_FOR_COMMAND = "listening_for_command"
    # Audio captured; speech-to-text is running.
    TRANSCRIBING = "transcribing"
    # The agent is producing a reply.
    PROCESSING_REQUEST = "processing_request"
    # The reply is being spoken aloud.
    SPEAKING_RESPONSE = "speaking_response"
    # A failure occurred. Always transient: the session recovers to IDLE.
    ERROR = "error"
    # The session is finished and the microphone is released.
    STOPPED = "stopped"


#: Legal transitions. Anything else is a programming error and is reported, not silently
#: allowed, because an unexpected transition is exactly the kind of thing that hides a
#: stuck listener.
TRANSITIONS: dict[VoiceState, frozenset[VoiceState]] = {
    VoiceState.IDLE: frozenset({
        VoiceState.WAKE_WORD_DETECTED,
        VoiceState.ERROR,
        VoiceState.STOPPED,
    }),
    VoiceState.WAKE_WORD_DETECTED: frozenset({
        VoiceState.LISTENING_FOR_COMMAND,
        VoiceState.ERROR,
        VoiceState.STOPPED,
    }),
    VoiceState.LISTENING_FOR_COMMAND: frozenset({
        VoiceState.TRANSCRIBING,
        VoiceState.ERROR,
        VoiceState.STOPPED,
    }),
    VoiceState.TRANSCRIBING: frozenset({
        VoiceState.PROCESSING_REQUEST,
        VoiceState.IDLE,                  # no command heard: back to waiting for the wake word
        VoiceState.ERROR,
        VoiceState.STOPPED,
    }),
    VoiceState.PROCESSING_REQUEST: frozenset({
        VoiceState.SPEAKING_RESPONSE,
        VoiceState.ERROR,
        VoiceState.STOPPED,
    }),
    VoiceState.SPEAKING_RESPONSE: frozenset({
        VoiceState.IDLE,
        VoiceState.ERROR,
        VoiceState.STOPPED,
    }),
    # ERROR is transient: it always recovers, so the listener is never left stopped.
    VoiceState.ERROR: frozenset({
        VoiceState.IDLE,
        VoiceState.STOPPED,
    }),
    VoiceState.STOPPED: frozenset(),
}


class IllegalTransition(RuntimeError):
    """A state change that the machine does not allow.

    Raised rather than ignored: an unexpected transition means the session's own logic is
    wrong, which is far more useful to know about than a listener that quietly wedges.
    """

    def __init__(self, current: VoiceState, target: VoiceState) -> None:
        self.current, self.target = current, target
        super().__init__(f"illegal voice state transition: {current.value} -> {target.value}")


def can_transition(current: VoiceState, target: VoiceState) -> bool:
    """Whether ``current -> target`` is a legal transition."""
    return target in TRANSITIONS.get(current, frozenset())


def transition(current: VoiceState, target: VoiceState) -> VoiceState:
    """Return ``target`` if the move is legal, else raise :class:`IllegalTransition`."""
    if not can_transition(current, target):
        raise IllegalTransition(current, target)
    return target


#: The states a caller can wait on to know the assistant is ready for voice.
LISTENING_STATES = frozenset({VoiceState.IDLE, VoiceState.LISTENING_FOR_COMMAND})
#: States in which the microphone is (or is about to be) held by the session.
MICROPHONE_STATES = frozenset({
    VoiceState.WAKE_WORD_DETECTED,
    VoiceState.LISTENING_FOR_COMMAND,
    VoiceState.TRANSCRIBING,
})
