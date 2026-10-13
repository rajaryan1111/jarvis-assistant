# Jarvis / ULTRON — System Architecture

**Status:** Baseline architecture and target direction v1.0  
**Basis:** Repository source/docs on `m2.2.1-groq-fixes` (PR #3 head); this document creation does not modify GitHub  
**Direction:** Balanced ULTRON; local capabilities where practical, cloud AI when useful; voice first

## 1. Goals
- Preserve working behavior and evolve incrementally.
- Separate voice, HUD, CLI, agent orchestration, tools, and provider implementations.
- Keep the central registry/policy as the only authorized route to state-changing operations.
- Keep local data and optional hardware features local where practical.
- Make failures bounded, observable, and honest.
- Avoid framework migrations without evidence of benefit.

## 2. High-level architecture

```text
Interfaces: microphone/main.py voice loop | ultron CLI | browser HUD/Flask API
                                  │ request + CommandContext
                                  ▼
Routing/application integration: main.handle_command | ultron.legacy/API backend
                    ┌─────────────┴────────────────┐
                    ▼                              ▼
        deterministic commands             AgentRuntime (bounded loop)
                    │                              │ catalog + schemas
                    │                              ▼
                    │                    ModelProvider adapter
                    │                    OpenAI / Groq; proposals only
                    └──────────────┬───────────────┘
                                   ▼
                     ToolRegistry.request / confirm
                     policy • confirmation • audit
                                   ▼
                     Narrow tool implementations
              macOS/system • reminders • memory • web/API integrations

Local config/data: ultron.config, session state, memory files, local RAG index
Optional hardware: microphone, speakers, camera, wake-word model
External services: selected LLM provider and configured integration APIs
```

This is a logical map, not a claim that all interfaces use identical internal call stacks. Legacy command routing and single-shot chat remain distinct from the agent loop.

## 3. Components and boundaries
| Component | Responsibility | Boundary |
|---|---|---|
| `main.py` | Legacy router, application wiring, voice entry path, tool construction | No state-changing bypass around registry |
| `ultron/agent/runtime.py` | Bounded agent loop, validation, ledger, reports, limits, cancellation/concurrency | Model proposes; runtime validates/routes |
| `ultron/agent/provider.py` | Provider-neutral types and OpenAI/Groq adapters | Provider never executes tools |
| `ultron/agent/catalog.py` | Reviewed allowlist and model-facing schemas | Being offered is not permission |
| `ultron/agent/schema.py` | Supported JSON-schema subset | Reject unsupported schemas and invalid arguments |
| `ultron/tools/base.py` | Tool contract, risk metadata, schema, side-effect/external-content flags | Narrow explicit capabilities |
| `ultron/tools/registry.py` | Authorization boundary, pending approvals, audit | Authoritative enforcement point |
| `ultron/security/policy.py` | Caller context, channels, risk tiers, decisions | Model text/external content cannot grant permission |
| `ultron/security/confirmation.py`, sessions/tokens | Pending-action confirmation and session/token handling | Approval is bound to authorized HUD path |
| `ultron/api/app.py` | Flask HUD/API, auth, Host/Origin/CSRF protections | Protected requests; safe defaults |
| `ultron/config.py` | Settings, provider selection, credential access | Secrets from environment/local ignored `.env` |
| `ultron/legacy.py`, `ultron/cli.py` | Backend and CLI integration | Preserve caller context and policy |
| `ultron/tools/macos.py`, `ultron/tools/system.py` | Narrow desktop/system actions | Platform checks, risk, previews, confirmations |
| `vision.py` | Optional vision utilities | Not authentication; validate on macOS |
| `knowledge.py` | Optional local RAG | Retrieved content is untrusted |
| `news.py` and integration helpers | External data | Handle failures, credentials, and returned content safely |

## 4. Request lifecycle
1. Interface receives voice/text and establishes `CommandContext`.
2. Deterministic routing handles recognized legacy commands.
3. If agent routing is selected, validate configuration and size/concurrency constraints.
4. Catalog offers only allowlisted, registered, available, schema-equipped tools not denied for the caller.
5. Provider returns text or a proposed tool call.
6. Runtime validates name, JSON, schema, duplicates, budgets, and risk escalation.
7. Execution goes through `ToolRegistry.request`; policy allows, requires confirmation, or denies.
8. Registry executes, audits, or returns a pending approval.
9. Bounded tool results enter the ledger as untrusted data.
10. Runtime returns evidence-based status or a failure/limit/cancellation report.

The model must not receive confirmation codes or an approval tool.

## 5. Security model
**Trust boundaries:** authenticated caller context is authorization state; model output, web/news/wiki results, retrieved documents, tool output, and arbitrary text are untrusted data. External providers receive request data required for the current operation; their retention behavior must not be assumed. Private local data must stay out of version control.

The current design uses risk tiers such as `OBSERVE`, `ROUTINE`, `CONSEQUENTIAL`, `DESTRUCTIVE`, and `FORBIDDEN`. Policy decisions occur outside the model. Protected actions use the approved HUD confirmation path; voice cannot approve its own pending high-impact action. Risk escalation may raise required risk, never lower it.

The documented HUD/API defaults to loopback and debug off, with protected endpoints, Host/Origin checks, CSRF for browser state changes, and fail-closed behavior when auth is not configured. Do not weaken these controls.

## 6. Provider architecture
`ModelProvider` separates runtime logic from provider details. Current adapters use the OpenAI SDK-compatible chat-completions interface:
- OpenAI adapter uses configured OpenAI client/credentials.
- Groq adapter uses a fixed Groq endpoint and Groq credentials.

Adapters return proposals, usage, finish reasons, and normalized errors; they do not execute tools. Provider choice must be explicit, credentials isolated, and arbitrary errors must not trigger broad retries. Use scripted providers/stubs for deterministic tests; live tests are opt-in and separately reported.

## 7. Voice-first path
Voice is the first interface to optimize, not an exception to security. Keep microphone dependencies optional; handle empty input and recognition errors; speak useful answers when voice interaction expects it; preserve the same context/registry path; support safe cancellation where possible; avoid blocking control paths on speech playback where feasible. Spoken confirmation must never approve consequential/destructive pending actions.

Trace and test the actual voice path on macOS before refactoring. Linux tests cannot verify microphone permissions, macOS `say`, camera, or system automation.

## 8. Memory and retrieval
Current memory/RAG features are optional/local components, not a general autonomous memory service. Future work should distinguish explicit user notes/preferences, run-local context, retrieved passages, external content, and derived summaries. Define provenance, retention, inspection, and deletion. Retrieved content cannot authorize actions or override security policy. Do not add a database/vector store without a measurable requirement.

## 9. Local/cloud boundaries
| Capability | Preferred execution | Notes |
|---|---|---|
| Policy, validation, audit, limits | Local | Authoritative even with cloud models |
| HUD/API/session state | Local | Loopback/protected by default |
| Reminders and user memory | Local where configured | Respect private paths |
| Local RAG | Local when installed | Retrieved text remains untrusted |
| Voice capture and macOS speech | Local device | Hardware/permission dependent |
| Vision | Local where practical | Optional dependency/camera permission |
| Agent reasoning | Configurable cloud provider today | Disclose request/tool-result data flow |
| Weather/news/web integrations | External service | Network/API may be required |

Do not promise full offline capability. Specify and test offline behavior per feature.

## 10. Reliability and observability
Bound model turns, tool calls, tool output, context, duration, and token usage. Keep retries narrow and error-specific. Never repeat a successful action because a later model turn failed. Preserve valid message structure after provider rejection. Report safe stop/error/usage/timing diagnostics without prompts, replies, arguments, keys, or tool results. Incomplete/unverified work must not be marked successful.

## 11. Target evolution (proposed, not all implemented)
1. Trace and baseline voice latency, recognition, cancellation, and speech output.
2. Add an interaction/session layer only if it reduces duplication without weakening authorization.
3. Formalize memory provenance, retention, and deletion.
4. Add voice UX and end-to-end tests using fakes; hardware smoke tests on the actual Mac.
5. Expand vision only after a concrete use case and privacy review.
6. Add tools only with schemas, risk classification, previews, policy tests, and failure recovery.

## 12. Decision rules
Do not introduce LangGraph, multi-agent orchestration, a database, or a new voice stack without a written problem statement and measured benefit. Never replace registry/policy with model instructions or UI-only checks. Keep runtime provider-neutral, platform operations behind tool interfaces, and deterministic routing unchanged unless a scoped change intentionally alters it. Document external data flows and secret requirements.

## 13. Verification boundaries
Automated tests do not prove real microphone/camera behavior, macOS permissions, live voice latency, provider stability under all conditions, third-party retention policy, or safety of an untested desktop action. Record environment, commit/working-tree state, checks run, and skips.