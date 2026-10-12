"""M2.1: one configurable model setting, bounded legacy OpenAI calls that never echo errors."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import owner_hud

from ultron.config import (
    DEFAULT_CHAT_MODEL,
    OPENAI_CLIENT_MAX_RETRIES,
    OPENAI_CLIENT_TIMEOUT,
    load_agent_config,
    load_chat_model,
)

KEY_ECHO = "sk" + "-proj-****" + "wxyz"


def test_chat_and_agent_models_come_from_one_validated_setting():
    assert load_chat_model({}) == DEFAULT_CHAT_MODEL == load_agent_config({}).model
    assert load_chat_model({"ULTRON_CHAT_MODEL": "my-model-1"}) == "my-model-1"
    # The agent follows the chat model unless it has its own override.
    assert load_agent_config({"ULTRON_CHAT_MODEL": "my-model-1"}).model == "my-model-1"
    assert load_agent_config({"ULTRON_CHAT_MODEL": "my-model-1", "ULTRON_AGENT_MODEL": "tools-2"}).model == "tools-2"
    for bad in ("gpt 4; rm -rf /", "x" * 101, "model\nX-Header: 1"):
        assert load_chat_model({"ULTRON_CHAT_MODEL": bad}) == DEFAULT_CHAT_MODEL
        assert load_agent_config({"ULTRON_AGENT_MODEL": bad}).model == DEFAULT_CHAT_MODEL


def test_shared_client_limits_are_bounded():
    assert 0 < OPENAI_CLIENT_TIMEOUT <= 60 and 0 <= OPENAI_CLIENT_MAX_RETRIES <= 1


class FakeCompletions:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        message = SimpleNamespace(content=self.reply, refusal=None, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


def fake_client(completions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


@pytest.fixture
def chat(legacy, monkeypatch):
    def install(reply=None, error=None):
        completions = FakeCompletions(reply, error)
        monkeypatch.setattr(legacy, "client", fake_client(completions))
        monkeypatch.setattr(legacy, "CHAT_MODEL", "configured-model")
        return completions
    return install


def test_single_shot_chat_uses_the_configured_model_and_bounded_output(legacy, chat):
    completions = chat("Hello there.")
    assert legacy.ask_gpt("hi", []) == "Hello there."
    sent = completions.calls[0]
    assert sent["model"] == "configured-model" and sent["max_completion_tokens"] == 200 and "max_tokens" not in sent


def test_single_shot_chat_errors_print_the_type_only(legacy, chat, capsys):
    chat(error=RuntimeError(f"Incorrect API key provided: {KEY_ECHO}"))
    assert legacy.ask_gpt("hi", []) == "Sorry, I am having trouble thinking right now."
    out = capsys.readouterr().out
    assert "RuntimeError" in out and KEY_ECHO not in out and "Incorrect API key" not in out


def test_empty_model_content_is_not_a_crash(legacy, chat):
    chat(None)
    assert legacy.ask_gpt("hi", []) == "Sorry, I don't have an answer for that."


def test_study_planner_uses_the_configured_model_and_never_echoes_errors(legacy, chat, capsys):
    completions = chat("- Day 1: basics")
    assert "study plan" in legacy.create_study_plan("chemistry", owner_hud()).lower()
    assert completions.calls[0]["model"] == "configured-model"
    chat(error=RuntimeError(f"quota for {KEY_ECHO}"))
    legacy.create_study_plan("physics", owner_hud())
    out = capsys.readouterr().out
    assert "RuntimeError" in out and KEY_ECHO not in out
