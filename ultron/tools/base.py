"""Tool interface and risk metadata.

A tool is a narrow, explicit capability. It declares its risk tier and side
effects up front so the registry can apply policy *before* it runs. Tools
receive already-parsed arguments; they never parse free text or execute
strings taken from documents or model output.
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
