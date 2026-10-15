"""AutoTune: capability-aware model routing over a fixed synthetic suite (M2.5).

``ultron autotune run`` benchmarks the *configured* model profiles (the same
``provider[:model]`` roster grammar as ``ultron race``) against a small, fixed, versioned
suite of synthetic tasks in three categories - coding, reasoning, summarisation - and
``ultron autotune route <category>`` recommends a profile from the stored results using a
transparent, configurable rule.

What this module measures - and what it deliberately does not:

* **Objective checks only.** Every task has a deterministic checker: exact arithmetic, a
  name match, strict JSON schema parsing, a static two-line function-shape regex, and
  length/fact constraint checks on a summarisation of *synthetic* text. Model output is
  **never executed** - no eval, no process spawning, no shell - the coding checks are
  static shape/format checks, and the docs say so.
* **No quality claims.** Prose quality, style and real-world code correctness are *not*
  measured and nothing here claims otherwise. Latency, completion, token usage and the
  objective check results are the whole picture.
* **No composite scores, no majority.** Results stay per task and per profile; routing
  looks at one category at a time (pass fraction >= a configurable threshold, then the
  lowest median latency among passing tasks, deterministic spec tie-break). There is no
  overall "score" field anywhere, and nothing ranks a profile by another category's
  results.
* **Bounded** - reuses the race machinery: at most ``RACE_ABSOLUTE_MAX_MODELS`` profiles
  in parallel per task, per-task deadline and per-model timeout, a total wall-clock budget
  after which remaining tasks are marked ``budget_skipped``, 512 output tokens per call,
  and fixed short prompts. Tasks run sequentially; profiles race in parallel within a task.
* **Advisory and non-mutating** - routing *prints* a recommendation and a reason; it never
  writes ``.env``, never changes provider configuration, credentials, history or policy.
  The only file written is ``autotune_results.json`` under the existing data directory
  (atomic, 0600, redacted), and only on an explicit run.
* **Local-only aware** - profile resolution goes through ``race.resolve_race``, so under
  ``ULTRON_LOCAL_ONLY=1`` a requested cloud profile refuses the whole run with zero
  provider clients constructed, and the default roster is the local provider only.

Prompts are fixed synthetic strings (no user data, no history, no project contents); the
suite fingerprint (SHA-256 over the task definitions) is stored with the results so a
stored report identifies exactly which evaluation inputs produced it.

Provenance: the *idea* of context/capability-aware model selection is publicly documented
behaviour of other multi-model tools (including the AGPL-3.0 project audited in
/home/user/g0d-audit-report-2026-10-16.md, whose "AutoTune" tunes sampling parameters -
a different mechanism this repository deliberately does not adopt). Everything here is an
original implementation on ULTRON's own race/provider layer; no external code, prompts or
scoring logic was copied. The repository licensing question remains an open owner decision.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from ultron.agent.race import (  # reuse the bounded race machinery; no parallel abstraction
    RACE_ABSOLUTE_MAX_MODELS,
    RacePlan,
    RaceReport,
    _clamp,
    resolve_race,
    run_race,
)
from ultron.config import default_data_dir
from ultron.security.paths import atomic_write_text
from ultron.security.redact import redact

log = logging.getLogger("ultron.autotune")

AUTOTUNE_RESULTS_FILENAME = "autotune_results.json"
AUTOTUNE_RESULTS_VERSION = 1
SUITE_NAME = "autotune-suite-v1"
CATEGORIES = ("coding", "reasoning", "summarisation")
AUTOTUNE_MAX_OUTPUT_TOKENS = 512  # per advisory call; the tasks are small by design
ANSWER_PREFIX_CHARS = 200  # stored per task: enough to inspect, bounded, redacted

# Total wall-clock budget for a whole tuning run, and per-task bounds (seconds).
DEFAULT_BUDGET = 240.0
BUDGET_RANGE = (30.0, 900.0)
DEFAULT_TASK_DEADLINE = 30.0
TASK_DEADLINE_RANGE = (5.0, 180.0)
DEFAULT_TASK_TIMEOUT = 20.0
TASK_TIMEOUT_RANGE = (2.0, 120.0)
DEFAULT_MIN_PASS = 0.5  # routing rule: fraction of a category's tasks a profile must pass
MIN_PASS_RANGE = (0.0, 1.0)

_SUMMARY_SOURCE = (
    "The team meeting moved to Thursday at nine in the morning. The forecast promises "
    "rain, so the rooftop lunch was cancelled. A short progress report will be shared "
    "before the meeting starts."
)  # fixed synthetic text: no user data, no project contents, reproducible forever


# ============================================================ the suite and its checkers
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold()).strip(".!?'\"")


def _words(text: str) -> list[str]:
    return [word for word in re.split(r"\s+", text.strip()) if word]


def _check_arithmetic(answer: str) -> tuple[bool, str]:
    digits = re.sub(r"\D", "", answer)
    return digits == "391", f"digits in the answer were {digits[:20]!r}, expected exactly '391'"


def _check_logic(answer: str) -> tuple[bool, str]:
    normalized = _norm(answer)
    ok = normalized.startswith("carol") and len(normalized) <= 20
    return ok, f"normalised answer {normalized[:40]!r} must start with 'carol' and stay <= 20 chars"


def _check_json_object(answer: str) -> tuple[bool, str]:
    """Strict: the whole reply must be one bare JSON object with the exact schema. Fenced
    or prose-wrapped content is a distinguishable failure, not a pass - the task asked for
    bare JSON, and instruction adherence is part of what is measured."""
    text = answer.strip()
    schema_error = "expected exactly the keys 'language' (=='python') and 'sum' (==5, integer)"
    try:
        parsed = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                inner = json.loads(match.group(0))
            except ValueError:
                return False, "reply is not valid JSON"
            if _json_schema_ok(inner):
                return False, "content correct but not bare JSON (prose or code fences present)"
        return False, "reply is not valid JSON"
    if not _json_schema_ok(parsed):
        return False, schema_error
    return True, "bare JSON object with the exact schema"


def _json_schema_ok(parsed: Any) -> bool:
    return (isinstance(parsed, dict) and set(parsed) == {"language", "sum"}
            and parsed["language"] == "python"
            and isinstance(parsed["sum"], int) and not isinstance(parsed["sum"], bool)
            and parsed["sum"] == 5)


_DEF_LINE = re.compile(r"^def\s+double\s*\(\s*n\s*\)\s*(->\s*int\s*)?:$")
_RETURN_LINE = re.compile(r"^return\s+(2\s*\*\s*n|n\s*\*\s*2|n\s*\+\s*n)$")


def _check_def_shape(answer: str) -> tuple[bool, str]:
    """Static shape check on exactly two lines. The code is NEVER executed - running model
    output would hand a model indirect authority over this machine, which nothing in this
    repository does."""
    lines = [line.strip() for line in answer.strip().splitlines() if line.strip()]
    if len(lines) != 2:
        return False, f"expected exactly 2 lines, got {len(lines)}"
    if not _DEF_LINE.match(lines[0]):
        return False, f"line 1 is not 'def double(n):' (got {lines[0][:40]!r})"
    if not _RETURN_LINE.match(lines[1]):
        return False, f"line 2 is not a return of 2*n, n*2 or n+n (got {lines[1][:40]!r})"
    return True, "two lines with the exact def/return shape (static check; not executed)"


def _check_summary_length(answer: str) -> tuple[bool, str]:
    text = answer.strip()
    if not text:
        return False, "the summary is empty"
    words = _words(text)
    if len(words) > 15:
        return False, f"summary has {len(words)} words, the limit is 15"
    body = re.sub(r"[.!?]+$", "", text)
    if re.search(r"[.!?]", body):
        return False, "summary is more than one sentence"
    return True, f"one sentence, {len(words)} word(s) <= 15"


def _check_summary_fact(answer: str) -> tuple[bool, str]:
    text = answer.strip()
    words = _words(text)
    if len(words) > 10:
        return False, f"answer has {len(words)} words, the limit is 10"
    if "thursday" not in _norm(text):
        return False, "the weekday 'thursday' does not appear in the answer"
    return True, f"contains 'thursday' within {len(words)} word(s) <= 10"


@dataclass(frozen=True)
class TaskSpec:
    """One fixed synthetic task with its objective rule and checker."""

    id: str
    category: str
    prompt: str
    rule: str  # human-readable statement of exactly what the checker requires
    check: Callable[[str], tuple[bool, str]]


AUTOTUNE_SUITE: tuple[TaskSpec, ...] = (
    TaskSpec("reasoning-arith", "reasoning",
             "What is 17 multiplied by 23? Reply with only the numeric result.",
             "the digits in the answer must be exactly '391'", _check_arithmetic),
    TaskSpec("reasoning-logic", "reasoning",
             "Alice is shorter than Bob. Bob is shorter than Carol. Who is the tallest? "
             "Reply with only the name.",
             "the normalised answer must start with 'carol' and be at most 20 characters",
             _check_logic),
    TaskSpec("coding-json", "coding",
             'Return exactly one JSON object with two keys: "language" whose value is the '
             'string "python", and "sum" whose value is the integer sum of 2 and 3. Output '
             "only the JSON - no prose, no code fences.",
             "the whole reply must parse as one JSON object with exactly the keys "
             "'language' (=='python') and 'sum' (==5, integer); fenced or prose-wrapped "
             "content fails with a distinguishing detail", _check_json_object),
    TaskSpec("coding-def-shape", "coding",
             "Write a Python function named double that takes one integer parameter n and "
             "returns twice its value. Output exactly two lines: the def line and the "
             "return line. No examples, no explanations, no markdown.",
             "exactly two lines: 'def double(n):' (an '-> int' annotation is allowed) and a "
             "return line equal to 2*n, n*2 or n+n; the code is never executed",
             _check_def_shape),
    TaskSpec("summarisation-length", "summarisation",
             "Summarise the following text in exactly one sentence of at most 15 words. "
             f"Reply with only the summary.\n\nText:\n{_SUMMARY_SOURCE}",
             "one sentence (no inner .!?), at most 15 words, non-empty", _check_summary_length),
    TaskSpec("summarisation-fact", "summarisation",
             "Using at most 10 words, say on which weekday the meeting in the following "
             f"text happens. Reply with only that sentence.\n\nText:\n{_SUMMARY_SOURCE}",
             "at most 10 words and the normalised answer contains 'thursday'",
             _check_summary_fact),
)


def suite_fingerprint(suite: Sequence[TaskSpec] = AUTOTUNE_SUITE) -> str:
    """A short stable hash of the task definitions: stored with results so they identify
    exactly which evaluation inputs produced them (reproducibility without storing keys
    or private content - the suite is fixed synthetic text)."""
    payload = json.dumps([{"id": t.id, "category": t.category, "prompt": t.prompt, "rule": t.rule}
                          for t in suite], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ============================================================ bounds and results
@dataclass(frozen=True)
class TuningBounds:
    budget_s: float = DEFAULT_BUDGET
    task_deadline: float = DEFAULT_TASK_DEADLINE
    task_timeout: float = DEFAULT_TASK_TIMEOUT
    max_output_tokens: int = AUTOTUNE_MAX_OUTPUT_TOKENS
    min_pass: float = DEFAULT_MIN_PASS


def tuning_bounds(env: Mapping[str, str], problems: list[str]) -> TuningBounds:
    """Read and clamp the ``ULTRON_AUTOTUNE_*`` bounds (reusing the race clamp helper)."""
    return TuningBounds(
        budget_s=_clamp(env.get("ULTRON_AUTOTUNE_BUDGET"), DEFAULT_BUDGET, *BUDGET_RANGE,
                        problems, "ULTRON_AUTOTUNE_BUDGET"),
        task_deadline=_clamp(env.get("ULTRON_AUTOTUNE_TASK_DEADLINE"), DEFAULT_TASK_DEADLINE,
                             *TASK_DEADLINE_RANGE, problems, "ULTRON_AUTOTUNE_TASK_DEADLINE"),
        task_timeout=_clamp(env.get("ULTRON_AUTOTUNE_TASK_TIMEOUT"), DEFAULT_TASK_TIMEOUT,
                            *TASK_TIMEOUT_RANGE, problems, "ULTRON_AUTOTUNE_TASK_TIMEOUT"),
        min_pass=_clamp(env.get("ULTRON_AUTOTUNE_MIN_PASS"), DEFAULT_MIN_PASS, *MIN_PASS_RANGE,
                        problems, "ULTRON_AUTOTUNE_MIN_PASS"),
    )


@dataclass(frozen=True)
class TaskOutcome:
    task_id: str
    category: str
    # "passed" | "failed" | "budget_skipped" | any race abstention kind (timeout, deadline,
    # unavailable, empty, bad_response, rate_limited, not_configured, rejected, ...)
    status: str
    detail: str  # objective checker reason or the abstention detail; redacted, bounded
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    answer_prefix: str = ""  # redacted, <= ANSWER_PREFIX_CHARS; "" for abstentions


@dataclass(frozen=True)
class CategoryStat:
    tasks: int
    passed: int
    median_passed_latency_ms: int = -1  # -1 when nothing passed


@dataclass(frozen=True)
class ProfileResult:
    spec: str
    provider: str
    model: str
    tasks: tuple[TaskOutcome, ...] = ()
    categories: Mapping[str, CategoryStat] = field(default_factory=dict)


@dataclass(frozen=True)
class TuningReport:
    version: int = AUTOTUNE_RESULTS_VERSION
    suite: str = SUITE_NAME
    suite_fingerprint: str = ""
    created_utc: str = ""
    local_only: bool = False
    budget_exhausted: bool = False
    wall_ms: int = 0
    bounds: TuningBounds = TuningBounds()
    profiles: tuple[ProfileResult, ...] = ()
    problems: tuple[str, ...] = ()


def _outcome_for(task: TaskSpec, spec: str, race: RaceReport) -> TaskOutcome:
    """Map one profile's race result for one task onto an objective outcome."""
    for answer in race.answers:
        if answer.spec == spec:
            passed, detail = task.check(answer.text)
            return TaskOutcome(task.id, task.category, "passed" if passed else "failed",
                               redact(detail)[:200], answer.latency_ms, answer.input_tokens,
                               answer.output_tokens, redact(answer.text)[:ANSWER_PREFIX_CHARS])
    for abstention in race.abstentions:
        if abstention.spec == spec:
            return TaskOutcome(task.id, task.category, abstention.kind,
                               redact(abstention.detail)[:200], abstention.latency_ms,
                               abstention.input_tokens, abstention.output_tokens)
    return TaskOutcome(task.id, task.category, "missing", "the race report named neither an "
                       "answer nor an abstention for this profile - treated as no result")


def _category_stats(outcomes: Sequence[TaskOutcome]) -> dict[str, CategoryStat]:
    stats: dict[str, CategoryStat] = {}
    for category in CATEGORIES:
        rows = [o for o in outcomes if o.category == category]
        if not rows:
            continue
        passed_latencies = [o.latency_ms for o in rows if o.status == "passed"]
        stats[category] = CategoryStat(
            tasks=len(rows), passed=sum(1 for o in rows if o.status == "passed"),
            median_passed_latency_ms=int(statistics.median(passed_latencies))
            if passed_latencies else -1)
    return stats


def run_tuning(plan: RacePlan, bounds: TuningBounds, *,
               suite: Sequence[TaskSpec] = AUTOTUNE_SUITE) -> TuningReport:
    """Race every profile on every suite task, sequentially per task, in parallel per
    profile - all bounds enforced through the existing race machinery. Never raises for
    provider faults; the report preserves every individual outcome."""
    if plan.refused:
        raise ValueError(f"autotune refused: {plan.refused}")
    if not plan.racers:
        raise ValueError("no profiles to tune")
    started = time.monotonic()
    specs = [racer.spec for racer in plan.racers]
    outcomes: dict[str, list[TaskOutcome]] = {spec: [] for spec in specs}
    budget_exhausted = False
    for task in suite:
        elapsed = time.monotonic() - started
        if elapsed >= bounds.budget_s:
            budget_exhausted = True
            for spec in specs:
                outcomes[spec].append(TaskOutcome(
                    task.id, task.category, "budget_skipped",
                    f"the total tuning budget ({bounds.budget_s:g}s) was exhausted before this task"))
            continue
        task_plan = replace(plan,
                            deadline=min(bounds.task_deadline, bounds.budget_s - elapsed),
                            per_model_timeout=bounds.task_timeout,
                            max_output_tokens=bounds.max_output_tokens)
        race_report = run_race(task.prompt, task_plan)
        for spec in specs:
            outcomes[spec].append(_outcome_for(task, spec, race_report))
    wall_ms = max(0, int((time.monotonic() - started) * 1000))
    profiles = tuple(
        ProfileResult(spec=spec, provider=spec.split(":", 1)[0], model=spec.split(":", 1)[1]
                      if ":" in spec else "", tasks=tuple(outcomes[spec]),
                      categories=_category_stats(outcomes[spec]))
        for spec in specs)
    return TuningReport(suite_fingerprint=suite_fingerprint(suite),
                        created_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        local_only=plan.local_only, budget_exhausted=budget_exhausted,
                        wall_ms=wall_ms, bounds=bounds, profiles=profiles)


# ============================================================ transparent routing
@dataclass(frozen=True)
class CandidateInfo:
    spec: str
    passed: int
    tasks: int
    fraction: float
    median_passed_latency_ms: int
    failures: tuple[tuple[str, str, str], ...]  # (task_id, status, detail)


@dataclass(frozen=True)
class RouteDecision:
    category: str
    min_pass: float
    winner: str  # "" when no profile qualifies - never fabricated
    candidates: tuple[CandidateInfo, ...] = ()
    rationale: tuple[str, ...] = ()


def _profiles_from(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [p for p in payload.get("profiles", []) if isinstance(p, Mapping)]


def route_category(payload: Mapping[str, Any], category: str, min_pass: float) -> RouteDecision:
    """The routing rule, in full and in public:

    1. a profile is a **candidate** for ``category`` when its fraction of passed tasks in
       that category is >= ``min_pass`` (``ULTRON_AUTOTUNE_MIN_PASS`` / ``--min-pass``);
    2. the **winner** is the candidate with the lowest median latency across its *passed*
       tasks in that category; ties break on the lexicographically smaller spec so the
       decision is reproducible;
    3. everything else - failures, abstentions, near-misses - is reported alongside, and
       when nobody qualifies the winner stays empty instead of being fabricated.

    Only this category's objective results are consulted: no cross-category composites, no
    majority vote, no quality score. The decision changes no configuration anywhere.
    """
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r} (use one of: {', '.join(CATEGORIES)})")
    candidates: list[CandidateInfo] = []
    rejected: list[str] = []
    for profile in _profiles_from(payload):
        spec = str(profile.get("spec", "?"))
        stats = profile.get("categories", {}).get(category)
        if not isinstance(stats, Mapping) or not stats.get("tasks"):
            rejected.append(f"{spec}: no stored {category} results")
            continue
        tasks = int(stats["tasks"])
        passed = int(stats["passed"])
        fraction = passed / tasks
        failures = tuple((str(t.get("task_id", "?")), str(t.get("status", "?")),
                          str(t.get("detail", ""))[:120])
                         for t in profile.get("tasks", [])
                         if isinstance(t, Mapping) and t.get("category") == category
                         and t.get("status") != "passed")
        info = CandidateInfo(spec, passed, tasks, round(fraction, 3),
                             int(stats.get("median_passed_latency_ms", -1)), failures)
        if fraction >= min_pass and passed > 0:
            candidates.append(info)
        else:
            rejected.append(f"{spec}: passed {passed}/{tasks} in {category} "
                            f"(< required {min_pass:g})" + ("; failures: " + "; ".join(
                                f"{task_id} {status}" for task_id, status, _ in failures)
                                                           if failures else ""))
    rationale = [
        f"routing rule for '{category}': pass fraction >= {min_pass:g} of the category's tasks, "
        "then the lowest median latency among passed tasks; ties break on the smaller spec text.",
        "inputs: only this category's stored objective results - no cross-category composites, "
        "no majority vote, no quality score; the winner is a recommendation and changes no "
        "configuration.",
    ]
    if not candidates:
        rationale.append(f"no profile qualifies for '{category}'.")
        rationale.extend(f"  not eligible: {line}" for line in rejected)
        return RouteDecision(category, min_pass, "", (), tuple(rationale))
    ordered = sorted(candidates, key=lambda c: (c.median_passed_latency_ms, c.spec))
    winner = ordered[0]
    rationale.append(f"winner: {winner.spec} - passed {winner.passed}/{winner.tasks}, median passed "
                     f"latency {winner.median_passed_latency_ms}ms.")
    for candidate in ordered[1:]:
        rationale.append(f"  also eligible: {candidate.spec} - passed {candidate.passed}/"
                         f"{candidate.tasks}, median passed latency "
                         f"{candidate.median_passed_latency_ms}ms.")
    rationale.extend(f"  not eligible: {line}" for line in rejected)
    for candidate in ordered:
        for task_id, status, detail in candidate.failures:
            rationale.append(f"  {candidate.spec} did not pass {task_id} ({status}): {detail}")
    return RouteDecision(category, min_pass, winner.spec, tuple(ordered), tuple(rationale))


# ============================================================ persistence (data dir, atomic, redacted)
def results_path(env: Mapping[str, str] | None = None) -> Path:
    return default_data_dir(env) / AUTOTUNE_RESULTS_FILENAME


def save_results(report: TuningReport, env: Mapping[str, str] | None = None) -> Path:
    """Persist one run atomically (0600) under the existing data directory - the same
    storage mechanism the API token, HUD session state and chat transcript use. Nothing
    else is written; user configuration is never touched."""
    path = results_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return path


def load_results(env: Mapping[str, str] | None = None) -> tuple[dict | None, list[str]]:
    """Stored results plus problem lines. Corrupt or wrong-version files are reported,
    never trusted and never raised through to the caller."""
    problems: list[str] = []
    path = results_path(env)
    if not path.is_file():
        return None, [f"no stored tuning results at {path} (run `ultron autotune run` first)"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"stored tuning results are unreadable ({type(exc).__name__}); "
                      "re-run `ultron autotune run`"]
    if not isinstance(payload, dict) or payload.get("version") != AUTOTUNE_RESULTS_VERSION:
        return None, ["stored tuning results have an unknown version; re-run `ultron autotune run`"]
    if payload.get("suite_fingerprint") and payload["suite_fingerprint"] != suite_fingerprint():
        problems.append(f"stored results used suite fingerprint {payload['suite_fingerprint']}; "
                        f"the current suite is {suite_fingerprint()} - results may not be comparable")
    return payload, problems


def clear_results(env: Mapping[str, str] | None = None) -> bool:
    try:
        results_path(env).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("autotune results could not be cleared (%s)", type(exc).__name__)
        return False


# ============================================================ rendering
def render_tuning(report: TuningReport) -> str:
    lines = [
        f"AutoTune report - suite {report.suite} (fingerprint {report.suite_fingerprint}), "
        f"{report.created_utc}, local-only: {'on' if report.local_only else 'off'}",
        f"Bounds: {len(report.profiles)} profile(s), task deadline {report.bounds.task_deadline:g}s, "
        f"per-model timeout {report.bounds.task_timeout:g}s, total budget {report.bounds.budget_s:g}s "
        f"({'EXHAUSTED' if report.budget_exhausted else 'kept'}, wall {report.wall_ms}ms), "
        f"{report.bounds.max_output_tokens} output tokens per call",
    ]
    for problem in report.problems:
        lines.append(f"[autotune] {problem}")
    for profile in report.profiles:
        model = f" model={profile.model}" if profile.model else ""
        lines.append(f"profile {profile.spec}{model}:")
        for outcome in profile.tasks:
            extra = f" in {outcome.latency_ms}ms" if outcome.latency_ms else ""
            tokens = (f", tokens {outcome.input_tokens}/{outcome.output_tokens}"
                      if outcome.input_tokens or outcome.output_tokens else "")
            lines.append(f"  [{outcome.status:>14}] {outcome.task_id}{extra}{tokens}"
                         + (f" - {outcome.detail}" if outcome.detail else ""))
        for category in CATEGORIES:
            stat = profile.categories.get(category)
            if stat is None:
                continue
            latency = (f", median passed latency {stat.median_passed_latency_ms}ms"
                       if stat.median_passed_latency_ms >= 0 else "")
            lines.append(f"  {category}: passed {stat.passed}/{stat.tasks}{latency}")
    lines.append("Objective checks only: code was NEVER executed, prose quality was NOT measured, "
                 "and no composite score exists. Results are advisory; nothing was reconfigured. "
                 "Use `ultron autotune route <category>` for a transparent recommendation.")
    return "\n".join(lines)


def render_stored(payload: Mapping[str, Any]) -> str:
    """A compact view of *loaded* results (plain JSON data, not dataclasses)."""
    bounds = payload.get("bounds", {})
    lines = [
        f"AutoTune stored results - suite {payload.get('suite')} "
        f"(fingerprint {payload.get('suite_fingerprint')}), {payload.get('created_utc')}, "
        f"local-only: {'on' if payload.get('local_only') else 'off'}",
        f"Bounds used: task deadline {bounds.get('task_deadline')}s, per-model timeout "
        f"{bounds.get('task_timeout')}s, total budget {bounds.get('budget_s')}s "
        f"({'EXHAUSTED' if payload.get('budget_exhausted') else 'kept'}), wall {payload.get('wall_ms')}ms",
    ]
    for profile in _profiles_from(payload):
        lines.append(f"profile {profile.get('spec')}:")
        for category in CATEGORIES:
            stat = profile.get("categories", {}).get(category)
            if isinstance(stat, Mapping) and stat.get("tasks"):
                lines.append(f"  {category}: passed {stat.get('passed')}/{stat.get('tasks')}, "
                             f"median passed latency {stat.get('median_passed_latency_ms')}ms")
        for task in profile.get("tasks", []):
            if isinstance(task, Mapping):
                lines.append(f"  [{str(task.get('status')):>14}] {task.get('task_id')} - "
                             f"{str(task.get('detail'))[:100]}")
    lines.append("Objective checks only; advisory; nothing was reconfigured. "
                 "Use `ultron autotune route <category>` for a recommendation.")
    return "\n".join(lines)


def render_route(decision: RouteDecision) -> str:
    lines = [f"AutoTune routing - category '{decision.category}'"]
    lines.extend(decision.rationale)
    if decision.winner:
        lines.append(f"recommended profile: {decision.winner} "
                     "(advisory only - apply it yourself, e.g. by passing it to `ultron race --models` "
                     "or setting ULTRON_PROVIDER; autotune never changes configuration)")
    else:
        lines.append("no profile is recommended; consider re-running the tuning or relaxing "
                     "--min-pass (and read the failure details above first)")
    return "\n".join(lines)


def enabled(env: Mapping[str, str]) -> bool:
    """``ULTRON_AUTOTUNE=0`` disables the (already explicitly invoked) run verb."""
    return (env.get("ULTRON_AUTOTUNE") or "").strip().lower() not in ("0", "false", "no", "off")


__all__ = ["AUTOTUNE_SUITE", "CATEGORIES", "SUITE_NAME", "TuningBounds", "TuningReport",
           "ProfileResult", "TaskOutcome", "CategoryStat", "CandidateInfo", "RouteDecision",
           "clear_results", "enabled", "load_results", "render_route", "render_stored",
           "render_tuning",
           "resolve_race", "results_path", "route_category", "run_tuning", "save_results",
           "suite_fingerprint", "tuning_bounds", "RACE_ABSOLUTE_MAX_MODELS"]
