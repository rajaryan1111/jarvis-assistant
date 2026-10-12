# M2.2: Groq as a model provider for the agent loop

M2.2 lets the M2.0 agent loop run on **Groq** as well as OpenAI, chosen by one setting. The agent
itself is unchanged: the same runtime, tool registry, central policy, confirmation store, limits,
cancellation and verification apply to every tool call, whichever provider proposed it.

> **Status of live validation: partial, owner's Mac only.** The development environment cannot reach
> `api.groq.com` and has no `GROQ_API_KEY`, so everything below is verified with the real `openai` SDK
> against a local Groq-shaped stub, plus Groq's published documentation (checked 2026-10-13). The owner's
> first real run (`openai/gpt-oss-120b`) passed 5 of 7 live scenarios; section 10 covers the two
> failures, what was changed and what is still unproven. Those changes have **not** yet been run
> against real Groq.

## 1. How to use it

```bash
# .env (git-ignored). Never put the key in code, tests or chat.
ULTRON_PROVIDER=groq
GROQ_API_KEY=<your key from console.groq.com/keys>
# optional:
# ULTRON_GROQ_MODEL=openai/gpt-oss-120b
# ULTRON_GROQ_REASONING_EFFORT=low
# ULTRON_AGENT_MAX_OUTPUT_TOKENS=1024
```

Then `uv run ultron doctor` should show:

```text
[ok]   GROQ_API_KEY: configured
[ok]   agent loop: provider=groq model=openai/gpt-oss-120b (up to 1024 output tokens per model call)
```

To switch back, remove `ULTRON_PROVIDER` (or set it to `openai`). OpenAI remains the default.

## 2. Settings

| Variable | Default | Meaning |
|---|---|---|
| `ULTRON_PROVIDER` | `openai` | `openai` or `groq` (case-insensitive). **Any other value turns the agent loop off** and `doctor` reports `[FAIL]`. ULTRON never falls back to another provider, so a typo such as `grok` can't send your requests to a provider you didn't choose |
| `GROQ_API_KEY` | — | Needed only with `ULTRON_PROVIDER=groq`. Loaded like every other credential (environment or `.env`; placeholders count as unset). Without it the agent loop is off, and built-in commands keep working |
| `ULTRON_GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model id (same validation as `ULTRON_AGENT_MODEL`). Must support tool use |
| `ULTRON_GROQ_REASONING_EFFORT` | `low` for gpt-oss, otherwise not sent | `low`, `medium` or `high` for gpt-oss; Groq also accepts `none` and `default` for Qwen. Invalid values fall back to the default |
| `ULTRON_AGENT_MAX_OUTPUT_TOKENS` | 400 (OpenAI) / 1024 (Groq) | Per model call, clamped to 64–4096 |

**Why a separate Groq model setting.** `ULTRON_AGENT_MODEL` and `ULTRON_CHAT_MODEL` name OpenAI models
(`gpt-4.1-mini`), and Groq uses different ids (`openai/gpt-oss-120b`). One shared variable would break
whenever you switch providers. With `ULTRON_PROVIDER=groq`, the OpenAI model variables are ignored by
the agent.

**What stays on OpenAI.** Only the agent loop uses the selected provider. The single-shot helpers
(`ask_gpt` fallback chat, study planner, knowledge-base answers) keep using `OPENAI_API_KEY` and
`ULTRON_CHAT_MODEL`, as before. With only a Groq key, those helpers say the OpenAI key is missing,
but the agent (which handles every request the keyword router doesn't know) works. Moving them to
Groq too would be a separate change.

## 3. Choosing the default model (verified from Groq's docs, not live)

The default must support tool calling and be usable with a self-serve key. On 2026-10-13, Groq's model
page listed:

| Model | Status | Access | Tool use | Parallel tool calls |
|---|---|---|---|---|
| **`openai/gpt-oss-120b`** | Production | Self-serve ($0.15 / $0.60 per 1M input/output tokens) | Yes | No |
| `openai/gpt-oss-20b` | Production | Self-serve ($0.075 / $0.30) | Yes | No |
| `llama-3.3-70b-versatile`, `llama-3.1-8b-instant` | Production | Enterprise (contact sales) | Yes | Yes |
| `qwen/qwen3.8-27b` | Preview (may be removed at short notice) | Self-serve | Yes | Yes |

Groq's tool-use page says all hosted models support tool use. `openai/gpt-oss-120b` is the
largest production model with self-serve pricing, so it is the default. Its lack of parallel tool
calls doesn't matter, because ULTRON always asks for **one tool call per turn**
(`parallel_tool_calls: false`) so it can run calls one at a time and audit each. Model lists change:
`GET https://api.groq.com/openai/v1/models` (with your key) lists what your account can use right now.

## 4. How it works

```text
ULTRON_PROVIDER ─► load_agent_config() ─► create_provider() ─► OpenAIChatProvider  (OpenAI, main.client)
                                                         └──► GroqChatProvider    (Groq client, fixed base URL)
                                                                   │
            AgentRuntime (unchanged): catalog ─► schema ─► ToolRegistry.execute ─► policy ─► confirmation
```

- **No new dependency.** Groq documents an OpenAI-compatible endpoint
  (`https://api.groq.com/openai/v1`), so `GroqChatProvider` reuses the `openai` SDK that ULTRON already
  ships. It subclasses `OpenAIChatProvider`: request building, response parsing, error mapping,
  per-call timeout and `max_retries=0` are shared, not copied. Groq's own `groq` SDK would add a
  dependency without adding anything the agent needs.
- **Fixed endpoint.** The Groq client is always created with an explicit `base_url`. `OPENAI_BASE_URL`
  (which the OpenAI SDK reads from the environment) can't redirect your Groq key to another host.
  There is no environment override for the Groq URL.
- **Key isolation.** The Groq key is sent only to Groq, and the OpenAI key only to OpenAI. A Groq key
  doesn't enable the OpenAI path and vice versa (tested).
- **Built once.** `main.py` creates the Groq provider once at startup. The OpenAI path is unchanged.

### What ULTRON sends to Groq

The same as for OpenAI (see [M2.1 § privacy](M2_1_REAL_MODEL_VALIDATION.md)): the system prompt, your
request, the tool catalog for that run (names, descriptions, JSON schemas) and the tool results in that
run (each capped at 1,500 characters). No memory file, notes or history beyond the current run, and no
credentials. Check Groq's own terms for how they handle API data. This project hasn't verified Groq's
retention policy.

## 5. Groq-specific behaviour

| Topic | What ULTRON does |
|---|---|
| **Reasoning tokens** | gpt-oss models think before answering, and that hidden reasoning counts against `max_completion_tokens`. OpenAI's 400-token limit could be used up before any answer or tool call, so Groq defaults to **1024** output tokens per call and **low** reasoning effort. If the limit is still hit with no answer, the run fails with "ran out of output tokens before answering (ULTRON_AGENT_MAX_OUTPUT_TOKENS)" instead of a vague "empty response". The 30k-token budget per run still applies and includes reasoning tokens |
| **Reasoning text** | Never requested (`include_reasoning: false` for gpt-oss, `reasoning_format: hidden` for Qwen), never logged, never stored, never sent back to the model. If a reply contains a `reasoning` field anyway, it is ignored |
| **Model-specific parameters** | gpt-oss **rejects** `reasoning_format` and Llama-style models reject reasoning parameters, so each family gets only what it accepts: gpt-oss gets `reasoning_effort` + `include_reasoning`; Qwen gets `reasoning_format` (+ effort only if you set it); other models get none |
| **Unsupported fields** | Never sent: `messages[].name`, `logprobs`, `top_logprobs`, `logit_bias`, `n`, `stream`, `max_tokens` (Groq answers 400 to several of these) |
| **Parallel tool calls** | Always off, which matches what gpt-oss supports |
| **Malformed tool calls** | Groq checks the model's tool call on its side. If the model writes a broken one, Groq answers **HTTP 400 `tool_use_failed`** (this can happen intermittently). ULTRON treats this as an *invalid model turn*: nothing runs, a `(malformed tool call)` step is recorded, and the model gets another turn. It counts toward the 3-invalid-calls limit and the step limit. Groq's error body includes the raw generation (`failed_generation`), which is never read, logged or shown |
| **Tool validation** | Groq's default check (a called tool must be one we offered) is left on. ULTRON's own catalog, schema and policy checks still run after it |
| **Temperature** | Not sent (provider default), same as OpenAI |

### Errors

| Groq response | ULTRON error kind | What you see |
|---|---|---|
| 401 `invalid_api_key`, 403 | `not_configured` | "…rejected the configured credentials (check GROQ_API_KEY)" |
| 429 `rate_limit_exceeded` (requests or tokens per minute/day) | `rate_limited` | "…rate limited right now; try again in a minute" |
| 400 `tool_use_failed` | `malformed_call` | Retried as above. After 3: "…kept proposing invalid tool calls" |
| 400 `output_parse_failed` (seen in a real run; **not documented by Groq**) | `rejected` | "The Groq service could not parse the language model's output (output_parse_failed)". Not retried: see section 10 |
| 400 (other), 404 `model_not_found`, 422 | `rejected` | "…rejected the request (check ULTRON_GROQ_MODEL supports tool calling)" |
| 413 | `rejected` | "The request was too large for the language model" |
| 498 (flex capacity), 500, 502, 503, network errors | `unavailable` | "…unavailable right now" |
| Timeout (per-call limit, 20 s by default) | `timeout` | "…did not answer in time" |
| Unparseable body, tool call without an id | `bad_response` | "…returned an unexpected response" |

The log line for a provider error contains only the kind, exception type, HTTP status, Groq's error
code, `param` and request id. It never includes the message or body, which can echo request details or
part of a key. The user-facing text names the variable to check and is fixed text, never provider
text. There are no automatic retries (`max_retries=0`), except the bounded model re-ask after
`tool_use_failed`. The run's `end` log line repeats the error kind and code next to a content-free
`stop=` reason (section 10).

## 6. Security parity

Groq tool calls go through exactly the same path as OpenAI's:

- only the run's allowlisted catalog is offered, and unknown tool names (`system__shell`) are invalid;
- arguments are validated against the tool schema;
- every call goes through `ToolRegistry.execute`: the central policy, risk tiers and the audit log;
- CONSEQUENTIAL actions become *delegated pending actions* that only a signed-in HUD click can approve
  (the owner bearer token, the wake-word client, voice and the model can't);
- after the run reads external content, ROUTINE actions are escalated to need approval;
- success is reported only when recorded tool results support it.

`tests/test_groq_provider.py` exercises each of these with Groq-shaped traffic through the real SDK.
That includes an HTTP test where a Groq-proposed action is denied for the bearer token and runs exactly
once after the HUD click.

Credential hygiene added in M2.2:

- `GROQ_API_KEY` is a registered credential (`doctor`, placeholder detection, redaction by value).
- `gsk_…` key shapes are redacted from logs and flagged by `ultron check-secrets`.
- The test session removes `GROQ_API_KEY`, `ULTRON_PROVIDER` and `OPENAI_BASE_URL` from the environment.

## 7. Tests

**Mocked (no key, no network, run in CI)**: `tests/test_groq_provider.py`, 79 tests:

- **Provider selection and config:** defaults; Groq selection; its own model variable; reasoning-effort
  validation; output-limit clamping; unknown provider turns the agent off (no fallback); missing or
  placeholder key; credential isolation; fixed Groq base URL even with `OPENAI_BASE_URL` set.
- **Request format** (real SDK → loopback stub): endpoint path, Groq bearer header, no retry header,
  `max_completion_tokens`, tools, `tool_choice`, `parallel_tool_calls: false`, the per-family reasoning
  parameters, and the absence of every unsupported field. OpenAI requests carry no Groq extras.
- **Parsing:** tool calls, usage, `reasoning` field dropped, malformed bodies, tool call without an id.
- **Errors:** 401/403/429/404/400/413/498/500/503, `tool_use_failed`, timeout and unreachable host. All
  without retries and without echoing the key, the provider message or `failed_generation`.
- **Runtime on Groq:** real tool results fed back by id; reasoning never re-sent or logged; a malformed
  call retried and then completed; repeated malformed calls stop the run (bounded by the invalid-call and
  step limits); reasoning that exhausts the output limit; truncated answers; mid-run failures report
  completed side effects.
- **Security parity:** see section 6, including the HUD-only approval over HTTP.
- **Wiring:** `main.py` routes to Groq with its output limit and needs no OpenAI key; unknown provider
  or missing key keeps the agent off; `ultron agent` and `ultron doctor` messages and exit codes; no key
  in output.
- **Secrets:** redaction by shape and by value, the scanner rule, test-session scrubbing, and live
  credential capture only when opted in.
- **Fixes from the first real run (section 10):** `output_parse_failed` fails closed (one request, no
  tool runs, no retry, nothing from the body leaks); other 400s and 429s are still not retried; every
  way a run can end has its own stop reason (9 endings checked, including asking, answering without
  tools, invalid and malformed calls, output limit and empty response); the prompt's lookup-before-asking
  rule; asking is still required when no tool can supply the detail.

**Live (opt-in, your key, never in CI)**: `tests/live/test_live_providers.py` runs the same 7
real-model scenarios as M2.1 against Groq:

- a real response grounded in a real tool call;
- a multi-step calendar → reminder run;
- asking instead of acting on an ambiguous request;
- web-content injection held for approval;
- a failed action not reported as success;
- an unknown model rejected cleanly;
- a bad key rejected cleanly.

```bash
ULTRON_LIVE_GROQ=1 uv run pytest tests/live -p no:cacheprovider -v
```

- It reads `GROQ_API_KEY` (and `ULTRON_GROQ_MODEL` / `ULTRON_GROQ_REASONING_EFFORT`) from your shell or `.env`.
- Opting in without a key **fails** with "GROQ_API_KEY is not set … Nothing was sent".
- Bounds: 5 turns, 12k tokens per scenario, 60k tokens per session, synthetic tools and data only. With
  gpt-oss-120b pricing that is under 4 US cents even if every token were output. Check current pricing.
- It prints a `live groq summary` table: status, stop reason, error kind and code, model calls, ordered
  `tool:status` steps, tokens in and out, and time. Failure messages carry the same line. No key,
  prompts, replies, tool arguments or error bodies.
- Assertions check behaviour (which tools ran, order, arguments, status, verification), not wording.
- Free-tier rate limits are low. A 429 is labelled `RATE-LIMITED` in the summary and in the failure
  message: the test still fails (it proved nothing), but it is not a functional result. Nothing is
  retried. Wait a minute and re-run, or set `ULTRON_LIVE_PAUSE_SECONDS=20` (0–60) to space the scenarios.

### Fixed in M2.2: the M2.1 live test could never find a key

`tests/conftest.py` removes provider keys from the environment and disables `.env` loading, so ordinary
tests can never use real credentials. It did this before the live module ran, so
`ULTRON_LIVE_OPENAI=1 uv run pytest tests/live` would always have failed with "OPENAI_API_KEY is not
set", even with a valid key. That went unnoticed because the live test couldn't run in development.

Now `conftest.py` captures `.env` + environment **only when a `ULTRON_LIVE_*` flag is `1`** and passes it
to the live tests through a `live_env` fixture. It never goes back into `os.environ`, so every other
test in the same session still runs without credentials (tested, and mutation-checked).

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `doctor`: `[FAIL] agent loop: ULTRON_PROVIDER='…' is not supported` | Typo. Use `openai` or `groq` |
| `doctor`: `agent loop off: GROQ_API_KEY is not set` | Add the key to `.env` (or export it), then restart ULTRON |
| "…rejected the configured credentials (check GROQ_API_KEY)" | Wrong, revoked or mistyped key. Create a new one at console.groq.com/keys |
| "…rejected the request (check ULTRON_GROQ_MODEL …)" | Unknown or retired model, or one your plan can't use. List models via `GET /openai/v1/models` |
| "…rate limited right now" | Requests- or tokens-per-minute limit (free-tier limits are low). Wait, or reduce usage |
| "ran out of output tokens before answering" | Reasoning used the whole limit. Raise `ULTRON_AGENT_MAX_OUTPUT_TOKENS` (e.g. 2048) or set `ULTRON_GROQ_REASONING_EFFORT=low` |
| "kept proposing invalid tool calls" | The model repeatedly wrote broken tool calls (`tool_use_failed`). Retry, or try another model |
| "…could not parse the language model's output (output_parse_failed)" | Groq could not parse what the model generated (undocumented code, section 10). Nothing ran. Retry the request; if it keeps happening, note the scenario and request id from the log |
| Single-shot chat says the OpenAI key is missing | Expected with only a Groq key: only the agent loop uses Groq (section 2) |

## 9. Limitations

- **Live Groq validation is partial** (see the status note at the top and section 10). The stub reproduces
  Groq's documented request and response shapes and error bodies, not the live service or real model
  judgement.
- **Unconfirmed against live Groq:** that it accepts `parallel_tool_calls: false` together with
  `include_reasoning: false` for gpt-oss. Groq's API reference lists both fields as supported. The
  live smoke test is the check.
- Model availability and pricing change. The default was picked from Groq's docs on 2026-10-13.
- Only the agent loop uses Groq. Single-shot chat, study plans and knowledge answers remain OpenAI-only.
- Linux CI cannot verify macOS behaviour. Nothing in this milestone is macOS-specific.

## 10. First real Groq run, and the fixes that followed

The owner ran `ULTRON_LIVE_GROQ=1 uv run pytest tests/live` on macOS with `openai/gpt-oss-120b`:
**5 passed, 2 failed** (7 OpenAI scenarios skipped). `doctor` recognised `GROQ_API_KEY` and the model.
A separate `ultron agent` command called `time.now` and then got HTTP 429 `rate_limit_exceeded`.

### Failure 1: multi-step "expected calendar before reminder, got []"

- **Evidence:** the check that `report.error is None` runs first and passed, so the run ended **without a
  provider error**. That rules out 429, `output_parse_failed` and other HTTP errors. `[]` lists only
  *completed* tools, so nothing completed. The old summary showed neither the stop reason nor the
  invalid/malformed steps, so the cause could not be told apart from:
  - the model asking a question (`ask_user` ends the run and records no step);
  - a plain answer without tools;
  - three invalid or malformed tool calls in a row;
  - reasoning exhausting the output limit;
  - an empty response.
- **Likely cause (not proven):** prompt rule 4 told the model to call `ask_user` when "what time" was
  missing. It never said to look it up with a provided read-only tool first. The objective ("30 minutes
  before my dentist appointment") gives no clock time, so asking was a reasonable reading of the rule.
  Replaying exactly that behaviour through the live harness offline (stub model calls `ask_user`)
  reproduces the reported message ("expected calendar before reminder, got []"). Other endings would also print `got []`,
  so this is consistent with the report, not proof.
- **Changes:**
  1. Rule 4 now says: first check whether a provided read-only tool can look the information up
     (tool descriptions are marked "read-only"), and only if none can, ask. No tool, schema, policy,
     approval or limit changed. The ambiguity scenario ("Set a reminder." with only `reminders.add`)
     still requires asking, and a deterministic test pins that asking ends the run with nothing executed.
  2. Every report now has a content-free `stop_reason`, and the `end` log line and live summary show it.
     A repeat failure will therefore say exactly why it happened.
- **Scenario and assertions unchanged:** the task (read the calendar, set a reminder 30 minutes earlier,
  verify it) is realistic and valid. Its assertions on order, exact time, status and verification are
  unchanged.

| `stop` | Meaning |
|---|---|
| `finish_tool` | The model called `finish` (statuses `completed`, `incomplete` or `answered`) |
| `text_answer` / `answer_truncated` | Plain text answer / cut short by the output limit |
| `asked_user` | The model asked a question (`needs_input`) |
| `awaiting_approval` / `denied` | A tool call is waiting for HUD approval / was denied |
| `provider_error` | A provider error ended the run (`error=` kind and code) |
| `invalid_calls` | 3 invalid or malformed tool calls in a row (see `tools=[…]`) |
| `output_limit_no_answer` / `empty_response` | Reasoning used the whole output limit / an empty reply |
| `step_limit`, `time_limit`, `token_budget`, `context_limit`, `tool_call_limit`, `per_tool_limit`, `duplicate_calls` | A limit was reached |
| `cancelled` / `internal_error` | Stopped by the user / an internal error |

### Failure 2: failed-action scenario, HTTP 400 `output_parse_failed`

- **Documentation check (2026-10-13):** `output_parse_failed` is **not documented** in Groq's errors
  page, API reference, tool-use guides or reasoning guide, and a web search found no Groq source. Groq
  documents only `tool_use_failed` as "the model failed to generate a valid tool call" (with a
  `failed_generation` field), for which it recommends a bounded retry.
- **Decision:** the code's name suggests the same kind of model-output failure, but that is an inference,
  not documented semantics. So it is **not** treated as retryable. It stays `rejected`: the run fails
  closed after one request, nothing executes, and nothing is reported as success. Only its message
  changed. The generic "check ULTRON_GROQ_MODEL supports tool calling" was misleading, since the same
  model completed tool calls in the other scenarios. Groq's body, including any `failed_generation`, is
  never read, logged or shown. Arbitrary 400s are still never retried.
- **Consequence:** if Groq returns this code again, the failed-action scenario will fail again, now labelled
  `error=rejected(output_parse_failed)`. That is an honest result, not a code bug to hide. If Groq
  documents the code as a model-generation failure, it can be added next to `tool_use_failed` (bounded
  by the same 3-invalid-calls and step limits) with a one-line change and the existing tests.
- **Not investigated further:** Groq's docs show `name` on tool-result messages; ULTRON sends only
  `tool_call_id`, which is the OpenAI format. Full tool round trips passed in the real smoke and
  injection scenarios, so this is not a known cause.

### Rate limits (HTTP 429)

The 429 was in a separate command, not in the live tests. Handling is unchanged: `rate_limited`,
no retries, with fixed text. The live summary now labels such rows `RATE-LIMITED`, and
`ULTRON_LIVE_PAUSE_SECONDS` can space scenarios on low tokens-per-minute plans.

### Re-running the two scenarios on your Mac

```bash
ULTRON_LIVE_GROQ=1 uv run pytest "tests/live/test_live_providers.py::test_live_multi_step_tool_output_drives_the_next_action[groq]" -p no:cacheprovider -v -s
ULTRON_LIVE_GROQ=1 uv run pytest "tests/live/test_live_providers.py::test_live_failed_action_is_not_reported_as_success[groq]" -p no:cacheprovider -v -s
```

Each prints one summary row. Its `stop=`, `error=` and `tools=[…]` fields show what happened.
