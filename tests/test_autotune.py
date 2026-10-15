"""M2.5 AutoTune: deterministic tests for the bounded tuning run, the objective checkers,
transparent routing, persistence and the CLI - plus the local-only boundary and the
security invariants (no execution of model output, no tool authority, no leakage).

Racers are scripted fakes on the real ``ModelProvider`` protocol; resolution and racing go
through the real ``ultron.agent.race`` machinery; one wire-level test drives real SDK
clients against loopback stubs. No paid calls, no Ollama install, no network.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import stat
import threading
import time
from dataclasses import asdict

import pytest
from openai_stub import StubOpenAI, completion

from ultron.agent import autotune as tune
from ultron.agent import race as race_mod
from ultron.agent.autotune import (
    AUTOTUNE_SUITE,
    CATEGORIES,
    TaskOutcome,
    TuningBounds,
    load_results,
    render_route,
    render_stored,
    render_tuning,
    results_path,
    route_category,
    run_tuning,
    save_results,
    suite_fingerprint,
    tuning_bounds,
)
from ultron.agent.provider import ModelTurn, ProviderError, Usage, create_provider
from ultron.agent.race import RacePlan, Racer
from ultron.cli import main as cli_main
from ultron.config import load_agent_config

FAKE_OPENAI = "sk-" + "tuneTest0" * 6  # assembled fake, never a literal credential
FAKE_GROQ = "gsk_" + "tuneTest0" * 6
SECRET = "gsk_" + "A1b2C3d4E5f6G7h8" * 3

GOOD_ANSWERS = {
    "17 multiplied": "391",
    "Alice is shorter": "Carol",
    "JSON object": '{"language": "python", "sum": 5}',
    "function named double": "def double(n):\n    return n * 2",
    "at most 15 words": "Thursday's meeting moved; rain cancelled the rooftop lunch.",
    "which weekday": "The meeting happens on Thursday.",
}


class Peak:
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


class ScriptedFake:
    """Answers by prompt substring; anything unmapped falls back to ``default``.

    ``default=""`` produces honest *empty* abstentions through the real race path.
    ``errors`` maps a prompt substring to ProviderError args for that task only.
    """

    label = "fake"
    key_env = ""
    model_env = ""

    def __init__(self, name="fake", model="m", answers=None, default="", delay=0.0, errors=None,
                 usage=(30, 6), peak=None, recorder=None, respect_timeout=True):
        self.name = name
        self.model = model
        self.answers = answers or {}
        self.default = default
        self.delay = delay
        self.errors = errors or {}
        self.usage = usage
        self.peak = peak
        self.recorder = recorder if recorder is not None else []
        self.respect_timeout = respect_timeout

    def complete(self, messages, tools, *, timeout, max_output_tokens):
        prompt = messages[1].content
        self.recorder.append({"messages": [(m.role, m.content) for m in messages],
                              "tools": list(tools), "timeout": timeout,
                              "max_output_tokens": max_output_tokens, "prompt": prompt})
        timed_out = self.respect_timeout and self.delay > timeout
        delay = timeout if timed_out else self.delay
        with self.peak or _NULL:
            if delay:
                time.sleep(delay)
            if timed_out:
                raise ProviderError("timeout", f"no answer within {timeout:g}s")
            for key, error in self.errors.items():
                if key in prompt:
                    raise ProviderError(*error)
            for key, text in self.answers.items():
                if key in prompt:
                    return ModelTurn(text=text, usage=Usage(*self.usage), finish_reason="stop")
            return ModelTurn(text=self.default, usage=Usage(*self.usage), finish_reason="stop")


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_NULL = _NullCtx()


def plan_of(*fakes, specs=None, local_only=False):
    racers = tuple(Racer(specs[i] if specs else f"fake:{i}", fake) for i, fake in enumerate(fakes))
    return RacePlan(racers, (), deadline=5.0, per_model_timeout=2.0, local_only=local_only)


def good_fake(name, **kw):
    return ScriptedFake(name=name, answers=GOOD_ANSWERS, **kw)


BOUNDS = TuningBounds(budget_s=60.0, task_deadline=5.0, task_timeout=2.0)


def statuses(report, spec):
    return {o.task_id: o.status for o in next(p for p in report.profiles if p.spec == spec).tasks}


# ============================================================ 1. the suite itself
def test_suite_is_small_fixed_versioned_and_bounded():
    assert len(AUTOTUNE_SUITE) == 6
    ids = [t.id for t in AUTOTUNE_SUITE]
    assert len(set(ids)) == 6
    per_category = {c: sum(1 for t in AUTOTUNE_SUITE if t.category == c) for c in CATEGORIES}
    assert per_category == {"coding": 2, "reasoning": 2, "summarisation": 2}
    assert all(t.prompt and t.rule and len(t.prompt) <= race_mod.RACE_MAX_QUESTION_CHARS
               for t in AUTOTUNE_SUITE)


def test_suite_fingerprint_is_stable_and_input_sensitive():
    assert suite_fingerprint() == suite_fingerprint() == tune.suite_fingerprint(AUTOTUNE_SUITE)
    edited = (dataclasses.replace(AUTOTUNE_SUITE[0], prompt="changed"), *AUTOTUNE_SUITE[1:])
    assert suite_fingerprint(edited) != suite_fingerprint()


# ============================================================ 2. objective checkers (pure, deterministic)
ARITH, LOGIC, JSON_TASK, DEF_TASK, LEN_TASK, FACT_TASK = AUTOTUNE_SUITE


def test_arithmetic_checker():
    assert ARITH.check("391")[0] and ARITH.check("The answer is 391.")[0]
    ok, why = ARITH.check("390")
    assert not ok and "391" in why


def test_logic_checker():
    assert LOGIC.check("Carol")[0] and LOGIC.check("carol.")[0] and LOGIC.check("Carol is the tallest")[0]
    assert not LOGIC.check("Bob")[0]
    assert not LOGIC.check("Carol " + "padding words " * 5)[0]  # over the 20-char rule


def test_json_checker_is_strict_and_distinguishes_wrapping():
    assert JSON_TASK.check('{"language": "python", "sum": 5}')[0]
    ok, why = JSON_TASK.check('```json\n{"language": "python", "sum": 5}\n```')
    assert not ok and "not bare JSON" in why  # content right, instruction violated -> still a fail
    assert not JSON_TASK.check('{"language": "python", "sum": 6}')[0]
    assert not JSON_TASK.check('{"language": "python", "sum": true}')[0]  # bool is not an int here
    assert not JSON_TASK.check('{"language": "python", "sum": 5, "extra": 1}')[0]
    ok, why = JSON_TASK.check("sure! here you go")
    assert not ok and "not valid JSON" in why


def test_def_shape_checker_is_static_only():
    assert DEF_TASK.check("def double(n):\n    return n * 2")[0]
    assert DEF_TASK.check("def double(n) -> int:\nreturn 2 * n")[0]
    assert DEF_TASK.check("def double(n):\nreturn n + n")[0]
    assert not DEF_TASK.check("def double(n):\n    return n * 2\n\nprint(double(3))")[0]  # 3+ lines
    assert not DEF_TASK.check("def triple(n):\n    return n * 3")[0]
    assert not DEF_TASK.check("```python\ndef double(n):\n    return n * 2\n```")[0]
    assert not DEF_TASK.check("def double(n):\n    print(n * 2)")[0]


def test_summary_length_checker():
    assert LEN_TASK.check("Thursday's meeting moved; rain cancelled the rooftop lunch.")[0]
    assert not LEN_TASK.check(" ".join(f"word{i}" for i in range(16)))[0]  # 16 words
    assert not LEN_TASK.check("It rained. The meeting moved to Thursday.")[0]  # two sentences
    assert not LEN_TASK.check("   ")[0]


def test_summary_fact_checker():
    assert FACT_TASK.check("The meeting happens on Thursday.")[0]
    assert FACT_TASK.check("Thursday")[0]
    assert not FACT_TASK.check("Wednesday")[0]
    assert not FACT_TASK.check("it is on thursday " + "x " * 10)[0]  # over 10 words


# ============================================================ 3. run_tuning: results, failures, bounds
def test_all_pass_report_shape_and_no_composite_score():
    report = run_tuning(plan_of(good_fake("a"), good_fake("b"), specs=("groq:fast", "openai")), BOUNDS)
    assert report.suite == tune.SUITE_NAME and report.suite_fingerprint == suite_fingerprint()
    assert not report.budget_exhausted and report.wall_ms >= 0
    fast = next(p for p in report.profiles if p.spec == "groq:fast")
    assert (fast.provider, fast.model) == ("groq", "fast")
    plain = next(p for p in report.profiles if p.spec == "openai")
    assert (plain.provider, plain.model) == ("openai", "")
    assert all(o.status == "passed" for o in fast.tasks)
    assert {c: (s.tasks, s.passed) for c, s in fast.categories.items()} == {
        "coding": (2, 2), "reasoning": (2, 2), "summarisation": (2, 2)}
    assert all(s.median_passed_latency_ms >= 0 for s in fast.categories.values())
    payload = json.dumps(asdict(report))
    assert "score" not in payload.lower()  # no composite score exists anywhere


def test_wrong_answers_are_preserved_per_task_with_reasons():
    weak = ScriptedFake(name="weak", default="unsure")  # passes only the 1-sentence length check
    report = run_tuning(plan_of(good_fake("good"), weak, specs=("good:p", "weak:p")), BOUNDS)
    weak_statuses = statuses(report, "weak:p")
    assert weak_statuses["reasoning-arith"] == "failed"
    assert weak_statuses["summarisation-length"] == "passed"  # "unsure" is one short sentence
    assert weak_statuses["summarisation-fact"] == "failed"
    weak_profile = next(p for p in report.profiles if p.spec == "weak:p")
    failed = [o for o in weak_profile.tasks if o.status == "failed"]
    assert all(o.detail for o in failed)  # every failure carries its objective reason
    assert weak_profile.categories["reasoning"].passed == 0
    assert weak_profile.categories["summarisation"].passed == 1


def test_provider_errors_and_timeouts_become_preserved_task_outcomes():
    flaky = ScriptedFake(name="flaky", answers=GOOD_ANSWERS,
                         errors={"17 multiplied": ("unavailable", "server on fire")})
    slow = ScriptedFake(name="slow", answers=GOOD_ANSWERS, delay=1.0)
    bounds = TuningBounds(budget_s=60.0, task_deadline=5.0, task_timeout=0.2)
    report = run_tuning(plan_of(flaky, slow, specs=("flaky:p", "slow:p")), bounds)
    assert statuses(report, "flaky:p")["reasoning-arith"] == "unavailable"
    assert statuses(report, "flaky:p")["reasoning-logic"] == "passed"  # the rest still ran
    assert "server on fire" in next(o for o in report.profiles[0].tasks
                                    if o.task_id == "reasoning-arith").detail
    assert set(statuses(report, "slow:p").values()) == {"timeout"}  # every task timed out
    assert report.profiles[1].categories == {} or all(
        s.passed == 0 for s in report.profiles[1].categories.values())


def test_budget_zero_sends_nothing_and_skips_every_task():
    recorder = []
    fake = good_fake("a", recorder=recorder)
    report = run_tuning(plan_of(fake, specs=("a:p",)), TuningBounds(budget_s=0.0, task_deadline=5.0,
                                                                    task_timeout=2.0))
    assert report.budget_exhausted is True
    assert set(statuses(report, "a:p").values()) == {"budget_skipped"}
    assert recorder == []  # not one model call was made


def test_budget_stops_mid_suite_and_bounds_total_calls():
    recorder = []
    fake = good_fake("a", delay=0.2, recorder=recorder)
    report = run_tuning(plan_of(fake, specs=("a:p",)), TuningBounds(budget_s=0.3, task_deadline=5.0,
                                                                    task_timeout=2.0))
    ran = [o for o in report.profiles[0].tasks if o.status != "budget_skipped"]
    skipped = [o for o in report.profiles[0].tasks if o.status == "budget_skipped"]
    assert report.budget_exhausted is True
    assert 1 <= len(ran) <= 2 and len(ran) + len(skipped) == 6
    assert len(recorder) == len(ran)  # exactly one bounded call per executed task


def test_refused_or_empty_plans_never_run():
    with pytest.raises(ValueError, match="refused"):
        run_tuning(RacePlan((), (), refused="local-only"), BOUNDS)
    with pytest.raises(ValueError, match="no profiles"):
        run_tuning(RacePlan((), ()), BOUNDS)


def test_adviser_requests_stay_two_messages_with_zero_tools_and_bounded_tokens():
    recorder = []
    run_tuning(plan_of(good_fake("a", recorder=recorder), specs=("a:p",)), BOUNDS)
    assert len(recorder) == 6
    for request in recorder:
        assert [role for role, _ in request["messages"]] == ["system", "user"]
        assert request["tools"] == []
        assert request["max_output_tokens"] == tune.AUTOTUNE_MAX_OUTPUT_TOKENS


def test_stored_texts_are_redacted_everywhere():
    fake = ScriptedFake(name="a", answers={**GOOD_ANSWERS, "17 multiplied": f"391 (key {SECRET})"})
    report = run_tuning(plan_of(fake, specs=("a:p",)), BOUNDS)
    outcome = next(o for o in report.profiles[0].tasks if o.task_id == "reasoning-arith")
    assert SECRET not in outcome.answer_prefix and "REDACTED" in outcome.answer_prefix
    assert len(outcome.answer_prefix) <= tune.ANSWER_PREFIX_CHARS
    assert SECRET not in json.dumps(asdict(report))


# ============================================================ 4. routing: transparent, per-category, configurable
def test_routing_is_per_category_and_never_cross_contaminates():
    coder = ScriptedFake(name="coder", default="", answers={
        "JSON object": GOOD_ANSWERS["JSON object"],
        "function named double": GOOD_ANSWERS["function named double"]})
    reasoner = ScriptedFake(name="reasoner", default="", answers={
        "17 multiplied": "391", "Alice is shorter": "Carol"})
    report = run_tuning(plan_of(coder, reasoner, specs=("coder:p", "reasoner:p")), BOUNDS)
    payload = asdict(report)
    coding = route_category(payload, "coding", 0.5)
    assert coding.winner == "coder:p"  # the reasoner failed coding and is not penalised elsewhere
    reasoning = route_category(payload, "reasoning", 0.5)
    assert reasoning.winner == "reasoner:p"
    summarisation = route_category(payload, "summarisation", 0.5)
    assert summarisation.winner == ""  # nobody answered: no winner is fabricated
    assert any("no profile qualifies" in line for line in summarisation.rationale)


def test_routing_prefers_the_faster_passer_and_is_deterministic():
    slow = good_fake("slow", delay=0.15)
    fast = good_fake("fast", delay=0.0)
    report = run_tuning(plan_of(slow, fast, specs=("slow:p", "fast:p")), BOUNDS)
    payload = asdict(report)
    first = route_category(payload, "coding", 0.5)
    second = route_category(payload, "coding", 0.5)
    assert first.winner == "fast:p" == second.winner
    text = "\n".join(first.rationale)
    assert "pass fraction >= 0.5" in text and "median latency" in text
    assert "slow:p" in text  # the loser's numbers are shown too - the decision is inspectable


def test_routing_tie_breaks_on_the_smaller_spec_text():
    twin_a = good_fake("a")
    twin_b = good_fake("b")
    report = run_tuning(plan_of(twin_a, twin_b, specs=("b:p", "a:p")), BOUNDS)
    decision = route_category(asdict(report), "reasoning", 0.5)
    assert decision.winner == "a:p"


def test_min_pass_is_configurable_and_failures_stay_visible():
    half = ScriptedFake(name="half", default="", answers={"which weekday": "Thursday"})
    report = run_tuning(plan_of(half, specs=("half:p",)), BOUNDS)
    payload = asdict(report)
    lenient = route_category(payload, "summarisation", 0.5)
    assert lenient.winner == "half:p"  # 1/2 passes the 0.5 threshold
    strict = route_category(payload, "summarisation", 1.0)
    assert strict.winner == ""
    text = "\n".join(strict.rationale)
    assert "passed 1/2" in text and "summarisation-length" in text  # the failure is named, not hidden


def test_route_rejects_unknown_categories_and_profiles_without_results():
    with pytest.raises(ValueError, match="unknown category"):
        route_category({"profiles": []}, "cooking", 0.5)
    decision = route_category({"profiles": [{"spec": "x:p", "categories": {}}]}, "coding", 0.5)
    assert decision.winner == "" and any("no stored coding results" in line for line in decision.rationale)


def test_render_route_states_it_changes_no_configuration():
    report = run_tuning(plan_of(good_fake("a"), specs=("a:p",)), BOUNDS)
    text = render_route(route_category(asdict(report), "coding", 0.5))
    assert "advisory only" in text and "never changes configuration" in text


# ============================================================ 5. persistence: safe, redacted, non-mutating
def test_save_load_roundtrip_is_atomic_private_and_reproducible(tmp_path):
    env = {"ULTRON_DATA_DIR": str(tmp_path)}
    report = run_tuning(plan_of(good_fake("a"), specs=("groq:m1",)), BOUNDS)
    path = save_results(report, env)
    assert path == results_path(env) == tmp_path / tune.AUTOTUNE_RESULTS_FILENAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # owner-only, like tokens and session state
    payload, problems = load_results(env)
    assert problems == [] and payload["suite_fingerprint"] == suite_fingerprint()
    assert payload["profiles"][0]["spec"] == "groq:m1"
    assert payload["profiles"][0]["categories"]["coding"]["passed"] == 2
    # reproducibility: the stored report identifies provider/model config and evaluation inputs
    assert payload["suite"] == tune.SUITE_NAME and payload["bounds"]["task_deadline"] == 5.0


def test_save_writes_only_the_results_file_and_never_touches_configuration(tmp_path):
    env = {"ULTRON_DATA_DIR": str(tmp_path), "OPENAI_API_KEY": FAKE_OPENAI}
    before = dict(env)
    report = run_tuning(plan_of(good_fake("a"), specs=("a:p",)), BOUNDS)
    save_results(report, env)
    assert sorted(p.name for p in tmp_path.iterdir()) == [tune.AUTOTUNE_RESULTS_FILENAME]
    assert env == before  # the mapping itself was not mutated either


def test_load_handles_missing_corrupt_and_foreign_files(tmp_path):
    env = {"ULTRON_DATA_DIR": str(tmp_path)}
    payload, problems = load_results(env)
    assert payload is None and any("no stored tuning results" in p for p in problems)
    results_path(env).write_text("{broken", encoding="utf-8")
    payload, problems = load_results(env)
    assert payload is None and any("unreadable" in p for p in problems)
    results_path(env).write_text(json.dumps({"version": 999}), encoding="utf-8")
    payload, problems = load_results(env)
    assert payload is None and any("unknown version" in p for p in problems)
    stale = asdict(run_tuning(plan_of(good_fake("a"), specs=("a:p",)), BOUNDS))
    stale["suite_fingerprint"] = "deadbeef0000"
    results_path(env).write_text(json.dumps(stale), encoding="utf-8")
    payload, problems = load_results(env)
    assert payload is not None and any("may not be comparable" in p for p in problems)


def test_clear_removes_the_results_file_only(tmp_path):
    env = {"ULTRON_DATA_DIR": str(tmp_path)}
    save_results(run_tuning(plan_of(good_fake("a"), specs=("a:p",)), BOUNDS), env)
    (tmp_path / "keep.txt").write_text("mine", encoding="utf-8")
    assert tune.clear_results(env) is True
    assert tune.clear_results(env) is False
    assert (tmp_path / "keep.txt").is_file()  # nothing else was touched


def test_render_stored_summarises_loaded_data(tmp_path):
    env = {"ULTRON_DATA_DIR": str(tmp_path)}
    save_results(run_tuning(plan_of(good_fake("a"), specs=("a:p",)), BOUNDS), env)
    payload, _ = load_results(env)
    text = render_stored(payload)
    assert "stored results" in text and "coding: passed 2/2" in text and "never executed" not in text
    assert "Objective checks only" in text


# ============================================================ 6. ULTRON_LOCAL_ONLY and governance
def test_local_only_refuses_cloud_profiles_with_zero_construction(monkeypatch):
    calls = []
    monkeypatch.setattr(race_mod, "_build_provider", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("ULTRON_LOCAL_ONLY", "1")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)
    monkeypatch.setenv("ULTRON_DATA_DIR", "/tmp/does-not-matter-here")
    assert cli_main(["autotune", "run", "--models", "openai,groq"]) == 1
    assert calls == []  # no provider client was built, so nothing could be sent anywhere


def test_local_only_default_roster_is_local_and_the_run_proceeds(monkeypatch, tmp_path):
    built = []

    def fake_build(provider_name, model, env, factory):
        built.append(provider_name)
        return good_fake(provider_name)

    monkeypatch.setattr(race_mod, "_build_provider", fake_build)
    monkeypatch.setenv("ULTRON_LOCAL_ONLY", "1")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)  # keys exist but must not be used
    monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path))
    assert cli_main(["autotune", "run", "--no-save"]) == 0
    assert built == ["ollama"]


def test_module_has_no_execution_or_private_data_surface():
    source = inspect.getsource(tune)
    for forbidden in ("eval(", "exec(", "subprocess", "os.system", "socket", "ultron.history",
                      "chat_history", "dotenv", "ToolRegistry", "AgentRuntime"):
        assert forbidden not in source, forbidden
    assert "registry" not in inspect.signature(run_tuning).parameters


def test_autotune_env_bounds_are_clamped_with_problems():
    problems: list[str] = []
    bounds = tuning_bounds({"ULTRON_AUTOTUNE_BUDGET": "9999", "ULTRON_AUTOTUNE_TASK_TIMEOUT": "x",
                            "ULTRON_AUTOTUNE_MIN_PASS": "2"}, problems)
    assert bounds.budget_s == 900.0 and bounds.task_timeout == 20.0 and bounds.min_pass == 1.0
    assert len(problems) == 3


def test_kill_switch_disables_runs_only():
    assert tune.enabled({}) is True
    assert tune.enabled({"ULTRON_AUTOTUNE": "0"}) is False
    assert tune.enabled({"ULTRON_AUTOTUNE": "off"}) is False
    assert tune.enabled({"ULTRON_AUTOTUNE": "1"}) is True


# ============================================================ 7. wire level: real SDK clients on loopback stubs
def test_full_tuning_run_over_real_http_against_two_local_stubs():
    with StubOpenAI() as first, StubOpenAI() as second:
        first.script(*[completion(GOOD_ANSWERS[key], usage=(20, 5)) for key in
                       ("17 multiplied", "Alice is shorter", "JSON object", "function named double",
                        "at most 15 words", "which weekday")])
        second.script(*[completion("I do not know.", usage=(18, 4))] * 6)
        config = load_agent_config({"ULTRON_PROVIDER": "ollama"})
        one = create_provider(config, ollama_base_url=first.base_url)
        two = create_provider(config, ollama_base_url=second.base_url)
        report = run_tuning(plan_of(one, two, specs=("ollama:good", "ollama:weak")), BOUNDS)
    assert all(o.status == "passed" for o in report.profiles[0].tasks)
    assert statuses(report, "ollama:weak")["reasoning-arith"] == "failed"
    assert len(first.requests) == 6 and len(second.requests) == 6
    assert report.profiles[0].categories["coding"].median_passed_latency_ms >= 0


# ============================================================ 8. the CLI verb
@pytest.fixture
def fake_build(monkeypatch, tmp_path):
    built = []

    def build(provider_name, model, env, factory):
        spec_model = model or "m"
        if provider_name == "groq":  # the deliberately weaker profile
            fake = ScriptedFake(name=provider_name, model=spec_model,
                                answers={"which weekday": "Thursday"})
        else:
            fake = good_fake(provider_name, model=spec_model)
        built.append((provider_name, spec_model, fake))
        return fake

    monkeypatch.setattr(race_mod, "_build_provider", build)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)
    monkeypatch.setenv("GROQ_API_KEY", FAKE_GROQ)
    monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path))
    return built


def test_cli_run_races_saves_and_reports(fake_build, tmp_path, capsys):
    assert cli_main(["autotune", "run"]) == 0
    out = capsys.readouterr().out
    assert "racing 2 profile(s) over 6 fixed synthetic tasks" in out
    assert "AutoTune report" in out and "code was NEVER executed" in out
    assert (tmp_path / tune.AUTOTUNE_RESULTS_FILENAME).is_file()
    assert "results stored in" in out
    assert len(fake_build) == 2


def test_cli_run_no_save_and_json(fake_build, tmp_path, capsys):
    assert cli_main(["autotune", "run", "--no-save", "--json"]) == 0
    payload = json.loads(_json_from(capsys))
    assert payload["suite"] == tune.SUITE_NAME and len(payload["profiles"]) == 2
    assert not (tmp_path / tune.AUTOTUNE_RESULTS_FILENAME).exists()


def _json_from(capsys):
    out = capsys.readouterr().out
    start = out.index("{")
    return out[start:]


def test_cli_run_exits_one_when_no_profile_ever_answered(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(race_mod, "_build_provider",
                        lambda name, model, env, factory: ScriptedFake(name=name, default=""))
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)
    monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path))
    assert cli_main(["autotune", "run", "--models", "openai"]) == 1
    assert "empty" in capsys.readouterr().out  # abstention kinds are visible


def test_cli_kill_switch_refuses_runs_but_show_and_clear_work(fake_build, tmp_path, monkeypatch,
                                                              capsys):
    assert cli_main(["autotune", "run"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("ULTRON_AUTOTUNE", "0")
    assert cli_main(["autotune", "run"]) == 1
    assert "REFUSED" in capsys.readouterr().err
    assert cli_main(["autotune", "show"]) == 0  # reading stored results is not a run
    assert "stored results" in capsys.readouterr().out


def test_cli_route_show_clear_flow(fake_build, tmp_path, capsys):
    assert cli_main(["autotune", "run"]) == 0
    capsys.readouterr()
    assert cli_main(["autotune", "route", "coding"]) == 0
    out = capsys.readouterr().out
    assert "winner: openai" in out and "routing rule" in out
    assert cli_main(["autotune", "route", "summarisation", "--min-pass", "1"]) == 0  # groq passed 1/2
    out = capsys.readouterr().out
    assert "winner: openai" in out and "not eligible: groq" in out
    assert cli_main(["autotune", "route", "coding", "--spec"]) == 0
    assert capsys.readouterr().out.strip() == "openai"
    assert cli_main(["autotune", "route", "coding", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["winner"] == "openai"
    assert cli_main(["autotune", "show"]) == 0
    assert "stored results" in capsys.readouterr().out
    assert cli_main(["autotune", "clear"]) == 0
    assert "cleared" in capsys.readouterr().out
    assert cli_main(["autotune", "clear"]) == 0
    assert "no stored" in capsys.readouterr().out
    assert cli_main(["autotune", "route", "coding"]) == 1  # nothing stored anymore


def test_cli_route_needs_a_category_and_show_needs_results(fake_build, capsys):
    assert cli_main(["autotune", "route"]) == 1
    assert "needs a category" in capsys.readouterr().err
    assert cli_main(["autotune", "show"]) == 1
    assert "no stored tuning results" in capsys.readouterr().out


def test_cli_budget_flag_is_clamped_visibly(fake_build, capsys):
    assert cli_main(["autotune", "run", "--no-save", "--budget", "5"]) == 0
    out = capsys.readouterr().out
    assert "ULTRON_AUTOTUNE_BUDGET=5 outside" in out and "clamped to 30" in out
    assert "total budget 30s" in out


def test_cli_models_cap_and_problems_are_inherited_from_race(fake_build, monkeypatch, capsys):
    monkeypatch.setenv("ULTRON_RACE_MAX_MODELS", "4")  # the default cap is 3
    assert cli_main(["autotune", "run", "--no-save", "--models",
                     "openai,groq,ollama,ollama:b,ollama:c"]) == 0
    out = capsys.readouterr().out
    assert "race cap is 4" in out and "racing 4 profile(s)" in out


def test_render_tuning_states_the_honest_limitations():
    report = run_tuning(plan_of(good_fake("a"), specs=("a:p",)), BOUNDS)
    text = render_tuning(report)
    assert "code was NEVER executed" in text
    assert "prose quality was NOT measured" in text
    assert "no composite score exists" in text
    assert "nothing was reconfigured" in text


# ============================================================ 9. regression compatibility
def test_race_module_surface_is_unchanged_by_autotune():
    # autotune reuses these exact names; if race ever changes them, this fails loudly here
    for name in ("resolve_race", "run_race", "RacePlan", "Racer", "RACE_ABSOLUTE_MAX_MODELS"):
        assert hasattr(race_mod, name)
    assert TaskOutcome("t", "coding", "passed", "", 1, 2, 3, "x").task_id == "t"
