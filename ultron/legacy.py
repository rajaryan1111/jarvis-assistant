"""Adapter between the API layer and the legacy JARVIS code in ``main.py``.

This is the boundary for existing JARVIS functionality: the API depends on
the small :class:`Backend` protocol, not on ``main.py`` internals, so the
legacy router can be replaced incrementally in later milestones.
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol

from ultron.security.confirmation import PendingAction
from ultron.security.policy import CommandContext
from ultron.tools.registry import ToolRegistry


class Backend(Protocol):
    @property
    def registry(self) -> ToolRegistry: ...

    def handle(self, text: str, ctx: CommandContext) -> str: ...

    def confirmation_gate(self, pending: PendingAction) -> tuple[bool, str]: ...

    def status(self) -> dict[str, Any]: ...

    def speak(self, text: str, ctx: CommandContext) -> str: ...

    def start_background(self) -> None: ...


class JarvisLegacyBackend:
    """Lazily imports ``main`` so that creating the app has no import side effects."""

    def __init__(self) -> None:
        self._main: Any = None

    @property
    def main(self) -> Any:
        if self._main is None:
            self._main = importlib.import_module("main")
        return self._main

    @property
    def registry(self) -> ToolRegistry:
        return self.main.TOOLS

    def handle(self, text: str, ctx: CommandContext) -> str:
        return self.main.handle_command(text, ctx)

    def confirmation_gate(self, pending: PendingAction) -> tuple[bool, str]:
        """Extra check before a confirmed action runs (legacy face-based security mode)."""
        return self.main.confirmation_gate(pending)

    def status(self) -> dict[str, Any]:
        return self.main.get_status()

    def speak(self, text: str, ctx: CommandContext) -> str:
        """Speak through the policy-checked ``audio.speak`` tool as the calling principal."""
        return self.main.speak(text, ctx)

    def start_background(self) -> None:
        self.main.start_background_services()
