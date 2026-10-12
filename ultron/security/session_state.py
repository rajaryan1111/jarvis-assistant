"""Persistent session state: what must stay revoked across server restarts.

One small JSON file (default ``<data dir>/session_state.json``, mode ``0600``) holds:

``generation``
    A random value embedded in every HUD session cookie and login link. Bumping it
    ("revoke all", token rotation, ``sessions reset``) invalidates every outstanding
    session and link at once. If the file is *missing* a fresh generation is created, so
    deleting the file logs everybody out; it can never resurrect an old session.
``revoked``
    SHA-256 digests of logged-out session ids, kept until the cookie would have expired.
``used_tickets``
    Digests of consumed one-time login-link nonces, so a link cannot be replayed after a
    restart either.
``retired_tokens``
    Fingerprints (SHA-256) of API/client tokens replaced by ``token rotate`` / ``revoke``.
    The server refuses a retired token even if it is still configured somewhere (e.g. an old
    ``ULTRON_API_TOKEN`` left in ``.env``, which overrides the token file).

No secret is stored: session ids, nonces and tokens appear only as SHA-256 digests (all of
them carry at least 96 bits of randomness, so the digests cannot be reversed).

**Fail closed.** If the file exists but cannot be read, is not a regular file owned by this
user with private permissions, or does not match the exact schema, :class:`SessionStateError`
is raised and the server rejects *every* session, login and bearer request with a recovery
message. Owner recovery (needs a shell as the same OS user): ``python -m ultron sessions reset``.

This protects against restarts, crashes, accidental edits and stale state. It does not
protect against an attacker who controls this OS account: they can read the token file too.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ultron.security.paths import atomic_write_text

STATE_VERSION = 1
STATE_FILE_NAME = "session_state.json"
MAX_STATE_BYTES = 512 * 1024
MAX_REVOKED_ENTRIES = 5000  # more than this (after pruning) -> new generation instead of growing
MAX_RETIRED_TOKENS = 100
PRUNE_GRACE_SECONDS = 60
RECOVERY_HINT = (
    "Owner recovery: run `python -m ultron sessions reset` in a terminal on this computer "
    "(this logs out every HUD session), then log in again."
)

_KEYS = frozenset({"version", "generation", "revoked", "used_tickets", "retired_tokens", "updated_at"})
_GENERATION_RE = re.compile(r"^[A-Za-z0-9_-]{22,64}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

try:  # POSIX advisory locking (macOS / Linux). Windows is not a supported host.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX
    _fcntl = None  # type: ignore[assignment]


class SessionStateError(Exception):
    """The persistent session state is unavailable or invalid. The message is safe to show."""

    def __init__(self, problem: str) -> None:
        super().__init__(f"{problem} {RECOVERY_HINT}")
        self.problem = problem
        self.user_message = f"Sign-in is unavailable: {problem} {RECOVERY_HINT}"


def _digest(label: str, value: str) -> str:
    return hashlib.sha256(f"{label}:{value}".encode()).hexdigest()


def session_digest(session_id: str) -> str:
    return _digest("ultron-session-id-v1", session_id)


def ticket_digest(nonce: str) -> str:
    return _digest("ultron-login-ticket-v1", nonce)


def token_fingerprint(token: str) -> str:
    """Stable, non-reversible identifier of a token (for the retired-token list and logs)."""
    return _digest("ultron-token-fp-v1", token)


def new_generation() -> str:
    return secrets.token_urlsafe(24)


@dataclass
class SessionState:
    generation: str
    revoked: dict[str, int] = field(default_factory=dict)
    used_tickets: dict[str, int] = field(default_factory=dict)
    retired_tokens: dict[str, int] = field(default_factory=dict)
    updated_at: int = 0

    def is_revoked(self, session_id: str) -> bool:
        return session_digest(session_id) in self.revoked

    def is_retired(self, token: str) -> bool:
        return token_fingerprint(token) in self.retired_tokens

    def to_json(self) -> str:
        return json.dumps({
            "version": STATE_VERSION,
            "generation": self.generation,
            "revoked": self.revoked,
            "used_tickets": self.used_tickets,
            "retired_tokens": self.retired_tokens,
            "updated_at": self.updated_at,
        }, indent=1, sort_keys=True) + "\n"


def _digest_map(value: object, name: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise SessionStateError(f"the session state file is corrupt ('{name}' is not an object).")
    out: dict[str, int] = {}
    for key, ts in value.items():
        if not isinstance(key, str) or not _DIGEST_RE.match(key):
            raise SessionStateError(f"the session state file is corrupt (bad entry in '{name}').")
        if isinstance(ts, bool) or not isinstance(ts, int) or ts < 0:
            raise SessionStateError(f"the session state file is corrupt (bad timestamp in '{name}').")
        out[key] = ts
    return out


def parse_state(text: str) -> SessionState:
    """Strictly validate the file contents. Anything unexpected is corruption (fail closed)."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise SessionStateError("the session state file is corrupt (not valid JSON).") from exc
    if not isinstance(data, dict) or set(data) != _KEYS:
        raise SessionStateError("the session state file is corrupt (unexpected structure).")
    if data["version"] != STATE_VERSION or isinstance(data["version"], bool):
        raise SessionStateError("the session state file has an unsupported version.")
    generation = data["generation"]
    if not isinstance(generation, str) or not _GENERATION_RE.match(generation):
        raise SessionStateError("the session state file is corrupt (bad generation).")
    updated_at = data["updated_at"]
    if isinstance(updated_at, bool) or not isinstance(updated_at, int) or updated_at < 0:
        raise SessionStateError("the session state file is corrupt (bad timestamp).")
    return SessionState(
        generation=generation,
        revoked=_digest_map(data["revoked"], "revoked"),
        used_tickets=_digest_map(data["used_tickets"], "used_tickets"),
        retired_tokens=_digest_map(data["retired_tokens"], "retired_tokens"),
        updated_at=updated_at,
    )


class SessionStateStore:
    """Thread- and process-safe access to the session state file.

    Reads are cached and re-validated whenever the file changes on disk (so a ``token
    rotate`` or ``sessions revoke-all`` from the CLI takes effect in a running server
    immediately). Writes are read-modify-write under an exclusive ``flock`` and replace the
    file atomically.
    """

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.RLock()
        self._cache: tuple[tuple[int, int, int], SessionState] | None = None

    # ------------------------------------------------------------------ helpers
    @property
    def _lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".lock")

    def _now(self) -> int:
        return int(self._clock())

    def _ensure_dir(self) -> None:
        parent = self.path.parent
        if parent.exists():
            return
        try:
            parent.mkdir(parents=True, mode=0o700)
            os.chmod(parent, 0o700)
        except OSError as exc:
            raise SessionStateError(f"the data directory could not be created ({type(exc).__name__}).") from exc

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        self._ensure_dir()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self._lock_path, flags, 0o600)
        except OSError as exc:
            raise SessionStateError(f"the session state lock could not be opened ({type(exc).__name__}).") from exc
        try:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_EX)
            yield
        finally:
            if _fcntl is not None:
                with contextlib.suppress(OSError):
                    _fcntl.flock(fd, _fcntl.LOCK_UN)
            os.close(fd)

    def _stat(self) -> os.stat_result | None:
        try:
            return os.lstat(self.path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SessionStateError(f"the session state file could not be checked ({type(exc).__name__}).") from exc

    def _check_file(self, st: os.stat_result) -> None:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise SessionStateError("the session state file is not a regular file.")
        if sys.platform != "win32":
            if st.st_uid != os.getuid():
                raise SessionStateError("the session state file is owned by another user.")
            if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise SessionStateError(
                    f"the session state file is accessible by other users (fix: chmod 600 '{self.path}')."
                )
        if st.st_size > MAX_STATE_BYTES:
            raise SessionStateError("the session state file is unexpectedly large.")

    def _read(self, st: os.stat_result) -> SessionState:
        self._check_file(st)
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.path, flags)
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                text = handle.read(MAX_STATE_BYTES + 1)
        except (OSError, UnicodeDecodeError) as exc:
            raise SessionStateError(f"the session state file could not be read ({type(exc).__name__}).") from exc
        return parse_state(text)

    def _write(self, state: SessionState) -> None:
        state.updated_at = self._now()
        try:
            atomic_write_text(self.path, state.to_json(), mode=0o600)
        except OSError as exc:
            raise SessionStateError(f"the session state file could not be written ({type(exc).__name__}).") from exc
        self._cache = None

    def _prune(self, state: SessionState) -> None:
        cutoff = self._now() - PRUNE_GRACE_SECONDS
        state.revoked = {k: v for k, v in state.revoked.items() if v >= cutoff}
        state.used_tickets = {k: v for k, v in state.used_tickets.items() if v >= cutoff}
        if len(state.revoked) + len(state.used_tickets) > MAX_REVOKED_ENTRIES:
            # Fail safe: log everybody out rather than letting the file grow without bound.
            state.generation = new_generation()
            state.revoked.clear()
            state.used_tickets.clear()
        if len(state.retired_tokens) > MAX_RETIRED_TOKENS:
            newest = sorted(state.retired_tokens.items(), key=lambda kv: kv[1])[-MAX_RETIRED_TOKENS:]
            state.retired_tokens = dict(newest)

    def _mutate(self, change: Callable[[SessionState], object]) -> object:
        with self._lock, self._file_lock():
            st = self._stat()
            state = SessionState(generation=new_generation()) if st is None else self._read(st)
            result = change(state)
            self._prune(state)
            self._write(state)
            return result

    # ------------------------------------------------------------------ public API
    def load(self) -> SessionState:
        """Current state. Creates a fresh one (new generation) if the file does not exist."""
        with self._lock:
            st = self._stat()
            if st is None:
                self._mutate(lambda _state: None)  # create under the lock (another process may race us)
                st = self._stat()
                if st is None:
                    raise SessionStateError("the session state file could not be created.")
            signature = (st.st_mtime_ns, st.st_size, st.st_ino)
            if self._cache is not None and self._cache[0] == signature:
                return self._cache[1]
            state = self._read(st)
            self._cache = (signature, state)
            return state

    def revoke_session(self, session_id: str, expires_at: int) -> None:
        def change(state: SessionState) -> None:
            state.revoked[session_digest(session_id)] = int(expires_at)

        self._mutate(change)

    def consume_ticket(self, nonce: str, expires_at: int) -> bool:
        """Record a login-link nonce as used. False if it was already used (replay)."""

        def change(state: SessionState) -> bool:
            key = ticket_digest(nonce)
            if key in state.used_tickets:
                return False
            state.used_tickets[key] = int(expires_at)
            return True

        return bool(self._mutate(change))

    def revoke_all(self) -> None:
        """Invalidate every HUD session and outstanding login link (new generation)."""

        def change(state: SessionState) -> None:
            state.generation = new_generation()
            state.revoked.clear()
            state.used_tickets.clear()

        self._mutate(change)

    def retire_tokens(self, tokens: Iterable[str], *, revoke_sessions: bool) -> None:
        """Never accept these tokens again; optionally also log out every session."""
        fingerprints = [token_fingerprint(t) for t in tokens if t]
        now = self._now()

        def change(state: SessionState) -> None:
            for fp in fingerprints:
                state.retired_tokens[fp] = now
            if revoke_sessions:
                state.generation = new_generation()
                state.revoked.clear()
                state.used_tickets.clear()

        self._mutate(change)

    def reset(self) -> bool:
        """Owner recovery: replace the state with a fresh one (logs everybody out).

        Retired-token fingerprints are kept if they can still be parsed from the old file.
        Returns True if they were kept, False if they had to be discarded.
        """
        with self._lock, self._file_lock():
            kept: dict[str, int] = {}
            salvaged = True
            st = self._stat()
            if st is not None:
                try:
                    kept = self._read(st).retired_tokens
                except SessionStateError:
                    salvaged = False
                    with contextlib.suppress(OSError):
                        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                            os.unlink(self.path)  # never write through a planted symlink
            self._write(SessionState(generation=new_generation(), retired_tokens=kept))
            return salvaged
