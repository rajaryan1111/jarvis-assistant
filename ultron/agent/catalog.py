"""Which registered tools the agent may propose, and how they are described to the model.

A tool is offered to the model only if **all** of these hold:

1. it is on the agent allowlist (``DEFAULT_AGENT_TOOLS``; M2.0 = read-only tools plus a few
   reversible routine actions, no destructive, security, camera or power tools),
2. it is registered and available on this platform,
3. it declares an argument schema (``ToolSpec.parameters``) in the supported subset, and
4. the policy would not outright deny it for the caller's context (e.g. voice with
   ``ULTRON_VOICE_TRUST=observe`` only sees read-only tools).

Being offered is not permission: every call still goes through ``ToolRegistry.request``.
Selection is dynamic — the model chooses from these descriptions and schemas; there is no
phrase table here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ultron.agent.provider import ToolDefinition
from ultron.agent.schema import check_schema
from ultron.security.policy import CommandContext, Decision, RiskTier
from ultron.tools.registry import ToolRegistry

FINISH = "finish"
ASK_USER = "ask_user"

# Read-only tools, then reversible routine actions. Keep this list short and reviewed:
# adding a tool here makes it reachable by a model (still under the full policy).
DEFAULT_AGENT_TOOLS: tuple[str, ...] = (
    "time.now",
    "system.status",
    "system.cpu_temperature",
    "reminders.list",
    "memory.list_notes",
    "web.search",
    "wiki.summary",
    "weather.current",
    "news.headlines",
    "project.inspect",
    "project.explain",
    "project.search",
    "memory.add_note",
    "reminders.add",
    "apps.open",
    "web.open_url",
    "system.set_volume",
    "system.brightness",
)

FINISH_SCHEMA: Mapping[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["outcome", "summary"],
    "properties": {
        "outcome": {"type": "string", "enum": ["achieved", "partial", "not_achieved"], "maxLength": 12},
        "summary": {"type": "string", "minLength": 1, "maxLength": 600,
                    "description": "One or two short sentences for the user (may be spoken aloud)."},
        "evidence": {"type": "string", "maxLength": 200, "pattern": r"(c\d{1,3}(\s*,\s*c\d{1,3})*)?",
                     "description": "Comma-separated call ids whose results prove the outcome, e.g. 'c1,c3'."},
    },
}
ASK_USER_SCHEMA: Mapping[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["question"],
    "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 300}},
}
_CONTROL_DEFINITIONS = (
    ToolDefinition(FINISH, "Finish the task and report the outcome with evidence (call ids).", FINISH_SCHEMA),
    ToolDefinition(ASK_USER, "Ask the user one short clarifying question when essential information is "
                             "missing. Ends this turn.", ASK_USER_SCHEMA),
)


def model_name(tool_name: str) -> str:
    """Provider-safe function name (``apps.open`` -> ``apps__open``; ``^[a-zA-Z0-9_-]{1,64}$``)."""
    return tool_name.replace(".", "__")


@dataclass(frozen=True)
class CatalogEntry:
    tool_name: str
    definition: ToolDefinition
    risk: RiskTier
    external_content: bool
    parameters: Mapping[str, Any]


def _describe(description: str, risk: RiskTier, external: bool) -> str:
    notes = {
        RiskTier.OBSERVE: "read-only",
        RiskTier.ROUTINE: "changes something on this computer (reversible)",
    }.get(risk, "high impact: needs the owner's approval in the HUD")
    if external:
        notes += "; returns untrusted external text"
    return f"{description} ({notes})"


def build_catalog(
    registry: ToolRegistry,
    ctx: CommandContext,
    allowlist: Iterable[str] = DEFAULT_AGENT_TOOLS,
) -> dict[str, CatalogEntry]:
    """Model-facing name -> entry, for the tools this caller may propose right now."""
    catalog: dict[str, CatalogEntry] = {}
    for name in allowlist:
        tool = registry.get(name)
        if tool is None or tool.spec.parameters is None or not registry.is_available(name):
            continue
        check_schema(tool.spec.parameters)
        if registry.decision_for(name, ctx) in (None, Decision.DENY):
            continue
        spec = tool.spec
        definition = ToolDefinition(model_name(name), _describe(spec.description, spec.risk, spec.external_content),
                                    spec.parameters)
        catalog[definition.name] = CatalogEntry(name, definition, spec.risk, spec.external_content, spec.parameters)
    return catalog


def definitions(catalog: Mapping[str, CatalogEntry]) -> list[ToolDefinition]:
    return [entry.definition for entry in catalog.values()] + list(_CONTROL_DEFINITIONS)
