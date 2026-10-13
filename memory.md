# Jarvis / ULTRON — Project Memory and Handoff

**Last updated:** 2026-10-13 (v3 requirements update)  
**Purpose:** Concise handoff log; source code and Git history remain authoritative.

## 1. Approved product direction

- Identity: **Balanced ULTRON** — a voice-first technical partner for assignments, research projects, coding/debugging, website/app creation, memory, vision, and approved computer control.
- Inspiration: the collaborative working relationship of Tony Stark and JARVIS; not fictional unrestricted autonomy.
- Execution preference: **local capabilities where practical; cloud AI when useful.**
- First interface priority: **voice and microphone**, with HUD/CLI and visible artifacts as companions.
- Required languages: **Hindi, English, and Hinglish**, including Romanized Hindi and Devanagari.
- Required product surfaces: **installable macOS desktop app with local “Ultron” wake word** plus a companion website/HUD; both share core runtime and authorization.
- Primary platform: macOS; initial product is a single-user assistant.
- Core interaction loop: understand → plan → bounded step → inspect evidence → continue or ask → summarize.

## 2. Repository and Git safety

- Repository: https://github.com/rajaryan1111/jarvis-assistant
- Default branch: `main`.
- PR #3: https://github.com/rajaryan1111/jarvis-assistant/pull/3 — last verified open/draft M2.2.1 Groq reliability work; do not merge without review/authorization.
- Last-known head for `m2.2.1-groq-fixes`: `e7789d8a5f78ed12e49a26d233fc260173b20c64`.
- Preserve all branches and PRs. Remote actions require explicit authorization; report actual GitHub results.
- Documentation branch `docs/project-foundation-v1` was created from `main` and contains the original six documents, commit `d43552128372338865e1a032cd64e6ea6b6697a4`.
- The revised v2 document set is on `docs/ultron-collaborative-assistant-v2`. The v3 trilingual desktop requirements are on `docs/ultron-trilingual-desktop-v3`. The expanded student-life vision is on `docs/student-life-companion-v4`.

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

## 4. Collaboration workflows to build

1. **Assignments/study:** source-grounded answers, step-by-step explanations, examples, exam-oriented formatting, source gaps clearly marked.
2. **Research:** question refinement, permitted-source review, citation/provenance tracking, literature matrix, methodology, datasets, reproducible experiments.
3. **Coding:** repo/branch inspection, focused patches, regression tests, diff review, accurate test reporting, no unapproved remote Git actions.
4. **Website/app building:** requirements, user flows, design, responsive implementation, API/backend, tests, run/deploy guidance, deployment only after approval.
5. **General computer help:** voice-first interaction, local context, narrow authorized tools, safe cancellation and evidence-backed status.

These are product requirements; they are not claims that every workflow is already implemented.

## 5. M2.3 reliability work status

Arena previously reported an M2.3 patch in an isolated checkout based on `e7789d8a5f78ed12e49a26d233fc260173b20c64`. It reported focused/regression/full tests, Ruff, compileall, secret scan, and clean-checkout patch application. Those results were Arena-reported and not independently rerun on the owner's Mac.

Before applying any such patch: obtain the actual artifact; verify checksum/base; run `git apply --check` against the exact base; inspect the diff; run local tests; get approval for any remote Git mutation.

## 6. Verification boundaries

Deterministic tests/stubs do not prove live provider behavior. macOS voice, microphone, camera, speech output, and desktop automation must be validated on the actual Mac. Research accuracy, generated-code correctness, and deployment success require evidence appropriate to each task. Never request/store API keys in this file or chat.

## 7. Six-document set

- `prd.md` — product vision, assignment/research/coding/website workflows, requirements and acceptance criteria.
- `architecture.md` — current components, proposed task coordinator, trust boundaries, data flow.
- `rules.md` — normative collaboration, source-integrity, security, coding, and Git rules.
- `phases.md` — milestone roadmap and exit criteria.
- `design.md` — voice-first interaction, work modes, HUD/task workspace.
- `memory.md` — dated handoff facts and current decisions.

## 8. Next steps

1. Verify the six revised docs on `docs/ultron-collaborative-assistant-v2`.
2. Review the product direction and scope before merging any documentation.
3. Trace and measure the existing voice path on macOS.
4. Build task/project workspace in small increments after runtime reliability.
5. Add source/citation handling, repository-aware coding, and website workflows with tests.
6. Keep current features distinct from proposed future behavior.

## 9. New approved requirements — 2026-10-13

The product direction now explicitly requires:
- First-class **Hindi, English, and Hinglish** understanding and responses.
- Hinglish means natural code-switching, Romanized Hindi, and Hindi in Devanagari; preserve English technical vocabulary and code identifiers.
- An **installable macOS laptop app** with hands-free “Ultron” wake-word activation, so the user does not have to open a browser for each session.
- A **companion website/HUD** remains available for typed use, visible task state, artifacts, sources, settings, and secure approvals.
- Desktop and website must share the same runtime/task context and central authorization policy.
- Prefer local wake-word detection and transparent microphone status; optional login startup is opt-in. Cloud-dependent speech/LLM features must be disclosed.
- These are requirements, not claims that the current repository already implements them.

## 10. Next implementation sequence

1. Inspect and measure the existing macOS voice pipeline and process lifecycle.
2. Choose a wake-word/STT/TTS stack based on trilingual tests, latency, resource use, privacy, and offline requirements.
3. Prototype local “Ultron” detection with mute/quit and visible microphone state.
4. Carry language preference through recognition, runtime, and speech output.
5. Package the existing runtime/HUD into a macOS app with clean lifecycle; retain the website as a companion.
6. Validate install, permissions, wake word, Hindi/English/Hinglish quality, and shared policy on the actual Mac.

## 11. Expanded BTech student-life product vision — 2026-10-13

ULTRON's approved target vision includes the broader BTech journey for CSE and ECE students, not just coding and voice chat. Proposed domains:
1. Academics/exams: source-grounded notes, question banks/PYQs, revision, quizzes, viva and backlog preparation.
2. CSE engineering: DSA, software projects, APIs, databases, AI/ML, testing and deployment.
3. ECE engineering: circuits, electronics, microcontrollers, embedded systems, HDL/FPGA, DSP, sensors and hardware debugging.
4. Hackathons/teams: idea selection, time/budget-aware MVP, task owners, integration, demo and pitch preparation.
5. Career/placements: skill-gap plans, interviews, truthful resumes/portfolios, internships and deadlines.
6. Research: literature review, provenance, citations, datasets and reproducible experiments.
7. Productivity/life admin: schedule, reminders, budget, hostel routines, travel and forms.
8. Personal/social: loneliness, friendships, dating, breakups, communication, confidence and bounded well-being support.
9. Creativity/accessibility/life transitions: hobbies, presentations, alternate learning formats and post-college decisions.

These are product requirements and proposed workflows, not claims that the current implementation already has them. Every domain needs acceptance tests, clear evidence of completion, privacy controls and bounded scope.

## 12. Student-life trust requirements

- Keep private reflection and relationship conversations separate from shared projects.
- Memory, reminders, mood check-ins, calendar/finance integrations and external sharing are opt-in and revocable.
- Support empathetic conversation without pretending to be human or replacing trusted people/professionals.
- Do not foster emotional dependency, diagnose, infer others' hidden feelings, assist stalking/manipulation, fabricate academic/research evidence, or guarantee placements.
- For hardware, distinguish theory/simulation from physical verification; for code, report actual tests.
- Let the user choose whether they want listening, explanation, planning or action.
- Budget calculations show assumptions; external transactions require explicit authorization.

## 13. Suggested roadmap after desktop/trilingual foundation

1. Build inspectable course/project workspaces and source-grounded study workflows.
2. Add project/hackathon planning and CSE/ECE evidence-aware build/test flows.
3. Add career/research workflows.
4. Add opt-in reminders and life-administration tools.
5. Add private social/well-being support modes with strong privacy and safety boundaries.
6. Expand accessibility, creativity and post-college decision support.
