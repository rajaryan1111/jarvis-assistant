"""Native macOS menu-bar shell for Ultron.

Deliberately thin. All lifecycle logic - one listening loop, releasing the microphone
on quit, a bounded join - lives in ``ultron.desktop.controller.DesktopController``,
which is tested without a Mac. This file only maps that controller onto AppKit.

Requires the optional ``macos`` extra (PyObjC). It is not imported by anything else,
so a machine without PyObjC is unaffected.
"""

from __future__ import annotations

import subprocess
import sys

#: Status-bar titles per session state. Short: the menu bar is narrow.
TITLES = {
    "IDLE": "o Ultron",
    "WAKE_WORD_DETECTED": "* Ultron",
    "LISTENING_FOR_COMMAND": "Listening",
    "TRANSCRIBING": "Transcribing",
    "PROCESSING_REQUEST": "Thinking",
    "SPEAKING_RESPONSE": "Speaking",
    "ERROR": "! Ultron",
    "STOPPED": "o Ultron",
}


def _require_appkit() -> None:
    try:
        import AppKit  # noqa: F401
    except ImportError as exc:  # pragma: no cover - host dependent
        raise SystemExit(
            "The native menu-bar app needs PyObjC.\n"
            "    uv sync --extra macos\n"
            "or use the voice loop without a window:\n"
            "    uv run python main.py\n"
            f"(import error: {exc})"
        ) from exc


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - needs AppKit
    """Build the status item, the menu and the application delegate, then run."""
    _require_appkit()
    import AppKit

    from ultron.desktop import DesktopController

    controller = DesktopController()

    class Reporter:
        """Applies state changes to the status item, always on the main thread."""

        def __init__(self) -> None:
            self.item = None

        def transition(self, previous, current) -> None:
            name = getattr(current, "name", str(current))
            title = TITLES.get(name, "o Ultron")
            item = self.item
            if item is None:
                return
            # AppKit is not thread-safe: never touch the item from the voice thread.
            item.performSelectorOnMainThread_withObject_waitUntilDone_(
                "setTitle:", title, False
            )

    reporter = Reporter()
    controller._on_transition = reporter.transition

    item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
        AppKit.NSVariableStatusItemLength
    )
    item.setTitle_("o Ultron")
    item.setToolTip_("Ultron voice assistant")
    reporter.item = item

    class Bridge(AppKit.NSObject):
        """The menu target. Every action is explicit; none runs at startup."""

        def init(self):
            self = AppKit.NSObject.init(self)
            self.start_item = None
            return self

        def start_(self, sender):
            if not controller.start() and self.start_item is not None:
                self.start_item.setTitle_("Already Listening")

        def stop_(self, sender):
            controller.stop()

        def diagnostics_(self, sender):
            subprocess.Popen([sys.executable, "-m", "ultron", "voice-status"])

        def hud_(self, sender):
            """Explicit user action only - never called automatically."""
            subprocess.Popen([sys.executable, "-m", "ultron", "open-hud"])

        def quit_(self, sender):
            controller.quit()
            AppKit.NSApp.terminate_(None)

    bridge = Bridge.alloc().init()

    menu = AppKit.NSMenu.alloc().initWithTitle_("Ultron")

    def add(title, action, key=""):
        entry = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        entry.setTarget_(bridge)
        menu.addItem_(entry)
        return entry

    bridge.start_item = add("Start Listening", "start:")
    add("Stop", "stop:")
    menu.addItem_(AppKit.NSMenuItem.separatorItem())
    add("Voice Diagnostics...", "diagnostics:")
    add("Open HUD (optional)", "hud:")
    menu.addItem_(AppKit.NSMenuItem.separatorItem())
    add("Quit Ultron", "quit:", "q")
    item.setMenu_(menu)

    class AppDelegate(AppKit.NSObject):
        def applicationDidFinishLaunching_(self, notification):
            AppKit.NSApp.activateIgnoringOtherApps_(True)

        def applicationWillTerminate_(self, notification):
            # Graceful shutdown: stop the worker and let it release the microphone.
            controller.quit()

    app = AppKit.NSApplication.sharedApplication()
    app.setDelegate_(AppDelegate.alloc().init())
    app.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
