# M2.5 — AutoTune: capability-aware model routing

## What this is

`ultron autotune run` benchmarks the model profiles you already configured (the same
`provider[:model]` roster grammar as `ultron race`, resolved through the same code) against
a **small, fixed, synthetic task suite** in three categories — coding, reasoning,
summarisation — and stores the objective results. `ultron autotune route <category>` then
recommends a profile for that category using a **transparent, configurable rule** and prints
every reason behind the decision.

It is opt-in and explicitly invoked: nothing tunes, benchmarks or routes in the background,
and the recommendation **changes no configuration** — applying it (e.g. `ULTRON_PROVIDER`,
`race --models`) is your action.

```
ultron autotune run                            # default roster: the usable configured providers
ultron autotune run --models groq:llama-3.3-70b,ollama:qwen3:8b --budget 120
ultron autotune route coding                   # recommendation + the full rule + every reason
ultron autotune route coding --spec            # just "groq:llama-3.3-70b" — composes with race:
ultron race --models "$(ultron autotune route coding --spec)" "review this design"
ultron autotune show                           # stored results summary
ultron autotune clear                          # delete the stored results
```

## What is measured — and what is not

Every task has a deterministic, published checker (the `rule` field printed in this doc's
suite table and stored in the module). **Objective only:**

| Category | Task | Objective check |
|---|---|---|
| reasoning | `reasoning-arith` | the digits in the answer are exactly `391` (17×23) |
| reasoning | `reasoning-logic` | normalised answer starts with `carol`, ≤20 characters |
| coding | `coding-json` | the whole reply parses as one **bare** JSON object with exactly `language=="python"` and `sum==5` (integer, not bool); fenced/prose-wrapped content **fails** with a distinguishing detail |
| coding | `coding-def-shape` | exactly two lines matching `def double(n):` (optional `-> int`) and `return 2*n / n*2 / n+n` — a **static shape check; the code is never executed** |
| summarisation | `summarisation-length` | one sentence (no inner `.!?`), ≤15 words, non-empty — of a fixed synthetic paragraph |
| summarisation | `summarisation-fact` | ≤10 words and contains `thursday` (the weekday stated in the synthetic text) |

Plus, for every task and profile: **latency**, completion status, provider-error kinds, and
token usage — the existing `ModelTurn.usage` accounting.

**Not measured, and not claimed:** prose quality, style, real-world code correctness, or
anything subjective. Model output is **never executed** — no eval, no process spawning, no
shell — because running model-generated code would hand a model indirect authority over the
machine, which nothing in this repository does. A "coding" pass means *produced the exact
requested structure*, not *wrote correct software*. There is **no composite score** anywhere
and **no majority vote**: results stay per task and per profile, and routing looks at one
category at a time.

## Bounds (all clamped, all in the report header)

| Knob | Default | Clamp | Meaning |
|---|---|---|---|
| profiles per run | race roster rules | **absolute max 4** (default cap 3, `ULTRON_RACE_MAX_MODELS`) | inherited from `ultron race` — over-cap entries are dropped with a named problem |
| `ULTRON_AUTOTUNE_TASK_TIMEOUT` | 20 s | 2–120 | per-model call timeout, per task |
| `ULTRON_AUTOTUNE_TASK_DEADLINE` | 30 s | 5–180 | overall deadline per task-race (also capped by the remaining budget) |
| `ULTRON_AUTOTUNE_BUDGET` | 240 s | 30–900 | total wall clock; tasks after exhaustion are marked `budget_skipped` (a zero/near-zero budget makes **zero** model calls) |
| output tokens | 512 | fixed | per advisory call; the tasks are small by design |
| input size | fixed | — | prompts are the suite's short synthetic texts; nothing of yours is sent |

Concurrency is the race machinery's: profiles race in parallel within a task (bounded
workers), tasks run sequentially. Worst case: 6 tasks × ≤4 profiles = ≤24 bounded calls.

## Failure behaviour

Per task, per profile, the outcome is preserved individually: `passed`, `failed` (with the
checker's objective reason), or the race abstention kind (`timeout`, `deadline`,
`unavailable`, `rate_limited`, `empty`, `bad_response` with the exception type only,
`budget_skipped`). One profile's failure never stops the others; one task's failure never
stops the suite. Reports quote answers only as redacted prefixes (≤200 chars).

## Routing rule (transparent and configurable)

1. **Candidate:** pass fraction ≥ `ULTRON_AUTOTUNE_MIN_PASS` / `--min-pass` (default 0.5) in
   *that category only* — no cross-category composites.
2. **Winner:** lowest median latency across the candidate's *passed* tasks in that category;
   ties break on the lexicographically smaller spec text (reproducible).
3. **Everything is printed:** the rule itself, each candidate's passes and median latency,
   every ineligible profile with its pass count and named failed tasks, and every failure
   detail. When nobody qualifies, the winner stays empty — it is never fabricated.

## Persistence and reproducibility

* Results go to **`<data dir>/autotune_results.json`** — the existing data directory
  (`ULTRON_DATA_DIR`), written with the same atomic, `0600` helper the API token and HUD
  session state use. `--no-save` skips the write; `ultron autotune clear` removes it. It is
  the **only** file this feature writes; `.env`, credentials, provider configuration,
  history and policy are never touched (regression-tested).
* Stored content: suite name + **suite fingerprint** (SHA-256 over the exact task
  definitions, so results identify their evaluation inputs), timestamp, bounds, local-only
  flag, and per-profile/per-task statuses, reasons, latencies, token counts and redacted
  answer prefixes. No secrets (everything is redacted before storage), no keys, no URLs, no
  conversation history, no project contents — the suite is fixed synthetic text, so results
  are comparable across runs of the same fingerprint.
* Loading validates version and structure; corrupt or foreign files are reported, never
  trusted, never raised through. A fingerprint mismatch warns that results may not be
  comparable.

## Privacy and security boundaries

* **Prompts are fixed synthetic strings** — your questions, notes, history (`ULTRON_HISTORY`
  is never read), tool results and project files are never sent to any profile.
* Requests are the race's: exactly two messages (advisory system rules + task prompt),
  **empty tool list**, no tool authority, no registry/runtime/policy access (governance-tested
  against the module source and signatures).
* All stored/rendered strings pass `redact()`.
* **`ULTRON_LOCAL_ONLY=1` is enforced through `race.resolve_race`:** an explicitly requested
  cloud profile **refuses the whole run** with zero provider clients constructed (spy-tested:
  no possible outbound request), the default roster becomes `ollama`-only, and a run under
  local-only proceeds fully locally. `ULTRON_AUTOTUNE=0` additionally disables the run verb
  (show/route/clear still work on stored data).
* No new dependencies (stdlib only), no telemetry, no external services, **no automatic
  model downloads** — profiles that don't exist locally simply abstain with the provider's
  error kind (for Ollama: a `rejected`/404 tells you to `ollama pull` it yourself).

## Tests

```
uv run pytest tests/test_autotune.py -q     # 43 deterministic tests
uv run pytest -q                            # full offline suite (race/history/agent regressions)
```

Covered: suite structure and fingerprint stability/sensitivity; every checker's pass and
fail paths (including fenced-JSON distinction and bool-vs-int); full-run report shape and
the absence of any composite score; per-task preservation of wrong answers, provider errors
and timeouts; zero-budget (zero calls) and mid-suite budget exhaustion (bounded call count);
refused/empty plans; request shape (two messages, zero tools, bounded tokens); redaction of
stored texts; routing per-category isolation, latency preference, deterministic tie-break,
configurable threshold, visible failures, no-fabricated-winner; atomic 0600 persistence,
configuration non-mutation, corrupt/version/fingerprint handling, clear; local-only refusal
with zero construction and the local default roster; the governance source checks; env
clamps; the kill switch; a full tuning run over **real HTTP against two loopback stub
servers** through the real SDK; every CLI path; and the race-module surface compatibility.

**Live tests:** no live autotune test exists or was run — the sandbox has no Ollama daemon
and no cloud credentials are used in CI. With a local server you can opt in yourself:
`ULTRON_RACE_MODELS=ollama ultron autotune run` (bounded, local, free). Nothing here claims
live results.

## Known limitations

* Six tasks per suite is a *screening* instrument, not a benchmark: it separates profiles
  that cannot follow basic formats from those that can, and it measures latency honestly —
  it cannot rank genuine capability, and the docs never claim it does.
* Checkers enforce the requested output format; a profile that knows the answer but ignores
  the format instruction fails. That is deliberate (instruction adherence is measurable,
  "quality" is not) and visible in each failure detail.
* Results are point-in-time: provider-side model updates silently change behaviour; the
  fingerprint identifies the *suite*, not the remote model revision. Re-run after changes.
* Median passed latency mixes tasks of different lengths; it is a tie-breaker, not a speed
  benchmark.
* Routing recommends; it never applies. Anything acting on it (setting `ULTRON_PROVIDER`,
  composing `race --models`) is an explicit owner action.

## Provenance and licensing

Capability/context-aware model *selection* is a publicly documented idea in multi-model
tooling — including the AGPL-3.0 project audited in
`/home/user/g0d-audit-report-2026-10-16.md`, whose "AutoTune" tunes **sampling parameters**
per query context, a different mechanism this repository deliberately does not adopt (it
would change the provider request contract; see `docs/M2_3_LOCAL_PROVIDER.md`). Everything
here is an original implementation on ULTRON's own race/provider layer: no external code,
prompts, scoring expressions or task tables were copied, and the reference's composite
scoring was explicitly rejected in favour of per-category objective results with preserved
uncertainty. The repository still has **no LICENSE file**; the AGPL-3.0 compatibility
question remains unresolved and is an owner decision. Nothing here claims legal clearance,
and **no claim is made that AutoTune improves answer quality** — it makes model selection
inspectable, which is a different thing.
