"""Legacy JARVIS command routing (main.py) with fake runners and an in-memory store."""

from __future__ import annotations

import re
import threading

import pytest

from ultron.security.policy import Channel, CommandContext

OSASCRIPT_SHUTDOWN = ["/usr/bin/osascript", "-e", 'tell application "System Events" to shut down']


def _code(reply: str) -> str:
    match = re.search(r"confirm (\d{4})", reply)
    assert match, reply
    return match.group(1)


def test_importing_main_starts_no_threads_and_runs_nothing():
    before = {t.name for t in threading.enumerate()}
    import main  # noqa: F401

    after = {t.name for t in threading.enumerate()}
    assert not {"reminder-watcher", "intruder-watcher"} & (after - before)


def test_standalone_confirm_shutdown_does_not_shut_down(legacy, fake_runner):
    """Regression: 'confirm shutdown' alone used to shut the Mac down."""
    ctx = CommandContext.local_voice()
    for phrase in ("confirm shutdown", "confirm restart", "yes confirm shutdown", "confirm shut down now"):
        reply = legacy.handle_command(phrase, ctx)
        assert "no pending action" in reply.lower()
    assert fake_runner.calls == []


def test_shutdown_needs_request_then_matching_code(legacy, fake_runner):
    ctx = CommandContext.local_voice()
    reply = legacy.handle_command("shutdown system", ctx)
    code = _code(reply)
    assert fake_runner.calls == []
    assert "code" in legacy.handle_command("confirm shutdown", ctx).lower()  # no code given
    wrong = "0000" if code != "0000" else "1111"
    assert "not correct" in legacy.handle_command(f"confirm {wrong}", ctx)
    assert fake_runner.calls == []
    assert legacy.handle_command(f"confirm shutdown {code}", ctx) == "Shutting down now, sir."
    assert fake_runner.calls == [OSASCRIPT_SHUTDOWN]
    assert "no pending action" in legacy.handle_command(f"confirm {code}", ctx).lower()
    assert len(fake_runner.calls) == 1


def test_spoken_digit_words_are_accepted(legacy, fake_runner):
    ctx = CommandContext.local_voice()
    code = _code(legacy.handle_command("restart system", ctx))
    words = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
             "6": "six", "7": "seven", "8": "eight", "9": "nine"}
    spoken = " ".join(words[d] for d in code)
    assert legacy.handle_command(f"confirm restart {spoken}", ctx) == "Restarting now, sir."
    assert fake_runner.calls[0][-1].endswith("to restart")


def test_confirming_a_different_action_is_rejected(legacy, fake_runner):
    ctx = CommandContext.local_voice()
    code = _code(legacy.handle_command("clear cache", ctx))
    reply = legacy.handle_command(f"confirm shutdown {code}", ctx)
    assert "not the one you tried to confirm" in reply
    assert fake_runner.calls == []


def test_cache_cleanup_regression_requires_confirmation(legacy):
    """Regression: 'clear cache' used to rm -rf ~/Library/Caches/* immediately."""
    ctx = CommandContext.local_voice()
    caches = legacy.cache_dir_for_tests
    (caches / "app").mkdir()
    (caches / "app" / "data").write_text("x")
    reply = legacy.handle_command("clean my system", ctx)
    assert "permanently delete 1 item" in reply
    assert (caches / "app" / "data").exists()
    assert "cancelled" in legacy.handle_command("cancel", ctx)
    assert (caches / "app" / "data").exists()
    code = _code(legacy.handle_command("clear cache", ctx))
    assert "removed 1 cache item" in legacy.handle_command(f"confirm {code}", ctx)
    assert caches.is_dir() and not any(caches.iterdir())


def test_untrusted_or_missing_context_cannot_trigger_destructive_actions(legacy, fake_runner):
    for phrase in ("shutdown system", "restart system", "clear cache", "clear reminders"):
        assert "can't do that" in legacy.handle_command(phrase)  # no ctx -> untrusted
        assert "can't do that" in legacy.handle_command(phrase, CommandContext.untrusted("llm"))
    assert legacy.handle_command("confirm 1234").startswith("Confirmations are only accepted")
    assert fake_runner.calls == []


def test_confirmation_codes_are_per_principal(legacy, fake_runner):
    alice = CommandContext("session:alice", Channel.HUD, authenticated=True)
    mallory = CommandContext("session:mallory", Channel.HUD, authenticated=True)
    code = _code(legacy.handle_command("shutdown system", alice))
    assert "no pending action" in legacy.handle_command(f"confirm {code}", mallory).lower()
    assert fake_runner.calls == []


def test_cancel_only_intercepted_when_something_is_pending(legacy):
    ctx = CommandContext.local_voice()
    reply = legacy.handle_command("cancel", ctx)
    assert "cancelled that request" not in reply


def test_clear_reminders_requires_confirmation(legacy):
    ctx = CommandContext.local_voice()
    legacy.memory["reminders"] = [{"text": "a", "time": 0, "repeat": None}, {"text": "b", "time": 0, "repeat": None}]
    reply = legacy.handle_command("clear reminders", ctx)
    assert "delete all 2 reminders" in reply
    assert len(legacy.memory["reminders"]) == 2
    legacy.handle_command(f"confirm reminders {_code(reply)}", ctx)
    assert legacy.memory["reminders"] == []


def test_gate_blocks_confirmation_when_security_mode_cannot_verify(legacy, monkeypatch, fake_runner):
    monkeypatch.setattr(legacy, "security_check", lambda: (False, "Face verification failed."))
    ctx = CommandContext.local_voice()
    code = _code(legacy.handle_command("shutdown system", ctx))
    assert legacy.handle_command(f"confirm {code}", ctx) == "Face verification failed."
    assert fake_runner.calls == []


def test_security_mode_fails_closed_without_vision(legacy, monkeypatch):
    monkeypatch.setattr(legacy, "is_security_enabled", lambda: True)
    monkeypatch.setattr(legacy.vision, "face_available", lambda: False)
    state = {"enabled": True, "last_auth_time": 0, "auth_timeout_sec": 60}
    monkeypatch.setattr(legacy, "_get_security_state", lambda: state)
    ok, msg = legacy.security_check()
    assert ok is False and "vision" in msg.lower()


@pytest.mark.parametrize("cmd,fragment", [
    ("remind me in 10 minutes to drink water", "remind you to drink water"),
    ("remind me in 1 hour to stretch", "remind you to stretch"),
    ("remind me tomorrow at 9 am to go college", "remind you to go college"),
])
def test_reminder_descriptions_are_clean(legacy, cmd, fragment):
    reply = legacy.set_reminder(cmd)
    assert fragment in reply
    text = legacy.memory["reminders"][-1]["text"]
    assert not text.startswith(("s ", "9 am", "at "))


@pytest.mark.parametrize("text,expected", [
    ("stop", True), ("Exit.", True), ("goodbye jarvis", True),
    ("stop the music", False), ("shutdown system", False), ("exit full screen", False), ("", False),
])
def test_cli_exits_only_on_whole_exit_phrases(text, expected):
    import main

    assert main.is_exit_command(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("1234", "1234"), ("12 34", "1234"), ("four two one nine", "4219"), ("48 two one", "4821"),
    ("shutdown", None), ("12", None),
])
def test_extract_code(text, expected):
    import main

    assert main._extract_code(text) == expected


def test_vision_commands_degrade_without_optional_dependencies(legacy, monkeypatch):
    monkeypatch.setattr(legacy.vision, "cv2", None)
    reply = legacy.handle_command("do you see me", CommandContext.local_voice())
    assert "not installed" in reply and "vision" in reply


def test_jarvis_logic_delegates_with_untrusted_default(monkeypatch):
    import jarvis_logic
    import main

    seen = {}

    def fake_handle(text, ctx):
        seen["args"] = (text, ctx)
        return "ok"

    monkeypatch.setattr(main, "handle_command", fake_handle)
    assert jarvis_logic.handle_command("what time is it") == "ok"
    text, ctx = seen["args"]
    assert text == "what time is it" and ctx.channel is Channel.UNTRUSTED and not ctx.can_authorize
    trusted = CommandContext.local_voice()
    jarvis_logic.handle_command("hi", trusted)
    assert seen["args"][1] is trusted


def test_jarvis_logic_reports_errors_without_details(monkeypatch):
    import jarvis_logic
    import main

    def boom(text, ctx):
        raise RuntimeError("details")

    monkeypatch.setattr(main, "handle_command", boom)
    assert jarvis_logic.handle_command("x") == "Sorry, something went wrong in my brain."


def test_knowledge_base_degrades_without_rag_extra(monkeypatch, tmp_path):
    import knowledge

    def missing():
        raise knowledge.RagUnavailable("sentence-transformers is not installed.")

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.txt").write_text("Binary search runs in logarithmic time.")
    monkeypatch.setattr(knowledge, "DOCS_DIR", str(docs))
    monkeypatch.setattr(knowledge, "INDEX_FILE", str(tmp_path / "index.json"))
    monkeypatch.setattr(knowledge, "_get_model", missing)
    assert "cannot build the knowledge base" in knowledge.rebuild_knowledge_base()
    assert not (tmp_path / "index.json").exists()
