"""Tool registry and system tools. All runners are fakes; all deletions happen under tmp_path."""

from __future__ import annotations

import os

import pytest

from ultron.security.confirmation import ConfirmationStore
from ultron.security.policy import Channel, CommandContext, RiskTier
from ultron.tools.base import FunctionTool, ToolResult, ToolSpec
from ultron.tools.registry import ToolRegistry
from ultron.tools.system import OSASCRIPT, ClearUserCachesTool, PowerTool

USER = CommandContext("session:alice", Channel.HUD, authenticated=True)
OTHER = CommandContext("session:mallory", Channel.HUD, authenticated=True)
UNTRUSTED = CommandContext.untrusted("document")


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _registry(fake_runner, tmp_path, platform="darwin", clock=None):
    clock = clock or Clock()
    reg = ToolRegistry(confirmations=ConfirmationStore(clock=clock), platform=platform, monotonic=clock)
    reg.register(PowerTool("shut down", runner=fake_runner))
    reg.register(PowerTool("restart", runner=fake_runner))
    caches = tmp_path / "Caches"
    caches.mkdir(exist_ok=True)
    reg.register(ClearUserCachesTool(cache_dir=caches))
    return reg, caches


def test_destructive_tools_declare_risk_and_platform(fake_runner):
    for tool in (PowerTool("shut down", fake_runner), PowerTool("restart", fake_runner), ClearUserCachesTool()):
        assert tool.spec.risk is RiskTier.DESTRUCTIVE
        assert tool.spec.platforms == {"darwin"}
    with pytest.raises(ValueError):
        PowerTool("rm -rf /", fake_runner)


def test_request_creates_pending_action_and_runs_nothing(fake_runner, tmp_path):
    reg, _ = _registry(fake_runner, tmp_path)
    ctx = CommandContext("session:alice", Channel.HUD, authenticated=True)
    outcome = reg.request("system.shutdown", {}, ctx)
    assert outcome.status == "pending_confirmation"
    assert outcome.pending.code in outcome.message
    assert fake_runner.calls == []
    assert ctx.events and ctx.events[-1][0] == "pending_action"


def test_confirm_with_code_runs_fixed_argv_once(fake_runner, tmp_path):
    reg, _ = _registry(fake_runner, tmp_path)
    pending = reg.request("system.shutdown", {}, USER).pending
    outcome = reg.confirm(USER, code=pending.code, expected_tool="system.shutdown")
    assert outcome.status == "completed"
    assert fake_runner.calls == [[OSASCRIPT, "-e", 'tell application "System Events" to shut down']]
    assert reg.confirm(USER, code=pending.code).status == "denied"  # single use
    assert len(fake_runner.calls) == 1


def test_standalone_confirm_without_pending_action_does_nothing(fake_runner, tmp_path):
    """Regression: 'confirm shutdown' used to shut down with no prior request."""
    reg, _ = _registry(fake_runner, tmp_path)
    for kwargs in ({}, {"code": "1234"}, {"code": "1234", "expected_tool": "system.shutdown"}, {"action_id": "x"}):
        assert reg.confirm(USER, **kwargs).status == "denied"
    assert fake_runner.calls == []


def test_untrusted_context_can_neither_request_nor_confirm(fake_runner, tmp_path):
    reg, _ = _registry(fake_runner, tmp_path)
    assert reg.request("system.shutdown", {}, UNTRUSTED).status == "denied"
    pending = reg.request("system.shutdown", {}, USER).pending
    assert reg.confirm(UNTRUSTED, code=pending.code).status == "denied"
    assert reg.confirm(OTHER, code=pending.code).status == "denied"
    assert reg.confirm(OTHER, action_id=pending.action_id).status == "denied"
    assert fake_runner.calls == []


def test_expired_confirmation_does_not_run(fake_runner, tmp_path):
    clock = Clock()
    reg, _ = _registry(fake_runner, tmp_path, clock=clock)
    pending = reg.request("system.restart", {}, USER).pending
    clock.t += 61
    assert reg.confirm(USER, code=pending.code).status == "denied"
    assert fake_runner.calls == []


def test_confirming_the_wrong_tool_is_rejected(fake_runner, tmp_path):
    reg, _ = _registry(fake_runner, tmp_path)
    pending = reg.request("system.clear_user_caches", {}, USER).pending
    assert reg.confirm(USER, code=pending.code, expected_tool="system.shutdown").status == "denied"
    assert fake_runner.calls == []


def test_gate_can_block_a_confirmed_action(fake_runner, tmp_path):
    reg, _ = _registry(fake_runner, tmp_path)
    pending = reg.request("system.shutdown", {}, USER).pending
    outcome = reg.confirm(USER, code=pending.code, gate=lambda p: (False, "Face check failed."))
    assert outcome.status == "denied" and outcome.message == "Face check failed."
    assert fake_runner.calls == []


def test_cancel_discards_pending_action(fake_runner, tmp_path):
    reg, _ = _registry(fake_runner, tmp_path)
    pending = reg.request("system.shutdown", {}, USER).pending
    assert reg.cancel(USER).status == "cancelled"
    assert reg.confirm(USER, code=pending.code).status == "denied"
    assert fake_runner.calls == []


def test_tools_are_unavailable_off_macos(fake_runner, tmp_path):
    reg, _ = _registry(fake_runner, tmp_path, platform="linux")
    outcome = reg.request("system.shutdown", {}, USER)
    assert outcome.status == "unavailable" and "macOS" in outcome.message


def test_unknown_tool_and_duplicate_registration(fake_runner, tmp_path):
    reg, _ = _registry(fake_runner, tmp_path)
    assert reg.request("system.format_disk", {}, USER).status == "unavailable"
    with pytest.raises(ValueError):
        reg.register(PowerTool("restart", fake_runner))


def test_failing_power_command_reports_failure(tmp_path):
    from tests.conftest import FakeRunner

    runner = FakeRunner(returncode=1)
    reg, _ = _registry(runner, tmp_path)
    pending = reg.request("system.restart", {}, USER).pending
    assert reg.confirm(USER, code=pending.code).status == "failed"


def test_observe_tools_run_without_confirmation(tmp_path):
    reg = ToolRegistry(platform="linux")
    spec = ToolSpec("status.read", "Read status.", RiskTier.OBSERVE)
    reg.register(FunctionTool(spec, lambda args: ToolResult(True, "all good")))
    assert reg.request("status.read", {}, UNTRUSTED).message == "all good"


def test_tool_exceptions_are_contained(tmp_path):
    reg = ToolRegistry(platform="linux")
    spec = ToolSpec("boom", "Explodes.", RiskTier.ROUTINE)

    def explode(_args):
        raise RuntimeError("secret detail")

    reg.register(FunctionTool(spec, explode))
    outcome = reg.request("boom", {}, USER)
    assert outcome.status == "failed" and "secret detail" not in outcome.message


# ----------------------------------------------------------------- cache cleanup
def _populate(caches, outside):
    (caches / "com.example.app").mkdir()
    (caches / "com.example.app" / "blob.bin").write_bytes(b"x" * 10)
    (caches / "loose.cache").write_text("cache")
    precious = outside / "precious.txt"
    precious.write_text("user data")
    (outside / "dir").mkdir()
    (outside / "dir" / "keep.txt").write_text("keep")
    os.symlink(precious, caches / "link-to-file")
    os.symlink(outside / "dir", caches / "link-to-dir")
    return precious


def test_cache_cleanup_requires_confirmation_and_deletes_only_cache_entries(fake_runner, tmp_path):
    """Regression: 'clear cache' used to run `rm -rf ~/Library/Caches/*` immediately."""
    reg, caches = _registry(fake_runner, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    precious = _populate(caches, outside)

    outcome = reg.request("system.clear_user_caches", {}, USER)
    assert outcome.status == "pending_confirmation"
    assert "4 items" in outcome.message and str(caches) in outcome.message
    assert len(list(caches.iterdir())) == 4  # nothing deleted before confirmation

    done = reg.confirm(USER, code=outcome.pending.code, expected_tool="system.clear_user_caches")
    assert done.status == "completed" and done.result.data == {"removed": 4, "failed": 0}
    assert caches.is_dir() and list(caches.iterdir()) == []  # the folder itself is kept
    # Symlinks were removed as links; their targets outside the cache folder survive.
    assert precious.read_text() == "user data"
    assert (outside / "dir" / "keep.txt").read_text() == "keep"
    assert fake_runner.calls == []  # no shell / subprocess involved


def test_cache_tool_refuses_symlinked_or_missing_cache_dir(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "important.txt").write_text("do not delete")
    link = tmp_path / "Caches"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(FileNotFoundError):
        ClearUserCachesTool(cache_dir=link).run({})
    assert (real / "important.txt").exists()
    with pytest.raises(FileNotFoundError):
        ClearUserCachesTool(cache_dir=tmp_path / "missing").preview({})


def test_cache_tool_failure_via_registry_is_reported_not_raised(fake_runner, tmp_path):
    reg = ToolRegistry(platform="darwin")
    reg.register(ClearUserCachesTool(cache_dir=tmp_path / "missing"))
    outcome = reg.request("system.clear_user_caches", {}, USER)
    assert outcome.status == "failed" and "did nothing" in outcome.message
