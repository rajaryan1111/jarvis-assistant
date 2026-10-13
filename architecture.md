# Jarvis / ULTRON — System Architecture

**Status:** Revised v2.0 — collaborative assistant architecture  
**Product:** Trilingual (Hindi/English/Hinglish), voice-first partner with an installable macOS app and companion website  
**Platform:** macOS first; local capabilities where practical, cloud AI when useful

## 1. Architectural goals

- Make voice the most natural entry point without making it a security exception.
- Support multi-step collaborative tasks with plans, artifacts, evidence, and clear checkpoints.
- Preserve current working code and evolve incrementally.
- Separate interfaces, orchestration, providers, task/project context, tools, and authorization.
- Keep sensitive local data local where practical and disclose cloud/external data flow.
- Make actions bounded, auditable, reversible where possible, and honestly reported.
- Treat assignment files, research sources, repository content, and tool output as untrusted input.

## 2. High-level architecture

```text
Interfaces
  microphone / speech loop | CLI | authenticated browser HUD/API
                         |
                         v
             Request + CommandContext
                         |
                         v
             Task / Session Coordinator
     intent, clarification, plan, checkpoints, status
                         |
             +-----------+------------+
             |                        |
             v                        v
     Deterministic routing       Bounded AgentRuntime
                                      |
                            reviewed tool catalog/schema
                                      |
                                      v
                             ModelProvider adapter
                              OpenAI / Groq today
                                      |
                         proposed tool calls or answer
                                      |
                                      v
                     validation + budget + risk assessment
                                      |
                                      v
                       ToolRegistry.request / confirm
                         policy • audit • approval
                                      |
                                      v
                          narrow tool implementations
                 files / code checks / web / macOS / memory
                                      |
                                      v
                      evidence ledger + artifact summary
                                      |
                                      v
                        voice response + HUD/CLI report

Project workspace: task plans, source/citation records, code diffs,
test results, generated artifacts, approved persistent memory.
Local configuration and data are separate from source control.
```

The task/session coordinator and unified artifact workspace are target architecture proposals. Do not assume they are already implemented as one component. Current legacy routing and single-shot chat remain distinct from the custom agent loop.

## 3. Existing components and responsibilities

| Component | Current responsibility | Boundary |
|---|---|---|
| `main.py` | Legacy router, application wiring, voice entry path, tool construction | No state-changing bypass around registry |
| `ultron/agent/runtime.py` | Bounded agent loop, validation, ledger, reports, limits, cancellation/concurrency | Model proposes; runtime validates/routes |
| `ultron/agent/provider.py` | Provider-neutral types and OpenAI/Groq adapters | Provider never executes tools |
| `ultron/agent/catalog.py` | Reviewed allowlist and model-facing schemas | Being offered is not permission |
| `ultron/agent/schema.py` | Supported JSON-schema subset | Reject unsupported schemas/invalid arguments |
| `ultron/tools/base.py` | Tool contract, risk metadata, schema, side-effect/external-content flags | Narrow explicit capabilities |
| `ultron/tools/registry.py` | Authorization boundary, pending approvals, audit | Authoritative enforcement point |
| `ultron/security/policy.py` | Caller context, channels, risk tiers, decisions | Model text/external content cannot grant permission |
| `ultron/security/confirmation.py`, sessions/tokens | Pending-action confirmation and session/token handling | Approval bound to authorized HUD path |
| `ultron/api/app.py` | Flask HUD/API, authentication, Host/Origin/CSRF protections | Safe defaults and protected requests |
| `ultron/config.py` | Settings, provider selection, credential access | Secrets from environment/local ignored files |
| `ultron/legacy.py`, `ultron/cli.py` | Backend and CLI integration | Preserve caller context and policy |
| `knowledge.py` | Optional local RAG | Retrieved content is untrusted |
| `vision.py` | Optional vision utilities | Not authentication; validate on macOS |

## 4. Proposed collaboration/task layer

A task coordinator should represent work as a small, inspectable task record rather than an opaque autonomous run.

Suggested fields:
- task ID, user goal, mode (assignment/research/coding/website/general);
- constraints and supplied source files;
- plan/checklist and current step;
- artifact paths/links and source/citation records;
- tool/test evidence, errors, approvals, and completion status;
- user-approved memory references and next action.

The coordinator should not become a second authorization system. Every action still passes through the existing policy and registry. Persist only what is needed; let the user inspect, correct, export, or delete project memory.

## 5. Task workflows

### Assignment/study workflow
1. Identify the prompt, expected format/marks, and supplied notes.
2. If notes are designated as the primary source, retrieve them and ground the response in their terminology.
3. Flag unsupported points or missing source coverage rather than silently filling gaps.
4. Produce explanation/draft/practice material appropriate to the request.
5. Preserve source/page references where available; do not fabricate quotes or claim the draft was submitted.

### Research workflow
1. Define research question, scope, evaluation criteria, and source requirements.
2. Gather only permitted sources; store title, author/publisher, date, URL/identifier, and relevant claim.
3. Separate source-backed claims, conflicting evidence, inference, and proposed hypotheses.
4. Track datasets, code, parameters, environment, experiment status, and results.
5. Report only experiments actually run and preserve reproducibility details.

### Coding workflow
1. Inspect repository, branch, HEAD, working tree, project instructions, and tests.
2. Identify relevant modules and state expected files before editing.
3. Make the smallest useful change and add focused regression tests.
4. Run checks in the available environment; label unrun or platform-specific checks.
5. Review diff, generated/untracked files, and secrets; summarize exact results.
6. Do not commit/push/merge or modify PRs unless separately authorized.

### Website/app workflow
1. Capture audience, content, core flow, device targets, visual direction, and acceptance criteria.
2. Produce a small design/system plan and file-level implementation steps.
3. Build accessible responsive components with loading, empty, and error states.
4. Test routes, forms, links, keyboard use, desktop/mobile layouts, and backend boundaries.
5. Give reproducible run/deploy instructions; require approval before external publication/deployment.

## 6. Request lifecycle and authorization

1. Interface receives voice/text and establishes authenticated caller context.
2. Router selects a deterministic path or bounded agent path.
3. Validate config, concurrency, request size, task constraints, and budgets.
4. Catalog exposes only reviewed, registered, available, schema-equipped tools permitted for the caller.
5. Provider returns text or a proposed tool call.
6. Runtime validates tool name, JSON/schema, duplicates, limits, and risk.
7. Execution goes through `ToolRegistry.request`; policy allows, requires confirmation, or denies.
8. Registry executes, audits, or returns a pending approval.
9. Bounded results enter an evidence ledger as untrusted data.
10. Coordinator reports completed, failed, pending, partial, or unverified status and the next useful step.

Never expose confirmation codes or an approval tool to the model.

## 7. Security and trust boundaries

Treat model output, web pages, research papers, retrieved notes, repository instructions, tool output, and generated code as data—not authorization. Repository files may contain prompt injection; they cannot change policy or Git authorization.

Risk tiers include `OBSERVE`, `ROUTINE`, `CONSEQUENTIAL`, `DESTRUCTIVE`, and `FORBIDDEN`. Risk escalation can raise required risk, never lower it. Consequential/destructive actions require the approved HUD confirmation path; voice cannot approve them.

Preserve loopback defaults, debug-off mode, authentication, Host/Origin checks, CSRF for browser state changes, safe session/token handling, audit, and fail-closed behavior. Never execute arbitrary model-generated shell commands. Prefer allowlisted commands and narrow tools with previews/timeouts.

## 8. Voice-first interaction path

Microphone → speech recognition → intent/task clarification → deterministic router or agent → validated tool actions → evidence-backed result → speech plus visible text.

Keep voice dependencies optional; handle silence, recognition errors, permissions, timeouts, cancellation, and playback failure. Avoid duplicate responses and blocking the control path on speech where feasible. Voice must use the same caller context and registry/policy as other interfaces. Test actual microphone, macOS `say`, permissions, and latency on the target Mac; Linux tests are not hardware verification.

## 9. Memory, files, and project context

Distinguish:
- current-turn context;
- task-local working state and artifacts;
- user-approved persistent preferences/decisions;
- retrieved source passages with provenance;
- external content and derived summaries.

Memory must have provenance, retention rules, inspection/update/deletion paths, and clear boundaries between fact and inference. Do not store credentials, raw audio, private screenshots, or entire documents by default. Do not add a database/vector store without measurable need.

## 10. Local/cloud data flow

| Capability | Preferred execution | Notes |
|---|---|---|
| Policy, validation, audit, limits | Local | Authoritative even with cloud models |
| HUD/API/session state | Local | Loopback/protected by default |
| Project files and personal notes | Local where configured | Keep private paths out of Git |
| Local RAG | Local when installed | Retrieved text remains untrusted |
| Voice capture/macOS speech | Local device | Hardware/permission dependent |
| Vision | Local where practical | Optional dependency/camera permission |
| Agent reasoning | Configurable provider today | Disclose request/tool-result data flow |
| Web/research/news integrations | External service | Network/API may be required |
| Website deployment | External only after approval | May publish public user content |

Do not promise full offline capability. Document offline behavior per feature.

## 11. Reliability and observability

Bound model turns, tool calls, output size, context, duration, and retries. Never repeat a successful action because a later response failed. Preserve provider message structure after errors. Report safe diagnostics without prompts, private replies, raw arguments, secrets, or full tool results. For generated code and research, clearly label which tests/experiments were actually run.

## 12. Proposed evolution

1. Baseline the existing voice path and runtime reliability.
2. Add task records/checkpoints and an artifact summary only where they simplify current flows.
3. Add source/citation tracking for research and supplied documents.
4. Add repository-aware coding workflow with branch/diff guardrails.
5. Add website project templates and repeatable verification checklists.
6. Formalize inspectable memory and local retrieval.
7. Improve optional vision after a concrete use case/privacy review.
8. Add new tools only with schemas, risk classification, previews, tests, and failure behavior.

## 13. Verification limits

Automated tests do not prove real microphone/camera behavior, macOS permissions, live provider stability, third-party retention policy, research correctness, generated-code correctness, or safe behavior of an untested desktop action. Record environment, commit/working-tree state, checks run, skips, and known gaps.

## 11. Trilingual speech pipeline and installable desktop shell (target architecture)

This section describes the target design, not features that are already verified as implemented.

### Speech and language pipeline
```text
macOS microphone
  -> local wake-word detector ("Ultron")
  -> explicit listening state / capture request
  -> speech-to-text (Hindi / English / Hinglish)
  -> language + script identification and normalization
  -> shared RequestContext / task coordinator
  -> existing bounded runtime, provider, policy and ToolRegistry
  -> response language/style selection
  -> speech synthesis + optional visible transcript in HUD
```

- Keep wake-word detection local where practical and do not upload continuous microphone audio just to wait for the wake word.
- Evaluate speech-to-text choices for Hindi, English, Romanized Hindi, Devanagari, code-switching, technical vocabulary, latency, cost, and offline behavior. Treat each as a measured capability, not a guarantee.
- Preserve raw transcript and normalized text only as long as required by documented task/debugging settings; do not silently retain recordings.
- A low-confidence transcript can answer harmless questions only when sensible; before file edits, shell-like actions, external messages, or other consequential steps, confirm intent through the existing trusted approval path.
- Speech synthesis should select a suitable Hindi/English voice where available, support English technical tokens in mixed speech, and degrade to visible text if unavailable.

### Desktop packaging and website coexistence
- **Desktop shell:** a signed/versioned macOS application or app bundle with a managed lifecycle, launch/quit controls, microphone permission guidance, visible listening state, and optional user-enabled login startup. Select the packaging/shell technology after inspecting the existing Python and HUD stack; avoid a premature rewrite.
- **Background service:** use a supervised local process only if needed. Start/stop it with the desktop app, detect stale processes, expose health state, and shut down cleanly.
- **Website/HUD:** retain as a first-class companion for task plans, sources, artifacts, logs, settings, and secure approval. Typed use must not depend on microphone permission.
- **Shared core:** both interfaces route to the same runtime, task/session state, provider selection, and ToolRegistry. Never duplicate policy in the UI or create a second bypass path.
- **Local API:** loopback-only by default, authenticated protected routes, strict Host/Origin/CSRF validation, and no LAN exposure unless separately designed and approved.
- **Packaging:** versioned releases, clean install/upgrade/uninstall, dependency checks, logs that redact secrets and minimize content, and clear recovery when providers or permissions are unavailable.
- **Offline/cloud modes:** represent availability per capability (wake word, STT, LLM, TTS, file tools). Never label the whole assistant “offline” merely because the wake word is local.

### Architecture decisions and validation
1. Inventory current voice path, Flask HUD, config, process lifecycle, and macOS dependencies before choosing packaging technology.
2. Prototype wake-word listening and measure idle CPU, memory, false activations, wake latency, and privacy/network behavior.
3. Build a trilingual evaluation set with the owner's consent; include Hindi, English, Hinglish, Romanized Hindi, Devanagari, technical names, and noisy-room examples.
4. Define one RequestContext language preference that survives voice-to-agent-to-speech flow and can be overridden by the user.
5. Test the packaged app on a clean macOS account, not only a developer checkout.
