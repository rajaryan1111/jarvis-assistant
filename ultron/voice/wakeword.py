"""Wake-word detection: configuration, the Porcupine engine, and honest diagnostics.

The assistant's wake word is **Ultron**. Ultron is *not* one of Porcupine's built-in
keywords, so it needs a custom ``.ppn`` model trained in the Picovoice Console. This
module therefore never claims wake-word support works: it reports exactly which of the
three requirements is missing -

1. the ``wakeword`` extra (``pvporcupine`` + ``pyaudio``),
2. ``PICOVOICE_ACCESS_KEY`` (a Picovoice Console account - external, never stored here),
3. a ``.ppn`` model file for the configured wake word,

- and :func:`setup_report` spells out the one command or console step that fixes each.
Nothing here reads, writes or logs an access key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: The product-facing wake word. This is the user-visible name; the repository, package
#: and module identifiers (``jarvis-assistant``, ``jarvis_logic.py``) are historical and
#: deliberately left alone so existing installs and scripts keep working.
WAKE_WORD = "ultron"

#: Alternative phrasings that are accepted as an activation. "Hey Ultron" is the natural
#: hands-free form; the engine matches whichever the trained model supports.
WAKE_WORD_ALIASES = ("ultron", "hey ultron")

#: Where the ``.ppn`` model is looked for, in order. The first existing file wins, so a
#: user can drop ``ultron.ppn`` in the project folder or point ``ULTRON_WAKE_MODEL`` at
#: one anywhere on disk.
DEFAULT_MODEL_NAMES = ("ultron.ppn", "hey-ultron.ppn")
MODEL_ENV_VAR = "ULTRON_WAKE_MODEL"
ACCESS_KEY_ENV_VAR = "PICOVOICE_ACCESS_KEY"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class WakeWordSetup:
    """What is configured, what is missing, and the exact step that fixes each gap."""

    wake_word: str
    extra_installed: bool
    access_key_present: bool
    model_path: Path | None
    missing: tuple[str, ...]
    steps: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing

    def describe(self) -> str:
        """A human-readable, secret-free status block."""
        lines = [f"Wake word: {self.wake_word}"]
        lines.append(f"  wakeword extra installed : {'yes' if self.extra_installed else 'NO'}")
        lines.append(f"  PICOVOICE_ACCESS_KEY set  : {'yes' if self.access_key_present else 'NO'}")
        lines.append(f"  .ppn model file          : {self.model_path or 'NOT FOUND'}")
        if self.ready:
            lines.append("  status                   : ready")
        else:
            lines.append("  status                   : wake-word listening is NOT available")
            for step in self.steps:
                lines.append(f"    - {step}")
        return "\n".join(lines)


def _extra_installed() -> bool:
    """Whether the optional wake-word dependencies are importable."""
    try:
        import pvporcupine  # noqa: F401
        import pyaudio  # noqa: F401
    except ImportError:
        return False
    return True


def resolve_model_path(explicit: str | None = None) -> Path | None:
    """The ``.ppn`` model to use, or ``None`` when there is none.

    An explicit path (``ULTRON_WAKE_MODEL``) wins; otherwise the conventional filenames
    are looked for next to the repository root.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    for name in DEFAULT_MODEL_NAMES:
        candidates.append(REPO_ROOT / name)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def setup_report(env: dict[str, str] | None = None) -> WakeWordSetup:
    """Inspect the configuration and report exactly what is missing.

    ``env`` is injectable so tests can exercise every combination without touching the
    real environment. The access key is only ever *tested for presence* - its value is
    never read into the report, logged or returned.
    """
    env = os.environ if env is None else env
    explicit = env.get(MODEL_ENV_VAR) or None
    model = resolve_model_path(explicit)
    extra = _extra_installed()
    key_present = bool(env.get(ACCESS_KEY_ENV_VAR, "").strip())

    missing: list[str] = []
    steps: list[str] = []
    if not extra:
        missing.append("wakeword extra")
        steps.append("install the optional dependencies: uv sync --extra wakeword")
    if not key_present:
        missing.append(ACCESS_KEY_ENV_VAR)
        steps.append("create a free AccessKey at https://console.picovoice.ai/ and put it in "
                     f".env as {ACCESS_KEY_ENV_VAR}=... (never commit it)")
    if model is None:
        missing.append("wake-word model")
        steps.append(
            f"train a custom '{WAKE_WORD}' keyword in the Picovoice Console "
            "(Porcupine > custom keywords), download the .ppn, and either save it as "
            f"{REPO_ROOT / DEFAULT_MODEL_NAMES[0]} or point {MODEL_ENV_VAR} at it"
        )
    return WakeWordSetup(
        wake_word=WAKE_WORD,
        extra_installed=extra,
        access_key_present=key_present,
        model_path=model,
        missing=tuple(missing),
        steps=tuple(steps),
    )


class WakeWordEngine:
    """Interface the voice session uses. Owns the microphone while waiting.

    ``start``/``stop`` are deliberately explicit about microphone ownership: the session
    releases the wake engine's stream before opening the command listener's, so the two
    never compete for the same device.
    """

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the wake word is heard. ``True`` means activated."""
        raise NotImplementedError

    @property
    def available(self) -> bool:
        raise NotImplementedError


class PorcupineWakeEngine(WakeWordEngine):
    """Real wake-word engine. Imports Porcupine lazily and fails loudly, not silently."""

    def __init__(self, *, access_key: str, model_path: Path, frame_handler=None) -> None:
        self._access_key = access_key
        self._model_path = Path(model_path)
        self._frame_handler = frame_handler   # optional callable(frame) for tests/UI
        self._porcupine = None
        self._stream = None
        self._pa = None

    @property
    def available(self) -> bool:
        return self._porcupine is not None and self._stream is not None

    def start(self) -> None:
        import struct

        import pvporcupine
        import pyaudio

        self._porcupine = pvporcupine.create(
            access_key=self._access_key, keyword_paths=[str(self._model_path)])
        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            rate=self._porcupine.sample_rate,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=self._porcupine.frame_length,
        )
        self._struct = struct

    def wait(self, timeout: float | None = None) -> bool:
        if self._porcupine is None or self._stream is None:
            raise RuntimeError("wake-word engine is not started")
        deadline = None if timeout is None else __import__("time").monotonic() + timeout
        while True:
            pcm = self._stream.read(self._porcupine.frame_length, exception_on_overflow=False)
            frame = self._struct.unpack_from("h" * self._porcupine.frame_length, pcm)
            if self._frame_handler is not None:
                self._frame_handler(frame)
            if self._porcupine.process(frame) >= 0:
                return True
            if deadline is not None and __import__("time").monotonic() >= deadline:
                return False

    def stop(self) -> None:
        """Release the microphone. Safe to call twice, and safe after a failed start."""
        for closer, target in ((self._stream, "stop_stream"), (self._stream, "close")):
            try:
                if closer is not None:
                    getattr(closer, target)()
            except Exception:  # noqa: BLE001, S110 - cleanup must not raise
                pass
        self._stream = None
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception:  # noqa: BLE001, S110
            pass
        self._pa = None
        try:
            if self._porcupine is not None:
                self._porcupine.delete()
        except Exception:  # noqa: BLE001, S110
            pass
        self._porcupine = None


class UnavailableWakeEngine(WakeWordEngine):
    """No wake-word engine available. The session reports why instead of pretending."""

    def __init__(self, setup: WakeWordSetup) -> None:
        self._setup = setup

    @property
    def available(self) -> bool:
        return False

    @property
    def setup(self) -> WakeWordSetup:
        return self._setup

    def start(self) -> None:
        raise RuntimeError(self._setup.describe())

    def stop(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> bool:
        raise RuntimeError(self._setup.describe())


def build_engine(env: dict[str, str] | None = None) -> WakeWordEngine:
    """The best available engine, or an :class:`UnavailableWakeEngine` that says why."""
    setup = setup_report(env)
    if not setup.ready:
        return UnavailableWakeEngine(setup)
    if setup.model_path is None:  # pragma: no cover - ready implies a model
        return UnavailableWakeEngine(setup)
    return PorcupineWakeEngine(
        access_key=(env or os.environ)[ACCESS_KEY_ENV_VAR],
        model_path=setup.model_path,
    )
