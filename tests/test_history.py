"""The local chat transcript (ultron/history.py): bounded, redacted, opt-out - plus the
export/import CLI verbs and the real ``main.handle_command`` fallback integration.

No network exists in this module and none is used here: unit tests pass explicit env dicts,
integration tests drive the production fallback path with a scripted ``ask_gpt``, and CLI
tests call ``ultron.cli.main`` with argv.
"""

from __future__ import annotations

import json

import pytest
from conftest import owner_hud

from ultron import history
from ultron.cli import main as cli_main

HUD = owner_hud


@pytest.fixture
def env(tmp_path):
    """History on and isolated to tmp_path (the global conftest default is off)."""
    return {"ULTRON_DATA_DIR": str(tmp_path), "ULTRON_HISTORY": "1"}


# ============================================================ 1. store behaviour
def test_records_an_exchange_and_replays_it_as_chat_context(tmp_path, env):
    history.record_exchange("first question", "first answer", env)
    history.record_exchange("second question", "second answer", env)
    assert history.recent(env=env) == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "second answer"},
    ]
    assert (tmp_path / history.HISTORY_FILENAME).is_file()


def test_disabled_by_flag_records_and_replays_nothing(tmp_path, env):
    env["ULTRON_HISTORY"] = "0"
    history.record_exchange("q", "a", env)
    assert history.recent(env=env) == [] and history.records(env) == []
    assert not (tmp_path / history.HISTORY_FILENAME).exists()


def test_transcript_is_bounded_and_keeps_the_newest(tmp_path, env):
    rounds = history.MAX_RECORDS // 2 + 30
    for i in range(rounds):
        history.record_exchange(f"q{i}", f"a{i}", env)
    stored = history.records(env)
    assert len(stored) == history.MAX_RECORDS
    assert stored[-1]["text"] == f"a{rounds - 1}"
    assert stored[0]["role"] == "user"


def test_texts_are_redacted_and_truncated_on_write(tmp_path, env):
    secret = "gsk_" + "A1b2C3d4E5f6G7h8" * 3
    history.record_exchange(f"my key is {secret}", "x" * (history.MAX_TEXT_CHARS + 50), env)
    stored = history.records(env)
    assert secret not in stored[0]["text"] and "REDACTED" in stored[0]["text"]
    assert len(stored[1]["text"]) == history.MAX_TEXT_CHARS


def test_recent_limit_is_capped(tmp_path, env):
    for i in range(40):
        history.record_exchange(f"q{i}", f"a{i}", env)
    assert len(history.recent(limit=1000, env=env)) == history.MAX_CONTEXT_LIMIT
    assert len(history.recent(limit=2, env=env)) == 2
    assert history.recent(limit=0, env=env) == []


def test_corrupt_lines_cost_the_line_not_the_transcript(tmp_path, env):
    history.record_exchange("good", "pair", env)
    with (tmp_path / history.HISTORY_FILENAME).open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
        handle.write(json.dumps({"ts": 1, "role": "system", "text": "wrong role"}) + "\n")
        handle.write(json.dumps({"ts": 1, "role": "user", "text": ""}) + "\n")
        handle.write(json.dumps({"ts": 1, "role": "user", "content": "no text field"}) + "\n")
    assert [r["text"] for r in history.records(env)] == ["good", "pair"]


def test_unwritable_history_never_raises(tmp_path, env):
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    env["ULTRON_DATA_DIR"] = str(blocker / "nested")  # mkdir under a plain file fails
    history.record_exchange("q", "a", env)  # warning only, no exception
    assert history.recent(env=env) == []


# ============================================================ 2. export and import
def test_export_writes_the_redacted_transcript(tmp_path, env):
    history.record_exchange("q1", "a1", env)
    dest = tmp_path / "out" / "export.jsonl"
    assert history.export_to(dest, env) == 2
    lines = dest.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["text"] == "q1"


def test_export_works_even_when_recording_is_disabled(tmp_path, env):
    history.record_exchange("q1", "a1", env)
    env["ULTRON_HISTORY"] = "0"
    assert history.export_to(tmp_path / "export.jsonl", env) == 2  # owner action on existing data


def test_import_appends_validated_records_and_skips_junk(tmp_path, env):
    src = tmp_path / "import.jsonl"
    src.write_text("\n".join([
        json.dumps({"ts": 1700000000.5, "role": "user", "text": "imported question"}),
        json.dumps({"ts": 1700000001, "role": "assistant", "text": "imported answer"}),
        "{broken",
        json.dumps({"ts": 1, "role": "system", "text": "wrong role"}),
        json.dumps({"ts": "soon", "role": "user", "text": "bad ts"}),
        json.dumps([1, 2, 3]),
    ]) + "\n", encoding="utf-8")
    assert history.import_from(src, env) == (2, 4)
    stored = history.records(env)
    assert [r["text"] for r in stored] == ["imported question", "imported answer"]
    assert stored[0]["ts"] == 1700000000.5  # original timestamps survive


def test_import_redacts_again_and_merges_with_existing(tmp_path, env):
    history.record_exchange("local q", "local a", env)
    secret = "gsk_" + "A1b2C3d4E5f6G7h8" * 3
    src = tmp_path / "import.jsonl"
    src.write_text(json.dumps({"ts": 1.0, "role": "user", "text": f"key {secret}"}) + "\n",
                   encoding="utf-8")
    assert history.import_from(src, env) == (1, 0)
    stored = history.records(env)
    assert len(stored) == 3 and secret not in stored[-1]["text"]


def test_import_missing_file_and_oversize_file_are_refused(tmp_path, env, monkeypatch):
    with pytest.raises(FileNotFoundError):
        history.import_from(tmp_path / "nope.jsonl", env)
    monkeypatch.setattr(history, "MAX_IMPORT_BYTES", 10)
    big = tmp_path / "big.jsonl"
    big.write_text(json.dumps({"ts": 1, "role": "user", "text": "longer than ten"}) + "\n",
                   encoding="utf-8")
    with pytest.raises(ValueError):
        history.import_from(big, env)


def test_import_record_cap_counts_the_rest_as_skipped(tmp_path, env, monkeypatch):
    monkeypatch.setattr(history, "MAX_IMPORT_RECORDS", 2)
    many = tmp_path / "many.jsonl"
    many.write_text("".join(json.dumps({"ts": i, "role": "user", "text": f"m{i}"}) + "\n"
                            for i in range(5)), encoding="utf-8")
    assert history.import_from(many, env) == (2, 3)
    assert len(history.records(env)) == 2


def test_clear_deletes_the_transcript(tmp_path, env):
    history.record_exchange("q", "a", env)
    assert history.clear(env) is True
    assert history.records(env) == []
    assert history.clear(env) is False  # second clear is a no-op, not an error


# ============================================================ 3. chat-fallback integration (main.py)
def test_chat_fallback_gets_previous_turns_and_records_them(legacy, monkeypatch, tmp_path):
    monkeypatch.setenv("ULTRON_HISTORY", "1")
    monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path))
    seen = []

    def scripted(prompt, chat_history):
        seen.append(list(chat_history))
        return f"reply to {prompt}"

    monkeypatch.setattr(legacy, "ask_gpt", scripted)
    monkeypatch.setattr(legacy, "agent_provider", lambda: None)  # force the chat fallback

    assert legacy.handle_command("tell me something nice", HUD()) == "reply to tell me something nice"
    assert seen[0] == []  # first turn: nothing stored yet
    assert legacy.handle_command("tell me something nicer", HUD()) == "reply to tell me something nicer"
    assert seen[1] == [
        {"role": "user", "content": "tell me something nice"},
        {"role": "assistant", "content": "reply to tell me something nice"},
    ]
    assert [r["text"] for r in history.records()] == [
        "tell me something nice", "reply to tell me something nice",
        "tell me something nicer", "reply to tell me something nicer",
    ]


def test_chat_fallback_is_untouched_when_history_is_off(legacy, monkeypatch, tmp_path):
    monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path))  # conftest keeps ULTRON_HISTORY=0
    seen = []
    monkeypatch.setattr(legacy, "ask_gpt", lambda prompt, chat_history: seen.append(list(chat_history))
                        or "gpt-reply")
    monkeypatch.setattr(legacy, "agent_provider", lambda: None)
    assert legacy.handle_command("tell me something nice", HUD()) == "gpt-reply"
    assert legacy.handle_command("tell me something nice", HUD()) == "gpt-reply"
    assert seen == [[], []]
    assert not (tmp_path / history.HISTORY_FILENAME).exists()


def test_chat_provider_failure_keeps_history_valid_and_subsequent_turn_works(legacy, monkeypatch, tmp_path):
    """A provider exception inside ask_gpt degrades to the friendly apology, leaves the
    JSONL transcript valid with any credential-shaped text redacted, and the next turn
    still runs cleanly with the earlier context intact."""
    monkeypatch.setenv("ULTRON_HISTORY", "1")
    monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path))
    fake_secret = "sk-" + "histErr0" * 6

    class BoomClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kw):
                    raise RuntimeError(f"upstream timeout while carrying {fake_secret}")

    monkeypatch.setattr(legacy, "OPENAI_API_KEY", fake_secret)
    monkeypatch.setattr(legacy, "client", BoomClient())
    monkeypatch.setattr(legacy, "agent_provider", lambda: None)

    apology = legacy.handle_command(f"explain my key {fake_secret}", HUD())
    assert apology == "Sorry, I am having trouble thinking right now."
    raw = (tmp_path / history.HISTORY_FILENAME).read_text(encoding="utf-8")
    assert fake_secret not in raw
    parsed = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert len(parsed) == 2 and parsed[1]["text"] == apology

    seen = []
    monkeypatch.setattr(legacy, "ask_gpt", lambda prompt, chat_history: seen.append(list(chat_history))
                        or "recovered")
    assert legacy.handle_command("and now a second question", HUD()) == "recovered"
    assert len(seen[0]) == 2 and seen[0][1]["content"] == apology


# ============================================================ 4. CLI verbs
def test_cli_history_show_export_import_clear(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ULTRON_HISTORY", "1")
    monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path))
    history.record_exchange("hello there", "general reply", None)

    assert cli_main(["history"]) == 0
    out = capsys.readouterr().out
    assert "2 record(s)" in out and "[user] hello there" in out

    export_file = tmp_path / "exported.jsonl"
    assert cli_main(["history", "--export", str(export_file)]) == 0
    assert "exported 2 record(s)" in capsys.readouterr().out and export_file.is_file()

    assert cli_main(["history", "--clear"]) == 0
    assert "cleared" in capsys.readouterr().out
    assert history.records(None) == []

    assert cli_main(["history", "--import", str(export_file)]) == 0
    assert "imported 2 record(s), skipped 0" in capsys.readouterr().out
    assert [r["text"] for r in history.records(None)] == ["hello there", "general reply"]

    assert cli_main(["history", "--import", str(tmp_path / "missing.jsonl")]) == 1
    assert "import failed" in capsys.readouterr().err
