"""M2.3: the local Ollama agent provider and the local-only guard - tested with no Ollama
install, no network and no credentials.

Provider selection, base-URL validation and ``ULTRON_LOCAL_ONLY`` are pure functions.
Wire-level tests drive the *real* ``openai`` SDK over real HTTP against the loopback stub
(tests/openai_stub.py) through the same ``create_provider`` path production uses, so request
serialisation, tool-call parsing, timeout and HTTP-error mapping are exercised exactly as
against a real local server. The base URL is loopback-validated in ``ultron.config`` and
re-checked in ``create_provider``; both layers are tested here.
"""

from __future__ import annotations

import logging

import pytest
from openai_stub import StubOpenAI, completion, error

from ultron.agent.provider import (
    OLLAMA_SDK_KEY,
    Message,
    OllamaChatProvider,
    ProviderError,
    ToolDefinition,
    create_provider,
)
from ultron.config import (
    DEFAULT_OLLAMA_MODEL,
    OLLAMA_BASE_URL,
    SUPPORTED_PROVIDERS,
    AgentConfig,
    agent_provider_status,
    load_agent_config,
)

OLLAMA = {"ULTRON_PROVIDER": "ollama"}
NO_ARGS = {"type": "object", "additionalProperties": False, "properties": {}}
TOOL = ToolDefinition("demo__lookup", "Look something up.", NO_ARGS)


@pytest.fixture
def stub():
    with StubOpenAI() as server:
        yield server


def build(stub=None, **env):
    """(config, provider) through the production factory; ``stub`` redirects the wire level."""
    config = load_agent_config({**OLLAMA, **env})
    if stub is None:
        return config, create_provider(config)
    return config, create_provider(config, ollama_base_url=stub.base_url)


def ask(p, tools=(TOOL,), timeout=5.0, max_output_tokens=1024):
    return p.complete([Message("system", "rules"), Message("user", "objective")], list(tools),
                      timeout=timeout, max_output_tokens=max_output_tokens)


# ============================================================ 1. provider selection and config
def test_ollama_selection_defaults_to_a_local_loopback_server():
    config = load_agent_config(OLLAMA)
    assert (config.provider, config.provider_error) == ("ollama", "")
    assert config.model == DEFAULT_OLLAMA_MODEL == "llama3.1:latest"
    assert config.max_output_tokens == 1024
    assert config.ollama_base_url == OLLAMA_BASE_URL == "http://127.0.0.1:11434/v1"
    assert config.local_only is False
    assert "ollama" in SUPPORTED_PROVIDERS


def test_ollama_model_is_configurable_and_invalid_values_fall_back(caplog):
    assert load_agent_config({**OLLAMA, "ULTRON_OLLAMA_MODEL": "qwen3:8b"}).model == "qwen3:8b"
    with caplog.at_level(logging.WARNING, logger="ultron.config"):
        config = load_agent_config({**OLLAMA, "ULTRON_OLLAMA_MODEL": "no such model!"})
    assert config.model == DEFAULT_OLLAMA_MODEL
    assert any("ULTRON_OLLAMA_MODEL" in r.getMessage() for r in caplog.records)


def test_loopback_base_urls_are_accepted():
    for url in ("http://localhost:11434/v1", "http://127.0.0.1:9999/v1", "http://[::1]:11434/v1"):
        config = load_agent_config({**OLLAMA, "ULTRON_OLLAMA_BASE_URL": url})
        assert config.provider_error == "", url
        assert config.ollama_base_url == url


def test_non_loopback_base_urls_turn_the_agent_off(caplog):
    """A non-loopback base URL must never be used: prompts would leave the machine while the
    owner believes the assistant is running locally. Fails closed, with a warning."""
    for url in ("https://api.example.com/v1", "http://192.168.1.50:11434/v1", "http://ollama.internal/v1",
                "file:///etc/passwd", "http://127.0.0.1.evil.example/v1"):
        with caplog.at_level(logging.WARNING, logger="ultron.config"):
            config = load_agent_config({**OLLAMA, "ULTRON_OLLAMA_BASE_URL": url})
        assert config.provider_error, url
        assert "loopback" in config.provider_error
        assert config.provider == "openai"  # fail-closed: provider reset, agent off
        assert agent_provider_status(config)[0] is False
        assert any("ULTRON_OLLAMA_BASE_URL" in r.getMessage() for r in caplog.records)


def test_existing_provider_defaults_are_unchanged():
    """Backward compatibility: openai stays the default with its exact previous behaviour."""
    base = AgentConfig()
    assert (base.provider, base.local_only, base.max_output_tokens) == ("openai", False, 400)
    config = load_agent_config({})
    assert (config.provider, config.model, config.provider_error) == ("openai", "gpt-4.1-mini", "")
    assert SUPPORTED_PROVIDERS[:2] == ("openai", "groq")


# ============================================================ 2. the local-only guard
def test_local_only_refuses_remote_agent_providers():
    for provider_name in ("openai", "groq"):
        config = load_agent_config({"ULTRON_PROVIDER": provider_name, "ULTRON_LOCAL_ONLY": "1"})
        assert config.provider_error and "ULTRON_LOCAL_ONLY" in config.provider_error, provider_name
        assert create_provider(config, openai_client=object(), groq_api_key="k") is None
        usable, reason = agent_provider_status(config)
        assert usable is False and "local" in reason.lower()


def test_local_only_refuses_the_default_provider_too():
    """ULTRON_LOCAL_ONLY=1 without ULTRON_PROVIDER must not silently keep using OpenAI."""
    config = load_agent_config({"ULTRON_LOCAL_ONLY": "1"})
    assert config.provider_error and create_provider(config, openai_client=object()) is None


def test_local_only_allows_the_local_provider_and_reports_itself():
    config = load_agent_config({**OLLAMA, "ULTRON_LOCAL_ONLY": "1"})
    assert config.provider_error == "" and config.local_only is True
    usable, line = agent_provider_status(config)
    assert usable is True and "local-only" in line and "no credential" in line
    provider = create_provider(config)
    assert isinstance(provider, OllamaChatProvider)


def test_local_only_with_unsupported_provider_keeps_the_unsupported_error():
    config = load_agent_config({"ULTRON_PROVIDER": "banana", "ULTRON_LOCAL_ONLY": "1"})
    assert "not supported" in config.provider_error


# ============================================================ 3. factory behaviour and credentials
def test_create_provider_needs_no_credential_for_ollama():
    _config, provider = build()
    assert isinstance(provider, OllamaChatProvider)
    assert (provider.name, provider.label, provider.model_env) == ("ollama", "Ollama (local)",
                                                                   "ULTRON_OLLAMA_MODEL")
    assert provider.model == DEFAULT_OLLAMA_MODEL
    assert OLLAMA_SDK_KEY == "ollama"  # an SDK placeholder; Ollama ignores it and it grants nothing


def test_status_needs_no_key_for_ollama_but_cloud_keys_are_still_required():
    usable, line = agent_provider_status(load_agent_config(OLLAMA), env={})
    assert usable is True and "no credential" in line and OLLAMA_BASE_URL in line
    usable_openai, reason_openai = agent_provider_status(load_agent_config({}), env={})
    assert usable_openai is False and "OPENAI_API_KEY" in reason_openai
    usable_groq, reason_groq = agent_provider_status(load_agent_config({"ULTRON_PROVIDER": "groq"}), env={})
    assert usable_groq is False and "GROQ_API_KEY" in reason_groq


def test_openai_base_url_env_cannot_redirect_the_local_provider(monkeypatch, stub):
    """The same guarantee Groq has: an ambient OPENAI_BASE_URL never changes where this talks."""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.example/v1")
    _config, provider = build(stub)
    assert str(provider._client.base_url).rstrip("/") == stub.base_url
    _config2, provider2 = build()
    assert str(provider2._client.base_url).rstrip("/") == OLLAMA_BASE_URL


def test_create_provider_refuses_non_loopback_overrides_directly():
    """Defence in depth: even a caller-supplied URL is rejected unless it is loopback."""
    config = load_agent_config(OLLAMA)
    for url in ("https://api.example.com/v1", "http://10.0.0.5:11434/v1", "ftp://127.0.0.1/v1"):
        assert create_provider(config, ollama_base_url=url) is None, url


def test_provider_error_config_never_builds_a_client():
    config = load_agent_config({**OLLAMA, "ULTRON_OLLAMA_BASE_URL": "https://remote.example/v1"})
    assert config.provider_error and create_provider(config) is None


# ============================================================ 4. wire level against the loopback stub
def test_ollama_turn_maps_text_and_usage(stub):
    stub.script(completion("It is local.", usage=(21, 7)))
    _config, provider = build(stub)
    turn = ask(provider)
    assert turn.text == "It is local."
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (21, 7)
    request = stub.requests[0]
    assert request["path"].endswith("/chat/completions")
    assert request["authorization"] == f"Bearer {OLLAMA_SDK_KEY}"
    assert request["body"]["model"] == DEFAULT_OLLAMA_MODEL


def test_ollama_tool_calls_parse_like_every_other_provider(stub):
    stub.script(completion("", tool_calls=[("call_l1", "demo__lookup", '{"query": "x"}')], usage=(30, 9)))
    _config, provider = build(stub)
    turn = ask(provider)
    assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [
        ("call_l1", "demo__lookup", '{"query": "x"}')]
    body = stub.requests[0]["body"]
    assert body["parallel_tool_calls"] is False and body["tool_choice"] == "auto"
    # No cloud-only knobs are sent to a local server (it may reject unknown fields).
    assert "reasoning_effort" not in body and "extra_body" not in body


def test_ollama_http_errors_map_to_provider_errors(stub):
    stub.script(error(500, None, "server on fire"), error(404, None, "model not found; pull it first"))
    _config, provider = build(stub)
    with pytest.raises(ProviderError) as first:
        ask(provider)
    assert first.value.kind == "unavailable"
    with pytest.raises(ProviderError) as second:
        ask(provider)
    assert second.value.kind == "rejected"
    # Messages are the provider-neutral ones; no server body text is echoed.
    assert "server on fire" not in str(second.value) and "fire" not in str(first.value)


def test_ollama_timeout_maps_to_the_timeout_kind(stub):
    stub.script((200, completion("late"), 1.2))
    _config, provider = build(stub)
    with pytest.raises(ProviderError) as err:
        ask(provider, timeout=0.4)
    assert err.value.kind == "timeout"


def test_unreachable_local_server_is_unavailable_not_a_crash():
    with StubOpenAI() as closed:
        base_url = closed.base_url
    provider = create_provider(load_agent_config(OLLAMA), ollama_base_url=base_url)
    with pytest.raises(ProviderError) as err:
        ask(provider, timeout=2)
    assert err.value.kind == "unavailable"
