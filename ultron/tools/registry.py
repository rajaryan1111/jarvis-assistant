"""Tool registry: the single authorization boundary for sensitive operations.

``request`` -> policy decision -> run now | create pending confirmation | deny
``confirm`` -> validate the pending action (principal, code/id, expiry, single use) -> gate -> run
Every decision is written to the ``ultron.audit`` logger (no argument values).

Every state-changing legacy action (voice, CLI, HUD, API and background services) is a
registered tool and reaches its implementation only through ``request``/``confirm``;
``tests/test_policy_boundary.py`` checks this statically and behaviourally.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ultron.security.confirmation import ConfirmationError, ConfirmationStore, PendingAction
from ultron.security.policy import CommandContext, Decision, evaluate
from ultron.security.redact import redact
from ultron.tools.base import Tool, ToolResult

audit_log = logging.getLogger("ultron.audit")

Status = Literal["completed", "failed", "pending_confirmation", "denied", "unavailable", "cancelled"]
Gate = Callable[[PendingAction], tuple[bool, str]]


@dataclass
class ToolOutcome:
    status: Status
    message: str
    result: ToolResult | None = None
    pending: PendingAction | None = None


class ToolRegistry:
    def __init__(
        self,
        confirmations: ConfirmationStore | None = None,
        platform: str = sys.platform,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self.confirmations = confirmations or ConfirmationStore(clock=monotonic)
        self.platform = platform
        self._now = monotonic

    # --- registration ------------------------------------------------------
    def register(self, tool: Tool) -> Tool:
        if tool.spec.name in self._tools:
            raise ValueError(f"tool already registered: {tool.spec.name}")
        self._tools[tool.spec.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe_pending(self, pending: PendingAction) -> dict[str, Any]:
        return pending.public(self._now())

    def has_pending(self, ctx: CommandContext) -> bool:
        return self.confirmations.pending_for(ctx.principal) is not None

    # --- execution ---------------------------------------------------------
    def _audit(self, event: str, tool: str, ctx: CommandContext, status: str) -> None:
        audit_log.info(
            "event=%s tool=%s principal=%s channel=%s status=%s",
            event, tool, ctx.principal, ctx.channel.value, status,
        )

    def request(self, name: str, arguments: Mapping[str, Any], ctx: CommandContext) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome("unavailable", "That action is not available.")
        if tool.spec.platforms is not None and self.platform not in tool.spec.platforms:
            self._audit("request", name, ctx, "unavailable")
            return ToolOutcome("unavailable", "That action is only available on macOS.")

        decision = evaluate(tool.spec.risk, ctx, internal_ok=tool.spec.internal_ok)
        if decision.decision is Decision.DENY:
            self._audit("request", name, ctx, "denied")
            return ToolOutcome("denied", f"I can't do that. {decision.reason}")
        if decision.decision is Decision.CONFIRM:
            try:
                preview = tool.preview(arguments)
            except Exception as exc:  # noqa: BLE001 - preview problems must not crash the assistant
                audit_log.warning("preview failed for %s: %s", name, redact(exc))
                return ToolOutcome("failed", "I couldn't prepare that action safely, so I did nothing.")
            pending = self.confirmations.create(
                name, arguments, ctx.principal, preview, ttl_seconds=tool.spec.confirmation_ttl_seconds
            )
            ctx.events.append(("pending_action", pending))
            self._audit("request", name, ctx, "pending_confirmation")
            ttl = pending.expires_in(self._now())
            return ToolOutcome(
                "pending_confirmation",
                f"{preview} To proceed, say or type 'confirm {pending.code}' within {ttl} seconds, "
                "or say 'cancel'.",
                pending=pending,
            )
        return self._execute(tool, arguments, ctx)

    def confirm(
        self,
        ctx: CommandContext,
        *,
        code: str | None = None,
        action_id: str | None = None,
        expected_tool: str | None = None,
        gate: Gate | None = None,
    ) -> ToolOutcome:
        if not ctx.can_authorize:
            self._audit("confirm", "-", ctx, "denied_untrusted")
            return ToolOutcome("denied", "Confirmations are only accepted from an authenticated user.")
        if code is None and action_id is None:
            if self.has_pending(ctx):
                return ToolOutcome(
                    "denied",
                    "Please include the confirmation code I gave you, for example 'confirm 1234', or say 'cancel'.",
                )
            return ToolOutcome("denied", "There is no pending action to confirm.")
        try:
            if action_id is not None:
                pending = self.confirmations.confirm_id(ctx.principal, action_id)
            else:
                pending = self.confirmations.confirm_code(ctx.principal, code or "", expected_tool)
        except ConfirmationError as err:
            self._audit("confirm", expected_tool or "-", ctx, f"rejected:{err.reason}")
            return ToolOutcome("denied", err.message)

        tool = self._tools.get(pending.tool_name)
        if tool is None:
            return ToolOutcome("unavailable", "That action is no longer available.")
        if gate is not None:
            ok, message = gate(pending)
            if not ok:
                self._audit("confirm", pending.tool_name, ctx, "gate_denied")
                return ToolOutcome("denied", message or "The additional security check failed.")
        self._audit("confirm", pending.tool_name, ctx, "accepted")
        return self._execute(tool, pending.arguments, ctx)

    def confirm_hint_map(self) -> tuple[tuple[str, str], ...]:
        """(hint word, tool name) pairs, longest hint first, for spoken confirmations."""
        pairs = [(hint, name) for name, tool in self._tools.items() for hint in tool.spec.confirm_hints]
        return tuple(sorted(pairs, key=lambda pair: -len(pair[0])))

    def invalidate_all(self, reason: str) -> int:
        """Drop every pending action (e.g. the security posture changed). Returns how many."""
        dropped = self.confirmations.clear()
        if dropped:
            audit_log.info("event=invalidate_all count=%d reason=%s", dropped, reason)
        return dropped

    def cancel(self, ctx: CommandContext) -> ToolOutcome:
        pending = self.confirmations.cancel(ctx.principal)
        if pending is None:
            return ToolOutcome("cancelled", "There was nothing pending to cancel.")
        self._audit("cancel", pending.tool_name, ctx, "cancelled")
        return ToolOutcome("cancelled", "Okay, I cancelled that request.")

    def _execute(self, tool: Tool, arguments: Mapping[str, Any], ctx: CommandContext) -> ToolOutcome:
        try:
            result = tool.run(arguments)
        except Exception as exc:  # noqa: BLE001 - report failure instead of crashing the request
            audit_log.warning("tool %s raised %s: %s", tool.spec.name, type(exc).__name__, redact(exc))
            self._audit("execute", tool.spec.name, ctx, "failed")
            return ToolOutcome("failed", "Something went wrong while doing that. Nothing else was attempted.")
        self._audit("execute", tool.spec.name, ctx, "completed" if result.ok else "failed")
        return ToolOutcome("completed" if result.ok else "failed", result.message, result=result)
