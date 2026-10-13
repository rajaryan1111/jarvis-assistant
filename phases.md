# Jarvis / ULTRON — Development Phases

**Status:** Proposed roadmap v1.0. Complete one testable milestone at a time; no automatic Git operations.

## Phase 0 — Documentation and safe baseline
**Goal:** Establish product intent and engineering guardrails.

Deliverables: `prd.md`, `architecture.md`, `rules.md`, `phases.md`, `design.md`, `memory.md`.

Exit criteria:
- All six documents agree on balanced ULTRON, local capabilities where practical, cloud AI where useful, and voice-first UX.
- Current code is distinguished from future proposals.
- Git/PR safety rules are explicit.
- Documentation creation does not imply repository mutation.

## Phase 1 — Runtime reliability
**Goal:** Make agent outcomes predictable and recoverable.

Work: review malformed-output recovery; bound retries; preserve successful evidence; prevent duplicate actions; verify status against the ledger; improve content-free diagnostics.

Exit criteria: regression tests cover malformed output, provider errors after completed tools, repeated invalid calls, limits, and false-success prevention. Deterministic suite, lint, compile, and secret scan pass. Live provider results are reported separately. PR remains unmerged until approved validation criteria are met.

## Phase 2 — Voice-first experience
**Goal:** Make microphone interaction the best-supported path without changing security.

Work: trace microphone → recognition → router/agent → response → speech on macOS; measure baseline latency; improve spoken answers and feedback for empty input, recognition/provider failures, cancellation, and pending approvals; avoid blocking control paths on speech where feasible.

Exit criteria: mocked voice-path tests; hardware smoke checklist on target Mac; voice cannot approve high-impact actions; latency targets are based on measurements.

## Phase 3 — Memory and local knowledge
**Goal:** Make personal context useful, controllable, and privacy-aware.

Work: define memory types, provenance, retention, inspection/deletion; improve RAG diagnostics; test untrusted-content labeling and injection containment.

Exit criteria: documented inspect/delete path; retrieval failures degrade gracefully; external content cannot authorize tools; storage safety tests pass.

## Phase 4 — Computer control and automation
**Goal:** Add capabilities only when user value is clear.

Work: inventory tools; define schema, risk, side effects, preview, platform, timeout, and failure behavior for each new tool; add registry/policy tests.

Exit criteria: no registry bypass; confirmations enforced outside the model; negative tests and clear user-facing status; macOS-only behavior validated on macOS.

## Phase 5 — Vision and contextual awareness
**Goal:** Improve useful local perception without turning it into a security shortcut.

Work: choose one use case at a time; document camera permissions, model files, resources, and retention; keep vision optional; keep face recognition separate from authentication.

Exit criteria: measurable use-case acceptance test; no unexpected capture/storage; vision cannot approve actions; hardware/privacy limitations documented.

## Phase 6 — Integration and polish
**Goal:** Make voice, HUD, CLI, tools, memory, and provider behavior coherent.

Work: review context propagation/status semantics; improve onboarding and diagnostics; measure against baseline; fix high-impact UX inconsistencies before adding features.

Exit criteria: cross-interface authorization tests; reproducible setup on a supported environment; release notes distinguish tested from unverified behavior.

## Change control
Priorities change only with an explicit decision recorded in `memory.md` and reflected here. Code generation alone does not complete a phase. No commit, push, merge, branch mutation, or GitHub modification without explicit authorization.