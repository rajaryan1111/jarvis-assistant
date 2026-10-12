"""Browser sessions, CSRF tokens and one-time login tickets.

All values are HMAC-signed with keys derived from the API token, so rotating
the token invalidates every session, CSRF token and ticket at once. Nothing
here reveals the API token itself.

Session cookie (format ``s2``)::

    s2.<session id>.<generation>.<issued at>.<expires at>.<HMAC>

The server accepts it only if the signature is valid, the generation equals the
current one in the persistent session state (:mod:`ultron.security.session_state`),
it was issued no later than now, has not expired, and is no older than the
*currently configured* session lifetime (lowering ``ULTRON_SESSION_HOURS`` shortens
existing sessions too). Revocation of individual sessions is checked separately
against the persistent state.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

_INT_RE = re.compile(r"^[0-9]{1,12}$")
_PART_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
CLOCK_SKEW_SECONDS = 60
MAX_COOKIE_CHARS = 512


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def secret_equals(a: str, b: str) -> bool:
    """Constant-time comparison that also works for non-ASCII input (never raises)."""
    return hmac.compare_digest(a.encode("utf-8", "surrogatepass"), b.encode("utf-8", "surrogatepass"))


def _derive(token: str, label: str) -> bytes:
    return hmac.new(token.encode("utf-8"), label.encode("utf-8"), hashlib.sha256).digest()


def session_label(session_id: str) -> str:
    """Short non-reversible label for logs and principals (never the id itself)."""
    return hashlib.sha256(f"ultron-session-label:{session_id}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class SessionClaims:
    session_id: str
    generation: str
    issued_at: int
    expires_at: int


class SessionSigner:
    COOKIE_NAME = "ultron_session"
    COOKIE_VERSION = "s2"
    TICKET_VERSION = "t2"

    def __init__(self, api_token: str, ttl_seconds: int, clock: Callable[[], float] = time.time) -> None:
        self._session_key = _derive(api_token, "ultron-session-v2")
        self._csrf_key = _derive(api_token, "ultron-csrf-v1")
        self._ticket_key = _derive(api_token, "ultron-login-ticket-v2")
        self.ttl_seconds = ttl_seconds
        self._clock = clock

    def _sign(self, key: bytes, payload: str) -> str:
        return _b64(hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest())

    # --- sessions ----------------------------------------------------------
    def issue(self, generation: str = "g0") -> tuple[str, str]:
        """Return ``(cookie_value, session_id)`` for a new session in ``generation``."""
        session_id = secrets.token_urlsafe(18)
        issued = int(self._clock())
        payload = f"{self.COOKIE_VERSION}.{session_id}.{generation}.{issued}.{issued + self.ttl_seconds}"
        return f"{payload}.{self._sign(self._session_key, payload)}", session_id

    def parse(self, cookie_value: str | None, generation: str = "g0") -> SessionClaims | None:
        """Validate a cookie completely (format, signature, generation, time). ``None`` if invalid."""
        if not cookie_value or len(cookie_value) > MAX_COOKIE_CHARS or cookie_value.count(".") != 5:
            return None
        version, session_id, cookie_gen, issued_raw, expires_raw, signature = cookie_value.split(".")
        if version != self.COOKIE_VERSION or not _PART_RE.match(session_id) or not _PART_RE.match(cookie_gen):
            return None
        if not _INT_RE.match(issued_raw) or not _INT_RE.match(expires_raw):
            return None
        payload = f"{version}.{session_id}.{cookie_gen}.{issued_raw}.{expires_raw}"
        if not secret_equals(signature, self._sign(self._session_key, payload)):
            return None
        if not secret_equals(cookie_gen, generation):
            return None  # revoked by "revoke all", rotation or a state reset
        issued, expires, now = int(issued_raw), int(expires_raw), self._clock()
        if issued > now + CLOCK_SKEW_SECONDS or expires <= now:
            return None
        if expires - issued > self.ttl_seconds or now - issued >= self.ttl_seconds:
            return None  # enforce the *current* lifetime server-side
        return SessionClaims(session_id, cookie_gen, issued, expires)

    def verify(self, cookie_value: str | None, generation: str = "g0") -> str | None:
        """Return the session id for a valid, unexpired cookie, else ``None``."""
        claims = self.parse(cookie_value, generation)
        return claims.session_id if claims else None

    # --- CSRF --------------------------------------------------------------
    def csrf_token(self, session_id: str) -> str:
        return self._sign(self._csrf_key, f"csrf.{session_id}")

    def check_csrf(self, session_id: str, value: str | None) -> bool:
        return bool(value) and secret_equals(value, self.csrf_token(session_id))

    # --- one-time login tickets (used by `ultron open-hud`) ----------------
    def issue_login_ticket(self, generation: str = "g0") -> str:
        payload = f"{self.TICKET_VERSION}.{int(self._clock())}.{secrets.token_urlsafe(12)}.{generation}"
        return f"{payload}.{self._sign(self._ticket_key, payload)}"

    def verify_login_ticket(
        self, ticket: str | None, max_age_seconds: int = 120, generation: str = "g0"
    ) -> tuple[str, int] | None:
        """Return ``(nonce, expires_at)`` if signature, generation and age are valid.

        Single use is enforced by the caller against the persistent session state.
        """
        if not ticket or len(ticket) > MAX_COOKIE_CHARS or ticket.count(".") != 4:
            return None
        version, issued_raw, nonce, ticket_gen, signature = ticket.split(".")
        if version != self.TICKET_VERSION or not _INT_RE.match(issued_raw) or not _PART_RE.match(nonce):
            return None
        payload = f"{version}.{issued_raw}.{nonce}.{ticket_gen}"
        if not secret_equals(signature, self._sign(self._ticket_key, payload)):
            return None
        if not secret_equals(ticket_gen, generation):
            return None
        issued = int(issued_raw)
        age = self._clock() - issued
        if not -5 <= age <= max_age_seconds:
            return None
        return nonce, issued + max_age_seconds


class UsedNonces:
    """In-memory set of consumed values with a retention window (not persistent; see session_state)."""

    def __init__(self, retention_seconds: int = 600, clock: Callable[[], float] = time.time) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._retention = retention_seconds
        self._clock = clock

    def _prune(self, now: float) -> None:
        for key, ts in list(self._seen.items()):
            if now - ts > self._retention:
                del self._seen[key]

    def consume(self, nonce: str) -> bool:
        now = self._clock()
        with self._lock:
            self._prune(now)
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
            return True

    def seen(self, nonce: str) -> bool:
        with self._lock:
            self._prune(self._clock())
            return nonce in self._seen


class LoginThrottle:
    """Simple failure-based throttle for the login endpoint."""

    def __init__(self, max_failures: int = 10, window_seconds: int = 300, clock: Callable[[], float] = time.time):
        self._failures: list[float] = []
        self._lock = threading.Lock()
        self.max_failures = max_failures
        self.window = window_seconds
        self._clock = clock

    def _prune(self, now: float) -> None:
        self._failures = [t for t in self._failures if now - t < self.window]

    def allowed(self) -> bool:
        with self._lock:
            self._prune(self._clock())
            return len(self._failures) < self.max_failures

    def record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._failures.append(now)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
