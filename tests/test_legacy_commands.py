"""Legacy JARVIS command routing (main.py) with fake runners and an in-memory store."""

from __future__ import annotations

import json
import re
import threading

import pytest
from conftest import owner_hud

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
    voice, hud = CommandContext.local_voice(), owner_hud()
    for phrase in ("confirm shutdown", "confirm restart", "yes confirm shutdown", "confirm shut down now"):
        assert "spoken confirmation" in legacy.handle_command(phrase, voice).lower()  # voice can never approve
        assert "no pending action" in legacy.handle_command(phrase, hud).lower()
    assert fake_runner.calls == []


def test_shutdown_needs_request_then_matching_code(legacy, fake_runner):
    ctx = owner_hud()
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


def test_digit_words_are_accepted_in_typed_confirmations(legacy, fake_runner):
    ctx = owner_hud()
    code = _code(legacy.handle_command("restart system", ctx))
    words = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
             "6": "six", "7": "seven", "8": "eight", "9": "nine"}
    spoken = " ".join(words[d] for d in code)
    assert legacy.handle_command(f"confirm restart {spoken}", ctx) == "Restarting now, sir."
    assert fake_runner.calls[0][-1].endswith("to restart")


def test_confirming_a_different_action_is_rejected(legacy, fake_runner):
    ctx = owner_hud()
    code = _code(legacy.handle_command("clear cache", ctx))
    reply = legacy.handle_command(f"confirm shutdown {code}", ctx)
    assert "not the one you tried to confirm" in reply
    assert fake_runner.calls == []


def test_cache_cleanup_regression_requires_confirmation(legacy):
    """Regression: 'clear cache' used to rm -rf ~/Library/Caches/* immediately."""
    ctx = owner_hud()
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
    ctx = owner_hud()
    legacy.memory["reminders"] = [{"text": "a", "time": 0, "repeat": None}, {"text": "b", "time": 0, "repeat": None}]
    reply = legacy.handle_command("clear reminders", ctx)
    assert "delete all 2 reminders" in reply
    assert len(legacy.memory["reminders"]) == 2
    legacy.handle_command(f"confirm reminders {_code(reply)}", ctx)
    assert legacy.memory["reminders"] == []


def test_gate_blocks_confirmation_when_security_mode_cannot_verify(legacy, monkeypatch, fake_runner):
    monkeypatch.setattr(legacy, "security_check", lambda: (False, "Face verification failed."))
    ctx = owner_hud()
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
    reply = legacy.set_reminder(cmd, CommandContext.local_voice())
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


# ============================================================ memory file robustness
def test_unreadable_memory_file_is_preserved_not_silently_wiped(legacy, monkeypatch, tmp_path, capsys):
    """An existing but unreadable memory.json must be set aside, never silently replaced
    by an empty structure: the next save would erase the owner's notes and reminders."""
    target = tmp_path / "memory.json"
    original = b'{"notes": ["\xff\xfe not utf-8"]}'
    target.write_bytes(original)
    monkeypatch.setattr(legacy, "MEMORY_FILE", str(target))

    mem = legacy.load_memory()

    assert mem["notes"] == []  # the app still starts
    assert not target.exists()  # ...but the unreadable file is no longer the live one
    preserved = list(tmp_path.glob("memory.json.unreadable-*"))
    assert preserved and preserved[0].read_bytes() == original  # owner's data survives
    assert "unreadable" in capsys.readouterr().out


def test_memory_file_round_trips_non_ascii_notes(legacy, monkeypatch, tmp_path):
    """Non-ASCII notes must load correctly: the read is pinned to utf-8, not the locale."""
    target = tmp_path / "memory.json"
    notes = ["क्रिकेट टिप्पणी — café", "日本語のメモ"]
    target.write_text(json.dumps({"notes": notes}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(legacy, "MEMORY_FILE", str(target))

    assert legacy.load_memory()["notes"] == notes


def test_system_stat_failures_do_not_print_raw_exception_text(legacy, monkeypatch, capsys):
    """A failing system-stat read must not echo the exception text to the terminal.

    ``main`` printed raw exception text on several legacy error paths. Exception text can
    carry paths, URLs and (with a network library in the chain) credentials, so it is now
    redacted before printing. The user-visible answer is unchanged.
    """
    secret = "sk-" + "Zq7" * 8  # credential-shaped, built at runtime

    class Exploding:
        def cpu_percent(self, *a, **k):
            raise RuntimeError(f"read failed for https://h/?api_key={secret}")

        def virtual_memory(self):
            raise RuntimeError(f"ram failed with {secret}")

        def sensors_battery(self):
            raise RuntimeError(f"battery failed with {secret}")

    monkeypatch.setattr(legacy, "psutil", Exploding())
    from ultron.security.policy import CommandContext

    ctx = CommandContext.local_voice()
    # "cpu usage" is its own branch; "system status" builds the whole report and so
    # exercises the CPU/RAM/battery reads inside system_report().
    for phrase in ("cpu usage", "system status"):
        reply = legacy.handle_command(phrase, ctx)
        assert reply, phrase
    printed = capsys.readouterr().out
    assert secret not in printed, "a credential-shaped value reached the terminal"
    assert "[REDACTED]" in printed


def test_date_questions_are_answered_locally_not_sent_to_the_web(legacy):
    """"What's the date" must be answered by the assistant itself.

    The router had a `time` branch but no `date` branch, so every date question
    fell through to the Wikipedia/AI path and came back as "Wikipedia is
    unavailable right now." — a confusing failure for a question that needs no
    network at all. These phrasings must all be answered offline.
    """
    import datetime

    today = datetime.datetime.now()
    expected_day = today.strftime("%A")
    expected_date = today.strftime("%d %B %Y")
    for phrase in ("what is the date", "what's the date today", "what day is it", "tell me the date"):
        reply = legacy.handle_command(phrase)
        assert expected_day in reply, (phrase, reply)
        assert expected_date in reply, (phrase, reply)
        assert "wikipedia" not in reply.lower(), (phrase, reply)
        assert "openai" not in reply.lower(), (phrase, reply)


def test_date_branch_does_not_swallow_unsupported_day_questions(legacy):
    """The date branch must be explicit, not a bare "day" substring match.

    "what day is my birthday party" has no feature behind it. Answering it with
    today's date would be a confidently wrong answer, so it must fall through to
    the normal path instead.
    """
    for phrase in ("what day is my birthday party", "what day is the meeting"):
        reply = legacy.handle_command(phrase)
        assert "Today is" not in reply, (phrase, reply)


def test_time_question_still_answers_with_time_and_date(legacy):
    """The existing `time` branch must keep returning time (and the date)."""
    import datetime

    now = datetime.datetime.now()
    reply = legacy.handle_command("what time is it")
    assert now.strftime("%I:%M %p") in reply, reply
    assert now.strftime("%d %B %Y") in reply, reply


def test_basic_courtesy_works_with_no_provider_configured(legacy):
    """Greetings and thanks must be answered offline, with no API key at all.

    A brand-new install has no provider key (that is the default `doctor` state), and
    without these replies the assistant answered "My OpenAI key is not configured."
    to "hello" — it could not even say good morning. These are deterministic and
    need no network, so they must work on day one.
    """
    expected = {
        "hello": "Hello",
        "hi jarvis": "Hello",
        "hey jarvis": "Hello",
        "good morning": "morning",
        "good afternoon": "afternoon",
        "good evening": "evening",
        "good night": "night",
        "thank you": "welcome",
        "thanks jarvis": "welcome",
        "how are you": "well",
        "who are you": "ULTRON",
    }
    for phrase, needle in expected.items():
        reply = legacy.handle_command(phrase)
        assert needle.lower() in reply.lower(), (phrase, reply)
        assert "openai" not in reply.lower(), (phrase, reply)
        assert "not configured" not in reply.lower(), (phrase, reply)


def test_trivial_local_questions_are_answered_offline(legacy):
    """"flip a coin" and "roll a dice" need no provider and no network."""
    import re

    coin = legacy.handle_command("flip a coin")
    assert re.search(r"\b(heads|tails)\b", coin, re.I), coin
    saw_tails = any(
        re.search(r"\btails\b", legacy.handle_command("flip a coin"), re.I) for _ in range(12)
    )
    assert saw_tails, "a coin that never lands tails is not a coin"

    dice = legacy.handle_command("roll a dice")
    assert re.search(r"\b[1-6]\b", dice), dice


def test_social_replies_do_not_swallow_real_commands(legacy):
    """Matching is on the whole command, so real requests are never hijacked."""
    for phrase in ("open hello kitty", "search for thank you notes", "play good morning"):
        reply = str(legacy.handle_command(phrase))
        assert not reply.lower().startswith("hello, sir"), (phrase, reply)
        assert "you are welcome" not in reply.lower(), (phrase, reply)
