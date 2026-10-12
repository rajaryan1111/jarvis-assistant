"""Risk tiers, request context and the authorization decision.

Every sensitive operation is a registered tool with a :class:`RiskTier`. The
policy decides — outside of any language model — whether it may run now,
needs an explicit user confirmation, or is denied.

Only *authenticated user channels* can authorise actions. Text that comes
from web pages, documents, knowledge-base retrieval or LLM output is content,
never authorisation: such text is never routed back into the command handler,
and a context marked ``UNTRUSTED`` can never act or confirm.

Decision table (see docs/M1_1_TOOL_SECURITY_BOUNDARY.md):

==================  =========  =================  ======================  =========
tier                untrusted  internal service   authenticated user      any
==================  =========  =================  ======================  =========
OBSERVE             allow      allow              allow
ROUTINE             deny       allow only if the  allow (no prompt)
                               tool opts in
CONSEQUENTIAL       deny       deny               confirm
DESTRUCTIVE         deny       deny               confirm
FORBIDDEN                                                                 deny
==================  =========  =================  ======================  =========
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any


class RiskTier(IntEnum):
    OBSERVE = 0  # read-only: time, status, reading local state
    ROUTINE = 1  # reversible local effects: open an app, set volume
    CONSEQUENTIAL = 2  # notable effects that are hard to undo: needs confirmation
    DESTRUCTIVE = 3  # irreversible / high impact: delete data, shutdown, restart
    FORBIDDEN = 4  # never allowed


class Channel(str, Enum):
    HUD = "hud"  # browser HUD with an authenticated session
    API = "api"  # bearer-token API client
    LOCAL_VOICE = "local_voice"  # `python main.py` microphone loop on this machine
    WAKEWORD = "wakeword"  # wake-word listener using the bearer token
    INTERNAL = "internal"  # background services in this process (reminder / intruder watchers)
    UNTRUSTED = "untrusted"  # anything else, including derived/retrieved content


USER_CHANNELS = frozenset({Channel.HUD, Channel.API, Channel.LOCAL_VOICE, Channel.WAKEWORD})


@dataclass
class CommandContext:
    """Who is asking, and through which channel. Passed to the command handler and tools."""

    principal: str
    channel: Channel
    authenticated: bool
    events: list[tuple[str, Any]] = field(default_factory=list)

    @property
    def can_authorize(self) -> bool:
        return self.authenticated and self.channel in USER_CHANNELS

    @property
    def is_internal(self) -> bool:
        return self.channel is Channel.INTERNAL

    @classmethod
    def untrusted(cls, source: str = "unspecified") -> CommandContext:
        return cls(principal=f"untrusted:{source}", channel=Channel.UNTRUSTED, authenticated=False)

    @classmethod
    def local_voice(cls) -> CommandContext:
        return cls(principal="local-cli", channel=Channel.LOCAL_VOICE, authenticated=True)

    @classmethod
    def internal(cls, service: str) -> CommandContext:
        """A background service of this process. It is not a user: it can never confirm anything."""
        return cls(principal=f"internal:{service}", channel=Channel.INTERNAL, authenticated=False)


class Decision(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason: str


def evaluate(risk: RiskTier, ctx: CommandContext, *, internal_ok: bool = False) -> PolicyDecision:
    """Decide whether an action of ``risk`` may run for ``ctx``.

    ``internal_ok`` is declared by the tool (``ToolSpec.internal_ok``): it lets a background
    service of this process run that ROUTINE tool (e.g. speak a due reminder). Internal
    services can never trigger anything that needs confirmation.
    """
    if risk >= RiskTier.FORBIDDEN:
        return PolicyDecision(Decision.DENY, "This action is never allowed.")
    if risk == RiskTier.OBSERVE:
        return PolicyDecision(Decision.ALLOW, "Read-only action.")
    if ctx.is_internal:
        if risk == RiskTier.ROUTINE and internal_ok:
            return PolicyDecision(Decision.ALLOW, "Background service action permitted by this tool.")
        return PolicyDecision(Decision.DENY, "Background services cannot perform this action.")
    if not ctx.can_authorize:
        return PolicyDecision(
            Decision.DENY,
            "Actions can only be requested by an authenticated user, not by untrusted or derived content.",
        )
    if risk >= RiskTier.CONSEQUENTIAL:
        return PolicyDecision(Decision.CONFIRM, "This action needs your explicit confirmation.")
    return PolicyDecision(Decision.ALLOW, "Routine action by an authenticated user.")
