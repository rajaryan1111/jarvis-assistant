"""Browser sessions, CSRF tokens and one-time login tickets.

All values are HMAC-signed with keys derived from the API token, so rotating
the token invalidates every session, CSRF token and ticket at once. Nothing
here reveals the API token itself.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _derive(token: str, label: str) -> bytes:
    return hmac.new(token.encode("utf-8"), label.encode("utf-8"), hashlib.sha256).digest()


class SessionSigner:
    COOKIE_NAME = "ultron_session"

    def __init__(self, api_token: str, ttl_seconds: int, clock: Callable[[], float] = time.time) -> None:
        self._session_key = _derive(api_token, "ultron-session-v1")
        self._csrf_key = _derive(api_token, "ultron-csrf-v1")
        self._ticket_key = _derive(api_token, "ultron-login-ticket-v1")
        self.ttl_seconds = ttl_seconds
        self._clock = clock

    # --- sessions ----------------------------------------------------------
    def _sign(self, key: bytes, payload: str) -> str:
        return _b64(hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest())

    def issue(self) -> tuple[str, str]:
        """Return ``(cookie_value, session_id)``."""
        session_id = secrets.token_urlsafe(18)
        expires = int(self._clock()) + self.ttl_seconds
        payload = f"{session_id}.{expires}"
        return f"{payload}.{self._sign(self._session_key, payload)}", session_id

    def verify(self, cookie_value: str | None) -> str | None:
        """Return the session id for a valid, unexpired cookie, else ``None``."""
        if not cookie_value or cookie_value.count(".") != 2:
            return None
        session_id, expires_raw, signature = cookie_value.split(".")
        expected = self._sign(self._session_key, f"{session_id}.{expires_raw}")
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            expires = int(expires_raw)
        except ValueError:
            return None
        return session_id if expires > self._clock() else None

    # --- CSRF --------------------------------------------------------------
    def csrf_token(self, session_id: str) -> str:
        return self._sign(self._csrf_key, f"csrf.{session_id}")

    def check_csrf(self, session_id: str, value: str | None) -> bool:
        return bool(value) and hmac.compare_digest(value, self.csrf_token(session_id))

    # --- one-time login tickets (used by `ultron open-hud`) ----------------
    def issue_login_ticket(self) -> str:
        payload = f"{int(self._clock())}.{secrets.token_urlsafe(12)}"
        return f"{payload}.{self._sign(self._ticket_key, payload)}"

    def verify_login_ticket(self, ticket: str | None, max_age_seconds: int = 120) -> str | None:
        """Return the ticket nonce if signature and age are valid (single use is enforced by the caller)."""
        if not ticket or ticket.count(".") != 2:
            return None
        issued_raw, nonce, signature = ticket.split(".")
        if not hmac.compare_digest(signature, self._sign(self._ticket_key, f"{issued_raw}.{nonce}")):
            return None
        try:
            issued = int(issued_raw)
        except ValueError:
            return None
        age = self._clock() - issued
        return nonce if -5 <= age <= max_age_seconds else None


class UsedNonces:
    """Remembers consumed values (login-ticket nonces, logged-out session ids) for a while."""

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
