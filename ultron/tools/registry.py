"""Tool registry: the single authorization boundary for sensitive operations.

``request`` -> policy decision -> run now | create pending confirmation | deny
``confirm`` -> only a signed-in HUD session (typed/clicked) may approve -> validate the pending
action (principal or delegated id, code/id, expiry, single use) -> gate -> run
Every decision is written to the ``ultron.audit`` logger (no argument values).

The agent loop (``ultron.agent``) uses exactly this ``request`` path for every tool call it
proposes. It may pass ``minimum_risk`` to *raise* (never lower) a tool's tier, e.g. after
the run has read untrusted web content. Pending actions it creates are delegated: approvable
only with the HUD CONFIRM button for that exact action id, never with a code.

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
from ultron.security.policy import Channel, CommandContext, Decision, RiskTier, VoiceTrust, evaluate
from ultron.security.redact import redact
from ultron.tools.base import Tool, ToolResult

audit_log = logging.getLogger("ultron.audit")

Status = Literal["completed", "failed", "pending_confirmation", "denied", "unavailable", "cancelled"]
Gate = Callable[[PendingAction], tuple[bool, str]]

# A request made by voice or an API client must be approved in the HUD: allow time to get there.
DELEGATED_CONFIRMATION_TTL_SECONDS = 120
CONFIRM_ONLY_IN_HUD = (
    "Confirmations are only accepted from the signed-in HUD (typed or clicked), "
    "not from voice, API clients or untrusted content."
)
CONFIRM_NOT_BY_VOICE = (
    "For your safety I can't accept a spoken confirmation. "
    "Press CONFIRM in the signed-in HUD, or say 'cancel'."
)


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
        voice_trust: VoiceTrust = VoiceTrust.ROUTINE,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self.voice_trust = voice_trust
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

    def pending_for_confirmer(self, ctx: CommandContext) -> list[dict[str, Any]]:
        """What the HUD may show with CONFIRM/CANCEL buttons: its own pending action plus actions
        requested by voice or API clients (which can only be approved here). Never the codes."""
        if not ctx.can_authorize:
            return []
        now = self._now()
        actions = self.confirmations.confirmable_by(ctx.principal, include_delegated=ctx.can_confirm)
        return [action.public(now) for action in actions]

    # --- execution ---------------------------------------------------------
    def _audit(self, event: str, tool: str, ctx: CommandContext, status: str) -> None:
        audit_log.info(
            "event=%s tool=%s principal=%s channel=%s input=%s agent=%s status=%s",
            event, tool, ctx.principal, ctx.channel.value, ctx.input_mode.value, ctx.agent_run or "-", status,
        )

    def is_available(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool is not None and (tool.spec.platforms is None or self.platform in tool.spec.platforms)

    def decision_for(self, name: str, ctx: CommandContext, minimum_risk: RiskTier | None = None) -> Decision | None:
        """What ``request`` would decide for ``ctx`` (no side effects), or None if unavailable."""
        tool = self._tools.get(name)
        if tool is None or not self.is_available(name):
            return None
        risk = max(tool.spec.risk, minimum_risk) if minimum_risk is not None else tool.spec.risk
        return evaluate(risk, ctx, internal_ok=tool.spec.internal_ok, voice_trust=self.voice_trust).decision

    def request(
        self,
        name: str,
        arguments: Mapping[str, Any],
        ctx: CommandContext,
        *,
        minimum_risk: RiskTier | None = None,
    ) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome("unavailable", "That action is not available.")
        if tool.spec.platforms is not None and self.platform not in tool.spec.platforms:
            self._audit("request", name, ctx, "unavailable")
            return ToolOutcome("unavailable", "That action is only available on macOS.")

        # ``minimum_risk`` can only make a request stricter, never more permissive.
        risk = max(tool.spec.risk, minimum_risk) if minimum_risk is not None else tool.spec.risk
        decision = evaluate(risk, ctx, internal_ok=tool.spec.internal_ok, voice_trust=self.voice_trust)
        if decision.decision is Decision.DENY:
            self._audit("request", name, ctx, "denied")
            return ToolOutcome("denied", f"I can't do that. {decision.reason}")
        if decision.decision is Decision.CONFIRM:
            try:
                preview = tool.preview(arguments)
            except Exception as exc:  # noqa: BLE001 - preview problems must not crash the assistant
                audit_log.warning("preview failed for %s: %s", name, redact(exc))
                return ToolOutcome("failed", "I couldn't prepare that action safely, so I did nothing.")
            # A HUD session approves its own requests. Anything else (voice, API client, or the
            # agent loop acting for any caller) is *delegated*: approvable only from the HUD by
            # action id, and its code is never shown.
            delegated = ctx.channel is not Channel.HUD or ctx.agent_run is not None
            ttl_seconds = tool.spec.confirmation_ttl_seconds
            if delegated:
                ttl_seconds = max(ttl_seconds or 0, DELEGATED_CONFIRMATION_TTL_SECONDS)
            pending = self.confirmations.create(
                name, arguments, ctx.principal, preview, ttl_seconds=ttl_seconds,
                delegated=delegated, requested_via=ctx.channel.value,
            )
            ctx.events.append(("pending_action", pending))
            self._audit("request", name, ctx, "pending_confirmation")
            ttl = pending.expires_in(self._now())
            if ctx.agent_run is not None:
                # Proposed by the agent loop: the person must approve that exact action.
                message = (f"{preview} The assistant proposed this, so it needs your approval: press CONFIRM in the "
                           f"signed-in HUD within {ttl} seconds, or say 'cancel'.")
            elif ctx.can_confirm:
                message = (f"{preview} To proceed, type 'confirm {pending.code}' or press CONFIRM within "
                           f"{ttl} seconds, or say 'cancel'.")
            else:
                # Voice or API client: no code (it would be spoken aloud and prove nothing).
                message = (f"{preview} I can't accept a spoken or API confirmation for this. Press CONFIRM "
                           f"in the signed-in HUD within {ttl} seconds, or say 'cancel'.")
            return ToolOutcome("pending_confirmation", message, pending=pending)
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
        if not ctx.can_confirm:
            # Voice, bearer-token clients, background services and untrusted content can never
            # approve: not with a code, not with an action id. Nothing else is tried instead.
            if ctx.can_authorize and ctx.is_voice:
                self._audit("confirm", "-", ctx, "denied_voice")
                return ToolOutcome("denied", CONFIRM_NOT_BY_VOICE)
            self._audit("confirm", "-", ctx, "denied_channel" if ctx.can_authorize else "denied_untrusted")
            return ToolOutcome("denied", CONFIRM_ONLY_IN_HUD)
        if code is None and action_id is None:
            own = self.confirmations.pending_for(ctx.principal)
            if own is not None and own.delegated:
                return ToolOutcome("denied", f"Press CONFIRM in the HUD to approve: {own.summary}")
            if self.has_pending(ctx):
                return ToolOutcome(
                    "denied",
                    "Please include the confirmation code I gave you, for example 'confirm 1234', or say 'cancel'.",
                )
            return ToolOutcome("denied", "There is no pending action to confirm.")
        try:
            if action_id is not None:
                pending = self.confirmations.confirm_id(ctx.principal, action_id, allow_delegated=True)
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
        self._audit("confirm", pending.tool_name, ctx, "accepted_delegated" if pending.delegated else "accepted")
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

    def cancel(self, ctx: CommandContext, action_id: str | None = None) -> ToolOutcome:
        """Cancel the caller's own pending action. Cancelling is always safe, so voice may do it.
        A HUD session may also cancel a delegated (voice/API) request by its exact id."""
        pending = None
        if action_id and ctx.can_confirm:
            own = self.confirmations.pending_for(ctx.principal)
            if own is not None and own.action_id == action_id:
                pending = self.confirmations.cancel(ctx.principal)
            else:
                pending = self.confirmations.cancel_delegated(action_id)
        elif ctx.can_authorize:
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
