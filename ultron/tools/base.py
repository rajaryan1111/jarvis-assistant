"""Tool interface and risk metadata.

A tool is a narrow, explicit capability. It declares its risk tier and side
effects up front so the registry can apply policy *before* it runs.

Tools receive explicit arguments and validate them; they never execute strings
taken from documents or model output. A few legacy data tools (reminders, smart
home, study progress) receive the user's own command text and parse it with the
same deterministic code as before — never with a model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from ultron.security.policy import RiskTier


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: RiskTier
    side_effects: frozenset[str] = frozenset()
    platforms: frozenset[str] | None = None  # e.g. {"darwin"}; None = any platform
    confirmation_ttl_seconds: int = 60
    # Background services of this process (Channel.INTERNAL) may run this ROUTINE tool.
    internal_ok: bool = False
    # Words that identify this tool in a spoken confirmation ("confirm restart 1234").
    confirm_hints: tuple[str, ...] = ()
    # JSON-Schema subset (ultron.agent.schema) for the arguments. Only tools that declare one
    # can ever be offered to the agent loop; model-proposed arguments are validated against it.
    parameters: Mapping[str, Any] | None = None
    # The result carries text from outside (web pages, news, encyclopedia, weather APIs).
    # It is untrusted data: after the agent has read it, later actions need HUD approval.
    external_content: bool = False


@dataclass
class ToolResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)


class Tool(Protocol):
    spec: ToolSpec

    def preview(self, arguments: Mapping[str, Any]) -> str:
        """Plain-language description of exactly what ``run`` would do. Must not have side effects."""
        ...

    def run(self, arguments: Mapping[str, Any]) -> ToolResult: ...


@dataclass
class FunctionTool:
    """Adapter that turns plain functions into a :class:`Tool`."""

    spec: ToolSpec
    run_fn: Callable[[Mapping[str, Any]], ToolResult]
    preview_fn: Callable[[Mapping[str, Any]], str] | None = None

    def preview(self, arguments: Mapping[str, Any]) -> str:
        return self.preview_fn(arguments) if self.preview_fn else self.spec.description

    def run(self, arguments: Mapping[str, Any]) -> ToolResult:
        return self.run_fn(arguments)
