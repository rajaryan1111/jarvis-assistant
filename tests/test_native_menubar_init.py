"""Regression tests for the native menu-bar shell's Objective-C bridge.

The bug these guard: the app died at launch with

    File "macos/menubar.py", line 82, in init
        self = AppKit.NSObject.init(self)
    TypeError: Need 0 arguments, got 1

so it exited before its menu-bar icon ever appeared. In PyObjC an Objective-C
*instance* method is not reachable through the class object: the receiver is already
bound, so passing ``self`` explicitly is an extra argument to a selector that takes
none. The correct idiom is ``super().init()``.

Because a real Mac is not available in CI, :class:`FakeAppKit` models the one binding
rule that matters - class-level access to an instance method raises - and the **real**
``macos.menubar.main()`` is executed against it. That is what makes the old code fail
and the new code pass, without a GUI, a microphone or PyObjC.

What is NOT verified here, and cannot be: the actual menu-bar icon on screen, macOS
microphone permission, code signing, and anything that needs real hardware. Those are
in docs/MAC_SMOKE_TEST.md and must be run on the Mac.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MENUBAR = REPO_ROOT / "macos" / "menubar.py"


# ============================================================ a faithful fake AppKit
class _InstanceOnly:
    """Models PyObjC's binding rule.

    An Objective-C instance method is reachable through an *instance*. Accessing it
    through the class and passing the receiver as an argument is a TypeError - which
    is exactly the error the Mac reported.
    """

    def __init__(self, func):
        self._func = func

    def __get__(self, obj, objtype=None):
        if obj is None:
            raise TypeError("Need 0 arguments, got 1")
        return self._func.__get__(obj, objtype)


class FakeNSObject:
    @classmethod
    def alloc(cls):
        return cls.__new__(cls)

    @_InstanceOnly
    def init(self):
        return self


class FakeStatusItem:
    def __init__(self, length):
        self.length = length
        self.title = None
        self.tooltip = None
        self.menu = None
        self.main_thread = []

    def setTitle_(self, title):
        self.title = title

    def setToolTip_(self, tip):
        self.tooltip = tip

    def setMenu_(self, menu):
        self.menu = menu

    def performSelectorOnMainThread_withObject_waitUntilDone_(self, selector, obj, wait):
        self.main_thread.append((selector, obj, wait))
        if selector == "setTitle:":
            self.title = obj


class FakeNSStatusBar:
    def __init__(self):
        self.items = []

    @classmethod
    def systemStatusBar(cls):
        return cls._shared

    def statusItemWithLength_(self, length):
        item = FakeStatusItem(length)
        self.items.append(item)
        return item

    _shared = None


class FakeNSMenu(FakeNSObject):
    def initWithTitle_(self, title):
        self.title = title
        self.items = []
        return self

    def addItem_(self, item):
        self.items.append(item)


class FakeNSMenuItem(FakeNSObject):
    def initWithTitle_action_keyEquivalent_(self, title, action, key):
        self.title = title
        self.action = action
        self.key = key
        self.target = None
        return self

    def setTarget_(self, target):
        self.target = target

    @classmethod
    def separatorItem(cls):
        item = cls.__new__(cls)
        item.title = "-"
        item.action = None
        item.key = ""
        item.target = None
        item.separator = True
        return item


class FakeNSApplication:
    def __init__(self):
        self.delegate = None
        self.ran = False
        self.activated = False
        self.terminated = False

    @classmethod
    def sharedApplication(cls):
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def setDelegate_(self, delegate):
        self.delegate = delegate

    def run(self):
        self.ran = True
        return 0

    def activateIgnoringOtherApps_(self, flag):
        self.activated = bool(flag)

    def terminate_(self, sender):
        self.terminated = True
        self.ran = False

    _shared = None


class FakeNSAlert(FakeNSObject):
    #: every alert ever built, so a test can assert one was shown
    created: list = []

    def init(self):
        self.message = None
        self.informative = None
        self.shown = 0
        FakeNSAlert.created.append(self)
        return self

    def setMessageText_(self, text):
        self.message = text

    def setInformativeText_(self, text):
        self.informative = text

    def runModal(self):
        self.shown += 1


def make_fake_appkit():
    """A fresh fake AppKit namespace, so tests never share state."""
    module = types.ModuleType("AppKit")
    module.NSObject = FakeNSObject
    module.NSVariableStatusItemLength = -1.0

    FakeNSStatusBar._shared = FakeNSStatusBar()
    module.NSStatusBar = FakeNSStatusBar

    module.NSMenu = FakeNSMenu
    module.NSMenuItem = FakeNSMenuItem

    FakeNSApplication._shared = None
    module.NSApplication = FakeNSApplication
    # AppKit exposes NSApp as a module global pointing at the shared application
    module.NSApp = FakeNSApplication.sharedApplication()

    FakeNSAlert.created = []
    module.NSAlert = FakeNSAlert
    return module


@pytest.fixture()
def appkit(monkeypatch):
    """Install the fake AppKit and import the real menu-bar module."""
    module = make_fake_appkit()
    monkeypatch.setitem(sys.modules, "AppKit", module)
    # The shell must not shell out during startup. Every route a helper could take is
    # recorded: the raw subprocess module, and the audited wrapper the shell imports.
    calls = {"popen": [], "run": [], "run_command": []}

    def fake_popen(argv, *a, **kw):
        calls["popen"].append(list(argv))
        return types.SimpleNamespace(poll=lambda: 0, wait=lambda timeout=None: 0)

    def fake_run(argv, *a, **kw):
        calls["run"].append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_run_command(argv, *a, **kw):
        calls["run_command"].append(list(argv))
        return types.SimpleNamespace(
            ok=True, returncode=0, stdout="", stderr="", error=None, timed_out=False
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", fake_run)

    import macos.menubar as menubar

    monkeypatch.setattr(menubar, "run_command", fake_run_command, raising=False)
    # tolerant on purpose: against a regression that removed _KEEP_ALIVE this must not
    # mask the failure - it must let the test run and fail on the real defect.
    if hasattr(menubar, "_KEEP_ALIVE"):
        monkeypatch.setattr(menubar, "_KEEP_ALIVE", [], raising=True)
    return types.SimpleNamespace(module=module, menubar=menubar, calls=calls)


def run_main(appkit):
    """Execute the real entry point against the fake AppKit."""
    return appkit.menubar.main(["ultron"])


def bridge_of(appkit):
    """The Bridge instance the shell built and kept alive."""
    kept = getattr(appkit.menubar, "_KEEP_ALIVE", [])
    return next(obj for obj in kept if type(obj).__name__ == "Bridge")


# ============================================================ the launch crash itself
def test_the_app_gets_past_initialisation(appkit):
    """The reported bug: launch died in ``Bridge.init`` with a TypeError."""
    code = run_main(appkit)

    assert code == 0, "main() must complete; the app used to die in Bridge.init"
    assert appkit.module.NSApplication.sharedApplication().ran, "the run loop never started"


def test_initialisation_produces_a_usable_bridge(appkit):
    """``init`` must return a fully initialised object, not a half-built one."""
    run_main(appkit)

    bridge = next(obj for obj in appkit.menubar._KEEP_ALIVE if type(obj).__name__ == "Bridge")
    assert bridge is not None
    assert hasattr(bridge, "start_item"), "init() did not run: start_item was never set"
    assert bridge.start_item is not None, "the Start Listening item was never wired up"


def test_the_old_idiom_is_gone_from_the_source():
    """No Objective-C class-level call may pass ``self`` explicitly.

    ``super(Bridge, self).init()`` is fine; ``appkit.NSObject.init(self)`` is the bug.
    """
    tree = ast.parse(MENUBAR.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not node.args or not (isinstance(node.args[0], ast.Name) and node.args[0].id == "self"):
            continue
        called_on = node.func.value
        is_super = (
            isinstance(called_on, ast.Call)
            and isinstance(called_on.func, ast.Name)
            and called_on.func.id == "super"
        )
        if not is_super:
            offenders.append(f"line {node.lineno}: {ast.unparse(node)}")
    assert not offenders, (
        "an Objective-C method is invoked through its class with the receiver as an "
        "argument, which raises TypeError on macOS:\n  " + "\n  ".join(offenders)
    )


def test_every_objective_c_initialiser_override_calls_super():
    """Any Python ``init``/``initWith...`` in an Objective-C subclass must chain up."""
    tree = ast.parse(MENUBAR.read_text(encoding="utf-8"))
    checked = 0
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            if fn.name != "init" and not fn.name.startswith("initWith"):
                continue
            checked += 1
            supers = [
                n for n in ast.walk(fn)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Call)
                and isinstance(n.func.value.func, ast.Name)
                and n.func.value.func.id == "super"
            ]
            assert supers, f"{cls.name}.{fn.name} never calls super()"
    assert checked >= 1, "expected at least one Objective-C initialiser override to guard"


# ============================================================ startup behaviour
def test_the_menu_bar_icon_is_created_with_a_title(appkit):
    run_main(appkit)

    items = appkit.module.NSStatusBar.systemStatusBar().items
    assert len(items) == 1, "exactly one status item must be created"
    assert items[0].title == "o Ultron"
    assert items[0].tooltip == "Ultron voice assistant"
    assert items[0].menu is not None, "the status item has no menu, so it cannot be clicked"


def test_the_menu_has_every_control(appkit):
    run_main(appkit)

    menu = appkit.module.NSStatusBar.systemStatusBar().items[0].menu
    titles = [entry.title for entry in menu.items]
    assert titles == [
        "Start Listening", "Stop", "-",
        "Voice Diagnostics...", "Open HUD (optional)", "-", "Quit Ultron",
    ]
    actions = [entry.action for entry in menu.items if getattr(entry, "action", None)]
    assert actions == ["start:", "stop:", "diagnostics:", "hud:", "quit:"], actions
    assert menu.items[-1].key == "q", "Quit needs a keyboard equivalent"


def test_every_menu_action_is_targeted_at_the_bridge(appkit):
    """An untargeted item silently does nothing when clicked."""
    run_main(appkit)

    bridge = bridge_of(appkit)
    menu = appkit.module.NSStatusBar.systemStatusBar().items[0].menu
    for entry in menu.items:
        if getattr(entry, "separator", False):
            continue
        assert entry.target is bridge, f"{entry.title!r} has no target"


def test_startup_opens_no_browser_and_starts_no_helper(appkit):
    """Requirement: normal startup must not launch a browser or a subprocess."""
    run_main(appkit)

    assert appkit.calls["popen"] == [], f"startup spawned a process: {appkit.calls['popen']}"
    assert appkit.calls["run"] == [], f"startup ran a helper: {appkit.calls['run']}"
    assert appkit.calls["run_command"] == [], (
        f"startup ran a helper through the audited path: {appkit.calls['run_command']}"
    )


def test_the_delegate_is_set_and_kept_alive(appkit):
    """``NSApplication.delegate`` is weak, so a temporary delegate is a dangling pointer."""
    run_main(appkit)

    app = appkit.module.NSApplication.sharedApplication()
    assert app.delegate is not None, "no application delegate was installed"
    kept = [type(obj).__name__ for obj in appkit.menubar._KEEP_ALIVE]
    assert "AppDelegate" in kept, "the delegate is only weakly held by NSApplication"
    assert "Bridge" in kept, "the menu target is only weakly held by NSMenuItem.target"


def test_launching_activates_the_app(appkit):
    run_main(appkit)
    app = appkit.module.NSApplication.sharedApplication()
    # the delegate callback is what activates it; call it as AppKit would
    app.delegate.applicationDidFinishLaunching_(None)
    assert app.activated is True


# ============================================================ error reporting
def test_a_failure_at_startup_is_reported_not_silently_swallowed(appkit, monkeypatch):
    """The original bug was invisible: the app just vanished. Now it must say why."""
    import macos.menubar as menubar

    def boom(_appkit):
        raise RuntimeError("synthetic launch failure")

    monkeypatch.setattr(menubar, "_run", boom, raising=False)

    code = menubar.main(["ultron"])

    assert code == 1, "a failed launch must exit non-zero"
    assert appkit.module.NSAlert.created, "no alert was shown for a launch failure"
    alert = appkit.module.NSAlert.created[-1]
    assert alert.shown == 1, "the alert was built but never displayed"
    assert "RuntimeError" in f"{alert.message} {alert.informative}"


def test_diagnostics_shows_the_captured_output(appkit, monkeypatch):
    """A bundled app has no console, so the diagnostic output must reach the user."""
    run_main(appkit)
    bridge = bridge_of(appkit)

    monkeypatch.setattr(
        appkit.menubar, "_run_helper",
        lambda argv: "Voice status for wake word 'ultron'\n  [--] PICOVOICE_ACCESS_KEY set  : NO",
    )
    bridge.diagnostics_(None)

    assert appkit.module.NSAlert.created, "no alert was shown"
    alert = appkit.module.NSAlert.created[-1]
    assert "PICOVOICE_ACCESS_KEY" in (alert.informative or ""), alert.informative


def test_diagnostics_goes_through_the_audited_subprocess_path(appkit):
    """Helpers must use ``ultron.tools.process.run_command``: argv only, an absolute
    executable, a timeout and redacted output. A raw subprocess call here is how a
    diagnostic becomes a credential leak."""
    run_main(appkit)
    bridge = bridge_of(appkit)

    bridge.diagnostics_(None)

    assert appkit.calls["run_command"], "the diagnostic did not run through run_command"
    argv = appkit.calls["run_command"][-1]
    assert argv[-2:] == ["-m", "ultron"] or argv[-3:] == ["-m", "ultron", "voice-status"], argv
    assert "voice-status" in argv, argv
    assert argv[0] == sys.executable, "the helper must run under the same interpreter"


def test_a_helper_failure_is_reported_not_swallowed(appkit, monkeypatch):
    """A missing interpreter or a timed-out helper must say so, not do nothing."""
    run_main(appkit)
    bridge = bridge_of(appkit)

    monkeypatch.setattr(
        appkit.menubar, "run_command",
        lambda *a, **kw: types.SimpleNamespace(
            ok=False, returncode=None, stdout="", stderr="", error="not_found", timed_out=False
        ),
    )
    bridge.diagnostics_(None)

    alert = appkit.module.NSAlert.created[-1]
    assert "not_found" in (alert.informative or ""), alert.informative


def test_quit_stops_the_controller_and_terminates_the_app(appkit, monkeypatch):
    """Quit must go through the controller so the microphone is actually released."""
    import ultron.desktop as desktop

    stopped = []

    class RecordingController(desktop.DesktopController):
        def quit(self):
            stopped.append("quit")
            return super().quit()

        def stop(self):
            stopped.append("stop")
            return super().stop()

    monkeypatch.setattr(desktop, "DesktopController", RecordingController)

    run_main(appkit)
    bridge = bridge_of(appkit)
    app = appkit.module.NSApplication.sharedApplication()

    bridge.quit_(None)

    assert "quit" in stopped, "quit_ did not ask the controller to release the microphone"
    assert app.terminated is True, "quit_ did not terminate the application"


def test_stop_menu_item_stops_the_controller(appkit, monkeypatch):
    import ultron.desktop as desktop

    stopped = []

    class RecordingController(desktop.DesktopController):
        def stop(self):
            stopped.append("stop")
            return super().stop()

    monkeypatch.setattr(desktop, "DesktopController", RecordingController)

    run_main(appkit)
    bridge_of(appkit).stop_(None)

    assert stopped == ["stop"]
