# M2.2: Groq as a model provider for the agent loop

M2.2 lets the M2.0 agent loop run on **Groq** as well as OpenAI, chosen by one setting. The agent
itself is unchanged: the same runtime, tool registry, central policy, confirmation store, limits,
cancellation and verification apply to every tool call, whichever provider proposed it.

> **Status of live validation: not done.** No request has reached Groq from the development
> environment (its network blocks `api.groq.com`), and no `GROQ_API_KEY` was used. Everything below
> is verified with the real `openai` SDK against a local Groq-shaped stub, plus Groq's published
> documentation (checked 2026-10-13). Run the opt-in live test (section 7) on your Mac to validate
> a real Groq response and a real tool call.

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
| 400 (other), 404 `model_not_found`, 422 | `rejected` | "…rejected the request (check ULTRON_GROQ_MODEL supports tool calling)" |
| 413 | `rejected` | "The request was too large for the language model" |
| 498 (flex capacity), 500, 502, 503, network errors | `unavailable` | "…unavailable right now" |
| Timeout (per-call limit, 20 s by default) | `timeout` | "…did not answer in time" |
| Unparseable body, tool call without an id | `bad_response` | "…returned an unexpected response" |

The log line for a provider error contains only the kind, exception type, HTTP status, Groq's error
code, `param` and request id. It never includes the message or body, which can echo request details or
part of a key. The user-facing text names the variable to check and is fixed text, never provider
text. There are no automatic retries (`max_retries=0`), except the bounded model re-ask after
`tool_use_failed`.

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

**Mocked (no key, no network, run in CI)**: `tests/test_groq_provider.py`, 65 tests:

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
- It prints a `live groq summary` table (status, tools, tokens, time) with no key, prompts or replies.
- Assertions check behaviour (which tools ran, order, arguments, status, verification), not wording.
- Free-tier rate limits are low. If a scenario fails with `error=rate_limited`, wait a minute and re-run.

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
| Single-shot chat says the OpenAI key is missing | Expected with only a Groq key: only the agent loop uses Groq (section 2) |

## 9. Limitations

- **No live Groq validation yet** (see the status note at the top). The stub reproduces Groq's documented
  request and response shapes and error bodies, not the live service or real model judgement.
- **Unconfirmed against live Groq:** that it accepts `parallel_tool_calls: false` together with
  `include_reasoning: false` for gpt-oss. Groq's API reference lists both fields as supported. The
  live smoke test is the check.
- Model availability and pricing change. The default was picked from Groq's docs on 2026-10-13.
- Only the agent loop uses Groq. Single-shot chat, study plans and knowledge answers remain OpenAI-only.
- Linux CI cannot verify macOS behaviour. Nothing in this milestone is macOS-specific.
