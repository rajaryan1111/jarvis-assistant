"""ULTRON Smart Router (M2.6): opt-in, transparent, task-aware model routing.

Chooses, per agent-loop request, which *already-configured* provider profile handles it -
using **validated AutoTune results** (docs/M2_5_AUTOTUNE.md) - and falls back to the
existing default ``ULTRON_PROVIDER`` path whenever anything is off, missing, stale,
ineligible or refused:

* **opt-in** - ``ULTRON_ROUTER=1`` (default off). When off, this module reads nothing,
  builds nothing and the default behaviour is byte-for-byte the old behaviour.
* **transparent** - every decision carries a full explanation: the matched classification
  rule, the results-validation state (suite fingerprint, age), the routing rule's own
  rationale (reused verbatim from ``autotune.route_category``) and the fallback reason.
  Explanations are logged, printed by ``ultron router "<text>"`` / ``--status`` and shown
  by ``ultron agent --steps``.
* **deterministic classification** - a fixed, published rule table (below) maps the request
  text to one AutoTune category (summarisation beats coding beats reasoning: "summarise
  this code" is a summarisation task) or to no category. No model call classifies anything.
* **validated results only** - stored results must load, match the *current* suite
  fingerprint and be newer than ``ULTRON_ROUTER_MAX_AGE_DAYS``; the eligibility rule itself
  (pass fraction >= ``ULTRON_ROUTER_MIN_PASS`` in that category, lowest median passed-task
  latency, deterministic tie-break) is **autotune's**, called, never reimplemented.
* **present-tense eligibility** - the winner must also build right now through
  ``race.resolve_race`` (credentials, configuration and Ollama loopback validation
  included); a winner that cannot build falls back with the reason printed.
* **``ULTRON_LOCAL_ONLY=1`` across routing AND every fallback path** - under local-only the
  stored profiles are filtered to ``ollama`` *before* the rule runs, so a cloud winner can
  never be selected or even constructed; ``resolve_race`` re-refuses cloud specs as a second
  layer; and the fallback is the default provider path, which is itself local-only-gated by
  ``load_agent_config``. No path can produce an outbound cloud request because of routing.
* **authority-neutral** - routing only chooses among providers the owner already
  configured. The chosen model runs the *same* runtime, tool registry, policy engine,
  confirmation flow and audit ledger, under the *same* bounds. No new tool authority, no
  adviser is promoted, and nothing here can reach the registry itself.
* **non-mutating** - reads the results file and the environment; writes nothing, touches no
  configuration, no credentials, no history. Applying a recommendation (``ULTRON_PROVIDER``,
  ``race --models``) remains an explicit owner action.

Original implementation; no external project's routing logic, tables or prompts copied.
"""

from __future__ import annotations

import calendar
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ultron.agent import autotune
from ultron.agent.race import _clamp, resolve_race
from ultron.config import TRUE_VALUES, load_agent_config

log = logging.getLogger("ultron.router")

ROUTER_DEFAULT_MIN_PASS = 0.5  # same default as autotune's own routing rule
ROUTER_DEFAULT_MAX_AGE_DAYS = 14.0
MAX_AGE_RANGE = (1.0, 365.0)
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # the timestamp format the stored results carry

# The classification rule table: fixed, published, first match wins in this order.
# Priority rationale: an explicit summarisation request is a summarisation task even when
# its subject is code; coding words beat generic reasoning words ("why does my script...").
CATEGORY_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("summarisation",
     re.compile(r"\b(summari[sz]e|summari[sz]es|summari[sz]ed|summari[sz]ing|summari[sz]ation|"
                r"summary|tl;dr|recap|condense|shorten|key points|key takeaways|brief overview|"
                r"in one sentence|in a paragraph)\b", re.IGNORECASE),
     "an explicit summarisation request"),
    ("coding",
     re.compile(r"\b(code|coding|program|programs|programming|script|function|method|class|bug|"
                r"bugs|debug|refactor|compile|compiler|runtime error|stack ?trace|exception|"
                r"regex|sql|python|javascript|typescript|java|html|css|api|unit tests?|"
                r"pull request|merge conflict|pip|npm|json|yaml)\b", re.IGNORECASE),
     "a programming/code-related request"),
    ("reasoning",
     re.compile(r"\b(why|prove|proof|logic|logical|puzzle|riddle|math|maths|calculate|compute|"
                r"estimate|reason|reasoning|trade-?offs?|compare|versus|vs|should (i|we|you)|"
                r"decide|decision|analy[sz]e|strategy|probability)\b", re.IGNORECASE),
     "a reasoning/analysis request"),
)


def classify(text: str) -> tuple[str, str] | None:
    """Deterministic task-category classification; ``None`` when no rule matches.

    Returns ``(category, explanation)`` - the explanation names the rule and the matched
    word so every routing decision can show *why* it saw what it saw.
    """
    for category, pattern, description in CATEGORY_RULES:
        match = pattern.search(str(text))
        if match:
            return category, (f"classified as '{category}' ({description}); "
                              f"matched {match.group(0)!r}")
    return None


# ============================================================ configuration
@dataclass(frozen=True)
class RouterConfig:
    enabled: bool = False
    min_pass: float = ROUTER_DEFAULT_MIN_PASS
    max_age_days: float = ROUTER_DEFAULT_MAX_AGE_DAYS
    local_only: bool = False
    problems: tuple[str, ...] = ()


def router_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get("ULTRON_ROUTER") or "").strip().lower() in TRUE_VALUES


def router_config(env: Mapping[str, str] | None = None) -> RouterConfig:
    env = os.environ if env is None else env
    problems: list[str] = []
    min_pass = _clamp(env.get("ULTRON_ROUTER_MIN_PASS"), ROUTER_DEFAULT_MIN_PASS,
                      *autotune.MIN_PASS_RANGE, problems, "ULTRON_ROUTER_MIN_PASS")
    max_age = _clamp(env.get("ULTRON_ROUTER_MAX_AGE_DAYS"), ROUTER_DEFAULT_MAX_AGE_DAYS,
                     *MAX_AGE_RANGE, problems, "ULTRON_ROUTER_MAX_AGE_DAYS")
    return RouterConfig(router_enabled(env), min_pass, max_age,
                        load_agent_config(env).local_only, tuple(problems))


# ============================================================ results validation
def _age_days(created_utc: Any, max_age_days: float) -> tuple[float, str]:
    """Age of the stored results in days, plus a staleness problem ('' when fresh)."""
    if not isinstance(created_utc, str) or not created_utc.strip():
        return -1.0, "results carry no usable timestamp: treated as stale"
    try:
        stamp = time.strptime(created_utc.strip(), _UTC_FORMAT)
    except ValueError:
        return -1.0, f"results timestamp {created_utc.strip()[:40]!r} is not parseable: treated as stale"
    age = (time.time() - calendar.timegm(stamp)) / 86400.0
    age = max(0.0, age)  # a future timestamp (clock skew) counts as fresh, not negative
    if age > max_age_days:
        return age, (f"results are {age:.1f} day(s) old, older than ULTRON_ROUTER_MAX_AGE_DAYS="
                     f"{max_age_days:g}: treated as stale, re-run `ultron autotune run`")
    return age, ""


def validate_results(env: Mapping[str, str], cfg: RouterConfig
                     ) -> tuple[dict | None, str, list[str]]:
    """``(payload|None, state, explanation lines)`` - the results are used ONLY when valid.

    States: ``missing`` / ``unreadable`` (load or version problems), ``stale-suite`` (the
    evaluation inputs changed since the run: fingerprint mismatch), ``stale-age``, or
    ``valid``. Validation reuses ``autotune.load_results`` (structure, version, corruption)
    and ``autotune.suite_fingerprint`` (input identity); nothing is re-implemented here.
    """
    payload, problems = autotune.load_results(env)
    if payload is None:
        state = "unreadable" if any("unreadable" in p or "version" in p for p in problems) else "missing"
        return None, state, list(problems)
    current = autotune.suite_fingerprint()
    stored = payload.get("suite_fingerprint")
    if stored != current:
        return None, "stale-suite", [
            f"results were produced by suite fingerprint {str(stored)[:16]!r} but the current suite "
            f"is {current!r}: the evaluation inputs changed, so the results no longer describe "
            "this suite - re-run `ultron autotune run`"]
    age, problem = _age_days(payload.get("created_utc"), cfg.max_age_days)
    if problem:
        return None, "stale-age", [problem]
    return payload, "valid", [f"results are valid (suite fingerprint {current}, {age:.1f} day(s) old, "
                              f"limit {cfg.max_age_days:g})"]


def _local_profiles(payload: Mapping[str, Any]) -> dict:
    """Under ULTRON_LOCAL_ONLY: only local profiles are eligible - filtered BEFORE the rule
    runs, so a cloud profile can never be selected or constructed through routing."""
    return {**payload, "profiles": [p for p in payload.get("profiles", [])
                                    if str(p.get("spec", "")).split(":", 1)[0] == "ollama"]}


# ============================================================ the decision
@dataclass(frozen=True)
class RoutingDecision:
    routed: bool = False
    category: str = ""  # "" when no rule matched
    winner: str = ""  # the winning profile spec (also set when it turned out unusable)
    provider: Any = None  # the routed provider; None => use the default path
    results_state: str = "disabled"  # disabled|missing|unreadable|stale-suite|stale-age|valid|""
    explanation: tuple[str, ...] = ()
    fallback_reason: str = ""  # "" when routed


def _fallback(lines: list[str], reason: str, state: str, category: str = "",
              winner: str = "") -> RoutingDecision:
    lines.append(f"fallback: {reason} - the existing default provider path is used unchanged")
    log.info("smart router: fallback (%s)", reason)
    return RoutingDecision(routed=False, category=category, winner=winner, results_state=state,
                           explanation=tuple(lines), fallback_reason=reason)


def route_request(text: str, env: Mapping[str, str] | None = None, *,
                  provider_factory: Callable[..., Any] | None = None) -> RoutingDecision:
    """The whole routing decision for one request text, with its full explanation.

    Never raises for missing/stale/ineligible results - every such case is a documented
    fallback to the default provider path. Performs no model call; a winning profile's
    client is constructed (lazily - construction sends nothing) to prove present-tense
    usability.
    """
    env = os.environ if env is None else dict(env)
    cfg = router_config(env)
    lines: list[str] = [f"config: {problem}" for problem in cfg.problems]
    if not cfg.enabled:
        lines.append("smart router is OFF (ULTRON_ROUTER is not 1/true/yes/on): nothing was read "
                     "and the default ULTRON_PROVIDER path handles this request")
        return _fallback(lines, "router disabled", "disabled")
    lines.append(f"smart router is ON (min-pass {cfg.min_pass:g}, max-age {cfg.max_age_days:g} days, "
                 f"local-only {'on' if cfg.local_only else 'off'})")

    classified = classify(text)
    if classified is None:
        lines.append("no task-category rule matched the request text (the published rule table "
                     "covers summarisation, coding and reasoning)")
        return _fallback(lines, "no category matched", "")
    category, reason = classified
    lines.append(reason)

    payload, state, validation = validate_results(env, cfg)
    lines.extend(validation)
    if payload is None:
        return _fallback(lines, f"autotune results are {state}", state, category=category)

    if cfg.local_only:
        before = len(payload.get("profiles", []))
        payload = _local_profiles(payload)
        excluded = before - len(payload.get("profiles", []))
        lines.append(f"ULTRON_LOCAL_ONLY=1: only local (ollama) profiles are eligible; {excluded} "
                     "stored cloud profile(s) excluded BEFORE any eligibility rule or client "
                     "construction")
    decision = autotune.route_category(payload, category, cfg.min_pass)  # autotune's rule, reused
    lines.extend(decision.rationale)
    if not decision.winner:
        return _fallback(lines, f"no profile qualifies for '{category}' under the routing rule",
                         state, category=category)

    kwargs = {} if provider_factory is None else {"provider_factory": provider_factory}
    plan = resolve_race(env, [decision.winner], **kwargs)
    if plan.refused or not plan.racers:
        detail = plan.refused or "; ".join(plan.problems) or "the winning profile could not be built"
        lines.append(f"winner {decision.winner} is not usable right now: {detail}")
        return _fallback(lines, "winner not usable", state, category=category, winner=decision.winner)
    lines.append(f"routing this request to {decision.winner}: same runtime, registry, policy, "
                 "confirmation and audit path, same bounds - routing grants no new authority")
    log.info("smart router: %s -> %s", category, decision.winner)
    return RoutingDecision(routed=True, category=category, winner=decision.winner,
                           provider=plan.racers[0].provider, results_state=state,
                           explanation=tuple(lines))


# ============================================================ status surfaces (read-only)
def status_lines(env: Mapping[str, str] | None = None) -> list[str]:
    """The router's configuration and results validation, for ``ultron router --status``."""
    env = os.environ if env is None else env
    cfg = router_config(env)
    lines = [f"ULTRON_ROUTER={'on' if cfg.enabled else 'off'} (min-pass {cfg.min_pass:g}, "
             f"max-age {cfg.max_age_days:g} days, local-only {'on' if cfg.local_only else 'off'})"]
    lines.extend(f"config: {problem}" for problem in cfg.problems)
    lines.append(f"results file: {autotune.results_path(env)}")
    lines.append("classification rules (first match wins): " + "; ".join(
        f"{category} = {description}" for category, _, description in CATEGORY_RULES))
    if not cfg.enabled:
        lines.append("routing is disabled: the default ULTRON_PROVIDER path handles everything")
        return lines
    payload, state, validation = validate_results(env, cfg)
    lines.append(f"results state: {state}")
    lines.extend(validation)
    if payload is not None:
        if cfg.local_only:
            payload = _local_profiles(payload)
            lines.append("ULTRON_LOCAL_ONLY=1: only local profiles are eligible")
        for category in autotune.CATEGORIES:
            decision = autotune.route_category(payload, category, cfg.min_pass)
            lines.append(f"  {category}: would route to {decision.winner or 'no eligible profile - default path'}")
    return lines


def config_summary(env: Mapping[str, str] | None = None) -> str:
    """One line for ``ultron doctor``."""
    env = os.environ if env is None else env
    cfg = router_config(env)
    if not cfg.enabled:
        return "off (ULTRON_ROUTER=1 enables; docs/M2_6_SMART_ROUTER.md)"
    _payload, state, _lines = validate_results(env, cfg)
    return f"on (min-pass {cfg.min_pass:g}, max-age {cfg.max_age_days:g}d, results {state})"
