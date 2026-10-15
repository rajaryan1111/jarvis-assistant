"""M2.4 multi-model racing and evidence-based synthesis: deterministic tests only.

No paid calls, no network beyond the loopback stub, no Ollama install: racers are scripted
fakes implementing the real ``ModelProvider`` protocol, resolution goes through the real
``load_agent_config``/``create_provider`` selection paths (with an injectable factory for
spies), and the wire-level tests drive two real SDK clients against two loopback stub
servers. Timing tests use small delays with generous margins.
"""

from __future__ import annotations

import inspect
import json
import threading
import time

import pytest
from openai_stub import StubOpenAI, completion

from ultron.agent import race as race_mod
from ultron.agent.provider import ModelTurn, ProviderError, Usage, create_provider
from ultron.agent.race import (
    EVIDENCE_DELIMITER,
    JUDGE_SYSTEM,
    RACE_ADVISER_SYSTEM,
    RACE_MAX_ANSWER_CHARS,
    RACE_MAX_QUESTION_CHARS,
    Answer,
    RacePlan,
    Racer,
    group_answers,
    resolve_judge,
    resolve_race,
    run_race,
    with_judge_synthesis,
)
from ultron.cli import main as cli_main
from ultron.config import load_agent_config

FAKE_OPENAI = "sk-" + "raceTest0" * 6  # assembled fake; never a real credential shape check target
FAKE_GROQ = "gsk_" + "raceTest0" * 6
SECRET = "gsk_" + "A1b2C3d4E5f6G7h8" * 3  # credential-shaped: must be redacted everywhere


class Peak:
    """Tracks how many fake providers were inside complete() at the same time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def __enter__(self):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)

    def __exit__(self, *exc):
        with self._lock:
            self.active -= 1


class FakeProvider:
    """A deterministic ModelProvider: scripted turn, ProviderError or crash, optional delay."""

    label = "fake"
    key_env = ""
    model_env = ""

    def __init__(self, name="fake", model="m", text="answer", delay=0.0, error=None, usage=(10, 5),
                 finish="stop", exception=None, peak=None, recorder=None, respect_timeout=True):
        self.name = name
        self.model = model
        self.text = text
        self.delay = delay
        self.error = error  # (kind, message) -> ProviderError
        self.usage = usage
        self.finish = finish
        self.exception = exception  # an arbitrary exception instance -> bad_response handling
        self.peak = peak
        self.recorder = recorder  # list capturing every request
        self.respect_timeout = respect_timeout

    def complete(self, messages, tools, *, timeout, max_output_tokens):
        if self.recorder is not None:
            self.recorder.append({"messages": [(m.role, m.content) for m in messages],
                                  "tools": list(tools), "timeout": timeout,
                                  "max_output_tokens": max_output_tokens})
        timed_out = self.respect_timeout and self.delay > timeout
        delay = timeout if timed_out else self.delay
        with self.peak or _NULL:
            if delay:
                time.sleep(delay)
            if timed_out:
                raise ProviderError("timeout", f"no answer within {timeout:g}s")
            if self.error:
                raise ProviderError(*self.error)
            if self.exception:
                raise self.exception
            return ModelTurn(text=self.text, usage=Usage(*self.usage), finish_reason=self.finish)


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_NULL = _NullCtx()


def plan_of(*fakes, specs=None, deadline=5.0, timeout=2.0, local_only=False):
    racers = tuple(Racer(specs[i] if specs else f"fake:{i}", fake) for i, fake in enumerate(fakes))
    return RacePlan(racers, (), deadline=deadline, per_model_timeout=timeout, local_only=local_only)


class SpyFactory:
    """Stands in for create_provider: records every (provider, model, kwargs) it was asked for."""

    def __init__(self):
        self.calls = []

    def __call__(self, config, **kwargs):
        self.calls.append((config.provider, config.model, sorted(kwargs)))
        return FakeProvider(name=config.provider, model=config.model)


def env_with(**extra):
    return {"OPENAI_API_KEY": FAKE_OPENAI, "GROQ_API_KEY": FAKE_GROQ, **extra}


# ============================================================ 1. resolution, caps and clamps
def test_defaults_race_the_configured_providers():
    spy = SpyFactory()
    plan = resolve_race(env_with(), provider_factory=spy)
    assert [r.spec for r in plan.racers] == ["openai", "groq"]
    assert plan.problems == () and plan.refused == ""
    assert plan.deadline == 45.0 and plan.per_model_timeout == 20.0 and plan.local_only is False
    assert ("openai", "gpt-4.1-mini", ["openai_client"]) in spy.calls  # each provider gets its own path
    assert ("groq", "openai/gpt-oss-120b", ["groq_api_key"]) in spy.calls


def test_no_usable_provider_is_a_problem_not_a_crash():
    plan = resolve_race({}, provider_factory=SpyFactory())
    assert plan.racers == () and plan.refused == ""
    assert any("no usable race providers" in p for p in plan.problems)


def test_explicit_specs_carry_models_through_the_real_config_path():
    spy = SpyFactory()
    plan = resolve_race({"GROQ_API_KEY": FAKE_GROQ}, ["groq:llama-3.3-70b", "ollama:qwen3:8b"],
                        provider_factory=spy)
    assert [r.spec for r in plan.racers] == ["groq:llama-3.3-70b", "ollama:qwen3:8b"]
    assert ("groq", "llama-3.3-70b", ["groq_api_key"]) in spy.calls
    assert ("ollama", "qwen3:8b", []) in spy.calls  # model override reached load_agent_config


def test_malformed_and_duplicate_entries_degrade_loudly():
    plan = resolve_race(env_with(), ["banana", "open ai:x", "openai:bad model!", "openai", "openai"],
                        provider_factory=SpyFactory())
    assert [r.spec for r in plan.racers] == ["openai"]
    assert sum("not 'provider[:model]'" in p for p in plan.problems) == 3
    assert any("duplicate" in p for p in plan.problems)


def test_model_cap_is_strict_and_entries_beyond_it_are_named():
    spy = SpyFactory()
    plan = resolve_race(env_with() | {"ULTRON_RACE_MAX_MODELS": "2"},
                        ["openai", "groq", "ollama", "ollama:second"], provider_factory=spy)
    assert len(plan.racers) == 2 and any("race cap is 2" in p and "ollama" in p for p in plan.problems)
    assert len(spy.calls) == 2  # dropped entries never reach provider construction


def test_absolute_cap_is_never_exceeded_even_by_configuration():
    plan = resolve_race(env_with() | {"ULTRON_RACE_MAX_MODELS": "99",
                                      "ULTRON_RACE_MODELS": "openai,groq,ollama,ollama:b,ollama:c"},
                        provider_factory=SpyFactory())
    assert len(plan.racers) <= race_mod.RACE_ABSOLUTE_MAX_MODELS == 4
    assert any("clamped" in p for p in plan.problems)


def test_deadline_and_timeout_are_clamped_with_problems():
    plan = resolve_race(env_with() | {"ULTRON_RACE_DEADLINE": "999", "ULTRON_RACE_TIMEOUT": "abc"},
                        provider_factory=SpyFactory())
    assert plan.deadline == 120.0 and plan.per_model_timeout == 20.0
    assert any("ULTRON_RACE_DEADLINE" in p and "clamped" in p for p in plan.problems)
    assert any("ULTRON_RACE_TIMEOUT" in p and "not a number" in p for p in plan.problems)


def test_provider_without_credential_is_excluded_with_a_problem():
    plan = resolve_race({"OPENAI_API_KEY": FAKE_OPENAI}, ["openai", "groq"], provider_factory=SpyFactory())
    assert [r.spec for r in plan.racers] == ["openai"]
    assert any(p.startswith("groq: not usable") for p in plan.problems)


def test_non_loopback_ollama_url_excludes_that_racer_only():
    env = env_with() | {"ULTRON_OLLAMA_BASE_URL": "https://remote.example/v1"}
    plan = resolve_race(env, ["openai", "ollama"], provider_factory=SpyFactory())
    assert [r.spec for r in plan.racers] == ["openai"]
    assert any(p.startswith("ollama: not usable") for p in plan.problems)


# ============================================================ 2. ULTRON_LOCAL_ONLY boundary
def test_local_only_refuses_an_explicit_cloud_racer_without_building_anything():
    spy = SpyFactory()
    plan = resolve_race(env_with() | {"ULTRON_LOCAL_ONLY": "1"}, ["openai"], provider_factory=spy)
    assert plan.racers == () and plan.local_only is True
    assert "ULTRON_LOCAL_ONLY" in plan.refused
    assert spy.calls == []  # no provider client was constructed: no possible outbound request
    with pytest.raises(ValueError, match="refused"):
        run_race("hello", plan)


def test_local_only_refuses_the_whole_race_even_with_a_local_entry_present():
    spy = SpyFactory()
    plan = resolve_race(env_with() | {"ULTRON_LOCAL_ONLY": "1"}, ["groq:m", "ollama"],
                        provider_factory=spy)
    assert plan.refused and plan.racers == () and spy.calls == []


def test_local_only_defaults_to_the_local_provider_only():
    spy = SpyFactory()
    plan = resolve_race(env_with() | {"ULTRON_LOCAL_ONLY": "1"}, provider_factory=spy)
    assert [r.spec for r in plan.racers] == ["ollama"] and plan.refused == ""
    assert all(call[0] == "ollama" for call in spy.calls)


def test_local_only_refuses_a_cloud_judge_but_the_local_race_still_runs():
    problems: list[str] = []
    racers = (Racer("ollama", FakeProvider(name="ollama")),)
    judge = resolve_judge(env_with() | {"ULTRON_LOCAL_ONLY": "1"}, "openai", racers, problems,
                          provider_factory=SpyFactory())
    assert judge is None
    assert any("ULTRON_LOCAL_ONLY" in p and "judge" in p for p in problems)


def test_local_only_allows_a_local_judge():
    problems: list[str] = []
    judge = resolve_judge({"ULTRON_LOCAL_ONLY": "1"}, "ollama", (), problems, provider_factory=SpyFactory())
    assert judge is not None and judge.spec == "ollama" and problems == []


# ============================================================ 3. bounded racing: parallelism, timeouts, deadline
def test_racers_run_in_parallel_and_concurrency_is_bounded():
    peak = Peak()
    fakes = [FakeProvider(name=f"f{i}", delay=0.15, peak=peak) for i in range(3)]
    started = time.monotonic()
    report = run_race("question", plan_of(*fakes, deadline=5.0))
    wall = time.monotonic() - started
    assert len(report.answers) == 3
    assert peak.peak == 3  # genuinely parallel
    assert wall < 0.44  # ~0.15s total, not 3 x 0.15s serial
    assert report.wall_ms < 440


def test_worker_count_never_exceeds_the_absolute_cap():
    peak = Peak()
    fakes = [FakeProvider(name=f"f{i}", delay=0.2, peak=peak) for i in range(6)]
    report = run_race("question", plan_of(*fakes, deadline=5.0))  # hand-built over-cap plan
    assert len(report.answers) == 6
    assert peak.peak <= race_mod.RACE_ABSOLUTE_MAX_MODELS  # bounded parallelism, not one thread per racer


def test_per_model_timeout_produces_a_timeout_abstention_and_partial_results():
    slow = FakeProvider(name="slow", delay=1.0)  # respects its timeout: fails at ~0.2s
    fast = FakeProvider(name="fast", text="fast answer", delay=0.0)
    report = run_race("question", plan_of(slow, fast, specs=("slow:p", "fast:p"), deadline=5.0, timeout=0.2))
    assert [a.spec for a in report.answers] == ["fast:p"]  # partial success is a valid outcome
    assert [(a.spec, a.kind) for a in report.abstentions] == [("slow:p", "timeout")]
    assert report.disagreement is False and report.groups[0].label == "A"


def test_overall_deadline_closes_the_race_and_late_racers_abstain():
    stuck = FakeProvider(name="stuck", delay=1.2, respect_timeout=False)  # ignores timeout on purpose
    quick = FakeProvider(name="quick", text="quick answer")
    started = time.monotonic()
    report = run_race("question", plan_of(stuck, quick, specs=("stuck:p", "quick:p"),
                                          deadline=0.3, timeout=5.0))
    assert time.monotonic() - started < 1.0  # the race did not wait for the stuck racer
    assert [a.spec for a in report.answers] == ["quick:p"]
    assert [(a.spec, a.kind) for a in report.abstentions] == [("stuck:p", "deadline")]
    assert any("cannot be force-killed" in note for note in report.notes)


def test_provider_errors_become_abstentions_with_their_kind():
    broken = FakeProvider(name="broken", error=("unavailable", "server on fire", ))
    good = FakeProvider(name="good", text="ok")
    report = run_race("q", plan_of(broken, good, specs=("broken:p", "good:p")))
    assert [(a.spec, a.kind, a.detail) for a in report.abstentions] == [
        ("broken:p", "unavailable", "server on fire")]
    assert len(report.answers) == 1


def test_unexpected_crashes_record_only_the_exception_type():
    crasher = FakeProvider(name="crash", exception=ValueError(f"boom {SECRET} inside"))
    report = run_race("q", plan_of(crasher, specs=("crash:p",)))
    abstention = report.abstentions[0]
    assert abstention.kind == "bad_response" and abstention.detail == "unexpected ValueError"
    assert SECRET not in report.abstentions[0].detail
    assert "boom" not in json.dumps(_asdict(report))


def test_empty_replies_abstain_but_keep_reported_usage():
    empty = FakeProvider(name="empty", text="   ", usage=(30, 7))
    report = run_race("q", plan_of(empty, specs=("empty:p",)))
    assert report.answers == ()
    abstention = report.abstentions[0]
    assert abstention.kind == "empty" and (abstention.input_tokens, abstention.output_tokens) == (30, 7)
    assert (report.total_input_tokens, report.total_output_tokens) == (30, 7)  # usage preserved, ensemble parity
    assert any("No model answered" in line for line in report.synthesis)


def test_answers_are_redacted_and_truncated():
    loud = FakeProvider(name="loud", text=f"key {SECRET} " + "x" * (RACE_MAX_ANSWER_CHARS + 500))
    report = run_race("q", plan_of(loud, specs=("loud:p",)))
    answer = report.answers[0]
    assert SECRET not in answer.text and "REDACTED" in answer.text
    assert len(answer.text) <= RACE_MAX_ANSWER_CHARS


def test_finish_order_is_recorded_and_the_fastest_racer_is_named():
    slow = FakeProvider(name="slow", text="slow answer", delay=0.2)
    fast = FakeProvider(name="fast", text="fast answer", delay=0.0)
    report = run_race("q", plan_of(slow, fast, specs=("slow:p", "fast:p")))
    assert [a.spec for a in report.answers] == ["fast:p", "slow:p"]
    assert report.fastest == "fast:p"


def test_usage_and_call_accounting_includes_abstentions():
    a = FakeProvider(name="a", text="one", usage=(11, 4))
    b = FakeProvider(name="b", text="", usage=(22, 0))  # empty: abstains with usage
    report = run_race("q", plan_of(a, b, specs=("a:p", "b:p")))
    assert report.model_calls == 2
    assert (report.total_input_tokens, report.total_output_tokens) == (33, 4)
    assert all(x.latency_ms >= 0 for x in report.answers)


def _asdict(report):
    from dataclasses import asdict

    return asdict(report)


# ============================================================ 4. privacy: what advisers actually receive
def test_advisers_receive_only_rules_and_the_redacted_question():
    recorder = []
    fake = FakeProvider(name="f", recorder=recorder)
    question = f"my key is {SECRET} and " + "pad " * 2000
    run_race(question, plan_of(fake, specs=("f:p",)))
    request = recorder[0]
    roles = [role for role, _ in request["messages"]]
    assert roles == ["system", "user"]  # exactly two messages: no history, no tool results, no evidence
    assert request["messages"][0][1] == RACE_ADVISER_SYSTEM
    sent = request["messages"][1][1]
    assert SECRET not in sent and "REDACTED" in sent
    assert len(sent) <= RACE_MAX_QUESTION_CHARS
    assert request["tools"] == []  # advisers get NO tools: no tool authority exists in this path
    assert request["max_output_tokens"] == race_mod.RACE_MAX_OUTPUT_TOKENS


def test_blank_question_sends_nothing():
    recorder = []
    fake = FakeProvider(name="f", recorder=recorder)
    with pytest.raises(ValueError, match="empty"):
        run_race("   \n\t ", plan_of(fake, specs=("f:p",)))
    assert recorder == []


def test_race_module_has_no_tool_runtime_or_history_surface():
    source = inspect.getsource(race_mod)
    for forbidden in ("ToolRegistry", "AgentRuntime", "subprocess", "ultron.history", "chat_history",
                      "os.system", "webbrowser"):
        assert forbidden not in source, forbidden
    # The race API cannot even be handed a registry: no parameter for one exists.
    assert "registry" not in inspect.signature(run_race).parameters


# ============================================================ 5. synthesis semantics
def test_near_identical_answers_group_and_disagreement_is_preserved():
    answers = [Answer("a:p", "Paris.", 5), Answer("b:p", "paris", 6), Answer("c:p", "It is Paris!", 7)]
    groups = group_answers(answers)
    assert [g.label for g in groups] == ["A", "B"]
    assert groups[0].specs == ("a:p", "b:p") and groups[1].specs == ("c:p",)
    report = run_race("capital of France?", plan_of(
        FakeProvider(name="a", text="Paris."), FakeProvider(name="b", text="paris"),
        FakeProvider(name="c", text="It is Paris!"), specs=("a:p", "b:p", "c:p")))
    assert report.disagreement is True
    text = "\n".join(report.synthesis)
    assert "UNRESOLVED" in text and "no majority is assumed correct" in text
    assert "agreement between models is NOT verification" in text
    assert "unique claim" in text
    assert "nothing below is verified fact" in text


def test_unanimous_textual_agreement_is_still_not_verification():
    report = run_race("q", plan_of(FakeProvider(name="a", text="Same answer."),
                                   FakeProvider(name="b", text="same answer"), specs=("a:p", "b:p")))
    assert report.disagreement is False and len(report.groups) == 1
    text = "\n".join(report.synthesis)
    assert "identical wording is still not verification" in text
    assert "may share the same mistake" in text


def test_all_distinct_answers_produce_one_group_each_without_ranking():
    report = run_race("q", plan_of(FakeProvider(name="a", text="alpha claim"),
                                   FakeProvider(name="b", text="beta claim"),
                                   FakeProvider(name="c", text="gamma claim"),
                                   FakeProvider(name="d", text="delta claim"),
                                   specs=("a:p", "b:p", "c:p", "d:p")))
    assert [g.label for g in report.groups] == ["A", "B", "C", "D"]
    assert report.disagreement is True
    assert all(len(g.specs) == 1 for g in report.groups)


def test_render_carries_the_advisory_footer_and_report_is_json_serialisable():
    report = run_race("q", plan_of(FakeProvider(name="a", text="ans"), specs=("a:p",)))
    rendered = race_mod.render(report)
    assert "untrusted data" in rendered and "no claim above is verified fact" in rendered
    payload = json.dumps(_asdict(report))  # the --json view must not fail on exotic types
    assert "disagreement" in payload


# ============================================================ 6. the judge call
def test_judge_sees_labelled_answers_and_evidence_racers_never_see_evidence():
    racer_recorder, judge_recorder = [], []
    racer = FakeProvider(name="r", text="claim one", recorder=racer_recorder)
    judge = FakeProvider(name="j", text="SYNTH: claim one is unsupported by the evidence.",
                         usage=(7, 3), recorder=judge_recorder)
    report = run_race("q", plan_of(racer, specs=("r:p",)))
    evidence = f"field notes mentioning {SECRET} values"
    final = with_judge_synthesis(report, Racer("j:p", judge), evidence)
    assert "SYNTH" in final.judge_synthesis and final.judge_provider == "j:p"
    assert SECRET not in final.judge_synthesis  # judge output is redacted too
    payload = judge_recorder[0]["messages"][1][1]
    assert EVIDENCE_DELIMITER in payload and "[A] r:p: claim one" in payload
    assert SECRET not in payload and "REDACTED" in payload  # evidence redacted before the judge sees it
    assert final.evidence_chars == len(payload.split(EVIDENCE_DELIMITER)[1].split("<<<")[1].split(">>>")[0].strip())
    assert judge_recorder[0]["tools"] == []  # the judge has no tools either
    assert judge_recorder[0]["messages"][0][1] == JUDGE_SYSTEM
    for request in racer_recorder:  # advisers never saw the evidence
        assert "field notes" not in request["messages"][1][1]
    assert final.model_calls == report.model_calls + 1
    assert final.total_input_tokens == report.total_input_tokens + 7


def test_judge_failure_leaves_the_deterministic_report_standing():
    judge = FakeProvider(name="j", error=("rate_limited", "slow down"))
    report = run_race("q", plan_of(FakeProvider(name="a", text="ans"), specs=("a:p",)))
    final = with_judge_synthesis(report, Racer("j:p", judge), "evidence text")
    assert final.judge_synthesis == "" and final.judge_error == "rate_limited"
    assert final.synthesis == report.synthesis  # unchanged, still authoritative
    assert any("judge" in note and "deterministic comparison above stands" in note for note in final.notes)
    assert final.evidence_chars == len("evidence text")


def test_judge_crash_and_empty_reply_are_graceful():
    report = run_race("q", plan_of(FakeProvider(name="a", text="ans"), specs=("a:p",)))
    crashed = with_judge_synthesis(report, Racer("j:p", FakeProvider(name="j", exception=RuntimeError("x"))), "")
    assert crashed.judge_error == "unexpected RuntimeError" and crashed.judge_synthesis == ""
    empty = with_judge_synthesis(report, Racer("j:p", FakeProvider(name="j", text="", usage=(9, 1))), "")
    assert empty.judge_error == "empty" and empty.total_input_tokens == report.total_input_tokens + 9


def test_no_judge_means_no_judge_call_and_no_evidence_channel():
    report = run_race("q", plan_of(FakeProvider(name="a", text="ans"), specs=("a:p",)))
    assert with_judge_synthesis(report, None, "ignored") is report
    assert report.judge_provider == "" and report.evidence_chars == 0


def test_judge_payload_marks_absent_evidence_explicitly():
    report = run_race("q", plan_of(FakeProvider(name="a", text="ans"), specs=("a:p",)))
    payload = race_mod.judge_payload(report, "")
    assert "none" in payload and "unverified model statements" in payload


# ============================================================ 7. wire level: two real SDK clients, two loopback stubs
def test_two_local_stub_servers_race_over_real_http():
    with StubOpenAI() as first, StubOpenAI() as second:
        first.script(completion("Answer from server one.", usage=(11, 4)))
        second.script(completion("Answer from server two.", usage=(22, 6)))
        config = load_agent_config({"ULTRON_PROVIDER": "ollama"})
        one = create_provider(config, ollama_base_url=first.base_url)
        two = create_provider(config, ollama_base_url=second.base_url)
        report = run_race("which local server answers?",
                          plan_of(one, two, specs=("ollama:one", "ollama:two"), deadline=10.0))
    assert sorted(a.spec for a in report.answers) == ["ollama:one", "ollama:two"]
    assert report.abstentions == () and report.disagreement is True
    assert (report.total_input_tokens, report.total_output_tokens) == (33, 10)
    assert len(first.requests) == 1 and len(second.requests) == 1
    assert first.requests[0]["body"]["model"] == "llama3.1:latest"


def test_dead_local_server_abstains_while_the_live_one_answers():
    with StubOpenAI() as closed:
        dead_url = closed.base_url
    with StubOpenAI() as alive:
        alive.script(completion("I am here.", usage=(5, 2)))
        config = load_agent_config({"ULTRON_PROVIDER": "ollama"})
        dead = create_provider(config, ollama_base_url=dead_url)
        live = create_provider(config, ollama_base_url=alive.base_url)
        report = run_race("roll call", plan_of(dead, live, specs=("ollama:dead", "ollama:live"),
                                               deadline=10.0, timeout=3.0))
    assert [a.spec for a in report.answers] == ["ollama:live"]
    assert [(a.spec, a.kind) for a in report.abstentions] == [("ollama:dead", "unavailable")]


# ============================================================ 8. the CLI verb
@pytest.fixture
def fake_build(monkeypatch):
    """Real resolution and racing, with scripted providers instead of real clients."""
    built = []

    def build(provider_name, model, env, factory):
        recorder = []
        fake = FakeProvider(name=provider_name, model=model or "m",
                            text=f"answer from {provider_name}", recorder=recorder)
        built.append((provider_name, model, fake, recorder))
        return fake

    monkeypatch.setattr(race_mod, "_build_provider", build)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)
    monkeypatch.setenv("GROQ_API_KEY", FAKE_GROQ)
    return built


def test_cli_race_prints_the_report_and_exits_zero(fake_build, capsys):
    assert cli_main(["race", "what", "is", "the", "capital", "of", "France"]) == 0
    out = capsys.readouterr().out
    assert "Advisory race report" in out and "answer from openai" in out and "answer from groq" in out
    assert "nothing below is verified fact" in out
    assert len(fake_build) == 2  # the default roster raced both configured providers


def test_cli_race_json_view_parses(fake_build, capsys):
    assert cli_main(["race", "question", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["disagreement"] is True and len(payload["groups"]) == 2  # the fakes differ
    assert len(payload["answers"]) == 2 and payload["question"] == "question"


def test_cli_race_deadline_flag_is_clamped_visibly(fake_build, capsys):
    assert cli_main(["race", "question", "--deadline", "999"]) == 0
    out = capsys.readouterr().out
    assert "[race] ULTRON_RACE_DEADLINE=999" in out and "clamped to 120" in out
    assert "deadline 120s" in out


def test_cli_race_exits_one_when_nobody_answers(monkeypatch, capsys):
    monkeypatch.setattr(race_mod, "_build_provider",
                        lambda name, model, env, factory: FakeProvider(name=name, error=("unavailable", "down")))
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)
    assert cli_main(["race", "question", "--models", "openai"]) == 1
    out = capsys.readouterr().out
    assert "No model answered" in out and "Abstention: openai - unavailable" in out


def test_cli_race_refuses_cloud_models_under_local_only_without_building(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(race_mod, "_build_provider",
                        lambda *a, **k: calls.append(a) or FakeProvider())
    monkeypatch.setenv("ULTRON_LOCAL_ONLY", "1")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)
    assert cli_main(["race", "question", "--models", "openai,groq"]) == 1
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err and "ULTRON_LOCAL_ONLY" in captured.err
    assert calls == []  # nothing was built, so nothing could be sent


def test_cli_race_evidence_goes_only_to_the_judge(fake_build, tmp_path, capsys):
    evidence_file = tmp_path / "notes.txt"
    evidence_file.write_text(f"the notes say {SECRET} nothing else", encoding="utf-8")
    assert cli_main(["race", "question", "--evidence", str(evidence_file), "--judge", "groq"]) == 0
    out = capsys.readouterr().out
    assert "Judge synthesis" in out and "answer from groq" in out
    racer_requests = [r for name, _model, _fake, rec in fake_build if name != "groq" for r in rec]
    assert racer_requests and all("the notes say" not in r["messages"][1][1] for r in racer_requests)


def test_cli_race_missing_evidence_file_is_a_clean_error(fake_build, tmp_path, capsys):
    assert cli_main(["race", "question", "--evidence", str(tmp_path / "nope.txt")]) == 1
    assert "cannot read the evidence file" in capsys.readouterr().err


def test_cli_race_help_documents_the_bounds(fake_build, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["race", "--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "--models" in out and "--deadline" in out and "advisory" in out
