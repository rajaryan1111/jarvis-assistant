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
