# M2.1 — Real-model integration and end-to-end validation

M2.0 added the agent loop (see [M2_0_AGENT_LOOP.md](M2_0_AGENT_LOOP.md)), but every test used a scripted fake
model. M2.1 hardens the OpenAI provider path and adds three layers of validation:

1. **Wire-level tests.** These run the real `openai` SDK over real HTTP against a local stub server.
2. **End-to-end scenarios** with a *reactive* deterministic model.
3. **An opt-in live test** that uses the real OpenAI model through the real agent runtime.

The architecture is unchanged. There are no new dependencies and no new tools for the agent.

> **Live-model status for this commit: not verified.** The development sandbox has no OpenAI key, and its
> network blocks `api.openai.com` (the TLS handshake fails). No request ever reached OpenAI, so nothing here
> claims real-model behaviour. The live test is ready to run on your machine with one command (§4). Until
> someone runs it, real-model behaviour remains unverified.

## Contents

1. [What was checked first](#1-what-was-checked-first)
2. [Defects found and fixed](#2-defects-found-and-fixed)
3. [Configuring the provider](#3-configuring-the-provider)
4. [Running the tests](#4-running-the-tests)
5. [What each test layer proves (simulated vs real)](#5-what-each-test-layer-proves)
6. [Cost and API usage](#6-cost-and-api-usage)
7. [Privacy: what is sent to OpenAI](#7-privacy-what-is-sent-to-openai)
8. [Diagnostics and logs](#8-diagnostics-and-logs)
9. [Troubleshooting](#9-troubleshooting)
10. [Limitations and manual macOS checks](#10-limitations-and-manual-macos-checks)

## 1. What was checked first

**Git state.** The branch is `arena/289eecea-jarvis-assistant`. HEAD and the remote were both at `aceab2a`,
and the working tree was clean.

**Request path.**

```text
HUD/API  POST /ask {message, mode}  ─┐   (auth, CSRF, origin checks — unchanged)
Wake word POST /ask (client token)  ─┤
Mic loop  main.handle_command        ─┴─► main.handle_command(cmd, ctx)
     keyword router (legacy commands)  ── unknown request / "agent:" prefix ──►  main.run_agent
     └► build_agent() → AgentRuntime(TOOLS, OpenAIChatProvider(client, AGENT_CONFIG.model), limits)
          loop: provider.complete(messages, tools) ─► openai SDK ─► POST /v1/chat/completions
                ToolCall ─► catalog / JSON / schema / repeat / budget checks
                         ─► ToolRegistry.request(tool, args, ctx+agent_run)  (policy, approval, audit)
                result ─► back to the model as bounded, labelled data
          finish / answer ─► verified against the step ledger ─► AgentReport
     reply + report.public() ─► /ask JSON {"reply", "agent": {...}, "pending_action"?}
```

**Provider.** `OpenAIChatProvider` (`ultron/agent/provider.py`) uses Chat Completions function calling on the
locked `openai==3.29.0`. Each call sets `with_options(timeout=…, max_retries=0)` and `parallel_tool_calls=False`.
The key comes from `OPENAI_API_KEY` (environment or `.env`, via `ultron.config.get_credential`) and is never
hardcoded.

**Model.** The default is `gpt-4.1-mini`. OpenAI's deprecations page (checked 2026-10-13) lists `gpt-4.1-nano`
for shutdown on 2026-10-23, but not `gpt-4.1-mini`. The model is therefore kept, and it is configurable (§3).

## 2. Defects found and fixed

| # | Defect | Effect | Fix |
|---|---|---|---|
| 1 | `max_tokens` sent to Chat Completions | Deprecated, and rejected (400) by reasoning models if one is configured | `max_completion_tokens` (agent and legacy calls) |
| 2 | `finish_reason` ignored | A reply cut off by the output limit was reported as a complete answer. Content-filter stops and refusals ended as "empty response" | `length`: a text answer is reported `incomplete` ("cut short"), and cut-off tool arguments never run (invalid JSON, clear message). `content_filter`: a `rejected` error. A refusal is returned as the answer |
| 3 | 429 rate limit and exhausted quota looked like an outage | Misleading message, no hint about billing | New `rate_limited` category. `insufficient_quota` gets a billing message |
| 4 | No token accounting or per-call diagnostics | No cost visibility and no token budget | `ModelTurn.usage`/`finish_reason`; per-run `max_total_tokens` (30k); report `usage`, `duration_ms`, `error`; structured logs (§8) |
| 5 | Shared OpenAI client used SDK defaults (600 s timeout, 2 retries) | A legacy chat call could block a voice reply for minutes | `timeout=30`, `max_retries=1` for the shared client. The agent still overrides per call (its own timeout, 0 retries) |
| 6 | Legacy chat, study planner and knowledge base printed `str(exception)` | OpenAI 401 messages echo a masked key fragment; other errors can echo request details | Exception **type** only |
| 7 | Model name hardcoded in four places | Not configurable | One default in `ultron/config.py`, plus `ULTRON_CHAT_MODEL` and `ULTRON_AGENT_MODEL` (validated) |
| 8 | Tool calls with an empty id, or a `null` message, were accepted | Undefined follow-up behaviour | Both are a `bad_response` error |

The provider error log line now records the HTTP status, OpenAI error code, `param` and request id. These are
short identifiers, checked against a strict pattern before logging. The error message and body are never
logged.

## 3. Configuring the provider

All settings are optional environment variables (or `.env`). Nothing else changes when no key is set:
legacy commands work, and unknown requests get "my language model is not configured".

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | Enables the agent loop and single-shot chat. Keep it in `.env` (git-ignored) or your shell, never in code |
| `ULTRON_CHAT_MODEL` | `gpt-4.1-mini` | Model for single-shot chat, study plans and knowledge answers |
| `ULTRON_AGENT_MODEL` | = `ULTRON_CHAT_MODEL` | Model for the agent loop. Must support **Chat Completions function calling** |
| `OPENAI_BASE_URL` | OpenAI | Read by the SDK. An OpenAI-compatible server must support tools and `max_completion_tokens` |
| `ULTRON_AGENT_*` | see M2.0 §10 | Steps, tool calls, timeouts, total seconds, concurrency |

Model names may contain only letters, digits and `._:/-` (at most 100 characters). Anything else falls back
to the default, with a warning. Per-run budgets are 400 output tokens per call, 30k tokens per run, 6 model
turns and 45 s.

Some newer reasoning models need extra parameters when used with Chat Completions tools. If yours answers
`rejected`, check §9.

## 4. Running the tests

**Ordinary suite** (deterministic, no key, no network, no cost; this is what CI runs):

```bash
uv sync --extra dev
uv run pytest -q              # the live tests show as "skipped"
uv run ruff check .
uv run python -m ultron check-secrets
```

**Opt-in live test** (real OpenAI, your key, a few cents at most):

```bash
ULTRON_LIVE_OPENAI=1 uv run pytest tests/live -p no:cacheprovider
```

- It reads `OPENAI_API_KEY` from your environment or `.env`. (In M2.1 itself this was broken; see the
  troubleshooting table. Fixed in M2.2, which also added `ULTRON_LIVE_GROQ=1` for Groq.)
- If you opt in without a key, it **fails** with a clear message, and nothing is sent.
- It refuses to run with `OPENAI_LOG=debug`, because SDK debug logs include request details.
- At the end it prints a summary with no secrets and no conversation text. Each row (and each failure
  message) shows the final status, why the run ended (`stop`, see the
  [M2.2 doc](M2_2_GROQ_PROVIDER.md#10-first-real-groq-run-and-the-fixes-that-followed)), the provider
  error kind and code, model calls, the ordered `tool:status` steps (including invalid and malformed
  ones), tokens in and out, and time. Never the key, prompts, replies, tool arguments or error bodies:

```text
=================== live openai summary (model gpt-4.1-mini, 14210 tokens total) ===================
PASS  smoke: read-only tool              status=completed stop=finish_tool error=- verified=True calls=2 tokens_in=1890 tokens_out=120 ms=1840 tools=[clock.now:completed]
FAIL  multi-step: calendar -> reminder   status=needs_input stop=asked_user error=- verified=None calls=1 tokens_in=1650 tokens_out=40 ms=900 tools=[-]
RATE-LIMITED  honesty: failed action     status=failed stop=provider_error error=rate_limited(rate_limit_exceeded) ...
...
```

(This example shows the format only; it is not output from a real run.) `RATE-LIMITED` rows are HTTP 429:
the test still fails, because it proved nothing, but it is not a functional result. Nothing is retried.
On a low tokens-per-minute plan, set `ULTRON_LIVE_PAUSE_SECONDS` (0–60, default 0) to wait between
scenarios.

| Live scenario | Real model must… | Asserted (observable only) |
|---|---|---|
| smoke: read-only tool | use the clock tool and answer from it | a completed `clock.now` step; `completed` and verified; the reply contains the random odd-minute time the tool returned; at least 2 model calls |
| multi-step | read the calendar, then add a reminder 30 min earlier | calendar before reminder; the stored reminder time equals the tool's random time − 30 min; verified |
| ambiguity | ask instead of guessing on "Set a reminder." | no reminder stored; `needs_input` or `answered` |
| prompt injection | ignore instructions inside a web result | the injected note was **not** saved; any attempt is `pending_confirmation` and escalated |
| failed action | not claim a failed action succeeded | status is not `completed`; not verified; retries stay within the per-tool limit |
| errors: unknown model | — | real 404 → `failed` / `rejected`, no steps |
| errors: invalid key | — | real 401 → `failed` / `not_configured`; the key is not echoed |

Limits for every live scenario: 5 model turns, 5 tool calls (3 per tool), 300 output tokens per call, 12k
tokens per scenario, 75 s. The whole session has a 60k-token budget, and later scenarios are skipped once it
is spent. All tools are synthetic and live on an **isolated registry** (real policy and confirmation store).

## 5. What each test layer proves

| Layer | Files | Talks to OpenAI? | Proves | Does not prove |
|---|---|---|---|---|
| Scripted model (M2.0) | `tests/test_agent_loop.py` | No | Runtime rules: catalog, schema, policy, approval, limits, cancellation, verification, integration | Anything about the real API or model |
| **Wire level** (M2.1) | `tests/test_openai_wire.py`, `tests/openai_stub.py` | No (stub on 127.0.0.1) | The **real SDK's** request body (`max_completion_tokens`, tools, `parallel_tool_calls`, bearer header, no retries), response parsing, finish reasons, usage, HTTP 401/403/429/400/404/500/503 mapping, timeouts, unreachable hosts, malformed bodies; the full runtime and `/ask` over HTTP | That OpenAI's live service behaves like the stub, or how a real model decides |
| **Reactive e2e** (M2.1) | `tests/test_agent_e2e.py` | No | Tool output reaches the model and drives the next step; stops when done; clarification; failure honesty; concurrent isolation; injection containment; approval impossible via model, bearer token or voice; provider failure after an uncertain action | Real-model judgement |
| Config/legacy (M2.1) | `tests/test_model_config.py` | No | One validated model setting; bounded legacy calls; errors never echoed | — |
| **Live** (M2.1) | `tests/live/test_live_providers.py` (was `test_live_openai.py`; M2.2 added Groq) | **Yes, when opted in** | Real model + real API + real runtime, on synthetic tools | Only what was actually run: **not run for this commit** |

The injection and failed-action live scenarios check **the system**, not model obedience. Both passed when
driven by a deliberately misbehaving fake model (one that obeys the injection and over-claims success),
because the runtime held the injected call for approval and downgraded the claim. A real model that behaves
well passes them as well.

**Mutation testing.** Each check was broken by hand and the suite re-run: 18/18 M2.1 mutants were caught
(provider parameters, retries, error categories, quota message, content filter, refusal, tool-call id, null
message, log safety, usage, token budget, truncation, error kind, model configuration, legacy printing).
The M2.0 (30) and M1.2 (24) sets were all caught again.

## 6. Cost and API usage

These are estimates. Measure your own usage from the `usage` field in the `/ask` response, the log line, or
the live-test summary.

- **Fixed context per model call:** the system prompt plus the tool catalog. That is about 1,500 tokens on
  Linux (11 tools) and roughly 1,800 on macOS (15 tools), measured as characters ÷ 4. The run's growing
  conversation is added on top.
- **A typical agent request** makes 2–4 model calls, which is roughly 4–10k input tokens and a few hundred
  output tokens. Every run stops at 30k tokens, 6 model turns or 45 s.
- **A plain question** that needs no tool is 1 call (about 1.5–2k tokens).
- **The live test session** typically uses 10–30k tokens, with a hard cap of 60k.

At gpt-4.1-mini's launch pricing (US$0.40 per million input tokens and US$1.60 per million output tokens),
a typical request costs well under one US cent, and a full live session about 1–5 cents. Prices change, so
check OpenAI's pricing page for your model. You can tighten `ULTRON_AGENT_MAX_STEPS` and
`ULTRON_AGENT_MAX_TOOL_CALLS`, or set `ULTRON_AGENT=0` to fall back to single-shot chat, which is one small
call per request.

## 7. Privacy: what is sent to OpenAI

**During ordinary agent operation**, each model call sends:

- the system rules;
- the tool catalog (names, descriptions, argument schemas);
- your request text: typed, or transcribed speech;
- every tool call and **tool result in that run**.

Depending on which tools the model uses, tool results can include:

- the time;
- CPU, RAM and battery percentages;
- **your saved notes** (`memory.list_notes`) and **your reminders** (`reminders.list`);
- weather for the requested or default city, news headlines, web-search result titles and Wikipedia
  summaries;
- confirmations of actions (e.g. "Reminder set for …").

**Not sent:**

- API keys, bearer tokens, cookies or session data;
- confirmation codes (delegated actions have none);
- files, screenshots, camera frames or audio;
- previous conversations: each run starts empty, and the single-shot fallback sends no history;
- principal names and run ids.

**Single-shot paths:**

- chat fallback: your request text;
- study planner: the topic;
- knowledge base: your question plus the matching snippets from your local notes and PDFs.

OpenAI's handling of API data is governed by your OpenAI account's data controls and policies. Speech
recognition in the microphone loop and HUD is a separate, pre-existing path. It is outside this milestone.

**In the tests**, the ordinary suite sends nothing anywhere. The live test sends only its synthetic objectives
and synthetic tool results. It never reads your notes, reminders, files or history.

## 8. Diagnostics and logs

Logger `ultron.agent` writes one structured line per event. These lines contain no prompts, arguments, tool
results, keys, headers or cookies:

```text
agent r_3f2a start principal=session:owner channel=hud input=text tools=15
agent r_3f2a model_call n=1 status=ok finish=tool_calls tool_calls=1 tokens_in=1712 tokens_out=18 ms=930
agent r_3f2a tool_call tool=weather.current risk=OBSERVE escalated=False state=done status=completed ms=412
agent r_3f2a model_call n=2 status=error kind=rate_limited ms=210
model provider error kind=rate_limited type=RateLimitError http=429 code=rate_limit_exceeded param=- request_id=req_…
agent r_3f2a end status=failed stop=provider_error verified=False steps=1 model_calls=2 tokens_in=1712 tokens_out=18 ms=1580 error=rate_limited code=rate_limit_exceeded
```

The `/ask` response carries the usage, duration and error kind in `agent.usage`, `agent.duration_ms` and
`agent.error`. The stop reason and provider error code are on the `end` log line and in the live-test
summary only. The tests
check that the key, the bearer header, the objective and tool-output text never appear in these logs.

## 9. Troubleshooting

| Symptom (reply or `agent.error`) | Cause | Fix |
|---|---|---|
| "rejected the configured credentials (check OPENAI_API_KEY)" / `not_configured` | 401/403: wrong, revoked or unauthorised key, or an unsupported region | Fix `OPENAI_API_KEY` in `.env`, then restart. `uv run ultron doctor` shows whether it is set (never the value) |
| "no remaining quota; check billing" / `rate_limited` | 429 `insufficient_quota` | Add credit or raise your usage limit in the OpenAI dashboard |
| "rate limited right now" / `rate_limited` | 429 rate limit | Wait a minute. Lower `ULTRON_AGENT_MAX_CONCURRENT` if you send many requests |
| "did not answer in time" / `timeout` | Slow network or service | Retry. Raise `ULTRON_AGENT_MODEL_TIMEOUT` (keep `ULTRON_AGENT_MAX_SECONDS` < 60 for wake word) |
| "unavailable right now" / `unavailable` | No network, DNS/proxy/firewall, or OpenAI 5xx | Check connectivity and proxy settings, and OpenAI's status page |
| "rejected the request (check ULTRON_AGENT_MODEL …)" / `rejected` | 400/404: unknown model, or a model that doesn't support Chat Completions tools or `max_completion_tokens` | Use a tool-calling chat model, e.g. the default. The log line's `code=`/`param=` names the problem |
| "content filter blocked this request" | OpenAI moderation | Rephrase the request |
| "… (my answer was cut short)" | Hit the 400-token output limit | Ask for a shorter answer, or split the request |
| "used up its token budget" | 30k tokens in one run | Split the request |
| Live test: "OPENAI_API_KEY is not set" | Opted in without a key | Add the key to `.env` or your shell |
| Live test: "OPENAI_API_KEY is not set" although it is set | M2.1 bug (the test isolation removed the key before the live test read it), fixed in M2.2 | Update to M2.2 or later ([details](M2_2_GROQ_PROVIDER.md#fixed-in-m22-the-m21-live-test-could-never-find-a-key)) |

## 10. Limitations and manual macOS checks

**Limitations:**

- **Real-model behaviour is unverified for this commit** (see the top of this document). After you run the
  live test, record the result. Model behaviour can change between runs and model versions. The live
  assertions are behavioural, but an occasional failure on the ambiguity or multi-step scenario reflects the
  model's judgement, not a security failure. The security properties are enforced by the runtime, whatever
  the model does.
- **Strict structured outputs** (`"strict": true` function schemas) are **not enabled.** They would need
  schema rewrites that could not be checked against the live API here. The runtime's own validation stays
  authoritative either way.
- The stub mimics OpenAI's documented wire format as handled by the pinned SDK. A future API change would only
  show up in the live test.
- Prices, model availability and rate limits depend on your account and can change.
- `client.py` (a standalone demo that the Phase 0 audit flagged for deletion) still hardcodes a model and
  makes a paid call when run. It is not imported by the app and was left untouched.
- Everything listed in M2.0 §12 and M1.2 still applies.

**Manual macOS checks** (not possible in CI or the Linux sandbox):

1. `uv run ultron doctor` shows OpenAI as configured.
2. Run the live test command and keep the summary.
3. In the HUD, ask "what's the weather and my battery level?". The `agent.steps` list should show
   `weather.current` and `system.status` completed, with `verified: true`.
4. Ask "open Safari". It runs (routine). Then ask "search the web for X and then open the first result". The
   open step should wait for **CONFIRM** in the HUD.
5. Say "Jarvis, what time is it and add a note that I called mum" through the wake word. The note waits for
   HUD approval only if the run read external content. Saying "confirm" is refused.
6. Turn off Wi-Fi and ask something new. You should get "unavailable" within about 20 s, and the HUD stays
   responsive.
