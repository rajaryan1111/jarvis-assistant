"""Voice input/output pipeline: recognition, language handling, response, speech.

Public surface:

* :func:`~ultron.voice.languages.detect_language` / :func:`~ultron.voice.languages.parse_language_command`
* :class:`~ultron.voice.transcribe.GoogleTranscriber`
* :class:`~ultron.voice.pipeline.VoicePipeline`
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
from ultron.voice.transcribe import GoogleTranscriber, Transcription

__all__ = [
    "AUTO", "DEFAULT_LANGUAGE", "ENGLISH", "HINGLISH", "HINDI", "SUPPORTED",
    "GoogleTranscriber", "Transcription", "VoicePipeline", "VoiceTurn",
    "detect_language", "normalise_mode", "parse_language_command", "profile_for",
]
