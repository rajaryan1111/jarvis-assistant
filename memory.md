# Jarvis / ULTRON — Project Memory and Handoff

**Last updated:** 2026-10-16 (post-integration handoff; main verified at `88dabc6cd41a6f71082db9b48aaa2a48e3cfc6a2`)  
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
- Current verified `main` head (2026-10-16): `88dabc6cd41a6f71082db9b48aaa2a48e3cfc6a2`.
- PR #4 (ensemble integration), #5 (security/agent foundation), and #6 (local provider, racing, AutoTune, Smart Router) are merged. PR #2 and #3 are closed/merged as their commits are represented in `main`.
- Latest `main` CI run: https://github.com/rajaryan1111/jarvis-assistant/actions/runs/38826067567 — 4/4 Python 3.10–3.13 jobs passed on the exact `main` head. The PR #4 integration head also passed 4/4: https://github.com/rajaryan1111/jarvis-assistant/actions/runs/38825978177.
- Before this handoff PR was opened, no open PRs were found during the 2026-10-16 recheck. This is point-in-time state and should be reverified before future merges.
- Live OpenAI/Groq/Ollama provider behavior and target-Mac voice/hardware behavior have not been verified by these CI runs.
- No `LICENSE` file has been added. The G0DM0D3 reference is AGPL-3.0; do not copy its source/prompts/scoring tables wholesale or imply the licensing question is resolved. Keep new implementation original and flag distribution/licensing for owner decision.
- Preserve existing branches and PRs; remote actions require explicit authorization. Report actual GitHub results.
- Documentation branches (`docs/project-foundation-v1`, `docs/ultron-collaborative-assistant-v2`, `docs/ultron-trilingual-desktop-v3`, `docs/student-life-companion-v4`) are historical context; check current GitHub state before relying on them.

## 3. Architecture summary

- Agent loop: `ultron/agent/runtime.py`.
- Provider-neutral types/OpenAI/Groq adapters: `ultron/agent/provider.py`.
- Agent allowlist/schemas: `ultron/agent/catalog.py`.
- Tool contracts: `ultron/tools/base.py`.
- Central enforcement boundary: `ultron/tools/registry.py`.
- macOS tools, path validation, bounded subprocess runner, read-only project inspection: `ultron/tools/macos.py`, `ultron/tools/paths.py`, `ultron/tools/process.py`, `ultron/tools/project.py`.
- Risk/context policy: `ultron/security/policy.py`.
- Confirmation store, session state, token management, log/tool redaction, secret scanner: `ultron/security/confirmation.py`, `ultron/security/sessions.py`, `ultron/security/session_state.py`, `ultron/security/tokens.py`, `ultron/security/redact.py`, `ultron/security/secret_scan.py`.
- Authenticated Flask HUD/API: `ultron/api/app.py`.
- Configuration/credential loading: `ultron/config.py`.
- Legacy routing/application wiring: `main.py`; integration adapter: `ultron/legacy.py`; CLI entry point: `ultron/cli.py`.
- Optional local wake-word listener: `wakeword_listener.py`; optional NewsAPI helper: `news.py`.
- Optional local retrieval: `knowledge.py`; optional vision utilities: `vision.py`.

Do not bypass the registry for state-changing operations. The model proposes calls; runtime validates; policy/registry authorize.

## 4. Collaboration workflows to build

1. **Assignments/study:** source-grounded answers, step-by-step explanations, examples, exam-oriented formatting, source gaps clearly marked.
2. **Research:** question refinement, permitted-source review, citation/provenance tracking, literature matrix, methodology, datasets, reproducible experiments.
3. **Coding:** repo/branch inspection, focused patches, regression tests, diff review, accurate test reporting, no unapproved remote Git actions.
4. **Website/app building:** requirements, user flows, design, responsive implementation, API/backend, tests, run/deploy guidance, deployment only after approval.
5. **General computer help:** voice-first interaction, local context, narrow authorized tools, safe cancellation and evidence-backed status.

These are product requirements; they are not claims that every workflow is already implemented.

## 5. M2.3 and Phase 2 reliability work status

On 2026-10-13, `arena/f489501f-jarvis-assistant` integrated `m2.2.1-groq-fixes`, `research/github-account-audit-2026-10-13`, and `research/ruflo-review-2026-10-13`, then completed and verified three engineering batches in the locked `.venv` environment (`uv sync --locked --extra dev`):
- **Phase 2 external integrations (`main.py`, `tests/test_weather.py`, `tests/test_cricket.py`, `tests/test_web_search.py`, `tests/test_wiki.py`):** fixed the two baseline failures from `research/github-account-audit-2026-10-13` (`test_web_and_document_text_is_never_routed_as_a_command` stub compatibility and `test_repository_has_no_hardcoded_credentials` fixture placeholders), added verified HTTP(S) URL extraction and DuckDuckGo redirect unwrapping to `_ddg_results`/`web.search`, and hardened Wikipedia summary error handling (`DisambiguationError`, `PageError`, empty summaries, network errors, and redaction).
- **Phase 3 / M2.3 agent runtime hardening (`ultron/security/redact.py`, `ultron/agent/provider.py`, `ultron/agent/runtime.py`, `ultron/cli.py`, `tests/test_agent_loop.py`, `tests/test_groq_provider.py`):** redacted `ULTRON_CLIENT_TOKEN` and tool results/errors before storage or provider transmission, rejected whitespace-only tool names and non-string tool arguments, added stop reasons for early exits and exposed `stop_reason`/`error_code` in `AgentReport.public()`, normalized whitespace for duplicate detection, refused ambiguous side-effecting retries with altered arguments after malformed turns, enforced cancellation/deadline checks between multi-call turns while preserving completed evidence, and prevented false verified completion when read-only or state-changing tools fail.
- **Local knowledge base / RAG hardening (`knowledge.py`, `tests/test_knowledge.py`):** validated index and entry shapes against corrupt JSON, added per-document `chunk_id` provenance (`source#chunk-N`) to indexed entries and retrieved context, handled empty LLM replies with a source-context fallback, and avoided logging raw exception text.
- **Phase 5 read-only project inspection (`ultron/tools/paths.py`, `ultron/tools/project.py`, `ultron/agent/catalog.py`, `main.py`, `tests/test_project_inspect.py`):** implemented and registered `project.inspect` (`RiskTier.OBSERVE`, `external_content=True`) with canonical root confinement (`resolve_project_root`, `resolve_confined_path`), rejection of traversal/absolute paths/symlinks (`O_NOFOLLOW`), exclusion of `.env*`/credentials/keys/tokens (`is_sensitive_path`), bounded depth/entries/file sizes/duration, graceful handling of malformed `pyproject.toml`/`package.json`/`Cargo.toml`, and read-only Git branch/status inspection via `run_command` (`--no-optional-locks`).
- **Phase 6 read-only coding-partner foundation (`ultron/tools/project.py`, `ultron/agent/catalog.py`, `main.py`, `docs/M1_1_TOOL_SECURITY_BOUNDARY.md`, `docs/M2_0_AGENT_LOOP.md`, `tests/test_policy_boundary.py`, `tests/test_project_explain_and_search.py`):** implemented and registered `project.explain` and `project.search` (`RiskTier.OBSERVE`, `external_content=True`), sharing Phase 5 path confinement and sensitive-file exclusion. `project.explain` provides Python AST and non-Python structural explanations with line citations, parameter/return/call/exception analysis, local module connection resolution, and `observed` vs `inferred` separation. `project.search` provides bounded `auto`/`literal`/`symbol`/`definition`/`import` search with optional extension filters and explicit partial-result (`truncated` + `stop_reason`) reporting. Both tools are strictly read-only and cannot modify files, execute repository code, or authorize state-changing actions.
- **Path-confinement hardening (`ultron/tools/paths.py`, `ultron/tools/project.py`, `tests/test_project_inspect.py`, `tests/test_project_explain_and_search.py`):** reviewed `Zie619/n8n-workflows` (`test_security.sh`, `api_server.py:validate_filename`, `test_workflows.py`) and hardened project-tool path handling (commit `a14f55068724b95a66622ce73c259aae7bdddc9b`) so percent-encoded-looking filenames are treated literally without URL decoding, traversal checks reject actual `..` path components while allowing harmless filenames containing two dots (`report..txt`, `v1..v2.diff`, `foo..bar.py`), and sensitive-directory (`.ssh`, `.aws`, `.gnupg`, `.kube`, `faces`), Windows drive-prefix, and symlink/root-confinement protections are enforced consistently across `project.inspect`, `project.explain`, and `project.search` with deterministic regression tests.
- **Verification (local sandbox and GitHub Actions):** local verification in the locked `.venv` environment passed (`compileall` exit `0`, `ruff check --no-cache .` exit `0`, `python -B -m ultron check-secrets` exit `0`, and local test result `pytest -p no:cacheprovider -q` → `890 passed, 14 skipped`, where the 14 skips are opt-in live OpenAI/Groq tests in `tests/live/test_live_providers.py`). Separately, GitHub Actions `CI` (run `38452804621` on commit `a14f55068724b95a66622ce73c259aae7bdddc9b`) passed on Python 3.10–3.13 (verified via job/step status on `api.github.com`; exact remote test counts were not read from blob logs).

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

The original documentation-review items below are historical. Current post-integration priorities are recorded in the 2026-10-16 status section at the end of this file. Do not treat a prior branch or PR status in older sections as current without rechecking GitHub.

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

## 14. New project-first voice copilot requirements — 2026-10-13

The approved direction is to make ULTRON a local-first, voice-driven project partner for a BTech student managing many CSE/ECE and interdisciplinary projects. The desired role is a capable senior-engineer mentor: help plan, implement, debug, test, review, document and present work; maintain scoped context per repository; explain consequences, alternatives, trade-offs, failure modes, verification and rollback before important decisions.

Target integrations include local Git/repositories, GitHub, terminal/IDE, Supabase, ChatGPT/Codex, Claude, Antigravity and other coding-related tools/sites. Connect through supported APIs, CLIs, MCP servers or documented interfaces first. Website access alone is not a verified integration, and no universal-control promise should be made. Browser automation must be visible and permitted; never bypass CAPTCHA, MFA, access controls or service rules.

Local-first means the orchestrator, permission policy, project context and secret handling should run locally where practical. It does not imply every model, speech or provider feature is offline. Use least privilege, OS-backed secret storage, workspace boundaries, minimal outbound context, and clear indicators for microphone, cloud processing and external actions.

Every consequential action needs a consequence-aware workflow: identify exact project/branch/account/environment; preview plan and diff; explain risks and recovery; request action-specific approval for external writes and high-impact operations; then verify with actual evidence. No automatic merge, production deployment, destructive database change or force-push by default. Preserve the user's final authority and existing work.

The six-document set now includes this target specification in prd.md, architecture.md, rules.md, phases.md and design.md; this file records handoff and sequencing. These are requirements/roadmap, not claims that integrations or behavior are implemented. Each adapter must be labelled planned, prototype, verified or unavailable and tested against its real target before advertised as working.

## 15. Recommended implementation sequence for the project copilot

1. Validate existing runtime and desktop/web architecture; keep main and existing PRs untouched.
2. Implement local project switching and read-only repository/Git analysis with explicit workspace roots.
3. Add mentor report, consequence/impact plans, evidence ledger and visible approval UI.
4. Add bounded local edits, isolated worktrees, safe command execution, test evidence and diff review.
5. Add GitHub read capabilities, then separately approval-gated writes.
6. Add and verify provider adapters individually (Codex/ChatGPT, Claude, Antigravity) using supported interfaces and minimal context.
7. Add Supabase read/staging workflows before production-capable operations.
8. Add visible browser automation only for permitted workflows lacking a suitable API.
9. Run security, privacy, reliability and target-Mac release validation before claiming product readiness.

Do not build a single unrestricted “AI can control everything” shell. Build a modular tool ecosystem with one policy gate, scoped permissions, reviewable plans and auditable outcomes.

## 16. External repository review — voice and project media — 2026-10-13

Reviewed as design/technology references for ULTRON; no external code, model weights, dependencies, or runtime integrations were copied or installed in this review.

1. **k2-fsa/OmniVoice** — https://github.com/k2-fsa/OmniVoice — Apache-2.0 repository; multilingual zero-shot TTS with voice design/cloning. Decision: evaluate as an optional TTS adapter for Hindi/English/Hinglish on the actual Apple Silicon Mac. Measure model/install size, cold start, latency, memory, pronunciation, and offline behavior. Not selected as default and not integrated.
2. **cjpais/handy** — https://github.com/cjpais/handy — MIT, offline-first cross-platform transcription app. Decision: adopt its design ideas for local STT, VAD, push-to-talk, visible recording state, cancellation, and graceful offline failure as evaluation requirements. Do not embed/fork it or assume its CLI is a stable integration API without further inspection.
3. **HeyGen** — user supplied https://github.com/heygen-comP, which did not identify a specific repository. A public HeyGen organization project, https://github.com/heygen-com/hyperframes, appears relevant to HTML-based deterministic demo-video rendering. Decision: optional future project-demo/pitch workflow only; not core ULTRON scope unless the user confirms this was the intended repo.

**Cross-cutting decision:** keep STT, VAD, language handling, wake-word detection, and TTS as separate replaceable adapters. Preserve local-first processing, explicit disclosure for remote audio, optional heavy dependencies, permission-aware voice controls, and measured language-specific quality. Voice cloning remains opt-in and consent-bound. These are research decisions and proposed acceptance criteria, not implemented features.

Research sources: OmniVoice README/license and Apple Silicon notes; Handy README/license and offline transcription workflow; HeyGen HyperFrames README and project-local agent skill documentation. See docs/research/voice-stack-repository-review.md for the comparative rationale and evaluation checklist.


## 17. Adopted development methodology — 2026-10-13

Adapted relevant practices from [obra/superpowers](https://github.com/obra/superpowers) for future Jarvis/ULTRON work. This is a project-specific workflow adaptation, not a claim that the Superpowers plugin is installed or a runtime dependency.

- Always-on project guidance: [AGENTS.md](AGENTS.md).
- Full workflow, task template, verification and Git safety: [docs/development-workflow.md](docs/development-workflow.md).
- Key practices: clarify non-trivial requirements; plan exact files/tests; test-first bug fixes; root-cause debugging; small reversible changes; independent review for major changes; fresh verification before completion claims.
- Preserve existing branches and draft PRs; no unapproved push, merge, deployment, or publication.
- Keep process proportional: trivial tasks do not need a formal design document or multi-agent workflow.
- Do not copy Superpowers skill text wholesale. Reuse the ideas in our own project context and preserve its MIT notice if any substantial portions are ever copied.


## 20. External repository review — Ruflo — 2026-10-13

Reviewed https://github.com/ruvnet/ruflo (repository license metadata: MIT) as a large agent-harness/orchestration reference. Decision: **do not integrate the full Ruflo harness, CLI, MCP catalog, plugins, swarms, federation, or adaptive memory** at this stage. ULTRON already has a provider-neutral agent runtime and central registry/policy; a second harness would add overlapping orchestration and security surface before a demonstrated need exists.

Useful ideas recorded for our roadmap: minimal structured per-task event history; cumulative step/time/retry/output budgets; action-specific least-privilege approvals and easy cancellation/revocation; bounded privacy-conscious audit retention; keyless integration smoke tests separated from live provider/Mac tests; capability labels that distinguish mocked, blocked, skipped, and verified behavior. Treat upstream performance and feature statements as claims until benchmarked in ULTRON's own environment.

Detailed report: [docs/research/ruflo-review.md](docs/research/ruflo-review.md). No source code, dependencies, models, plugins or services were installed/copied. Research only.


## 21. GitHub account repository audit — 2026-10-13

Reviewed repository inventories and relevant docs/source directories across 18 owned repositories and 2 additional collaborator repositories accessible to the linked GitHub account. The full report is [docs/research/github-account-audit-2026-10-13.md](docs/research/github-account-audit-2026-10-13.md).

Highest-value reusable patterns:
- From GraphOne: shared bounded HTTP, classified retries, source adapters, schema/freshness validation, idempotency, provenance, explicit failure counts, provider fallback, and deterministic fixtures.
- From AI Orbit Tools Ingestion: source registry, separate discovery from official-source verification, do not guess blocked/missing facts, explainable deduplication, resumable checkpoints, and source-specific fixtures.
- From IS Copilot: staged retrieval/reranking/evidence assembly, confidence and unknown states, lexical fallback, and explicit Hindi/Hinglish/PDF use cases.
- From ULTRON itself: extend the current provider-neutral runtime, bounded agent loop, central tool registry/policy, authenticated HUD, optional RAG, and existing test seams rather than introducing a second orchestration framework.
- Candidate future research sources: arXiv Atom, OpenAlex, selected RSS feeds, Hacker News Algolia, Hugging Face metadata, and OpenRouter model metadata. These are not integrated or approved as production dependencies. Remote OK is only relevant if a career-search workflow is explicitly in scope.

No code, dependency, dataset, personal media, or private repository content was copied. No tests were run across all repositories; this was a targeted reuse audit, not a line-by-line security audit. No PR was opened, no merge/deployment occurred, and main/existing PRs were left unchanged.


## 22. Post-integration status and next safe milestone — 2026-10-16

### Verified integration state

- `main` is at `88dabc6cd41a6f71082db9b48aaa2a48e3cfc6a2` after merging PRs #5, #6, and #4 in that order.
- Latest main CI passed all four supported Python versions (3.10–3.13). This is CI evidence only, not live provider or macOS hardware validation.
- Before PR #7 was opened, no open PRs were found in the latest recheck.
- The merged tree now includes the provider-neutral bounded agent loop, central policy/registry, Groq and Ollama adapters, local-only provider guard, bounded model racing, AutoTune, opt-in Smart Router, and opt-in advisory ensemble. Feature descriptions in older sections may predate this integration; use source and current tests as authority.

### Next milestone recommendation: Phase 7.1 — safe local knowledge and memory continuity

Start with a fresh source/test audit, not a speculative rewrite. Existing `knowledge.py` already has offline embeddings, chunk provenance, malformed-index handling, and source-context fallback. Preserve those behaviors. The first work item should be a focused review of resource limits, path confinement, index validation, retrieval determinism, and prompt-injection containment, followed by regression tests for confirmed defects only.

Acceptance criteria:
- Knowledge indexing stays confined to the configured knowledge directory and does not follow symlinks outside it.
- Explicit limits bound file count, file bytes, extracted text, chunk count, embedding batches, index size, and retrieval context.
- Index entries are validated for shape, embedding dimensions, finite numeric values, and reasonable lengths before ranking.
- Source labels are useful and stable without leaking unrelated absolute local paths.
- Retrieved document content is treated only as untrusted evidence; it cannot authorize or trigger tools.
- Existing offline fallback, chunk provenance, and malformed-index behavior remain covered by tests.
- Run focused tests, full suite, lint, compile, and secret scan on the final tree before proposing a merge. Live provider and macOS checks must be reported separately.

### Explicitly unresolved

- No live Groq/OpenAI/Ollama validation was run during the integration; perform provider-specific tests on the owner's machine when credentials/services are available.
- macOS microphone, wake word, TTS, camera, and app lifecycle need target-Mac smoke testing.
- Choose and document the repository license only after reviewing the G0DM0D3 AGPL-3.0 reference and intended distribution. Do not add a license by assumption.
- Do not claim that Arena/local filesystem work was performed by this GitHub-only integration process.

## 23. Phase 7.1 first slice — bounded knowledge indexing and retrieval — 2026-10-16

- The previous session's local backup (`jarvis-assistant-reconciliation-backup-2026-10-16-105650`) was **not accessible** in the new environment, and an exhaustive search (local reflog/stash/fsck, all 19 remote branches, full git history, the owner's other GitHub repositories) found **no trace** of the prior voice-pipeline or task-continuity work. Per the owner's decision, that work is being **re-implemented from scratch, test-first**, starting with Phase 7.1. Nothing has been claimed as recovered.
- Branch `integration/knowledge-voice-task-continuity` was created from verified `origin/main` (`de166b6ddf42aacb1508ac29746c6b7afd5cec1c`). PR #8 (knowledge symlink confinement) remains open and untouched; its scope is deliberately excluded from this slice to avoid duplicating its overlap.
- Fresh audit of `knowledge.py` against the Phase 7.1 acceptance criteria: symlink confinement (PR #8 scope), basename-only source labels, and untrusted-evidence handling (answers are never re-routed as commands) were already satisfied. Confirmed defects: unbounded indexing resources and weak retrieval validation.
- Implemented on the branch (test-first; 9 regression tests added to `tests/test_knowledge.py`): explicit bounds for file count, per-file bytes, PDF pages, extracted text, chunks per document, total entries, and index file size (on write and read); deterministic (sorted) document order; retrieval now skips entries with mismatched embedding dimensions, non-finite values, or oversized text, and bounds total retrieved context.
- Verified on the branch: `compileall` OK, `ruff check` clean, `python -B -m ultron check-secrets` clean, `pytest -p no:cacheprovider -q` → **1175 passed, 21 skipped** (skips are opt-in live provider tests only; baseline before this change was 1166 passed). Live provider and macOS checks remain out of scope for CI.
- Remaining continuity work on the same branch: review PR #8's knowledge-security overlap, then the modular Hindi/English/Hinglish voice pipeline and bounded task persistence with safe manual resume and memory redaction, each test-first. No push, PR, or merge without owner approval of the final diff and test results.
