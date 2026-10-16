"""Language profiles, transcript classification and spoken language-switch parsing.

The voice path recognises English, Hindi and Hinglish (romanised Hindi). Two things are
deliberately *not* invented here:

* No provider capability is assumed. ``stt_locale`` is the BCP-47 tag handed to the
  speech recogniser; Google's recogniser accepts ``en-IN`` and ``hi-IN``. Hinglish has no
  dedicated locale, so it is recognised with ``en-IN`` (which returns romanised text) and
  classified from the transcript.
* ``tts_voice`` is ``None`` by default: speech output keeps whatever voice the owner
  configured for the ``audio.speak`` tool. A per-language voice name can be supplied when
  the owner has one installed; nothing is auto-selected, because available ``say`` voices
  are machine-specific and cannot be probed without running the binary.

``detect_language`` is a heuristic and is conservative: anything it cannot classify
confidently is treated as English, which is the historical behaviour. Detection is
transcript-based, so in ``auto`` mode the recogniser locale follows the previous turn
(see :mod:`ultron.voice.pipeline`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ENGLISH = "en"
HINDI = "hi"
HINGLISH = "hinglish"

#: Languages with a dedicated recogniser locale.
SUPPORTED = (ENGLISH, HINDI, HINGLISH)
#: Historical behaviour: English (India) unless the owner or the pipeline says otherwise.
DEFAULT_LANGUAGE = ENGLISH
#: Opt-in mode: classify each transcript and follow it with the recogniser locale.
AUTO = "auto"
#: Everything ``ULTRON_VOICE_LANGUAGE`` may be set to.
MODES = (*SUPPORTED, AUTO)

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_WORD_RE = re.compile(r"[a-z']+")

# Common romanised-Hindi function words. Short and high-signal on purpose: a single
# ambiguous word ("ji", "bhai") must not flip the language, hence the threshold below.
_HINGLISH_MARKERS = frozenset({
    "kya", "hai", "hain", "nahi", "nahin", "haan", "mera", "meri", "mere",
    "tum", "tumhara", "aap", "aapka", "kar", "karo", "karna", "raha", "rahi",
    "kaise", "kyun", "kab", "kahan", "kaun", "achha", "accha", "theek", "thik",
    "bahut", "abhi", "matlab", "chahiye", "chahie", "bolo", "bol",
})
_HINGLISH_THRESHOLD = 2

# Filler a spoken language switch is allowed to carry ("jarvis, please switch to hindi").
_SWITCH_FILLER = ("jarvis", "please", "sir", "now", "can", "you", "could", "you",
                  "to", "into", "in", "mein", "may", "mode", "language", "speak", "talk", "use", "switch",
                  # Hindi/Hinglish imperatives a spoken switch is allowed to carry.
                  "bolo", "bol", "kaho", "batao", "bhasha", "baat", "karo")


@dataclass(frozen=True)
class LanguageProfile:
    """One supported language: how it is recognised and (optionally) spoken."""

    code: str
    label: str
    stt_locale: str
    tts_voice: str | None = None


PROFILES = {
    ENGLISH: LanguageProfile(ENGLISH, "English", "en-IN"),
    HINDI: LanguageProfile(HINDI, "Hindi", "hi-IN"),
    # Romanised Hindi is transcribed by the English locale; there is no Hinglish locale.
    HINGLISH: LanguageProfile(HINGLISH, "Hinglish", "en-IN"),
}


def profile_for(code: str) -> LanguageProfile:
    """The profile for a language code, falling back to the default (never raises)."""
    return PROFILES.get(code, PROFILES[DEFAULT_LANGUAGE])


def normalise_mode(value: str | None, env: dict[str, str] | None = None) -> str:
    """Resolve a configured language mode. Unknown or empty values fall back to English."""
    raw = value if value is not None else (env or {}).get("ULTRON_VOICE_LANGUAGE", "")
    candidate = (raw or "").strip().lower()
    return candidate if candidate in MODES else DEFAULT_LANGUAGE


def detect_language(text: str) -> str:
    """Classify a transcript as ``en``, ``hi`` or ``hinglish``.

    Devanagari script means Hindi. Two or more romanised-Hindi marker words mean
    Hinglish. Everything else - including empty text - is English, the safe default.
    """
    if not text or not text.strip():
        return DEFAULT_LANGUAGE
    if _DEVANAGARI_RE.search(text):
        return HINDI
    words = set(_WORD_RE.findall(text.lower()))
    if len(words & _HINGLISH_MARKERS) >= _HINGLISH_THRESHOLD:
        return HINGLISH
    return ENGLISH


def parse_language_command(text: str) -> str | None:
    """A spoken request to change language, or ``None`` for anything else.

    Deliberately strict: only short utterances made of switch words and a language name
    are treated as commands, so ordinary questions that merely mention a language
    ("how do I say water in hindi") keep flowing to the normal command handler.
    """
    if not text:
        return None
    words = _WORD_RE.findall(text.lower())
    if not words or len(words) > 8:
        return None
    names = {HINDI, HINGLISH, ENGLISH, "hindi", "hinglish", "english"}
    named = set(words) & names
    if not named:
        return None
    if not set(words) <= (set(_SWITCH_FILLER) | named):
        return None
    return HINDI if "hindi" in named else HINGLISH if HINGLISH in named else ENGLISH
