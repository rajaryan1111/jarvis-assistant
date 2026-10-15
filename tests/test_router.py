"""Deterministic tests for the opt-in Smart Router (docs/M2_6_SMART_ROUTER.md).

Everything here runs on explicit env dicts, hand-crafted (schema-accurate) AutoTune
results and fake provider factories: no model call, no network, no credential, no daemon.
Covered: the published classification rules, results validation (missing/corrupt/version/
fingerprint/age), eligibility and the reused autotune routing rule, winner usability,
ULTRON_LOCAL_ONLY across routing AND every fallback path (zero client construction),
transparency (explanations, logging, status), default-behaviour preservation when the
router is off, non-mutation of env/results, main.run_agent wiring, the CLI verb and
regressions against race/autotune reuse.
"""

from __future__ import annotations

import json
import logging
import time
from types import SimpleNamespace

import pytest

from ultron.agent import autotune
from ultron.agent import race as race_mod
from ultron.agent import router as smart_router
from ultron.cli import main as cli_main

FAKE_GROQ = "gsk_" + "routerTest0" * 6  # assembled fake, never a literal credential
FAKE_OPENAI = "sk-" + "routerTest0" * 6  # assembled fake, never a literal credential

CODING_TEXT = "fix the bug in my python function"
SUMMARY_TEXT = "summarize this python code in one sentence"
REASONING_TEXT = "prove that sqrt(2) is irrational"
NO_CATEGORY_TEXT = "what's the weather tomorrow"


# ============================================================ helpers
def cat_stats(tasks: int = 2, passed: int = 2, median: int = 100) -> dict:
    return {"tasks": tasks, "passed": passed, "median_passed_latency_ms": median}


def profile(spec: str, provider: str, model: str, categories: dict, tasks: list | None = None) -> dict:
    return {"spec": spec, "provider": provider, "model": model,
            "categories": categories, "tasks": tasks if tasks is not None else [
                {"task_id": f"t{i}", "category": cat, "status": "passed", "detail": "",
                 "latency_ms": 50, "input_tokens": 10, "output_tokens": 5,
                 "answer_prefix": "ok"}
                for i, cat in enumerate(categories) for _ in range(categories[cat]["passed"])]}


def good_profiles() -> list[dict]:
    """groq:fast wins everything on latency; openai passes slower; ollama:q passes coding."""
    return [
        profile("groq:fast", "groq", "fast", {"coding": cat_stats(median=80),
                                              "reasoning": cat_stats(median=80),
                                              "summarisation": cat_stats(median=80)}),
        profile("openai", "openai", "gpt", {"coding": cat_stats(median=200),
                                            "reasoning": cat_stats(median=200),
                                            "summarisation": cat_stats(median=200)}),
    ]


def payload_for(profiles: list[dict], *, days_old: float = 0.0, fingerprint: str | None = None,
                version: int = autotune.AUTOTUNE_RESULTS_VERSION,
                created_utc: str | None = None) -> dict:
    if created_utc is None:
        created_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_old * 86400))
    return {"version": version, "suite": autotune.SUITE_NAME,
            "suite_fingerprint": fingerprint if fingerprint is not None else autotune.suite_fingerprint(),
            "created_utc": created_utc, "local_only": False, "budget_exhausted": False,
            "wall_ms": 1234, "bounds": {}, "profiles": profiles, "problems": []}


def store(env: dict, payload: dict) -> None:
    path = autotune.results_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def routed_env(tmp_path, **over) -> dict:
    """Env with the router on, credentials for the good profiles, and valid stored results."""
    env = {"ULTRON_DATA_DIR": str(tmp_path / "data"), "ULTRON_ROUTER": "1",
           "GROQ_API_KEY": FAKE_GROQ, "OPENAI_API_KEY": FAKE_OPENAI, **over}
    store(env, payload_for(good_profiles()))
    return env


class SpyFactory:
    """Records provider construction; returns a stand-in provider (never a real client)."""

    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, config, **kwargs):
        self.calls.append((getattr(config, "provider", "?"), tuple(sorted(kwargs))))
        return SimpleNamespace(name="fake-provider", config=config)


# ============================================================ classification rules
class TestClassifier:
    def test_coding_request(self):
        category, reason = smart_router.classify(CODING_TEXT)
        assert category == "coding"
        assert "bug" in reason and "classified as 'coding'" in reason

    def test_summarisation_beats_coding(self):
        # published priority: an explicit summarisation request stays summarisation
        category, reason = smart_router.classify(SUMMARY_TEXT)
        assert category == "summarisation"
        assert "summari" in reason.lower()

    def test_reasoning_request(self):
        category, _ = smart_router.classify(REASONING_TEXT)
        assert category == "reasoning"

    def test_coding_beats_reasoning(self):
        category, _ = smart_router.classify("why does my script print None")
        assert category == "coding"

    def test_no_rule_matches(self):
        assert smart_router.classify(NO_CATEGORY_TEXT) is None

    def test_deterministic_and_case_insensitive(self):
        first = smart_router.classify("DEBUG my Python FUNCTION")
        assert first is not None and first[0] == "coding"
        assert smart_router.classify("DEBUG my Python FUNCTION") == first

    def test_rule_table_is_fixed_and_published(self):
        assert [category for category, _, _ in smart_router.CATEGORY_RULES] == \
            ["summarisation", "coding", "reasoning"]


# ============================================================ opt-in / default behaviour
class TestDisabledByDefault:
    def test_off_when_unset_reads_nothing(self, tmp_path):
        env = {"ULTRON_DATA_DIR": str(tmp_path / "missing-data")}  # results exist nowhere
        decision = smart_router.route_request(CODING_TEXT, env)
        assert decision.routed is False and decision.provider is None
        assert decision.results_state == "disabled"  # never even looked for results
        assert decision.fallback_reason == "router disabled"
        assert any("smart router is OFF" in line for line in decision.explanation)
        assert any("default ULTRON_PROVIDER path" in line for line in decision.explanation)

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "2", "maybe"])
    def test_off_values(self, value):
        assert smart_router.router_enabled({"ULTRON_ROUTER": value}) is False

    @pytest.mark.parametrize("value", ["1", "true", "YES", "On"])
    def test_on_values(self, value):
        assert smart_router.router_enabled({"ULTRON_ROUTER": value}) is True

    def test_main_path_untouched_when_off(self, tmp_path, monkeypatch):
        """run_agent with the router off: default provider in, no router event appended."""
        import main as legacy
        sentinel = SimpleNamespace(name="default-provider")
        captured = {}

        class FakeRuntime:
            def run(self, objective, ctx):
                return SimpleNamespace(reply="done")

        def fake_build_agent(provider=None):
            captured["provider"] = provider
            return FakeRuntime()

        monkeypatch.setattr(legacy, "AGENT_CONFIG", SimpleNamespace(enabled=True))
        monkeypatch.setattr(legacy, "agent_provider", lambda: sentinel)
        monkeypatch.setattr(legacy, "build_agent", fake_build_agent)
        monkeypatch.delenv("ULTRON_ROUTER", raising=False)
        ctx = legacy.CommandContext.untrusted("router-test")
        assert legacy.run_agent(CODING_TEXT, ctx) == "done"
        assert captured["provider"] is sentinel
        assert not [event for event in ctx.events if event[0] == "router_decision"]


# ============================================================ results validation
class TestResultsValidation:
    def test_valid_results_route(self, tmp_path):
        spy = SpyFactory()
        env = routed_env(tmp_path)
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=spy)
        assert decision.routed is True and decision.category == "coding"
        assert decision.winner == "groq:fast" and decision.provider is not None
        assert decision.results_state == "valid" and decision.fallback_reason == ""
        joined = "\n".join(decision.explanation)
        assert "smart router is ON" in joined
        assert "classified as 'coding'" in joined
        assert "results are valid" in joined and autotune.suite_fingerprint() in joined
        assert "pass fraction" in joined  # autotune's rule rationale, reused verbatim
        assert "routing this request to groq:fast" in joined
        assert "grants no new authority" in joined

    def test_missing_results_fall_back(self, tmp_path):
        env = {"ULTRON_DATA_DIR": str(tmp_path / "nope"), "ULTRON_ROUTER": "1",
               "GROQ_API_KEY": FAKE_GROQ}
        decision = smart_router.route_request(CODING_TEXT, env)
        assert decision.results_state == "missing" and decision.routed is False
        assert decision.fallback_reason == "autotune results are missing"
        assert any("no stored tuning results" in line for line in decision.explanation)

    def test_corrupt_results_fall_back(self, tmp_path):
        env = {"ULTRON_DATA_DIR": str(tmp_path / "data"), "ULTRON_ROUTER": "1"}
        path = autotune.results_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        decision = smart_router.route_request(CODING_TEXT, env)
        assert decision.results_state == "unreadable" and decision.routed is False

    def test_wrong_version_falls_back(self, tmp_path):
        env = routed_env(tmp_path)
        store(env, payload_for(good_profiles(), version=autotune.AUTOTUNE_RESULTS_VERSION + 1))
        decision = smart_router.route_request(CODING_TEXT, env)
        assert decision.results_state == "unreadable" and decision.routed is False

    def test_stale_suite_fingerprint_falls_back(self, tmp_path):
        env = routed_env(tmp_path)
        store(env, payload_for(good_profiles(), fingerprint="deadbeefcafe"))
        decision = smart_router.route_request(CODING_TEXT, env)
        assert decision.results_state == "stale-suite" and decision.routed is False
        joined = "\n".join(decision.explanation)
        assert "deadbeefcafe" in joined and autotune.suite_fingerprint() in joined
        assert "re-run `ultron autotune run`" in joined

    def test_old_results_fall_back(self, tmp_path):
        env = routed_env(tmp_path)
        store(env, payload_for(good_profiles(), days_old=40))
        decision = smart_router.route_request(CODING_TEXT, env)
        assert decision.results_state == "stale-age" and decision.routed is False
        assert any("40.0 day(s) old" in line and "ULTRON_ROUTER_MAX_AGE_DAYS" in line
                   for line in decision.explanation)

    def test_max_age_is_configurable(self, tmp_path):
        env = routed_env(tmp_path, ULTRON_ROUTER_MAX_AGE_DAYS="60")
        store(env, payload_for(good_profiles(), days_old=40))
        spy = SpyFactory()
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=spy)
        assert decision.routed is True and decision.winner == "groq:fast"

    def test_unparseable_timestamp_is_stale(self, tmp_path):
        env = routed_env(tmp_path)
        store(env, payload_for(good_profiles(), created_utc="not-a-timestamp"))
        decision = smart_router.route_request(CODING_TEXT, env)
        assert decision.results_state == "stale-age" and decision.routed is False
        assert any("not parseable" in line for line in decision.explanation)

    def test_no_category_match_skips_validation(self, tmp_path):
        env = routed_env(tmp_path)
        decision = smart_router.route_request(NO_CATEGORY_TEXT, env)
        assert decision.routed is False and decision.category == ""
        assert decision.results_state == ""  # classification failed first; results untouched
        assert decision.fallback_reason == "no category matched"
        assert any("no task-category rule matched" in line for line in decision.explanation)

    def test_validate_results_direct(self, tmp_path):
        env = routed_env(tmp_path)
        cfg = smart_router.router_config(env)
        payload, state, lines = smart_router.validate_results(env, cfg)
        assert payload is not None and state == "valid" and lines


# ============================================================ configuration handling
class TestConfiguration:
    def test_defaults(self):
        cfg = smart_router.router_config({})
        assert cfg.enabled is False
        assert cfg.min_pass == smart_router.ROUTER_DEFAULT_MIN_PASS == 0.5
        assert cfg.max_age_days == smart_router.ROUTER_DEFAULT_MAX_AGE_DAYS == 14.0
        assert cfg.local_only is False and cfg.problems == ()

    def test_local_only_read_from_agent_config(self):
        cfg = smart_router.router_config({"ULTRON_ROUTER": "1", "ULTRON_LOCAL_ONLY": "1"})
        assert cfg.local_only is True

    @pytest.mark.parametrize("raw,expected", [("0.9", 0.9), ("0", 0.0), ("1", 1.0),
                                              ("2", 1.0), ("-3", 0.0), ("junk", 0.5)])
    def test_min_pass_clamped(self, raw, expected):
        cfg = smart_router.router_config({"ULTRON_ROUTER_MIN_PASS": raw})
        assert cfg.min_pass == expected
        if raw in ("2", "-3", "junk"):
            assert any("ULTRON_ROUTER_MIN_PASS" in p for p in cfg.problems)

    @pytest.mark.parametrize("raw,expected", [("60", 60.0), ("0", 1.0), ("9999", 365.0),
                                              ("junk", 14.0)])
    def test_max_age_clamped(self, raw, expected):
        cfg = smart_router.router_config({"ULTRON_ROUTER_MAX_AGE_DAYS": raw})
        assert cfg.max_age_days == expected

    def test_config_problems_are_explained(self, tmp_path):
        env = routed_env(tmp_path, ULTRON_ROUTER_MIN_PASS="99")
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=SpyFactory())
        assert any(line.startswith("config: ") and "ULTRON_ROUTER_MIN_PASS" in line
                   for line in decision.explanation)


# ============================================================ eligibility (autotune's rule, reused)
class TestEligibility:
    def test_min_pass_rejects_weak_winner(self, tmp_path):
        spy = SpyFactory()
        env = routed_env(tmp_path, ULTRON_ROUTER_MIN_PASS="0.9")
        profiles = [
            profile("groq:fast", "groq", "fast", {"coding": cat_stats(passed=1)},
                    tasks=[{"task_id": "c1", "category": "coding", "status": "passed",
                            "detail": "", "latency_ms": 40},
                           {"task_id": "c2", "category": "coding", "status": "failed",
                            "detail": "wrong answer", "latency_ms": 41}]),
            profile("openai", "openai", "gpt", {"coding": cat_stats(median=200)}),
        ]
        store(env, payload_for(profiles))
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=spy)
        assert decision.routed is True and decision.winner == "openai"
        joined = "\n".join(decision.explanation)
        assert "groq:fast" in joined and "passed 1/2" in joined  # the rejection is printed

    def test_nobody_qualifies_falls_back(self, tmp_path):
        env = routed_env(tmp_path)
        profiles = [profile("groq:fast", "groq", "fast", {"coding": cat_stats(passed=0)})]
        store(env, payload_for(profiles))
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=SpyFactory())
        assert decision.routed is False and decision.winner == ""
        assert decision.fallback_reason == "no profile qualifies for 'coding' under the routing rule"
        assert any("no profile qualifies" in line for line in decision.explanation)

    def test_categories_do_not_leak(self, tmp_path):
        """groq's coding speed must not win a summarisation request it failed."""
        spy = SpyFactory()
        env = routed_env(tmp_path)
        profiles = [
            profile("groq:fast", "groq", "fast", {"coding": cat_stats(median=10),
                                                  "summarisation": cat_stats(passed=0)}),
            profile("openai", "openai", "gpt", {"coding": cat_stats(median=500),
                                                "summarisation": cat_stats(median=500)}),
        ]
        store(env, payload_for(profiles))
        decision = smart_router.route_request(SUMMARY_TEXT, env, provider_factory=spy)
        assert decision.routed is True and decision.category == "summarisation"
        assert decision.winner == "openai"

    def test_deterministic_tie_break(self, tmp_path):
        spy = SpyFactory()
        env = routed_env(tmp_path)
        stats = {"coding": cat_stats(median=100)}
        profiles = [profile("groq:b", "groq", "b", stats), profile("groq:a", "groq", "a", stats)]
        store(env, payload_for(profiles))
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=spy)
        assert decision.winner == "groq:a"  # lexicographic tie-break, autotune's rule


# ============================================================ present-tense usability
class TestUsability:
    def test_winner_without_credentials_falls_back(self, tmp_path):
        spy = SpyFactory()
        env = routed_env(tmp_path)
        env.pop("GROQ_API_KEY")  # winner groq:fast can no longer build
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=spy)
        assert decision.routed is False and decision.winner == "groq:fast"
        assert decision.fallback_reason == "winner not usable"
        joined = "\n".join(decision.explanation)
        assert "not usable right now" in joined and "missing credential" in joined
        assert spy.calls == []  # refused before any factory call

    def test_winner_is_built_through_race_path(self, tmp_path):
        spy = SpyFactory()
        env = routed_env(tmp_path)
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=spy)
        assert decision.routed is True and spy.calls and decision.provider is not None
        assert spy.calls[0][0] == "groq"  # built through the existing selection path

    def test_ollama_winner_builds_via_factory(self, tmp_path):
        spy = SpyFactory()
        env = routed_env(tmp_path)
        store(env, payload_for([profile("ollama:q", "ollama", "q", {"coding": cat_stats()})]))
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=spy)
        assert decision.routed is True and decision.winner == "ollama:q"
        assert spy.calls and spy.calls[0][0] == "ollama"


# ============================================================ LOCAL_ONLY across every path
class TestLocalOnly:
    def _mixed(self):
        return [
            profile("groq:fast", "groq", "fast", {"coding": cat_stats(median=10)}),
            profile("ollama:q", "ollama", "q", {"coding": cat_stats(median=900)}),
        ]

    def test_cloud_profiles_excluded_before_construction(self, tmp_path):
        spy = SpyFactory()
        env = routed_env(tmp_path, ULTRON_LOCAL_ONLY="1")
        store(env, payload_for([p for p in self._mixed() if p["provider"] == "groq"]))
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=spy)
        assert decision.routed is False and spy.calls == []  # ZERO clients constructed
        joined = "\n".join(decision.explanation)
        assert "ULTRON_LOCAL_ONLY=1" in joined and "BEFORE" in joined
        assert decision.fallback_reason == "no profile qualifies for 'coding' under the routing rule"

    def test_local_winner_selected_over_faster_cloud(self, tmp_path):
        spy = SpyFactory()
        env = routed_env(tmp_path, ULTRON_LOCAL_ONLY="1")
        store(env, payload_for(self._mixed()))
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=spy)
        assert decision.routed is True and decision.winner == "ollama:q"
        assert all(provider == "ollama" for provider, _ in spy.calls)  # no cloud client built

    def test_cloud_winner_allowed_when_local_only_off(self, tmp_path):
        spy = SpyFactory()
        env = routed_env(tmp_path)
        store(env, payload_for(self._mixed()))
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=spy)
        assert decision.routed is True and decision.winner == "groq:fast"

    def test_race_layer_still_refuses_cloud_specs(self):
        """Second gate (regression): even a hand-fed cloud spec is refused pre-construction."""
        spy = SpyFactory()
        plan = race_mod.resolve_race({"ULTRON_LOCAL_ONLY": "1", "GROQ_API_KEY": FAKE_GROQ},
                                     ["groq:fast"], provider_factory=spy)
        assert plan.refused and not plan.racers and spy.calls == []

    def test_fallback_path_is_local_only_gated(self, tmp_path):
        """The fallback is the default provider path - which load_agent_config itself gates
        fail-closed: a cloud default under ULTRON_LOCAL_ONLY=1 turns the loop OFF entirely."""
        from ultron.config import load_agent_config
        cfg = load_agent_config({"ULTRON_LOCAL_ONLY": "1", "ULTRON_PROVIDER": "groq",
                                 "GROQ_API_KEY": FAKE_GROQ})
        assert cfg.provider_error and "ULTRON_LOCAL_ONLY=1" in cfg.provider_error


# ============================================================ transparency
class TestTransparency:
    def test_routing_is_logged(self, tmp_path, caplog):
        env = routed_env(tmp_path)
        with caplog.at_level(logging.INFO, logger="ultron.router"):
            smart_router.route_request(CODING_TEXT, env, provider_factory=SpyFactory())
        assert "coding -> groq:fast" in caplog.text

    def test_fallback_is_logged(self, tmp_path, caplog):
        with caplog.at_level(logging.INFO, logger="ultron.router"):
            smart_router.route_request(CODING_TEXT, {"ULTRON_ROUTER": "0"})
        assert "fallback" in caplog.text and "router disabled" in caplog.text

    def test_every_decision_carries_an_explanation(self, tmp_path):
        env = routed_env(tmp_path)
        for text in (CODING_TEXT, NO_CATEGORY_TEXT):
            decision = smart_router.route_request(text, env, provider_factory=SpyFactory())
            assert decision.explanation, f"no explanation for {text!r}"
        assert smart_router.route_request(CODING_TEXT, {"ULTRON_ROUTER": "0"}).explanation

    def test_status_lines_valid(self, tmp_path):
        env = routed_env(tmp_path)
        lines = "\n".join(smart_router.status_lines(env))
        assert "ULTRON_ROUTER=on" in lines and "results state: valid" in lines
        for category in autotune.CATEGORIES:
            assert f"{category}: would route to" in lines
        assert "groq:fast" in lines and "first match wins" in lines

    def test_status_lines_disabled_and_missing(self, tmp_path):
        lines = "\n".join(smart_router.status_lines({"ULTRON_DATA_DIR": str(tmp_path)}))
        assert "ULTRON_ROUTER=off" in lines and "routing is disabled" in lines
        env = {"ULTRON_DATA_DIR": str(tmp_path / "x"), "ULTRON_ROUTER": "1"}
        assert "results state: missing" in "\n".join(smart_router.status_lines(env))

    def test_config_summary(self, tmp_path):
        assert "off" in smart_router.config_summary({})
        env = routed_env(tmp_path)
        summary = smart_router.config_summary(env)
        assert summary.startswith("on (") and "results valid" in summary


# ============================================================ non-mutation guarantees
class TestNonMutation:
    def test_env_dict_is_not_mutated(self, tmp_path):
        env = routed_env(tmp_path)
        before = dict(env)
        smart_router.route_request(CODING_TEXT, env, provider_factory=SpyFactory())
        assert env == before

    def test_results_file_is_never_rewritten(self, tmp_path):
        env = routed_env(tmp_path)
        path = autotune.results_path(env)
        before = path.read_bytes()
        listing_before = sorted(p.name for p in path.parent.iterdir())
        spy = SpyFactory()
        smart_router.route_request(CODING_TEXT, env, provider_factory=spy)
        smart_router.route_request(NO_CATEGORY_TEXT, env, provider_factory=spy)
        smart_router.status_lines(env)
        assert path.read_bytes() == before
        assert sorted(p.name for p in path.parent.iterdir()) == listing_before

    def test_module_performs_no_writes_or_deletes(self):
        with open(smart_router.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for banned in ("atomic_write", "save_results", "clear_results", ".write(", "open(",
                       "os.remove", "unlink", "mkdir", "setenv", "environ["):
            assert banned not in source, f"router must not use {banned!r}"

    def test_routing_rule_is_autotunes_not_a_copy(self, tmp_path, monkeypatch):
        calls = []
        real = autotune.route_category

        def spy_rule(payload, category, min_pass):
            calls.append((category, min_pass))
            return real(payload, category, min_pass)

        monkeypatch.setattr(autotune, "route_category", spy_rule)
        env = routed_env(tmp_path)
        decision = smart_router.route_request(CODING_TEXT, env, provider_factory=SpyFactory())
        assert decision.routed is True
        assert calls == [("coding", 0.5)]  # exactly once: the rule is reused, never duplicated


# ============================================================ main.run_agent wiring
class TestRunAgentWiring:
    def _patch_main(self, monkeypatch, tmp_path, sentinel):
        import main as legacy
        captured = {}

        class FakeRuntime:
            def run(self, objective, ctx):
                return SimpleNamespace(reply="done")

        def fake_build_agent(provider=None):
            captured["provider"] = provider
            return FakeRuntime()

        monkeypatch.setattr(legacy, "AGENT_CONFIG", SimpleNamespace(enabled=True))
        monkeypatch.setattr(legacy, "agent_provider", lambda: sentinel)
        monkeypatch.setattr(legacy, "build_agent", fake_build_agent)
        return legacy, captured

    def test_routed_provider_reaches_runtime_and_events(self, tmp_path, monkeypatch):
        sentinel = SimpleNamespace(name="default-provider")
        legacy, captured = self._patch_main(monkeypatch, tmp_path, sentinel)
        env = routed_env(tmp_path)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        ctx = legacy.CommandContext.untrusted("router-test")
        assert legacy.run_agent(CODING_TEXT, ctx) == "done"
        assert captured["provider"] is not sentinel  # the tuned winner replaced the default
        decisions = [value for kind, value in ctx.events if kind == "router_decision"]
        assert len(decisions) == 1 and decisions[0].routed is True
        assert decisions[0].winner == "groq:fast"
        assert [kind for kind, _ in ctx.events] == ["router_decision", "agent_report"]

    def test_fallback_decision_still_recorded(self, tmp_path, monkeypatch):
        sentinel = SimpleNamespace(name="default-provider")
        legacy, captured = self._patch_main(monkeypatch, tmp_path, sentinel)
        monkeypatch.setenv("ULTRON_ROUTER", "1")
        monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path / "empty"))
        ctx = legacy.CommandContext.untrusted("router-test")
        assert legacy.run_agent(CODING_TEXT, ctx) == "done"
        assert captured["provider"] is sentinel  # default path, unchanged
        decisions = [value for kind, value in ctx.events if kind == "router_decision"]
        assert len(decisions) == 1 and decisions[0].routed is False
        assert decisions[0].results_state == "missing"


# ============================================================ CLI verb
class TestCliVerb:
    def test_dry_run_routes(self, tmp_path, monkeypatch, capsys):
        env = routed_env(tmp_path)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        code = cli_main(["router", "fix", "my", "python", "bug"])
        out = capsys.readouterr().out
        assert code == 0
        assert "[router] would route to groq:fast" in out
        assert "no model call was made" in out and "classified as 'coding'" in out

    def test_dry_run_fallback_exits_one(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("ULTRON_ROUTER", "1")
        monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path / "empty"))
        code = cli_main(["router", NO_CATEGORY_TEXT])
        captured = capsys.readouterr()
        assert code == 1
        assert "would keep the default provider path" in captured.err
        assert "no task-category rule matched" in captured.out

    def test_status_flag(self, tmp_path, monkeypatch, capsys):
        env = routed_env(tmp_path)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        assert cli_main(["router", "--status"]) == 0
        out = capsys.readouterr().out
        assert "results state: valid" in out and "coding: would route to groq:fast" in out

    def test_no_text_shows_status(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path / "empty"))
        assert cli_main(["router"]) == 0
        assert "ULTRON_ROUTER=off" in capsys.readouterr().out

    def test_disabled_router_explains_itself(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path / "empty"))
        code = cli_main(["router", CODING_TEXT])
        captured = capsys.readouterr()
        assert code == 1 and "router disabled" in captured.err
        assert "smart router is OFF" in captured.out

    def test_parser_smoke(self):
        from ultron.cli import build_parser
        args = build_parser().parse_args(["router", "--status"])
        assert args.status is True and args.text == []


# ============================================================ regressions
class TestRegressions:
    def test_race_surface_untouched(self):
        for name in ("resolve_race", "run_race", "RacePlan", "Racer", "RaceReport"):
            assert hasattr(race_mod, name)

    def test_autotune_surface_untouched(self):
        for name in ("route_category", "load_results", "suite_fingerprint", "results_path",
                     "CATEGORIES", "AUTOTUNE_RESULTS_VERSION"):
            assert hasattr(autotune, name)

    def test_autotune_route_cli_still_works(self, tmp_path, monkeypatch, capsys):
        """The pre-existing advisory verb is unaffected by the router landing."""
        env = routed_env(tmp_path)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        assert cli_main(["autotune", "route", "coding", "--spec"]) == 0
        assert capsys.readouterr().out.strip() == "groq:fast"

    def test_agent_report_event_order_preserved(self, tmp_path, monkeypatch):
        import main as legacy

        class FakeRuntime:
            def run(self, objective, ctx):
                return SimpleNamespace(reply="ok")

        monkeypatch.setattr(legacy, "AGENT_CONFIG", SimpleNamespace(enabled=True))
        monkeypatch.setattr(legacy, "agent_provider", lambda: SimpleNamespace())
        monkeypatch.setattr(legacy, "build_agent", lambda provider=None: FakeRuntime())
        monkeypatch.delenv("ULTRON_ROUTER", raising=False)
        ctx = legacy.CommandContext.untrusted("regression")
        legacy.run_agent("hello there", ctx)
        assert [kind for kind, _ in ctx.events] == ["agent_report"]  # byte-identical to before
