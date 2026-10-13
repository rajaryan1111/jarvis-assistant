# Jarvis / ULTRON — Development Phases

**Status:** Revised roadmap v2.0. Complete one testable milestone at a time; no automatic Git operations.  
**Product goal:** A dependable voice-first technical partner for assignments, research, coding, and website/app creation.

## Phase 0 — Product and safety foundation

**Goal:** Align product direction, architecture, and engineering rules.

Deliverables: `prd.md`, `architecture.md`, `rules.md`, `phases.md`, `design.md`, `memory.md`.

Exit criteria:
- All six documents describe the same voice-first collaborative assistant.
- Assignment, research, coding, and website workflows are explicitly included.
- Existing implementation is separated from proposals.
- Security, source integrity, Git authorization, and deployment approval are documented.

## Phase 1 — Runtime reliability

**Goal:** Make agent outcomes predictable and recoverable.

Work: review malformed-output recovery; bound retries; preserve successful evidence; prevent duplicate actions; verify status against the ledger; improve content-minimizing diagnostics.

Exit criteria: regression tests cover malformed output, provider errors after completed tools, repeated invalid calls, limits, and false-success prevention. Deterministic suite, lint, compile, and secret scan pass. Live provider results are reported separately. PRs remain unmerged until approved validation criteria are met.

## Phase 2 — Voice-first experience

**Goal:** Make microphone interaction the best-supported path without changing security.

Work: trace microphone → recognition → router/agent → response → speech on macOS; measure baseline latency; improve spoken answers and feedback for silence, recognition/provider failures, cancellation, and pending approvals; avoid blocking control paths on speech where feasible.

Exit criteria: mocked voice-path tests; hardware smoke checklist on target Mac; voice cannot approve high-impact actions; latency targets are based on measurements.

## Phase 3 — Task collaboration and project workspace

**Goal:** Make ULTRON useful across multi-step work rather than isolated questions.

Work: define task modes (assignment, research, coding, website/app, general); represent plans/checklists, current step, blockers, artifacts, evidence, and next action; allow user clarification and checkpoints; keep a clear task history without silently persisting private content.

Exit criteria:
- User can see what ULTRON plans to do and what it actually did.
- Partial/failed/unverified tasks are distinguished from completed work.
- Task state can be resumed safely without repeating completed side effects.
- Persistence and deletion behavior are documented and tested.

## Phase 4 — Assignment and research partner

**Goal:** Improve source-grounded study help and evidence-based research.

Work: file/notes intake; source-first answers; document/page citation where supported; literature matrix; research question/methodology planning; dataset/experiment logs; report drafting and revision.

Exit criteria:
- When asked to use supplied notes, outputs reflect those notes and flag gaps.
- Research claims retain traceable sources and dates.
- No fabricated citations, quotations, experiments, or results.
- User can distinguish source-backed claims, inference, and proposed next steps.

## Phase 5 — Coding partner

**Goal:** Make repository-aware coding and debugging safe and repeatable.

Work: repository inspection; error/log analysis; small patch workflow; test selection; diff review; dependency/environment diagnostics; branch/base checks; artifact summaries.

Exit criteria:
- Branch, HEAD, and working tree are checked before edits.
- Changes are scoped, reviewable, and regression-tested.
- Test outcomes and platform-specific gaps are reported accurately.
- No remote Git operation occurs without explicit authorization.
- No secrets or unrelated user files are added.

## Phase 6 — Website and app builder

**Goal:** Turn product ideas into usable, responsive web projects.

Work: requirements and acceptance criteria; user flows; design system; component architecture; frontend/backend implementation; forms/API integration; responsive and accessibility tests; setup/deployment documentation.

Exit criteria:
- Desktop and mobile behavior is checked.
- Routes, links, forms, loading/empty/error states work as agreed.
- Secrets remain server-side and private data is not accidentally published.
- Deployment/publication occurs only after explicit approval.

## Phase 7 — Memory and local knowledge

**Goal:** Make project continuity useful, inspectable, and privacy-aware.

Work: distinguish run-local context, project notes, explicit preferences, source passages, and derived summaries; define provenance, retention, inspection/update/deletion; improve local RAG diagnostics and injection containment.

Exit criteria: documented inspect/delete path; retrieval failures degrade gracefully; external content cannot authorize tools; storage safety tests pass.

## Phase 8 — Computer control and automation

**Goal:** Add actions only when they have clear value and safe boundaries.

Work: inventory tools; define schema, risk, side effects, preview, platform, timeout, and failure behavior; add registry/policy tests; improve safe cancellation and result verification.

Exit criteria: no registry bypass; confirmations enforced outside the model; negative tests and clear user-facing status; macOS-only behavior validated on macOS.

## Phase 9 — Vision and contextual awareness

**Goal:** Improve useful local perception without making it a security shortcut.

Work: choose one use case at a time; document camera permissions, model files, resources, and retention; keep vision optional; keep face recognition separate from authentication.

Exit criteria: measurable use-case acceptance test; no unexpected capture/storage; vision cannot approve actions; hardware/privacy limitations documented.

## Phase 10 — Integration and polish

**Goal:** Make voice, HUD, CLI, task workspace, tools, memory, and provider behavior coherent.

Work: cross-interface context/status review; onboarding and diagnostics; measure against baseline; fix high-impact UX inconsistencies before adding features.

Exit criteria: cross-interface authorization tests; reproducible setup on a supported environment; release notes distinguish tested from unverified behavior.

## Change control

Priorities change only with an explicit decision recorded in `memory.md` and reflected here. Code generation alone does not complete a phase. No commit, push, merge, branch mutation, PR change, or deployment without explicit authorization.

The student-life phases 11–16 are expansion phases after the trilingual desktop foundation and core workspace. Do not implement all domains at once; validate one workflow and its privacy boundaries before proceeding.

## Phase 2A — Trilingual voice and wake-word prototype

**Goal:** Make Hindi, English, and Hinglish reliable first-class spoken languages and support hands-free activation.

Work:
- Trace the existing mic → speech recognition → router/runtime → response → speech path.
- Add per-utterance language/script detection and user override; cover Hindi, English, Hinglish, Romanized Hindi, and Devanagari.
- Prototype local wake-word detection for “Ultron”; add visible listening state, mute/quit, cancellation, and false-wake recovery.
- Measure language-specific recognition quality, wake latency, false activations, idle resource use, and cloud/network behavior.

Exit criteria:
- A documented evaluation set and results exist for all three languages and both Hindi scripts.
- Wake-word listening does not require a browser and does not upload idle audio solely for detection.
- Protected actions still require trusted HUD approval; speech alone cannot approve.
- Hardware tests are performed on the target Mac; mocks are not described as hardware validation.

## Phase 6A — Installable macOS application

**Goal:** Let the owner install and launch ULTRON as a laptop app, independent of manually opening a browser.

Work:
- Choose packaging after inventorying the existing Python runtime and Flask HUD; prefer a minimal shell over a wholesale rewrite.
- Package runtime/dependencies, lifecycle management, microphone permission guidance, status, mute, quit, logs, upgrade and uninstall.
- Add optional launch-at-login only with explicit opt-in.
- Share the same task context and security policy with the website/HUD.
- Keep the companion website for typed interaction, visual task review, settings, artifacts, and secure approvals.

Exit criteria:
- Clean install/launch/upgrade/uninstall checks pass on a supported macOS version.
- Saying “Ultron” activates a session with no browser manually opened.
- The user can tell whether ULTRON is idle, listening, processing, speaking, muted, or failed.
- Website and desktop app use the same runtime/policy, and local APIs are not exposed to the LAN by default.
- Documentation clearly distinguishes offline capabilities from cloud-dependent capabilities.

## Phase 10A — Trilingual desktop/web release validation

**Goal:** Verify the combined product before calling it ready.

Work: run cross-interface tests for language preference, task continuity, permissions, wake-word activation, cancellation, approvals, provider failures, and recovery; test a clean install; document supported macOS versions and known language limits.

Exit criteria: reproducible test checklist, measured trilingual voice results, clean installer lifecycle, and no authorization bypass across desktop/website paths.

## Phase 11 — Student workspace and study companion

**Goal:** Make the core useful across the real BTech study cycle.

Work: workspace records for courses and projects; notes/PDF/question-bank intake; source-grounded answers; syllabus/topic map; deadlines and a study checklist; quiz/revision workflows; inspect/correct/delete saved memory.

Exit criteria: source fidelity tests; realistic plan revision; private workspace retention controls; task cancellation/resumption tests; no fabricated source or completion claims.

## Phase 12 — Engineering project and hackathon copilot

**Goal:** Support mixed CSE/ECE teams from idea through evidence-backed demo.

Work: project requirements and budget; challenge/deadline/team constraints; MVP and task dependencies; repository-aware code changes; electronics datasheet/circuit/firmware workflows; integration checklist; test/simulation/hardware evidence; demo and pitch practice.

Exit criteria: plan reflects actual time/budget/team constraints; work ownership is explicit; code and hardware verification levels are distinguished; all test results are accurately reported; no remote Git mutation without authorization.

## Phase 13 — Career and research workflows

**Goal:** Help students develop demonstrable skills and make informed next-step decisions.

Work: role exploration; skill-gap matrix; interview practice; truthful resume/portfolio review; internship/opportunity tracker; source-aware literature review; citation and experiment provenance.

Exit criteria: no fabricated credentials/citations/results; assumptions and uncertainty are visible; deadlines can be inspected and reminders cancelled; opportunity data has traceable sources when sourced externally.

## Phase 14 — Productivity and life administration

**Goal:** Help users manage time and practical responsibilities without over-optimizing their lives.

Work: user-controlled calendar/reminders; deadline prioritization; budget and trip planning; hostel/life checklists; time-aware rescheduling; weekly reflection.

Exit criteria: reminders are opt-in, visible and cancellable; plans respect user constraints; budgets show assumptions; no external transaction without explicit approval.

## Phase 15 — Private social and well-being support

**Goal:** Offer empathetic, bounded support for loneliness, friendship, dating, breakups, stress and confidence.

Work: listen/reflect/plan modes; conversation rehearsal; respectful message drafts; consent and boundary prompts; optional private journaling/check-ins; supportive signposting to trusted people/professionals where appropriate.

Exit criteria: private data stays out of shared workspaces; no diagnosis, emotional manipulation, dependency-building or claims about others' hidden feelings; no stalking/coercion assistance; user can disable/delete personal data; appropriate escalation behavior is reviewed.

## Phase 16 — Accessibility, creativity and life transitions

**Goal:** Adapt learning and workflows to different needs and support interests beyond coursework.

Work: alternate explanation formats; accessible text/voice interaction; presentation/public-speaking practice; creative project support; life-after-college option comparisons.

Exit criteria: user-controlled preferences; accessible fallback when voice fails; career/life advice is transparent about assumptions and does not guarantee outcomes.

## Phase 17 — Local project command center and impact planning

**Goal:** Let the user select and understand authorized local projects by voice, with accurate scoped context.

Work: project/repository registry; active workspace/branch indicator; Git status/history/diff; project summaries; task/dependency/decision/technical-debt records; voice routing; read-only architecture and risk reports.

Exit criteria: ambiguous targets trigger clarification; reports separate inspected facts from assumptions; unrelated files/user changes remain untouched; project contexts do not leak; all access respects workspace roots.

## Phase 18 — Senior-engineer mentor and consequence analysis

**Goal:** Help the user reason like a stronger engineer, not just generate code.

Work: architecture reviews; alternatives; complexity/cost/security/privacy/performance/maintainability trade-offs; failure modes; dependency/migration impact; technical-debt register; deadline-aware advice; rollback and verification plans; evidence labels.

Exit criteria: material recommendations state evidence, assumptions, alternatives, consequences and next steps; unsafe premises are challenged respectfully; hypotheses are never labelled verified facts.

## Phase 19 — Safe local implementation loop

**Goal:** Complete bounded coding tasks from voice request to reviewed local diff and test evidence.

Work: plan/preview; branch-aware edits; allowlisted command execution; constrained test runner; lint/type-check/test workflows; diff review; cancellation; handoff; baseline/final Git status comparison.

Exit criteria: no unapproved external writes; existing user changes preserved; each edit has a reviewable diff; tests reported accurately; failure/cancellation leaves clear state and recovery instructions.

## Phase 20 — GitHub integration with approval-gated writes

**Goal:** Connect project work to GitHub without compromising repository ownership or branch safety.

Work: read repositories/issues/PRs/checks; map local branch to remote; prepare commits and PR descriptions; review CI evidence; create/update remote resources only after exact-action approval.

Exit criteria: least-privilege scopes; branch/base verified before writes; no force-push or auto-merge; write approvals auditable; real GitHub tests distinguished from mocks.

## Phase 21 — Coding-provider adapters and cross-review

**Goal:** Use coding agents as optional specialists under ULTRON's task plan.

Work: adapters for ChatGPT/Codex, Claude, Antigravity and other tools through supported APIs, CLIs, MCP servers or documented mechanisms; bounded provider roles; minimal context; isolated worktrees; disagreement report; cost/latency/data-transfer display.

Exit criteria: only verified capabilities advertised; providers cannot grant permissions; secrets excluded; concurrent edits isolated; provider output validated before it is evidence.

## Phase 22 — Supabase and cloud-service workflows

**Goal:** Help build and maintain connected applications without accidental cloud or production damage.

Work: project/schema inspection; migration drafting; local/staging validation; type generation where supported; environment separation; backup/rollback planning; explicit deployment/database approval.

Exit criteria: exact project/environment shown before mutations; production writes default-deny without explicit confirmation; migrations include validation/recovery; credentials least-privilege and secure.

## Phase 23 — Visible browser automation and connector coverage

**Goal:** Support coding-related sites without suitable APIs, without promising universal automation.

Work: visible browser sessions; per-site capability registry; user-confirmed navigation/submissions; session isolation; website-change detection; manual handoff for CAPTCHA/MFA or unsupported flows.

Exit criteria: no security-control or service-policy bypass; user can stop automation; unsupported workflows identified honestly; external submissions require preview and specific approval.

## Phase 24 — Reliability, recovery and release hardening

**Goal:** Make multi-tool workflows predictable and auditable before broad daily use.

Work: adapter contract tests; fault injection; timeout/rate-limit handling; idempotency; audit log; permission revocation; backup/recovery drills; offline behavior; privacy/security review; real macOS packaging and microphone tests.

Exit criteria: each integration marked planned/prototype/verified/unavailable; failures never masquerade as success; cancellation/recovery tested; high-impact approvals reviewed; macOS validated on target hardware.

## Research-informed voice-stack evaluation — 2026-10-13

This is a scoped evaluation task supporting Phases 2/2A and desktop packaging; it does not change phase numbering or claim implementation.

### Candidates
- **Handy** (MIT): reference for offline transcription UX, VAD, configurable push-to-talk, local STT model choices, cancellation and cross-platform desktop integration. First evaluate its architecture and documented interfaces; do not assume it will be embedded.
- **OmniVoice** (Apache-2.0): optional multilingual TTS candidate. Test Hindi/English/Hinglish and technical pronunciation on the actual Apple Silicon Mac before deciding whether to add an adapter.
- **HeyGen HyperFrames** (Apache-2.0): optional future project-demo/pitch-video tool only; not part of the core voice milestone. The provided heygen-comP URL did not identify a specific repository; HyperFrames is a possible intended source and must not be treated as confirmed intent.

### Evaluation exit criteria
- Written comparison against the current speech path, with versions, license/model terms, dependencies and maintenance notes.
- Repeatable STT/TTS test set covering Hindi, English, Romanized Hindi, Devanagari, code-switching, technical words, paths, numbers and noisy/quiet speech.
- Measurements on target Mac: cold start, per-utterance latency, real-time factor where applicable, peak memory, install/model size, network requirements and offline behavior.
- Failure tests for no microphone permission, unavailable model, no network, cancellation, timeout, empty transcript, low confidence and playback interruption.
- Optional dependencies remain isolated; existing core tests must still run without downloading ML models.
- No candidate becomes the default until the results and trade-offs are reviewed and approved. Record actual evidence and known limitations.


## 12. Development quality gates for every phase

All phases use the project's [development workflow](docs/development-workflow.md). These gates apply in addition to phase-specific exit criteria:

1. **Scope gate:** define behavior, non-goals, affected files/interfaces, acceptance criteria, and relevant security/privacy boundaries.
2. **Baseline gate:** inspect the current Git state and run the relevant baseline checks; record pre-existing failures rather than attributing them to the patch.
3. **Test gate:** for a bug or behavior change, demonstrate a focused test failing before implementing the fix where practical; then verify it passes and run the full relevant suite.
4. **Review gate:** inspect the diff and request an independent review for major features or security-sensitive changes when a reviewer tool is available.
5. **Evidence gate:** run fresh checks on the final changed tree and report exact results, skips, live-vs-mocked coverage, and unresolved risks.
6. **Integration gate:** present integration choices and wait for explicit approval before remote pushes, merges, deployments, destructive operations, or publication.

Passing deterministic tests does not prove live provider, microphone, speech, camera, or macOS automation behavior. Keep hardware/live acceptance checks explicit.


## 18. Quality and retrieval evaluation gate — 2026-10-13

**Gate A — before broadening the agent runtime**
- [ ] Add a compact deterministic regression suite for malformed tool calls, policy bypass attempts, retry/idempotency, cancellation and budgets, and evidence-backed completion status.
- [ ] Add source-fidelity/citation fixtures for study and research tasks.
- [ ] Keep optional model-graded and live-provider evaluations separate from the default deterministic test suite.
- [ ] Record baseline results before changing the runtime or adopting new orchestration.

**Gate B — before upgrading retrieval**
- [ ] Assemble a small representative corpus of notes, question banks, and project docs with query/expected-evidence fixtures.
- [ ] Measure current retrieval coverage/relevance, citation support, unsupported-answer rate, and latency.
- [ ] Compare keyword, semantic, and hybrid retrieval under the same conditions; evaluate reranking only after baseline comparison.
- [ ] Decide based on repeatable benefit, acceptable Mac resource use, privacy, and maintenance cost.

**Gate C — before advanced architecture**
- [ ] Only consider vector-store migration, knowledge graphs, multi-agent research, or extra external services when an observed workload exposes a specific limitation.
- [ ] Document measured benefit, new failure modes, cost, rollback, license, and data-flow implications.

These are proposed exit criteria, not claims of completed implementation. Full rationale: [awesome-llm-apps review](docs/research/awesome-llm-apps-review.md).


## 19. Runtime observability and budget milestone — 2026-10-13

- [ ] Define a minimal structured task-event schema with task/correlation IDs and explicit execution statuses.
- [ ] Add secret/content redaction and tests proving sensitive payloads are not persisted by default.
- [ ] Define one cumulative budget across model calls, tool calls, retries, duration, and output/context size; preserve artifacts and explicit partial status on exhaustion.
- [ ] Add deterministic tests for cancellation, uncertain side-effect outcomes, duplicate execution prevention, and action-specific approval binding.
- [ ] Add keyless integration smoke tests using provider/tool stubs; place live-provider and actual-Mac checks in separate optional suites with clear statuses and cost caps.
- [ ] Before requiring audit logs for consequential actions, specify bounded retention, permissions, write-failure behavior, rotation/concurrency handling, and recovery.

Do not add a telemetry platform, cost database, external orchestrator, or swarm subsystem for this milestone. Start with the smallest mechanism that makes tasks debuggable. These criteria are planned, not marked complete; see [Ruflo review](docs/research/ruflo-review.md).
