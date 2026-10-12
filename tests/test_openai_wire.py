"""M2.1: the real OpenAI SDK over real HTTP, against a local OpenAI-compatible stub.

Unlike the scripted-model tests, these exercise the actual ``openai`` client: request
serialisation, response parsing, finish reasons, HTTP errors, timeouts and retries, then
the full agent runtime and the HUD/API on top. Loopback only: no OpenAI account, no key,
no cost. (Real-model behaviour is covered by the opt-in tests in tests/live/.)
"""

from __future__ import annotations

import json
import logging
import re
import time
from types import SimpleNamespace

import pytest
from conftest import owner_hud
from flask.testing import FlaskClient
from openai_stub import STUB_API_KEY, StubOpenAI, completion, error

from ultron.agent.catalog import model_name
from ultron.agent.provider import Message, OpenAIChatProvider, ProviderError, ToolDefinition
from ultron.agent.runtime import AgentLimits, AgentRuntime, RunRegistry, ToolExecutor
from ultron.api.app import create_app
from ultron.config import load_settings
from ultron.legacy import JarvisLegacyBackend
from ultron.security.policy import RiskTier
from ultron.security.tokens import TokenLoadResult, generate_token
from ultron.tools.base import FunctionTool, ToolResult, ToolSpec

NO_ARGS = {"type": "object", "additionalProperties": False, "properties": {}}
CITY_ARG = {"type": "object", "additionalProperties": False, "required": ["city"],
            "properties": {"city": {"type": "string", "minLength": 1, "maxLength": 60}}}
TOOL = ToolDefinition("demo__lookup", "Look something up.", NO_ARGS)
# A fragment shaped like what OpenAI echoes back in a 401 message (built, never a real key).
KEY_ECHO = "sk" + "-proj-****" + "wxyz"


@pytest.fixture
def stub():
    with StubOpenAI() as server:
        yield server


def provider(stub, model="stub-model"):
    return OpenAIChatProvider(stub.client(), model)


def ask(p, tools=(TOOL,), timeout=5.0, max_output_tokens=64):
    return p.complete([Message("system", "rules"), Message("user", "objective")], list(tools),
                      timeout=timeout, max_output_tokens=max_output_tokens)


def add_tool(legacy, name, risk, run, parameters=NO_ARGS, external=False):
    legacy.TOOLS.register(FunctionTool(ToolSpec(name=name, description=f"Synthetic {name}.", risk=risk,
                                                parameters=parameters, external_content=external), run))


def runtime(legacy, stub, allowlist, **limits):
    return AgentRuntime(legacy.TOOLS, provider(stub), limits=AgentLimits(**limits), runs=RunRegistry(),
                        executor=ToolExecutor(), allowlist=allowlist)


# ============================================================ request format and parsing
def test_request_body_matches_the_chat_completions_tool_calling_contract(stub):
    stub.script(completion(tool_calls=[("call_1", "demo__lookup", "{}")]))
    turn = ask(provider(stub), max_output_tokens=77)
    sent = stub.requests[0]
    body = sent["body"]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["authorization"] == f"Bearer {STUB_API_KEY}"  # the SDK sends the key in the header only
    assert sent["retry_count"] == "0"
    assert body["model"] == "stub-model" and body["max_completion_tokens"] == 77 and "max_tokens" not in body
    assert body["tool_choice"] == "auto" and body["parallel_tool_calls"] is False
    assert body["tools"] == [{"type": "function", "function": {"name": "demo__lookup",
                                                               "description": "Look something up.",
                                                               "parameters": NO_ARGS}}]
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert turn.tool_calls[0].name == "demo__lookup" and turn.tool_calls[0].id == "call_1"
    assert turn.finish_reason == "tool_calls" and (turn.usage.input_tokens, turn.usage.output_tokens) == (50, 10)


def test_no_tools_means_no_tool_fields(stub):
    stub.script(completion("Hello."))
    turn = ask(provider(stub), tools=())
    assert "tools" not in stub.requests[0]["body"] and "parallel_tool_calls" not in stub.requests[0]["body"]
    assert turn.text == "Hello." and turn.finish_reason == "stop" and turn.tool_calls == ()


def test_refusal_is_returned_as_text_and_content_filter_as_an_error(stub):
    stub.script(completion(None, refusal="I can't help with that."), completion("", finish_reason="content_filter"))
    p = provider(stub)
    assert ask(p).text == "I can't help with that."
    with pytest.raises(ProviderError) as err:
        ask(p)
    assert err.value.kind == "rejected" and err.value.code == "content_filter"


def test_tool_call_without_an_id_is_a_bad_response(stub):
    stub.script(completion(tool_calls=[("", "demo__lookup", "{}")]))
    with pytest.raises(ProviderError) as err:
        ask(provider(stub))
    assert err.value.kind == "bad_response"


@pytest.mark.parametrize("body", [b"<html>502 Bad Gateway from a proxy</html>", {"id": "x", "object": "chat"},
                                  {"choices": []}, {"choices": [{"index": 0, "message": None}]}])
def test_malformed_success_bodies_are_bad_responses(stub, body):
    stub.script((200, body))
    with pytest.raises(ProviderError) as err:
        ask(provider(stub))
    assert err.value.kind == "bad_response"


# ============================================================ HTTP errors, timeouts, retries
@pytest.mark.parametrize(("reply", "kind", "phrase"), [
    (error(401, "invalid_api_key", f"Incorrect API key provided: {KEY_ECHO}."), "not_configured", "OPENAI_API_KEY"),
    (error(403, "unsupported_country_region_territory", type_="request_forbidden"), "not_configured", "credentials"),
    (error(429, "rate_limit_exceeded", type_="requests"), "rate_limited", "rate limited"),
    (error(429, "insufficient_quota", type_="insufficient_quota"), "rate_limited", "quota"),
    (error(400, "unsupported_parameter", param="max_completion_tokens"), "rejected", "ULTRON_AGENT_MODEL"),
    (error(404, "model_not_found"), "rejected", "ULTRON_AGENT_MODEL"),
    (error(500, None, type_="server_error"), "unavailable", "unavailable"),
    (error(503, None, type_="server_error"), "unavailable", "unavailable"),
])
def test_http_errors_map_to_safe_categories_without_retrying(stub, caplog, reply, kind, phrase):
    stub.script(reply, completion("must never be fetched"))
    with caplog.at_level(logging.INFO), pytest.raises(ProviderError) as err:
        ask(provider(stub))
    assert err.value.kind == kind and phrase in err.value.message and err.value.status == reply[0]
    assert len(stub.requests) == 1  # max_retries=0: no silent second request
    for text in (err.value.message, caplog.text):
        assert KEY_ECHO not in text and STUB_API_KEY not in text and "Incorrect API key" not in text
    assert f"http={reply[0]}" in caplog.text and "request_id=req_stub123" in caplog.text


def test_slow_provider_times_out_within_the_requested_bound(stub):
    stub.script((200, completion("late"), 3.0))
    started = time.monotonic()
    with pytest.raises(ProviderError) as err:
        ask(provider(stub), timeout=0.4)
    assert err.value.kind == "timeout" and time.monotonic() - started < 2.0 and len(stub.requests) == 1


def test_unreachable_provider_is_unavailable():
    import openai

    with StubOpenAI() as closed:
        base_url = closed.base_url
    client = openai.OpenAI(api_key=STUB_API_KEY, base_url=base_url)
    with pytest.raises(ProviderError) as err:
        ask(OpenAIChatProvider(client, "stub-model"), timeout=2)
    assert err.value.kind == "unavailable"


# ============================================================ the agent runtime over real HTTP
def test_real_sdk_round_trip_feeds_actual_tool_results_back_to_the_model(legacy, stub, caplog):
    seen = []
    add_tool(legacy, "demo.weather", RiskTier.OBSERVE,
             lambda a: seen.append(a) or ToolResult(True, f"{a['city']}: 31°C, clear (station K7Q2)."),
             parameters=CITY_ARG)
    name = model_name("demo.weather")
    stub.script(
        completion(tool_calls=[("call_w1", name, json.dumps({"city": "Pune"}))], usage=(120, 15)),
        completion(tool_calls=[("call_f1", "finish", json.dumps({"outcome": "achieved", "evidence": "c1",
                                                                  "summary": "Pune is 31°C and clear."}))],
                   usage=(180, 20)),
    )
    with caplog.at_level(logging.INFO, logger="ultron.agent"):
        report = runtime(legacy, stub, ["demo.weather"]).run("weather in Pune?", owner_hud())
    assert report.status == "completed" and report.verified is True and seen == [{"city": "Pune"}]
    assert report.usage == {"model_calls": 2, "input_tokens": 300, "output_tokens": 35}
    # The second request carries the assistant tool call and the *actual* tool output, by id.
    second = stub.requests[1]["body"]["messages"]
    assistant, tool_message = second[-2], second[-1]
    assert assistant["tool_calls"][0]["id"] == "call_w1" and tool_message["tool_call_id"] == "call_w1"
    assert "station K7Q2" in json.loads(tool_message["content"])["message"]
    assert [t["function"]["name"] for t in stub.requests[0]["body"]["tools"]] == [name, "finish", "ask_user"]
    # Structured diagnostics, no content.
    assert "model_call n=1 status=ok finish=tool_calls" in caplog.text and "tool_call tool=demo.weather" in caplog.text
    for private in ("Pune", "K7Q2", "weather in", STUB_API_KEY, "Bearer"):
        assert private not in caplog.text


@pytest.mark.parametrize("failure", [error(429, "rate_limit_exceeded"), error(503, None),
                                     (200, b"not json at all")])
def test_provider_failure_after_a_state_change_reports_what_happened(legacy, stub, failure):
    notes = []
    add_tool(legacy, "demo.note", RiskTier.ROUTINE, lambda a: notes.append(a) or ToolResult(True, "Noted."))
    stub.script(completion(tool_calls=[("call_n1", model_name("demo.note"), "{}")]), failure)
    report = runtime(legacy, stub, ["demo.note"]).run("note it then confirm", owner_hud())
    assert report.status == "failed" and report.verified is False and notes == [{}]
    assert report.reply.startswith("Done so far: demo.note: Noted.") and report.error in (
        "rate_limited", "unavailable", "bad_response")


def test_provider_failure_before_any_tool_call_changes_nothing(legacy, stub):
    notes = []
    add_tool(legacy, "demo.note", RiskTier.ROUTINE, lambda a: notes.append(a) or ToolResult(True, "Noted."))
    stub.script(error(401, "invalid_api_key"))
    report = runtime(legacy, stub, ["demo.note"]).run("note it", owner_hud())
    assert report.status == "failed" and report.error == "not_configured" and notes == [] and report.steps == []


def test_output_cut_off_by_the_token_limit_is_not_reported_as_complete(legacy, stub):
    stub.script(completion("The answer is that the first three steps are", finish_reason="length"))
    report = runtime(legacy, stub, []).run("explain", owner_hud())
    assert report.status == "incomplete" and "cut short" in report.reply


def test_truncated_tool_arguments_never_execute(legacy, stub):
    ran = []
    add_tool(legacy, "demo.weather", RiskTier.OBSERVE, lambda a: ran.append(a) or ToolResult(True, "x"),
             parameters=CITY_ARG)
    cut = completion(tool_calls=[("call_t1", model_name("demo.weather"), '{"city": "Pu')], finish_reason="length")
    stub.script(cut, cut, cut)
    report = runtime(legacy, stub, ["demo.weather"]).run("weather", owner_hud())
    assert ran == [] and report.status == "failed" and all(s.status == "invalid" for s in report.steps)
    assert "cut off" in report.steps[0].message


def test_token_budget_stops_the_run(legacy, stub):
    add_tool(legacy, "demo.a", RiskTier.OBSERVE, lambda a: ToolResult(True, "a"))
    add_tool(legacy, "demo.b", RiskTier.OBSERVE, lambda a: ToolResult(True, "b"))
    stub.script(completion(tool_calls=[("c_a", model_name("demo.a"), "{}")], usage=(900, 200)),
                completion(tool_calls=[("c_b", model_name("demo.b"), "{}")]))
    report = runtime(legacy, stub, ["demo.a", "demo.b"], max_total_tokens=1000).run("a then b", owner_hud())
    assert report.status == "limit_reached" and "token budget" in report.reply and len(stub.requests) == 1


# ============================================================ through the HUD/API
BASE = "http://127.0.0.1:5001"
ORIGIN = {"Origin": BASE}


class LocalClient(FlaskClient):
    def open(self, *args, **kwargs):
        kwargs.setdefault("base_url", BASE)
        return super().open(*args, **kwargs)


@pytest.fixture
def api(legacy, tmp_path):
    token = generate_token()
    backend = JarvisLegacyBackend()
    backend._main = legacy
    app = create_app(load_settings({"ULTRON_DATA_DIR": str(tmp_path)}), backend=backend,
                     token_result=TokenLoadResult(token, "env", None))
    app.testing = True
    app.test_client_class = LocalClient
    client = app.test_client()
    assert client.post("/login", json={"token": token}, headers=ORIGIN).status_code == 200
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/").get_data(as_text=True)).group(1)
    return SimpleNamespace(client=client, headers={**ORIGIN, "X-CSRF-Token": csrf})


def test_api_reports_provider_outages_without_crashing_or_leaking(legacy, stub, api, monkeypatch):
    monkeypatch.setattr(legacy, "agent_provider", lambda: provider(stub))
    stub.script(error(429, "insufficient_quota", f"You exceeded your quota ({KEY_ECHO})."))
    res = api.client.post("/ask", json={"message": "what's on today?", "mode": "agent"}, headers=api.headers)
    body = res.get_json()
    assert res.status_code == 200 and body["agent"]["status"] == "failed" and body["agent"]["error"] == "rate_limited"
    assert "quota" in body["reply"] and KEY_ECHO not in res.get_data(as_text=True)
    assert body["agent"]["usage"]["model_calls"] == 1


def test_api_agent_run_over_http_uses_the_registry_and_reports_usage(legacy, stub, api, monkeypatch):
    monkeypatch.setattr(legacy, "agent_provider", lambda: provider(stub))
    stub.script(completion(tool_calls=[("call_t", model_name("time.now"), "{}")]),
                completion(tool_calls=[("call_f", "finish", json.dumps(
                    {"outcome": "achieved", "summary": "Here is the time.", "evidence": "c1"}))]))
    res = api.client.post("/ask", json={"message": "what time is it?", "mode": "agent"}, headers=api.headers)
    agent = res.get_json()["agent"]
    assert agent["status"] == "completed" and agent["verified"] is True
    assert [(s["tool"], s["status"]) for s in agent["steps"]] == [("time.now", "completed")]
    assert agent["usage"] == {"model_calls": 2, "input_tokens": 100, "output_tokens": 20}
    assert agent["duration_ms"] >= 0 and "error" not in agent
