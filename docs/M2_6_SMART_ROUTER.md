# M2.6 — Smart Router (opt-in, task-aware provider routing)

The Smart Router sends each **agent-loop request** to the configured model profile that
AutoTune (M2.5) measured as the fastest eligible one *for that task category* — and falls
back to the existing default `ULTRON_PROVIDER` path, with the reason printed, whenever
anything is off, unmatched, missing, stale, ineligible, refused or unbuildable.

It is **off by default**. Nothing changes for any existing installation until the owner
sets `ULTRON_ROUTER=1`.

```
ULTRON_ROUTER=1 uv run ultron agent "fix the bug in my python function"
uv run ultron router "fix the bug in my python function"   # dry-run explanation, exit 0/1
uv run ultron router --status                              # config + results validation
uv run ultron agent --steps "..."                          # decision printed with the steps
```

Implementation: `ultron/agent/router.py` (no new dependencies, no process spawning, no
telemetry). Wiring: `main.run_agent` (one guarded branch + one event), `ultron/cli.py`
(`router` verb, `--steps` lines, `doctor` line). Tests: `tests/test_router.py` (77,
deterministic — no model call, no network, no credential, no daemon).

## What the router is — and is not

- It **chooses among providers the owner already configured**. It never adds a provider,
  edits `.env`, rewrites configuration, or promotes an advisory model to tool authority.
- The chosen model runs the **same** `AgentRuntime`, tool registry, policy engine,
  confirmation flow and audit ledger, under the **same** bounds (`AGENT_CONFIG` limits are
  untouched). Routing grants no new authority; advisers remain advisers.
- It makes **no quality claim**. "Eligible" means exactly: AutoTune's objective checks
  passed at the required fraction in that category, and the median passed-task latency was
  the lowest among candidates. Nothing else is inferred.
- Classification never calls a model. It is a fixed, published regex rule table evaluated
  on the request text in-process.

## Decision pipeline (in order; any stop = documented fallback)

1. **Switch** — `ULTRON_ROUTER` parses via the standard `TRUE_VALUES` (`1/true/yes/on`).
   Off ⇒ nothing is read, nothing is logged, `run_agent` behaves byte-for-byte as before.
2. **Classification** — first matching rule wins, in this fixed priority:

   | priority | category | rule (word list, case-insensitive) |
   |---|---|---|
   | 1 | summarisation | summarise/summarize/summary/tl;dr/recap/condense/shorten/key points/key takeaways/brief overview/in one sentence/in a paragraph |
   | 2 | coding | code/program*/script/function/method/class/bug*/debug/refactor/compile*/runtime error/stack trace/exception/regex/sql/python/javascript/typescript/java/html/css/api/unit test*/pull request/merge conflict/pip/npm/json/yaml |
   | 3 | reasoning | why/prove/proof/logic*/puzzle/riddle/math*/calculate/compute/estimate/reason*/trade-off*/compare/versus/vs/should i|we|you/decide/decision/analyse/analyze/strategy/probability |

   Priority rationale: "summarise this code" is a summarisation task; "why does my script
   print None" is a coding task. No match ⇒ fallback (`no category matched`) — the router
   never guesses. The table lives in `CATEGORY_RULES` and is printed by `--status`.
3. **Results validation** — the stored AutoTune file must: load and carry the current
   results version (`autotune.load_results`, reused); match the **current suite
   fingerprint** (`autotune.suite_fingerprint`, reused) so results describing different
   evaluation inputs are never used; and be **no older than
   `ULTRON_ROUTER_MAX_AGE_DAYS`** (default 14, clamped 1–365; unparseable timestamps count
   as stale; a future timestamp from clock skew counts as fresh). States: `missing`,
   `unreadable`, `stale-suite`, `stale-age`, `valid`.
4. **Eligibility** — `autotune.route_category(payload, category, min_pass)` is called
   **as-is**: pass fraction ≥ `ULTRON_ROUTER_MIN_PASS` (default 0.5, clamped 0–1) in *that
   category only*, then lowest median passed-task latency, lexicographic spec tie-break.
   No winner ⇒ fallback. The router contains no second routing rule.
5. **Present-tense usability** — the winner spec must build right now through
   `race.resolve_race(env, [winner])` (credentials, provider configuration and the Ollama
   loopback check included). Unbuildable ⇒ fallback (`winner not usable`), reason printed.
6. **Route** — the built provider is passed to the existing `build_agent(provider)`
   injection point; the run proceeds exactly as a default-provider run.

## ULTRON_LOCAL_ONLY=1: enforced across routing *and* every fallback path

- **Before the rule runs**: under local-only the stored profiles are filtered to `ollama`
  specs, so a cloud profile can never be selected — and never constructed (test-verified:
  zero factory calls).
- **Second gate**: `race.resolve_race` independently refuses cloud specs under local-only
  (`plan.refused`), before building any client — the guarantee M2.4 established, unmodified.
- **Fallback path**: the default provider path is itself gated by `load_agent_config`,
  which fails closed — `ULTRON_LOCAL_ONLY=1` with a cloud `ULTRON_PROVIDER` turns the agent
  loop off with an explicit error rather than silently going remote.

No path through the router can produce an outbound cloud request when local-only is set.

## Fallback matrix (every case returns to the default path, with the reason printed)

| case | results_state | fallback_reason |
|---|---|---|
| `ULTRON_ROUTER` off/unset | disabled | router disabled |
| no classification rule matched | (not evaluated) | no category matched |
| results file absent | missing | autotune results are missing |
| corrupt JSON / wrong version | unreadable | autotune results are unreadable |
| suite fingerprint changed | stale-suite | autotune results are stale-suite |
| older than max age / bad timestamp | stale-age | autotune results are stale-age |
| rule produced no winner | valid | no profile qualifies for '<category>' under the routing rule |
| winner cannot build now | valid | winner not usable |

Fallback means `agent_provider()` — the exact provider the loop would have used with the
router switched off.

## Transparency surfaces

- `RoutingDecision.explanation` — every step: config (with clamp problems), switch state,
  matched rule + matched word, validation lines, autotune's full routing rationale,
  local-only filtering note, usability outcome, and the final routing/fallback line.
- `ultron router "<text>"` — prints the explanation as a dry run (exit 0 = would route,
  1 = would fall back). The winner's client is constructed to prove usability; clients are
  lazy, **no model call is made and nothing is sent**.
- `ultron router --status` — config, results file path, validation state, the published
  rule table, and the current per-category winners.
- `ultron agent --steps` — prints the decision's explanation above the run's steps.
- `ultron doctor` — one status line (`smart router: on (… results valid)` / `off (…)`).
- `run_agent` appends `("router_decision", RoutingDecision)` to `ctx.events` (only when the
  router is on); event consumers filter by kind, so existing consumers are unaffected.
- Logging on `ultron.router` (INFO): `coding -> groq:fast` or `fallback (<reason>)`.

## Configuration reference

| variable | default | meaning |
|---|---|---|
| `ULTRON_ROUTER` | `0` | kill switch; `1/true/yes/on` enables routing for the agent loop |
| `ULTRON_ROUTER_MIN_PASS` | `0.5` | pass fraction a profile needs in the category (0–1, clamped) |
| `ULTRON_ROUTER_MAX_AGE_DAYS` | `14` | results older than this are stale (1–365, clamped) |
| `ULTRON_LOCAL_ONLY` | `0` | existing flag; restricts routing to local profiles (see above) |

Invalid values are clamped and reported in the explanation; the router never writes to
`.env`, the results file or any other state (test-verified byte-identity).

## Limitations (honest list)

- **Keyword classification is conservative.** Requests outside the three categories (or
  phrased without a listed word) are not routed — by design; the default path handles them.
- **Routing applies to the agent loop only** (`run_agent`). The chat model, race roster and
  autotune itself are untouched. `autotune route` remains the manual/advisory surface.
- **The loop must already be available.** `agent_available()` still gates on the default
  provider; the router re-selects *within* an available loop rather than enabling one.
- **Results are point-in-time.** Providers change without notice; the fingerprint + age
  gates bound the damage, but re-running `ultron autotune run` after model/config changes
  is the owner's action.
- **No quality claim.** Latency and objective pass rates are not answer quality.
- Per-request overhead when enabled: one results-file read plus one provider construction
  (both local, no network at construction).

## Provenance and licence note

Original implementation for this repository. The general idea of task-aware model routing
is publicly documented idea space (also present in G0DM0D3, AGPL-3.0); **no code, prompts,
scoring logic or model tables were copied** from any external project, and the eligibility
rule here is exactly M2.5's published AutoTune rule, reused by import. The repository's
licensing question regarding AGPL-3.0 idea-space overlap remains open and is unchanged by
this feature; the repo licence was not modified.
