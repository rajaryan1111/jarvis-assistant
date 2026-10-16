"""Native desktop front ends.

``DesktopController`` owns the voice session lifecycle (start, stop, quit, and the
no-duplicate-loop rule) without any GUI dependency, so it can be tested everywhere.
``macos/ultron_menubar.py`` is the AppKit shell that drives it.
"""

from ultron.desktop.controller import JOIN_TIMEOUT_SECONDS, DesktopController

__all__ = ["DesktopController", "JOIN_TIMEOUT_SECONDS"]
