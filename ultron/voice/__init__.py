"""Voice input/output pipeline: recognition, language handling, response, speech.

Public surface:

* :func:`~ultron.voice.languages.detect_language` / :func:`~ultron.voice.languages.parse_language_command`
* :class:`~ultron.voice.transcribe.GoogleTranscriber`
* :class:`~ultron.voice.pipeline.VoicePipeline`
* :class:`~ultron.voice.session.VoiceSession` - the state machine that owns the microphone
* :mod:`ultron.voice.wakeword` - wake-word configuration, engine and setup diagnostics
* :mod:`ultron.voice.states` / :mod:`ultron.voice.diagnostics` - states and safe logging
"""

from ultron.voice.languages import (
    AUTO,
    DEFAULT_LANGUAGE,
    ENGLISH,
    HINDI,
    HINGLISH,
    SUPPORTED,
    detect_language,
    normalise_mode,
    parse_language_command,
    profile_for,
)
from ultron.voice.pipeline import VoicePipeline, VoiceTurn
from ultron.voice.session import SessionTurn, VoiceSession
from ultron.voice.states import VoiceState
from ultron.voice.transcribe import GoogleTranscriber, Transcription
from ultron.voice.wakeword import WAKE_WORD, build_engine, setup_report

__all__ = [
    "AUTO",
    "DEFAULT_LANGUAGE",
    "ENGLISH",
    "HINGLISH",
    "HINDI",
    "SUPPORTED",
    "WAKE_WORD",
    "GoogleTranscriber",
    "SessionTurn",
    "Transcription",
    "VoicePipeline",
    "VoiceSession",
    "VoiceState",
    "VoiceTurn",
    "build_engine",
    "detect_language",
    "normalise_mode",
    "parse_language_command",
    "profile_for",
    "setup_report",
]
