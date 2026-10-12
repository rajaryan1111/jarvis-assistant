"""Pending-action confirmations.

A destructive request creates a :class:`PendingAction` bound to:
- one specific tool and its arguments,
- the principal (session / client) that requested it,
- a random single-use code and an unguessable id,
- an expiry time (default 60 s) and a small number of attempts.

A bare phrase such as "confirm shutdown" is therefore never enough on its own.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


class ConfirmationError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass
class PendingAction:
    action_id: str
    code: str
    tool_name: str
    arguments: dict[str, Any]
    principal: str
    summary: str
    created_at: float
    expires_at: float
    attempts: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def expires_in(self, now: float) -> int:
        return max(0, int(round(self.expires_at - now)))

    def public(self, now: float) -> dict[str, Any]:
        """Safe representation for the HUD (the code is spoken/shown in the reply text, not here)."""
        return {
            "action_id": self.action_id,
            "tool": self.tool_name,
            "summary": self.summary,
            "expires_in": self.expires_in(now),
        }


class ConfirmationStore:
    def __init__(
        self,
        ttl_seconds: int = 60,
        max_attempts: int = 3,
        code_length: int = 4,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_attempts = max_attempts
        self.code_length = code_length
        self._clock = clock
        self._pending: dict[str, PendingAction] = {}  # principal -> action (one at a time)
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for principal, action in list(self._pending.items()):
            if action.expires_at <= now:
                del self._pending[principal]

    def create(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        principal: str,
        summary: str,
        ttl_seconds: int | None = None,
    ) -> PendingAction:
        now = self._clock()
        action = PendingAction(
            action_id=secrets.token_urlsafe(16),
            code="".join(secrets.choice("0123456789") for _ in range(self.code_length)),
            tool_name=tool_name,
            arguments=dict(arguments),
            principal=principal,
            summary=summary,
            created_at=now,
            expires_at=now + (ttl_seconds or self.ttl_seconds),
        )
        with self._lock:
            self._purge(now)
            self._pending[principal] = action  # replaces any older request from the same principal
        return action

    def pending_for(self, principal: str) -> PendingAction | None:
        with self._lock:
            self._purge(self._clock())
            return self._pending.get(principal)

    def _take(self, principal: str, expected_tool: str | None) -> PendingAction:
        now = self._clock()
        action = self._pending.get(principal)
        if action is None:
            raise ConfirmationError("none_pending", "There is no pending action to confirm.")
        if action.expires_at <= now:
            del self._pending[principal]
            raise ConfirmationError("expired", "That confirmation has expired. Please ask again.")
        if expected_tool and expected_tool != action.tool_name:
            raise ConfirmationError(
                "mismatch", f"The pending action is '{action.summary}', not the one you tried to confirm."
            )
        return action

    def confirm_code(self, principal: str, code: str, expected_tool: str | None = None) -> PendingAction:
        with self._lock:
            action = self._take(principal, expected_tool)
            if not secrets.compare_digest(code.encode(), action.code.encode()):
                action.attempts += 1
                if action.attempts >= self.max_attempts:
                    del self._pending[principal]
                    raise ConfirmationError("too_many_attempts", "Too many wrong codes. The request was cancelled.")
                raise ConfirmationError("wrong_code", "That confirmation code is not correct.")
            del self._pending[principal]
            return action

    def confirm_id(self, principal: str, action_id: str) -> PendingAction:
        with self._lock:
            action = self._take(principal, None)
            if not secrets.compare_digest(action_id.encode(), action.action_id.encode()):
                raise ConfirmationError("not_found", "That pending action no longer exists.")
            del self._pending[principal]
            return action

    def clear(self) -> int:
        """Invalidate every pending action for every principal. Returns how many were dropped."""
        with self._lock:
            count = len(self._pending)
            self._pending.clear()
            return count

    def cancel(self, principal: str) -> PendingAction | None:
        with self._lock:
            self._purge(self._clock())
            return self._pending.pop(principal, None)
