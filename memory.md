# Jarvis / ULTRON — Project Memory and Handoff

**Last updated:** 2026-10-13  
**Purpose:** Concise handoff log; source code and Git history remain authoritative.

## 1. Approved product direction
- Identity: **Balanced ULTRON** — voice, computer control, memory, vision, and AI agents.
- Execution preference: **Local capabilities where practical; cloud AI when useful.**
- First interface priority: **Voice and microphone.**
- Primary platform: macOS; initial product is a single-user assistant.

## 2. Repository and Git safety
- Repository: https://github.com/rajaryan1111/jarvis-assistant
- Default branch: `main`.
- PR #3: https://github.com/rajaryan1111/jarvis-assistant/pull/3 — draft/open M2.2.1 Groq reliability work; do not merge.
- Last-known head for `m2.2.1-groq-fixes`: `e7789d8a5f78ed12e49a26d233fc260173b20c64`.
- `m2.3-reliability` was absent from GitHub in the prior branch search.
- Preserve all branches and PRs. **No commit, push, merge, branch mutation, or GitHub modification without explicit approval.**
- Arena must not do M2.3 work on `main` or `m2.2.1-groq-fixes`; stop if it cannot use the required branch/base.

These are last-known observations, not a guarantee remote state has not changed. Verify before any future Git action.

## 3. Architecture summary
- Agent loop: `ultron/agent/runtime.py`.
- Provider-neutral types/OpenAI/Groq adapters: `ultron/agent/provider.py`.
- Agent allowlist/schemas: `ultron/agent/catalog.py`.
- Tool contracts: `ultron/tools/base.py`.
- Central enforcement boundary: `ultron/tools/registry.py`.
- Risk/context policy: `ultron/security/policy.py`.
- Authenticated Flask HUD/API: `ultron/api/app.py`.
- Configuration/credential loading: `ultron/config.py`.
- Legacy routing/application wiring: `main.py`; integration adapter: `ultron/legacy.py`.
- Optional local retrieval: `knowledge.py`; optional vision utilities: `vision.py`.

Do not bypass the registry for state-changing operations. The model proposes calls; runtime validates; policy/registry authorize.

## 4. M2.3 reliability work status
Arena reported an M2.3 patch in an isolated checkout based on `e7789d8a5f78ed12e49a26d233fc260173b20c64`. It reported focused/regression/full tests, Ruff, compileall, secret scan, and clean-checkout patch application. These results are Arena-reported and not independently rerun on the owner's Mac.

Arena reported `/tmp/m2.3-reliability.patch` inside its own sandbox. A verified downloadable copy, checksum, and byte size had not yet been established. Do not apply or push based only on a report or guessed link.

Reported intended changes:
- defer `ask_user` in selected cases when a relevant permitted read-only lookup exists;
- bounded retry for malformed model output;
- more conservative completion verification when no tool succeeds;
- related tests/docs.

Before applying: obtain the actual artifact; verify checksum/base; run `git apply --check` against the exact base; inspect the diff; run local tests; get approval for any Git mutation.

## 5. Verification boundaries
Deterministic tests/stubs do not prove live provider behavior. The prior Groq report described intermittent live behavior and called for a full live suite before PR #3 is ready. macOS voice, microphone, camera, speech output, and desktop automation must be validated on the actual Mac. Never request/store API keys in this file or chat.

## 6. Six-document set
- `prd.md` — product goals and acceptance criteria.
- `architecture.md` — components, boundaries, data flow, target evolution.
- `rules.md` — mandatory engineering/Git rules.
- `phases.md` — milestones and exit criteria.
- `design.md` — voice/HUD interaction and UX.
- `memory.md` — dated handoff facts and unresolved status.

## 7. Immediate next steps
1. Review all six documents for consistency.
2. Confirm whether the M2.3 patch can be exported as a real downloadable artifact.
3. Do not alter GitHub until explicitly approved.
4. Trace current end-to-end voice path and measure baseline before refactoring.
5. Record decisions with dates and update PRD/architecture/roadmap when they change product behavior.