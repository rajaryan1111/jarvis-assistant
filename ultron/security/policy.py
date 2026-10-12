"""Risk tiers, request context and the authorization decision.

Every sensitive operation is a registered tool with a :class:`RiskTier`. The
policy decides — outside of any language model — whether it may run now,
needs an explicit user confirmation, or is denied.

Only *authenticated user channels* can authorise actions. Text that comes
from web pages, documents, knowledge-base retrieval or LLM output is content,
never authorisation: such text is never routed back into the command handler,
and a context marked ``UNTRUSTED`` can never act or confirm.

Authentication (who is this?) and authorization (may they do this now?) are
separate (see docs/M1_2_IDENTITY_SESSIONS_VOICE.md):

* ``can_authorize``: an authenticated user channel may *request* actions.
* ``can_confirm``: only a signed-in HUD session using typed or clicked input may
  *approve* a pending CONSEQUENTIAL/DESTRUCTIVE action. Voice (microphone, wake
  word, HUD speech recognition) and bearer-token API clients never can: speech
  is not proof of identity and a static token in a script is not a person.
* Agent loop (docs/M2_0_AGENT_LOOP.md): tool calls proposed by the model run with the
  requesting user's context marked ``agent_run``. Such a context can request (subject to
  the same table) but can never approve, and its pending actions are approvable only by
  the HUD CONFIRM button for that exact action.

Decision table (see docs/M1_1_TOOL_SECURITY_BOUNDARY.md and M1_2):

==================  =========  =================  =====================  ====================
tier                untrusted  internal service   authenticated user     voice input
==================  =========  =================  =====================  ====================
OBSERVE             allow      allow              allow                  allow
ROUTINE             deny       allow only if the  allow (no prompt)      allow if
                               tool opts in                              ULTRON_VOICE_TRUST=
                                                                         routine (default)
CONSEQUENTIAL       deny       deny               confirm (HUD only)     request only; approve
DESTRUCTIVE         deny       deny               confirm (HUD only)     in the HUD (standalone
                                                                         mic loop: deny)
FORBIDDEN           deny       deny               deny                   deny
==================  =========  =================  =====================  ====================

The local ``python -m ultron agent`` command (``Channel.CLI``) is an authenticated user like
the HUD for OBSERVE/ROUTINE, but has no approver attached, so CONSEQUENTIAL and above are
denied there with guidance (like the standalone microphone loop).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any

log = logging.getLogger("ultron.security")


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
    CLI = "cli"  # `python -m ultron agent "..."` typed by the OS user in a local shell


USER_CHANNELS = frozenset({Channel.HUD, Channel.API, Channel.LOCAL_VOICE, Channel.WAKEWORD, Channel.CLI})
VOICE_CHANNELS = frozenset({Channel.LOCAL_VOICE, Channel.WAKEWORD})
# Processes with no HUD attached: a pending action created there could never be approved.
NO_APPROVER_CHANNELS = frozenset({Channel.LOCAL_VOICE, Channel.CLI})


class InputMode(str, Enum):
    TEXT = "text"  # typed, or a button click in the HUD
    VOICE = "voice"  # speech recognition (microphone loop, wake word, HUD speech input)


class VoiceTrust(str, Enum):
    """How much a *spoken* request may do (``ULTRON_VOICE_TRUST``). Voice can never approve."""

    OBSERVE = "observe"  # read-only answers only
    ROUTINE = "routine"  # default: read-only + routine local actions; high-impact needs the HUD


def voice_trust_from_env(env: Mapping[str, str] | None = None) -> VoiceTrust:
    """Read ``ULTRON_VOICE_TRUST``. An unknown value falls back to the *more restrictive* mode."""
    env = os.environ if env is None else env
    raw = (env.get("ULTRON_VOICE_TRUST") or "").strip().lower()
    if not raw:
        return VoiceTrust.ROUTINE
    try:
        return VoiceTrust(raw)
    except ValueError:
        log.warning("ULTRON_VOICE_TRUST=%r is not 'routine' or 'observe'; using 'observe'", raw[:20])
        return VoiceTrust.OBSERVE


@dataclass
class CommandContext:
    """Who is asking, and through which channel. Passed to the command handler and tools."""

    principal: str
    channel: Channel
    authenticated: bool
    events: list[tuple[str, Any]] = field(default_factory=list)
    input_mode: InputMode = InputMode.TEXT
    # Set when the agent loop acts on this user's behalf (tool calls proposed by a model).
    agent_run: str | None = None

    @property
    def can_authorize(self) -> bool:
        """May *request* actions (subject to the policy)."""
        return self.authenticated and self.channel in USER_CHANNELS

    @property
    def is_voice(self) -> bool:
        """Spoken input. Voice is never strong identity: a recording, a speaker or someone
        nearby sounds the same to the microphone."""
        return self.input_mode is InputMode.VOICE or self.channel in VOICE_CHANNELS

    @property
    def can_confirm(self) -> bool:
        """May *approve* a pending high-impact action: a signed-in HUD session, typed/clicked.
        Never on behalf of the agent loop: a model can propose, only the person can approve."""
        return (self.can_authorize and self.channel is Channel.HUD and not self.is_voice
                and self.agent_run is None)

    @property
    def is_internal(self) -> bool:
        return self.channel is Channel.INTERNAL

    @classmethod
    def untrusted(cls, source: str = "unspecified") -> CommandContext:
        return cls(principal=f"untrusted:{source}", channel=Channel.UNTRUSTED, authenticated=False)

    @classmethod
    def local_voice(cls) -> CommandContext:
        """The standalone ``python main.py`` microphone loop. Local presence is assumed for
        routine requests; it is not proof of identity and can never approve anything."""
        return cls(principal="local-cli", channel=Channel.LOCAL_VOICE, authenticated=True,
                   input_mode=InputMode.VOICE)

    @classmethod
    def internal(cls, service: str) -> CommandContext:
        """A background service of this process. It is not a user: it can never confirm anything."""
        return cls(principal=f"internal:{service}", channel=Channel.INTERNAL, authenticated=False)

    @classmethod
    def local_cli(cls) -> CommandContext:
        """The OS user typing ``python -m ultron agent`` in a local shell (no approver attached)."""
        return cls(principal="local-shell", channel=Channel.CLI, authenticated=True)


class Decision(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason: str


VOICE_READ_ONLY_REASON = (
    "Voice commands are limited to read-only requests here (ULTRON_VOICE_TRUST=observe). "
    "Use the HUD for this."
)
STANDALONE_VOICE_REASON = (
    "High-impact actions can't be approved by voice alone. Start the HUD server (python server.py), "
    "open the HUD (python -m ultron open-hud) and ask there."
)
CLI_NO_APPROVER_REASON = (
    "High-impact actions need approval in the signed-in HUD. Start the HUD server (python server.py), "
    "open the HUD (python -m ultron open-hud) and ask there."
)


def evaluate(
    risk: RiskTier,
    ctx: CommandContext,
    *,
    internal_ok: bool = False,
    voice_trust: VoiceTrust = VoiceTrust.ROUTINE,
) -> PolicyDecision:
    """Decide whether an action of ``risk`` may run for ``ctx``.

    ``internal_ok`` is declared by the tool (``ToolSpec.internal_ok``): it lets a background
    service of this process run that ROUTINE tool (e.g. speak a due reminder). Internal
    services can never trigger anything that needs confirmation.

    A CONFIRM decision only creates a pending action; approving it is a separate step that
    requires ``ctx.can_confirm`` (see ``ToolRegistry.confirm``). The standalone microphone
    loop runs in its own process with no HUD attached, so a pending action it created could
    never be approved there: it is denied with guidance instead.
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
    if ctx.is_voice and voice_trust is VoiceTrust.OBSERVE:
        return PolicyDecision(Decision.DENY, VOICE_READ_ONLY_REASON)
    if risk >= RiskTier.CONSEQUENTIAL:
        if ctx.channel in NO_APPROVER_CHANNELS:
            reason = STANDALONE_VOICE_REASON if ctx.channel is Channel.LOCAL_VOICE else CLI_NO_APPROVER_REASON
            return PolicyDecision(Decision.DENY, reason)
        return PolicyDecision(Decision.CONFIRM, "This action needs your explicit confirmation.")
    return PolicyDecision(Decision.ALLOW, "Routine action by an authenticated user.")
