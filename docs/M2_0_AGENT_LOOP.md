# M2.0 — The core agent loop

M2.0 lets JARVIS handle a request that needs **more than one step**. For example, *"what's the weather
in Pune, and remind me at 6 pm to take an umbrella if it will rain"* is handled by letting a language
model choose tools one at a time, look at the real results and then report back. Every tool call goes
through the security boundary from M1.1/M1.2 (`ToolRegistry.request`). The model can **propose**
actions. It cannot approve them, raise its own permissions or run code.

It is a small custom loop (`ultron/agent/`, under 1 000 lines including docstrings). There is no LangGraph, no multi-agent
framework and no new dependency. It uses the `openai` SDK that was already locked (3.29.0).

## Contents

1. [What was there before](#1-what-was-there-before)
2. [Lifecycle of one run](#2-lifecycle-of-one-run)
3. [Modules](#3-modules)
4. [Tools: description, selection, validation, execution](#4-tools)
5. [Authorization and confirmation](#5-authorization-and-confirmation)
6. [Untrusted content and prompt injection](#6-untrusted-content-and-prompt-injection)
7. [Limits](#7-limits)
8. [Errors, uncertain outcomes and completion verification](#8-errors-uncertain-outcomes-and-completion-verification)
9. [Integration (voice, HUD/API, CLI, legacy router)](#9-integration)
10. [Configuration](#10-configuration)
11. [Tests and verification](#11-tests-and-verification)
12. [Limitations and deferred work](#12-limitations-and-deferred-work)

## 1. What was there before

Checked in the source at `42048eb` before any coding:

- **LLM use was single-shot.** `ask_gpt` sent one chat-completions request with no tools. Routing was
  the keyword router in `main.handle_command`. There was no agent code anywhere in the repository.
- **The registry was already the only way to act.** Since M1.1, every action is a `Tool` with a `RiskTier`.
  `ToolRegistry.request(name, args, ctx)` runs policy → confirmation → audit → execution, and a static
  test ensures entry points never call the implementations directly. M2.0 reuses this as is.
- **Gap:** tools had no machine-readable argument schemas, and most read-only answers (time, weather,
  news, ...) were not registry tools at all. M2.0 adds both (see §4).
- **Structured tool calling:** the locked `openai` 3.29.0 supports chat-completions `tools`,
  `tool_choice`, `parallel_tool_calls` and per-request `with_options(timeout=, max_retries=)`. That is
  the provider path used here.

## 2. Lifecycle of one run

```text
objective + caller's CommandContext
  │
  ├─ refuse if: no provider · objective too long · too many concurrent runs
  ├─ catalog = tools this caller may propose (allowlist ∩ registered ∩ has schema ∩ policy ≠ DENY)
  │
  └─ loop (≤ max_steps model turns; deadline; cancel flag checked before/after every model call)
       model turn ──► plain answer ────────────────► verify against ledger ─► report
                 ├──► ask_user(question) ───────────► report "needs_input"
                 ├──► finish(outcome, summary, evidence) ─► verify ─► report
                 └──► tool call
                        1. JSON object?  known tool in *this* catalog?  schema valid?
                        2. not an identical repeat?  not an uncertain tool?  budget left?
                        3. ToolRegistry.request(tool, args, ctx+agent_run, minimum_risk)
                             (policy · confirmation · audit · execution, with a timeout)
                        4. record the real result in the ledger (c1, c2, ...)
                        5. pending approval / denial  ─► stop and report
                           otherwise give the result back to the model as bounded, labelled data
```

Planning and execution are kept apart. The model only produces proposals (`ModelTurn`). The runtime
alone decides whether a proposal is executed, and only by calling the registry.

## 3. Modules

| Module | Responsibility |
|---|---|
| `ultron/agent/schema.py` | A small JSON-Schema subset: `check_schema` (rejects unsupported schemas when the catalog is built) and `validate` (checks the model's arguments) |
| `ultron/agent/provider.py` | Provider-neutral types (`Message`, `ToolCall`, `ModelTurn`, `ToolDefinition`, `ProviderError`) and `OpenAIChatProvider`, the only OpenAI-specific code |
| `ultron/agent/catalog.py` | `DEFAULT_AGENT_TOOLS` allowlist, `build_catalog` (what this caller may be offered), the `finish`/`ask_user` control tools |
| `ultron/agent/runtime.py` | `AgentRuntime` (the loop), `AgentLimits`, `AgentReport`/`StepRecord`, `RunRegistry` (cancel + concurrency cap), `ToolExecutor` (timeouts, bounded threads) |
| `ultron/tools/base.py` | `ToolSpec.parameters` (argument schema) and `ToolSpec.external_content` (result is untrusted outside text) |
| `ultron/tools/registry.py` | adds `decision_for`, `is_available`, `request(minimum_risk=)`; agent-caused pending actions are delegated; audit log has `agent=<run id>` |
| `ultron/security/policy.py` | `CommandContext.agent_run`, `Channel.CLI`, `can_confirm` is false for agent contexts |
| `ultron/config.py` | `AgentConfig` / `load_agent_config` (bounded `ULTRON_AGENT_*` settings) |
| `main.py` | 8 new read-only registry tools; `build_agent`, `run_agent`, routing and cancel |
| `ultron/legacy.py`, `ultron/api/app.py`, `ultron/cli.py` | integration (§9) |

The runtime depends only on `ModelProvider.complete(messages, tools, *, timeout, max_output_tokens)`.
A different provider only needs another adapter, and the loop does not change.

## 4. Tools

**Description.** A tool's `ToolSpec` gives the model everything it sees: the name (`apps.open` is shown as
`apps__open`, since providers do not allow dots), the description with a note on its risk and whether it
returns outside content, and the argument schema. Every object schema must set
`additionalProperties: false`, and every string must have a `maxLength`.

**Selection is dynamic.** The model picks a tool from those descriptions. There is no phrase table in
the agent code. `test_a_newly_registered_tool_is_selected_by_its_description_without_phrase_tables`
registers a new tool and shows that it is offered and used with no other change.

**Which tools are offered (M2.0).** A tool is offered only if all of the following are true:

1. It is on `DEFAULT_AGENT_TOOLS`.
2. It is registered and available on this OS.
3. It has a schema.
4. The policy would not deny it outright for this caller.

| Tier | Offered tools |
|---|---|
| OBSERVE (read-only) | `time.now`, `system.status`, `system.cpu_temperature`, `reminders.list`, `memory.list_notes`, `web.search`*, `wiki.summary`*, `weather.current`*, `news.headlines`* |
| ROUTINE (reversible) | `memory.add_note`, `reminders.add`, `apps.open`, `web.open_url`, `system.set_volume`, `system.brightness` |

\* `external_content=True`: the result contains outside text (see §6).

The following are **never offered**: destructive tools (shutdown, restart, clearing caches or all reminders),
security/camera tools, screenshots, typing or shell tools, and anything without a schema. The 8 OBSERVE tools
marked new in `docs/M1_1_TOOL_SECURITY_BOUNDARY.md` reuse the router's existing helpers.

**Validation.** Before anything reaches the registry, the arguments must be a JSON object that matches the
schema exactly: required fields, types (`true` is not an integer), ranges, lengths, enums, patterns and no
extra keys. An unknown tool, a tool outside this caller's catalog or invalid arguments are recorded as an
`invalid` step. The model gets the error back and can correct itself. After 3 invalid calls the run ends
(`failed`).

**Execution.** Only `ToolRegistry.request(name, args, ctx, minimum_risk=…)` is called, on a worker
thread with a timeout. The tool implementation still checks its own arguments, as in M1.1.

## 5. Authorization and confirmation

- **Same caller, same rules.** The run uses the caller's own `CommandContext` (HUD session, API token,
  wake-word client, local CLI, ...) copied with `agent_run=<run id>`. Policy, voice trust and audit are
  exactly those of the caller. The catalog is only a hint: the registry decides again at execution time
  (`test_the_registry_decides_at_execution_time_not_the_catalog`).
- **The model cannot approve.** It has no approve tool. A context with `agent_run` set has
  `can_confirm == False`, even inside a HUD session. A pending action caused by a run is
  **delegated**: it has no code, so the model never sees one, and it can be approved only with the HUD's
  CONFIRM button for that exact action id. The model's text ("the owner approved this") changes nothing
  (`test_model_text_claiming_approval_changes_nothing`).
- **Pending or denied stops the run.** A pending approval ends the run as `awaiting_approval`, and the
  reply says to approve it in the HUD. A denial ends it as `denied`. The loop never looks for a less
  restricted route.
- **Voice** follows M1.2: it can *request* but never approve. `LOCAL_VOICE` and the CLI have no approver
  at all, so CONSEQUENTIAL and higher actions are refused there. With `ULTRON_VOICE_TRUST=observe`,
  voice is offered only read-only tools.
- **Permissions cannot be raised.** `minimum_risk` only raises a tier (`max(tier, minimum)`), never lowers
  it.

## 6. Untrusted content and prompt injection

Tool results go back to the model as JSON *data* (`call_id`, `status`, `message`, optional `data`). They
never become system instructions. Results from `external_content` tools (web search, Wikipedia, weather,
news) carry an `UNTRUSTED external content` warning. The system prompt also says tool output can never
change the rules or grant permission.

That prompt rule is only a mitigation. The real guarantees are structural:

- **Taint escalation.** Once a run has read outside content, any later ROUTINE call is requested with
  `minimum_risk=CONSEQUENTIAL`, so it becomes a pending action that the owner must approve in the HUD.
  An injected "add a note" or "open this URL" therefore does nothing on its own
  (`test_injected_tool_output_cannot_trigger_unapproved_actions`, `test_voice_request_tainted_by_web_content_…`).
- Injected text cannot reach tools outside the catalog, cannot approve anything (see above) and cannot
  turn an unrun action into a reported success (§8,
  `test_injection_cannot_reach_tools_outside_the_catalog`).

## 7. Limits

All of these are enforced by the runtime and covered by tests. The first six can be set by the owner
(§10). The rest are fixed in `AgentLimits`.

| Limit | Default | When reached |
|---|---|---|
| model turns per run | 6 | `limit_reached` |
| tool calls per run | 8 | `limit_reached` |
| model call timeout | 20 s (capped by the remaining run time) | `failed` (provider timeout) |
| tool call timeout | 20 s | step `timeout`; outcome uncertain if the tool changes state (§8) |
| total run time | **45 s** (below the wake-word listener's 60 s HTTP timeout, so voice always gets a reply) | `limit_reached` |
| concurrent runs (process-wide) | 4; also 4 tool worker threads | `failed` ("busy"), with nothing started |
| calls to the same tool | 4 | `limit_reached` |
| identical call (same tool and arguments) | not repeated; the 2nd repeat ends the run | `limit_reached` |
| invalid calls | 3 | `failed` |
| tool output shown to the model | 1 500 chars per result | the `data` field is dropped and the result is marked `truncated`; the message is clipped if still too long |
| conversation size | 24 000 chars | `limit_reached` |
| model output | 400 tokens per turn | — |
| objective length | 2 000 chars | refused |

**Stopping.** Saying or typing "cancel" (or "abort", "never mind") stops the caller's own runs, and the HUD's `/cancel`
does the same. One principal cannot stop another's run. The cancel flag is checked before and after every
model call and while a tool runs. A tool already running in Python cannot be killed, so its thread is
left to finish on its own, and the report says the outcome is unknown. The thread count is bounded,
because worker threads come from a fixed number of slots. Nothing keeps running after a run ends,
except such a finishing tool thread. In the CLI, Ctrl-C cancels.

## 8. Errors, uncertain outcomes and completion verification

**Nothing crashes the caller.** The following all end in a report with status `failed` and a plain
explanation, never an exception:

- provider errors (timeout, unavailable, rejected, not configured, malformed response)
- an empty model turn
- an exception raised by a tool
- an internal bug in a run

Provider error messages never include response bodies, keys or headers
(`test_openai_adapter_maps_errors_without_leaking_details`).

**No silent retries.** The OpenAI client is called with `max_retries=0`. A failed call is shown to the
model as a failure, and an identical call is never repeated. If a tool that **changes state** times out,
is cancelled or raises, its outcome is *uncertain*. The run then refuses that tool for the rest of the run
and tells the user to check, rather than doing it twice. Destructive tools are not offered at all.

**Completion is verified against the ledger, not the model's word.** Every executed call is recorded as
a `StepRecord` (`c1`, `c2`, ...) with its real status. When the model finishes with `outcome="achieved"`,
or answers in plain text after using tools, the runtime checks the following:

- every cited evidence id exists and its step **succeeded**;
- no state-changing step failed, was denied or has an uncertain outcome;
- no attempted call was left un-run (unknown tool, invalid arguments, refused) without a later successful call to the same tool;
- at least one tool call actually succeeded.

If any check fails, the status becomes `incomplete`. The reply then says *"I couldn't verify that this was
done: …"* and lists the specific problems, instead of repeating the model's claim. `partial` and
`not_achieved` outcomes are reported as `incomplete`. A plain answer with no tools is `answered`
(`verified: null`). The system prompt also asks the model to check an action with a read-only tool
(e.g. list reminders after adding one) before finishing.

## 9. Integration

There is still only one way to act: every entry point uses the same `TOOLS` registry, policy and
confirmation store.

- **Voice and router (`main.handle_command`).** Legacy commands still go through the keyword router
  first, with unchanged behaviour. A request the router does not recognise goes to the agent when it
  is enabled and `OPENAI_API_KEY` is set. Otherwise it goes to the old single-shot chat (`ask_gpt`).
  The prefix `agent: …` forces the agent. Sleep mode still blocks everything except wake-up.
- **HUD/API `POST /ask`.** Takes `mode` (`auto`, the default, or `agent`), with the same auth, CSRF and
  origin checks as before. The JSON adds `agent` (`status`, `verified`, `run_id`, `steps`, `problems`),
  plus `pending_action` when approval is needed. `POST /cancel` also stops the caller's running tasks
  and returns `stopped_tasks`. The HUD layout is unchanged. Its existing CONFIRM card approves an
  agent-proposed action by id.
- **Wake-word listener.** It sends requests with the limited client token as before. Runs use the
  wake-word context (voice rules, no approver).
- **CLI.** `uv run ultron agent "what time is it and how is my battery?" [--steps]` runs as
  `Channel.CLI` (the OS user at this shell, with no approver attached). Read-only and routine tools work.
  Anything that needs approval is refused, with a pointer to the HUD. Ctrl-C cancels.
- **Isolation.** Each run has its own messages, ledger, catalog, budget, cancel flag and context.
  Approvals stay tied to the requesting principal (`test_concurrent_runs_keep_contexts_results_and_approvals_separate`).
  No conversation history is shared between runs (§12).
- **Logs.** `ultron.agent` logs run ids, tool names, statuses and counts only. It never logs objectives,
  arguments, results, keys or tokens (`test_agent_logs_never_contain_objectives_arguments_or_results`).
  The registry audit line shows `agent=<run id>`.

## 10. Configuration

All settings are optional environment variables (or `.env`). Invalid values fall back to the default
with a warning. Values outside the range are clamped.

| Variable | Default | Range / meaning |
|---|---|---|
| `ULTRON_AGENT` | on | set to `0` (or anything other than `1`/`true`/`yes`/`on`) to disable the agent. Unknown requests then use single-shot chat |
| `ULTRON_AGENT_MODEL` | `gpt-4.1-mini` | model id; letters, digits and `._:/-` only, ≤ 100 chars. Must support tool calling |
| `ULTRON_AGENT_MAX_STEPS` | 6 | 1–20 |
| `ULTRON_AGENT_MAX_TOOL_CALLS` | 8 | 1–30 |
| `ULTRON_AGENT_MODEL_TIMEOUT` | 20 | 2–120 s |
| `ULTRON_AGENT_TOOL_TIMEOUT` | 20 | 1–120 s |
| `ULTRON_AGENT_MAX_SECONDS` | 45 | 5–600 s (keep it under 60 s if you use the wake-word listener) |
| `ULTRON_AGENT_MAX_CONCURRENT` | 4 | 1–8 |

The model needs `OPENAI_API_KEY`, which is loaded as in M1. Without it the agent reports that it is
unavailable, and `ultron agent` exits with code 2.

## 11. Tests and verification

`tests/test_agent_loop.py` (100 tests) uses a scripted fake model and fake tools only. It makes no
network calls (an autouse fixture fails any outbound request), uses no keys, performs no shutdowns and
touches no user files. Test sections, by requirement:

| Requirement | Tests (section in the file) |
|---|---|
| deterministic multi-step objective, several tools, verified | §1 `test_multi_step_objective_succeeds_with_dynamically_selected_tools` |
| dynamic selection from descriptions/schemas | §2 |
| invalid tool names, malformed arguments, malformed `finish` | §3, §11 |
| authorization before execution, denial stops the run | §4 |
| sensitive actions use HUD approval; the agent cannot approve | §5, `test_agent_context_cannot_confirm_even_from_the_hud` |
| bypass attempts, prompt injection in tool results | §6/7 |
| tool, provider and timeout errors | §8 |
| step/call/time/context limits, repeats, loops, concurrency cap | §9 |
| cancellation (runtime, spoken, API) | §10 |
| honest reporting of incomplete/unverified work | §11 |
| concurrent runs isolated | §12 |
| router, API (auth kept), wake word, CLI integration | §13 |
| OpenAI adapter (fake client), schema, config, logging | §14–15 |

`tests/test_policy_boundary.py` now also statically checks `ultron/agent/*.py`. They must never
reference `subprocess`, `os.system`, `webbrowser`, `vision.`, `save_memory` or a raw `_tool_`
implementation.

**Mutation testing.** I broke each check by hand and ran the suite. All **30** mutants of the agent checks
were caught:

- catalog membership and policy filter
- argument and `finish` validation
- taint (set and escalation)
- uncertain outcomes
- duplicate, invalid, total and per-tool limits
- the deadline and cancel checks
- denial and pending stop the run
- verification (problems ignored, unrun attempts, failed evidence)
- the `agent_run` marker
- `can_confirm` for agents, delegated pending actions and button-only approval
- `minimum_risk` honoured
- CLI has no approver
- `bool` is not an integer
- provider `max_retries=0` and `parallel_tool_calls=False`

The M1.2 set (24) was re-run against the new code and all 24 were caught again.

## 12. Limitations and deferred work

- **Not verified with a real model.** All tests use a scripted model. How well a real model follows the
  rules, picks tools and cites evidence was not measured. The safety properties do not depend on it,
  since the runtime enforces them, but answer quality does. Models without tool calling get `rejected`.
- **macOS not verified here.** The tests ran on Linux (Python 3.11 locally, 3.10–3.13 in CI). The macOS
  tools (`apps.open`, volume, brightness) were exercised with a fake runner only.
- **No memory between runs.** Each run starts fresh. Follow-up questions ("and tomorrow?") are not
  linked to the previous run. Persistent conversation state is deferred.
- **Tool calls run one at a time** (`parallel_tool_calls=False`), which is slower but easier to audit.
- **Taint is coarse.** After any outside content, every routine action in that run needs HUD approval.
  Over voice this means such multi-step requests usually end with "approve it in the HUD".
- **A running tool cannot be killed.** After a timeout or cancel, its thread may still finish, and the
  action may still happen. The run reports this as uncertain and never repeats it.
- **Pending actions are in memory** (as in M1.2) and are lost on restart.
- **The model sees tool results.** That includes your notes and reminders when it lists them, which are
  sent to the configured OpenAI model. Voice and router answers already did this for plain chat.
- Everything in the M1.2 limitations still applies: an attacker who controls the same OS account is out
  of scope, routine voice actions run for any speaker, and the face check is not authentication.
- **Deferred to later milestones:** conversation memory, more tools (each with schema, tier and tests),
  streaming/progress in the HUD, other providers, persistent pending actions, and approval flows on
  macOS (Touch ID).
- **Out of scope by design:** GUI automation, shell or terminal tools, arbitrary file writes, background
  autonomous operation and multi-agent orchestration.
