"""Native macOS menu-bar shell for Ultron.

Deliberately thin. All lifecycle logic - one listening loop, releasing the microphone
on quit, a bounded join - lives in ``ultron.desktop.controller.DesktopController``,
which is tested without a Mac. This file only maps that controller onto AppKit.

Requires the optional ``macos`` extra (PyObjC). It is not imported by anything else,
so a machine without PyObjC is unaffected.

PyObjC notes that matter here (they are the difference between an app that starts and
one that dies before its icon appears):

* **Initialisers.** Inside a Python subclass of an Objective-C class, call the
  superclass initialiser with ``super()``. Objective-C *instance* methods are not
  reachable through the class object, so ``AppKit.NSObject.init(self)`` passes the
  receiver as an explicit argument to a selector that takes none and raises
  ``TypeError: Need 0 arguments, got 1``. That is the crash this module used to have.
* **Weak references.** ``NSApplication.delegate`` and ``NSMenuItem.target`` are
  ``assign``/``weak``. A delegate or target that only exists as a temporary is
  collected by Python and becomes a dangling pointer, so every long-lived Objective-C
  object we hand to AppKit is kept in :data:`_KEEP_ALIVE`.
* **Thread affinity.** AppKit is not thread-safe, so the status item is only ever
  touched on the main thread.
"""

from __future__ import annotations

import sys

from ultron.tools.process import run_command

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

#: Strong references to Objective-C objects AppKit holds weakly. Without these the
#: delegate and the menu target can be garbage-collected while the app is running.
_KEEP_ALIVE: list = []

#: Bound on a helper process started from a menu action. A diagnostic that hangs must
#: not make the menu bar unresponsive.
HELPER_TIMEOUT_SECONDS = 30.0

#: Cap on text shown in an alert, so a huge report cannot produce an unusable dialog.
ALERT_MAX_CHARS = 4000


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


def _run_helper(argv: list[str]) -> str:
    """Run a helper command and return its output. Never raises.

    Goes through ``ultron.tools.process.run_command`` - the repository's single audited
    subprocess path: argv only (never a shell string), an absolute executable, a timeout
    that kills the child, and **redacted** output, so a diagnostic can never turn into a
    credential leak. A bundled app started from Finder has no console, so anything a
    helper prints is otherwise invisible - which is what made the diagnostics menu item
    look like it did nothing.
    """
    result = run_command(argv, timeout=HELPER_TIMEOUT_SECONDS, max_output=ALERT_MAX_CHARS)
    if result.error == "timeout":
        return f"Timed out after {HELPER_TIMEOUT_SECONDS:g}s: {' '.join(argv)}"
    if result.error is not None:
        return f"Could not run {' '.join(argv)}: {result.error}"
    if not result.ok:
        # A non-zero exit is still useful information; show it rather than hiding it.
        return "\n".join(
            part for part in (result.stdout, result.stderr) if part
        ).strip() or f"The command failed with exit code {result.returncode}."
    text = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    return text or "(the command produced no output)"


def _show_alert(appkit, title: str, message: str) -> None:
    """Show a modal alert. Only ever called from an explicit menu action or a fatal
    error - never at startup, and never on wake-word activation."""
    text = message if len(message) <= ALERT_MAX_CHARS else message[:ALERT_MAX_CHARS] + "\n..."
    alert = appkit.NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(text)
    alert.runModal()


def _run(appkit) -> int:
    """Build the status item, the menu and the application delegate, then run."""
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

    item = appkit.NSStatusBar.systemStatusBar().statusItemWithLength_(
        appkit.NSVariableStatusItemLength
    )
    item.setTitle_("o Ultron")
    item.setToolTip_("Ultron voice assistant")
    reporter.item = item

    class Bridge(appkit.NSObject):
        """The menu target. Every action is explicit; none runs at startup."""

        def init(self):
            # The PyObjC idiom: super() gives the already-bound superclass
            # initialiser. Writing `appkit.NSObject.init(self)` instead passes the
            # receiver as an argument to a selector that takes none, which raises
            # TypeError and kills the app before the menu-bar icon appears.
            self = super().init()
            if self is None:
                return None
            self.start_item = None
            return self

        def start_(self, sender):
            if not controller.start() and self.start_item is not None:
                self.start_item.setTitle_("Already Listening")

        def stop_(self, sender):
            controller.stop()

        def diagnostics_(self, sender):
            # Explicit menu action only. The output is shown in a dialog because a
            # bundled app launched from Finder has no console to print it to.
            _show_alert(appkit, "Ultron voice diagnostics",
                        _run_helper([sys.executable, "-m", "ultron", "voice-status"]))

        def hud_(self, sender):
            """Explicit user action only - never called automatically."""
            output = _run_helper([sys.executable, "-m", "ultron", "open-hud"])
            if "Opened the HUD" not in output:
                _show_alert(appkit, "Ultron", output)

        def quit_(self, sender):
            controller.quit()
            appkit.NSApp.terminate_(None)

    bridge = Bridge.alloc().init()
    _KEEP_ALIVE.append(bridge)

    menu = appkit.NSMenu.alloc().initWithTitle_("Ultron")

    def add(title, action, key=""):
        entry = appkit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        entry.setTarget_(bridge)
        menu.addItem_(entry)
        return entry

    bridge.start_item = add("Start Listening", "start:")
    add("Stop", "stop:")
    menu.addItem_(appkit.NSMenuItem.separatorItem())
    add("Voice Diagnostics...", "diagnostics:")
    add("Open HUD (optional)", "hud:")
    menu.addItem_(appkit.NSMenuItem.separatorItem())
    add("Quit Ultron", "quit:", "q")
    item.setMenu_(menu)

    class AppDelegate(appkit.NSObject):
        def applicationDidFinishLaunching_(self, notification):
            appkit.NSApp.activateIgnoringOtherApps_(True)

        def applicationWillTerminate_(self, notification):
            # Graceful shutdown: stop the worker and let it release the microphone.
            controller.quit()

    app = appkit.NSApplication.sharedApplication()
    # The delegate is held weakly by NSApplication, so it is kept alive here too.
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    _KEEP_ALIVE.append(delegate)
    app.run()
    return 0


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - needs AppKit
    """Build the status item, the menu and the application delegate, then run."""
    _require_appkit()
    import AppKit

    try:
        return _run(AppKit)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - a bundled app has no console
        # Without this the app simply vanishes with the traceback going nowhere, which
        # is exactly how the original launch crash presented. Only the exception *type*
        # is shown: its text can carry a path, a token or part of a key.
        try:
            _show_alert(
                AppKit,
                "Ultron could not start",
                f"{type(exc).__name__}: {exc}\n\n"
                "The menu-bar app stopped before it was ready. Run "
                "`uv run ultron voice-status` for a static check of the voice "
                "pipeline, and `uv run python main.py` for the terminal voice loop.",
            )
        except Exception as alert_error:  # noqa: BLE001 - never mask the original failure
            print(
                f"Ultron could not start ({type(exc).__name__}), and the error dialog "
                f"failed too ({type(alert_error).__name__}).",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
