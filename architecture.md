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

The task/session coordinator and unified artifact workspace are target architecture proposals. Do not assume they are already implemented as one component. Current legacy routing and single-shot chat remain distinct from the custom agent loop. The student-life modules below are also proposed workflows until implemented and tested.

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

## 12. Student-life companion architecture (target proposal)

This expands the task layer into bounded, user-controlled workspaces. It is a proposed architecture; it does not assert that the current code already implements these services.

### 12.1 Context/workspace model

A task belongs to a workspace type such as course, project, hackathon, career, research, life-admin, or private reflection. Suggested records:

- **Workspace:** ID, type, user-visible name, approved members (if collaboration is later supported), retention policy and references to source folders.
- **Task:** goal, due date if supplied, priority, estimate, state, owner, dependencies, next action and cancellation state.
- **Artifact/evidence:** file or URL, provenance, generated/modified status, test or measurement evidence and verification level.
- **Decision:** question, options considered, user decision, timestamp and rationale when the user elects to save it.
- **Reminder:** user-approved message, schedule, timezone, enabled/disabled state and cancellation.
- **Memory item:** explicit user-approved preference or project fact, source/provenance, scope, retention and deletion status.

Personal reflection, relationship conversations and well-being notes must be separate from course/team workspaces by default. Do not copy private conversation text into project memory automatically.

### 12.2 Domain workflows

- **Study:** ingest approved notes/questions, preserve source/page references, map syllabus topics, create a study plan and practice loop.
- **Hackathon:** capture challenge, deadline, budget, team skills, constraints, MVP, owners, dependencies, blockers, demo path and evidence. Team collaboration requires explicit sharing and access control.
- **CSE/ECE projects:** inspect repository/configuration and relevant circuit/datasheet files; propose a small change; run available checks; record whether the evidence is static analysis, unit/integration test, simulation or physical measurement.
- **Career:** user-selected role goals, skill evidence, gap plan, resume drafts, interview sessions and an opportunity tracker. No fabricated achievements or guaranteed outcomes.
- **Personal support:** allow modes such as listen, reflect, plan, or draft a message. Keep the content in a private workspace and avoid unsolicited diagnosis or emotional-state inference.
- **Life admin:** user-approved reminders, calendars and budgets with clear data sources, timezones, assumptions and cancellation.
- **Research:** source registry, claim/source links, literature matrix, dataset provenance, environment/parameters and experiment evidence.

### 12.3 Cross-cutting services

- **Language policy:** carry detected language/script and user preference in RequestContext; support Hindi, English, Hinglish, Romanized Hindi and Devanagari across input, reasoning context and speech/text output.
- **Planning:** deterministic task records and explicit checkpoints; avoid turning a life conversation into an action plan unless requested.
- **Memory/retrieval:** scope retrieval to the active workspace; distinguish user-approved memory from untrusted documents and transient chat.
- **Notifications:** only user-configured reminders; make schedules visible, editable and cancellable.
- **Evidence ledger:** record what was actually read, changed, tested, measured or sent. A generated explanation is not proof of an external result.
- **Authorization:** every consequential action still passes through the central ToolRegistry and risk policy; the UI and model cannot grant themselves permission.
- **Privacy controls:** inspect/export/delete by workspace, mute voice, disable optional check-ins, and clear transient data.

### 12.4 Student safety and trust boundaries

Personal support is not a separate privileged agent. It uses the same access controls but stricter data minimization. No automatic sentiment labels or permanent mood histories. No emotional manipulation, exclusivity claims, stalking/surveillance assistance, or inference of another person's hidden feelings. For persistent/severe distress, provide supportive guidance toward trusted people or professional resources; do not diagnose.

Budget tools must show assumptions and arithmetic and never purchase or transfer money without explicit authorization. Academic and research tools preserve citations, source fidelity, and actual experiment status. ECE/hardware workflows explicitly label simulation versus physical verification.

### 12.5 Architecture rollout

Begin with local, inspectable task/workspace records and user-approved memory; then add study and project workflows; next reminders and opportunity tracking; then optional private support modes. Do not introduce calendars, email, finance, health, or social integrations until their permissions, retention, cancellation and failure behavior are defined and tested.

## 14. Local-first multi-tool project orchestration (target architecture)

### 14.1 Core design
Add a Project Orchestrator above the shared runtime and central ToolRegistry. It coordinates local repositories and external coding services but does not give an LLM unrestricted shell, filesystem, browser or account access. Every tool uses typed schemas, scoped permissions, risk classification, timeouts, output limits and audit records.

Target flow:
1. Local wake-word/speech layer receives a command and resolves language/intent.
2. Context resolver identifies active project, repository, branch, task and autonomy level; it asks if the target is ambiguous.
3. Planner returns a structured plan: objective, steps, tools, expected changes, risks, verification and rollback.
4. Policy engine classifies each action and checks workspace scope, credentials, approval and data-sharing rules.
5. ToolRegistry dispatches to local adapters or approved external connectors.
6. Evidence collector stores bounded records of commands, exit codes, changed paths, diffs, tests, provider calls and approvals.
7. Mentor/reviewer interprets evidence, challenges assumptions and explains consequences.
8. HUD presents progress, provider boundaries, diffs, approvals and handoff.

The model proposes; deterministic policy authorizes; adapters execute; evidence supports claims. No model response can grant itself permission.

### 14.2 Adapter registry
Use a versioned interface for adapters such as:
- Local workspace/filesystem and Git: status, diff, branch, commit preparation and history; external writes gated.
- Shell and test runner: allowlisted commands, working-directory confinement, timeouts, bounded output and environment sanitization.
- GitHub: repositories, issues, PRs, checks, review comments, branch/file operations, commits and pushes subject to scopes/approvals.
- Supabase: project metadata, schema inspection, SQL/editor workflows, logs and deployment only when explicitly supported and authorized.
- ChatGPT/Codex, Claude and Antigravity through supported APIs, CLIs, MCP endpoints or documented app integration surfaces.
- Documentation/search and browser workflows for services without suitable APIs, with visible browser state and human approval for consequential submissions.

Each adapter declares capability IDs, read/write status, authentication, minimum scopes, local/cloud processing, outbound data classes, rate limits, idempotency, timeout/retry policy, audit schema and test status. Do not imply a brand-name integration exists merely because its website opens. If a service lacks an official programmable interface, label the connector experimental and use a visible, authorized workflow; never bypass CAPTCHA, MFA, access controls or service restrictions.

### 14.3 Local execution and macOS security
- Keep coordinator, project index, policy engine and approval UI local by default; bind local service to loopback and authenticate local clients.
- Use macOS Keychain or equivalent secure credential storage. Redact credentials from outputs and logs. Never put secrets in prompts, generated docs, commits or persistent memory.
- Constrain workspace roots and deny traversal outside them unless separately permitted. Show active root, repository and branch in the HUD.
- Run risky commands in constrained processes with timeouts and bounded output. Do not let untrusted text bypass command allowlists.
- Treat prompt injection from README files, issues, webpages, dependency output and generated code as untrusted content; it cannot change authorization.
- Provide global stop/cancel, per-adapter disable, emergency mute, and clear indicators for microphone, camera, screen capture, browser and cloud model activity.

### 14.4 Project graph and evidence ledger
Model scoped records: Project, Repository, Workspace, Branch, Task, Plan, Decision, Artifact, CommandRun, TestRun, ProviderReview, Approval, IntegrationGrant and RiskNote. Link recommendations/changes to evidence and timestamps. Keep project memories separate; cross-project retrieval is explicit and visible.

For each task track requested outcome, baseline commit/status, plan version, files touched, commands actually run, tests passed/failed/skipped/not run, external data shared, approval events, resulting commit/PR/deployment IDs when available, limitations and next step. A green UI badge never substitutes for test evidence.

### 14.5 Senior-engineer mentor/reviewer
Combine repository context, task requirements, project constraints and verified evidence into an impact review. Identify alternatives, assumptions, complexity/cost/security/privacy/performance/maintenance trade-offs, failure modes, migration/rollback paths and confidence/evidence labels. Multiple providers may review a bounded task, but ULTRON must compare findings, expose disagreement and not treat model consensus as proof.

Review structure: recommendation; evidence; alternatives; expected benefits; risks/downstream consequences; verification plan; rollback; unanswered questions; next action. Prefer the smallest maintainable change that meets requirements unless the user chooses another trade-off. Distinguish advice from verified facts.

### 14.6 Consequence and approval engine
Use a deterministic risk matrix for read-only, local reversible, shared/external write and destructive/high-impact actions. Approval tokens bind to exact action, target, scope and plan version; plan changes invalidate approval. Voice can request or summarize approval but cannot silently approve high-impact actions. External writes show preview, destination, identity/account, effects and recovery options in the HUD.

Support dry-run/preview, idempotency keys where available, cancellation, compensating actions and rollback notes. Do not retry non-idempotent operations after uncertain outcomes without checking whether the first attempt succeeded. Production database changes require exact environment confirmation, backup/restore strategy, migration review and target identifier.

### 14.7 Multi-provider workflow
Allow bounded roles such as planner, implementation agent, code reviewer, test analyst or documentation reviewer. The coordinator owns the plan and source-of-truth state; providers receive only minimum relevant context. Avoid sending whole repositories by default. Show provider name, files/context sent, known/estimated cost where available, and local/remote processing. Do not let agents edit the same working tree concurrently without isolation and conflict resolution.

### 14.8 Rollout and verification
Start with local repository read-only analysis, project switching, impact plans and human-reviewed diffs. Next add safe local edits/tests; then GitHub reads and approval-gated writes; then individual provider adapters; then Supabase staging workflows; finally scoped browser automation. Every adapter needs mocked contract tests and real integration smoke tests when credentials are available. Mark capabilities planned, prototype, verified or unavailable, with date and tested version.

## 15. External speech components and adapter boundaries — research proposal (2026-10-13)

Keep the voice pipeline modular rather than adopting an external desktop application as ULTRON's core:

1. **Audio capture / VAD:** local capture, visible listening state, explicit mute/stop/cancel, bounded recording duration, and silence handling.
2. **STT adapter:** a stable internal contract returning transcript text, detected language/script when available, timestamps/confidence when supported, and structured errors. Handy (https://github.com/cjpais/handy) is a reference for offline-first UX and local transcription choices, not an assumed runtime dependency.
3. **Language handling:** preserve Hindi, English, Romanized Hindi, Devanagari, and within-utterance code-switching. Keep language identification and text normalization replaceable and testable independently.
4. **Wake-word adapter:** separate local “Ultron” detection from speech recognition. Wake-word detection must not require continuous cloud audio streaming.
5. **TTS adapter:** accept text, language preference, and output settings; return audio or a typed failure. OmniVoice (https://github.com/k2-fsa/OmniVoice) is an evaluation candidate for multilingual speech output, not an approved default.
6. **Playback/session coordinator:** handle interruption, stop, timeout, device errors, and transition from spoken response to HUD transcript without losing task state.

### Candidate selection and integration constraints
- Use small adapter interfaces and mock-based contract tests; optional ML/audio dependencies must not become mandatory for the existing CLI/API test suite.
- Evaluate OmniVoice on the actual Apple Silicon Mac using MPS where supported. Record install/model size, first-run download requirements, cold-start time, real-time factor, peak memory, pronunciation quality, and offline behavior. Do not extrapolate GPU benchmarks to the Mac.
- Evaluate Handy's useful patterns and documented integration surfaces before considering subprocess or IPC integration. Do not assume its CLI is a stable API or that its UI/runtime should be embedded.
- Keep audio and voice references local by default. Any remote inference must disclose provider, payload category, and retention uncertainty; never transmit audio silently.
- Voice cloning requires explicit opt-in and permission for the source voice. Store reference audio only with explicit consent, provide deletion controls, and do not imitate a real person to deceive others.
- HyperFrames (https://github.com/heygen-com/hyperframes) is a separate optional media-generation workflow for project demos, not a dependency of the voice runtime.
- Mark every adapter planned, prototype, verified, or unavailable. A repository review or successful import is not proof of end-to-end integration.


## 16. Engineering workflow (development-time, not runtime architecture)

Repository changes use a repeatable development loop: inspect current branch and working tree; identify existing behavior and acceptance criteria; plan exact files/interfaces/tests for non-trivial work; implement through a verified test-first cycle; review the resulting diff; and report fresh evidence. Bugs require root-cause investigation and a reproducible failing test where feasible. Independent tasks may be isolated in separate branches/worktrees; shared files and coupled changes stay together.

This workflow does not create a second runtime policy system. Runtime authorization remains in the existing security policy and tool registry. Development instructions are documented in [AGENTS.md](AGENTS.md) and [docs/development-workflow.md](docs/development-workflow.md). External Superpowers skills may inspire the process but are not runtime dependencies.


## 18. Evaluation and retrieval evolution policy — 2026-10-13

Keep evaluation separate from runtime orchestration. Maintain deterministic tests for tool schemas, authorization, retries/idempotency, budgets/cancellation, and truthful status. Optional model-graded checks may evaluate response quality, multilingual behavior, retrieval usefulness, and citation support, but should be separately invoked and labelled with model/version, cost, dataset, and scoring limitations. Do not put nondeterministic live-provider calls in the default fast test suite without an explicit reason.

For local knowledge retrieval, treat keyword search, semantic search, hybrid search, and reranking as competing strategies behind a replaceable interface—not mandatory layers. Establish a representative query/document baseline first, compare retrieval quality and unsupported-answer rate, and add reranking only when it improves measured outcomes enough to justify latency and dependencies. Keep source document and page/section/chunk provenance connected to retrieved evidence and expose it to the answer/report layer.

Knowledge graphs, a new vector database, or multi-agent research are deferred until representative tasks show a specific limitation the existing design cannot address. See [the awesome-llm-apps review](docs/research/awesome-llm-apps-review.md). This is an evolution policy, not a statement that hybrid retrieval/evaluations are already implemented.


## 19. Task event history, budgets, and audit boundary — 2026-10-13

The task/session coordinator should emit structured lifecycle events keyed by task/correlation ID: accepted, planned, provider interaction, tool proposal, policy decision, approval, tool start/end, cancellation, error, and final status. Store only minimal diagnostic metadata, redact secrets, and do not persist raw audio, transcript, screenshot, prompts, document contents, or tool payloads by default. Observability consumers may render recorded facts but must not infer unrecorded work.

A single cumulative budget object should flow through every loop and delegated call. It should cap steps, elapsed time, retries, output/context, and optional provider token/spend values where trustworthy. Budget exhaustion terminates new work but preserves completed results and labels the final report partial. Side-effecting tools require idempotency/reconciliation semantics independent of transport retry behavior.

When persistent auditing is added, use project-scoped storage, restrictive permissions, bounded retention/rotation, and defined behavior for write errors, disk-full, invalid paths, concurrent writers, and recovery. Decide per risk tier whether audit persistence is mandatory before an action; do not silently claim that ordinary logs are tamper-proof. Keep the central registry/policy as the only authorization boundary. See [Ruflo review](docs/research/ruflo-review.md).
