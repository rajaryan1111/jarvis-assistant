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
- Follow-up hardening (independent review fixes, test-first, 5 more regression tests): reconciled the two capacity limits after measuring a realistic 384-dim entry at ~9.3 KiB — `MAX_TOTAL_CHUNKS` is now 6,000 so the advertised chunk limit is reachable within the 64 MiB `MAX_INDEX_BYTES` backstop (~7,200 worst-case entries fit), and the refusal message names both limits. PDF extraction now accumulates incrementally and stops extracting pages once the character budget is spent (pypdf per-page parser allocation remains an accepted, documented limitation). Index serialization streams one entry at a time through the same atomic temp-file replacement, so the full JSON string and a second UTF-8 copy are never materialized; the on-disk format is byte-identical and pinned by a test. Verified: `pytest` → 1180 passed, 21 skipped; ruff, compileall, check-secrets clean; `uv.lock` untouched (no dependency changes; `uv` unavailable in the sandbox, CI validates).

## 24. Phase 7.1 sprint hardening, audit corrections and two-PR integration prep — 2026-10-16

- Documentation correction first: the per-document read bound counts **characters, not bytes** (the constant is `MAX_FILE_CHARS`, 8 MiB of characters; non-BMP text can occupy up to ~4x that in memory). Earlier notes saying "per-file bytes" were wrong. `_load_index()` now raises `_IndexTooLarge` for an over-cap index, which `answer_from_knowledge()` reports as an oversized index with a rebuild hint instead of the misleading "no indexed notes yet", and the question embedding is validated for finiteness (`q_values`) so a NaN/empty vector can no longer produce misleading ranked results. Two regression tests cover both.
- Independent audit of PR #9 (read-only) found no Critical/High code defects; the implementation was sound. Three real gaps were then fixed test-first on the same branch (each new test failed before its fix): the rebuild now also refuses files that resolve outside the knowledge root (`_within_docs_root`, defence in depth behind discovery, so a discovery regression or planted link cannot pull outside data into the index); the success message now counts only files that actually yielded text (it previously counted unextractable files, overstating coverage); a mid-stream write failure is pinned to leave the previous index byte-identical with no temp files, and the index file is pinned to 0600. Knowledge suite: 23 tests.
- Sprint audit of the rest of the assistant found and fixed two more real defects: the chat transcript (`ultron/history.py`) was written with default umask permissions (typically 0644) via a hand-rolled temp+replace — it now goes through the shared atomic writer (0600, `O_EXCL` temp, fsync, temp cleanup on failure), matching the knowledge index and the data directory's 0700; and `load_memory()` read `memory.json` without an encoding and swallowed every failure into an empty structure, so an existing-but-unreadable file was silently replaced by the next save (erasing notes/reminders) — the read is now pinned to utf-8 and an unreadable file is moved aside as `memory.json.unreadable-<timestamp>` (recoverable, never deleted) with a printed notice. Two regression tests each.
- PR interaction: PR #8 (symlink confinement, head `8c77a65`, base `de166b6`) and PR #9 both append tests at the end of `tests/test_knowledge.py`, so **whichever merges second hits a trivial add/add conflict**. Verified by `git merge-tree`: `knowledge.py` auto-merges cleanly and all 19 knowledge tests (18 from #9 + 1 from #8) pass against the merged code. Recommended order: merge PR #8 first, then update PR #9's branch by keeping **both** appended test blocks and re-running CI. PR #8's security fix is deliberately NOT merged into PR #9's branch.
- Verified in the sandbox: `pytest -p no:cacheprovider -q` → **1191 passed, 21 skipped** (baseline on `main` was 1166 passed; skips are opt-in live provider tests only), `ruff check` clean, `compileall` clean, `python -B -m ultron check-secrets` clean, `git diff --check` clean. CI (GitHub Actions, Python 3.10–3.13) ran compileall + ruff + check-secrets + pytest. **Not verified:** `uv sync --locked` (no `uv` in the sandbox; CI uses the committed `uv.lock`), live-provider tests (opt-in), macOS-specific behaviour (Linux sandbox).
- Remaining known limitations (documented, not fixed): pypdf per-page parsing allocation is outside our control (page/char caps limit but do not eliminate it); `_load_index()` materializes the whole index in memory per query (bounded by the 64 MiB cap, ~2–4x expansion as Python objects); basename-only source labels can collide for same-named files in different directories; no lock serializes concurrent rebuilds (each write is atomic, last writer wins); the secret scanner skips files larger than 2 MB by design.

## 25. Voice pipeline and task continuity — 2026-10-16

- **Voice (new `ultron/voice/` package).** Audit of the existing surface: `main.listen()` hardcoded `recognize_google(language="en-IN")` and printed the raw exception (`print("Listen error:", e)`); `wakeword_listener.listen_command()` duplicated the same hardcoded locale; `ultron/tools/macos.py` `SpeakTool` already validates a configurable `voice` (default "Daniel"). Implemented as three separated, individually testable stages plus a pipeline: `languages.py` (en/hi/hinglish profiles and locales `en-IN`/`hi-IN`/`en-IN` — there is no Hinglish locale, romanised Hindi is recognised with the English locale and classified from the transcript; Devanagari → hi, ≥2 romanised markers → hinglish, else en as the safe default; strict spoken-switch parsing so "how do I say water in hindi" still reaches the router), `transcribe.py` (one `GoogleTranscriber` with typed failures `unavailable`/`timeout`/`no_match`/`error`; no raw recogniser text reaches the user or logs), `pipeline.py` (`VoicePipeline` with injectable transcriber/responder/speaker; a spoken switch is handled before the responder runs; auto mode follows detection from the next turn — the one-turn lag is deliberate and pinned by a test). `main.listen()` and `wakeword_listener.listen_command()` now share that transcriber; defaults preserve existing behaviour (English, same `You: ...` line, `""` on failure). 38 mock-based tests; **no microphone, network recogniser, TTS binary, or credential was used**, and no provider capability was invented. No per-language `say` voice is auto-selected (machine-specific voices cannot be probed here); `ULTRON_VOICE_LANGUAGE` (en|hi|hinglish|auto) selects the mode.
- **Task continuity (new `ultron/tasks.py`).** There was no task persistence at all. Tasks are one bounded JSON document under `ULTRON_DATA_DIR`: ≤200 tasks, capped text fields, a strictly validated versioned schema (one invalid entry invalidates the document, which is then quarantined as `tasks.json.unreadable-<ts>` — never silently repaired, never overwriting valid data), every text field redacted on write *and* read, written 0600 through the shared atomic writer, and a task left `RUNNING` by a dead process is reported `INTERRUPTED` on the next start (surfaced by `main.report_interrupted_tasks()`). Resuming restores context only: a task whose steps could send messages, change/delete files or spend money requires explicit confirmation, the confirmation is persisted, and a fresh interruption clears it — so a resume never re-executes anything by itself. 44 tests: lifecycle, restart recovery, corrupt/oversized/invalid documents, interrupted writes (failure-injected through the atomic writer), duplicate resumes, bounds, redaction and 0600 permissions.
- Verified on the branch: `pytest -p no:cacheprovider -q` → **1273 passed, 21 skipped** (baseline on `main` was 1166; skips are opt-in live-provider tests only), `ruff check` clean, `compileall` clean, `python -B -m ultron check-secrets` clean, `git diff --check` clean. **Not verified (unchanged):** `uv sync --locked` (no `uv` in the sandbox), live providers, macOS microphone/wake-word/TTS behaviour, hardware-dependent voice paths.
- Deliberately not done: no large orchestration framework, no new dependencies (`uv.lock` untouched), no claim that live or macOS behaviour was tested, and PR #8/#9 remain unmerged.

## 26. PR #8 / PR #9 integration preparation — 2026-10-16

- **The previously prepared integration branch was stale.** `integration/knowledge-hardening-merged` @ `8a6b25c` was built from `5299f69`, i.e. *before* the voice-pipeline and task-continuity commits; it contains neither `ultron/voice/` nor `ultron/tasks.py` (verified with `git merge-base --is-ancestor` and `git ls-tree`). It must not be used.
- **New prepared integration branch:** `integration/pr8-pr9-integration-2026-10-16` @ `4530f7dee71f1f17aa8360649083255894732ab4`, a merge of PR #8's head `8c77a65` into PR #9's head `a7ddc8e` (parents recorded in that order). Only `tests/test_knowledge.py` conflicted; `knowledge.py` auto-merged.
- **Conflict resolution preserved both sides' tests verbatim:** 24 `def test_` in `tests/test_knowledge.py` = PR #9's 23 + PR #8's `test_knowledge_rebuild_skips_symlinked_files_outside_docs_root`, plus the `from pathlib import Path` import that PR #8's test needs. Proven by diffing the resolved file against each parent: 0 test functions removed from either side, and the only addition relative to PR #9 is PR #8's test block. `knowledge.py` keeps both confinement layers — PR #8's `_list_documents()` refuses symlink targets outside the docs root, PR #9's `_within_docs_root()` refuses out-of-root paths discovered any other way.
- **Verified on the exact candidate tree:** `pytest -p no:cacheprovider -q` → **1274 passed, 21 skipped** (1273 from PR #9 plus PR #8's 1 test; the 21 skips are opt-in live-provider tests), `ruff check` clean, `compileall` clean, `python -B -m ultron check-secrets` clean, `git diff --check` clean. The environment was rebuilt from scratch for this verification (system Python 3.11.2, pytest 9.1.1, ruff 0.17.1) after the previous sandbox's virtualenv was lost; both PRs' CI runs (Python 3.10–3.13) were re-confirmed green independently.
- **Not run locally:** `uv sync --locked` — no `uv` binary in the sandbox. CI runs it on every PR (`uv sync --locked --extra dev`), and neither `pyproject.toml` nor `uv.lock` is modified by PR #8, PR #9 or the merge, so the lock remains valid; CI on the session branch (`3c686b1`, run `38943374002`, 4/4 pass) re-confirmed it. The candidate branch itself gets **no CI**, because the workflow only triggers on pushes to `main`/`arena/**` and on PRs targeting `main` — opening a PR for it is the remaining step and needs owner approval.
- **Recommended merge order:** merge PR #8 first (small, self-contained security fix), then update PR #9's branch by keeping both appended test blocks — or take the prepared integration branch as-is. Nothing was merged, `main` was not touched, and no PR was opened for the integration branch.

## 27. Completion sprint — audit findings, verification hardening and release prep — 2026-10-16

- **Live state re-verified first** (nothing from the previous report was assumed): branch `integration/pr8-pr9-integration-2026-10-16` @ `c5fed2d` intact (parents `a7ddc8e` + `8c77a65`, 15 files / +2198/−106 vs `main` = exactly the union of PR #9's +2145/−100 and PR #8's +53/−6), PR #8 @ `8c77a65` and PR #9 @ `a7ddc8e` both open/mergeable/CI 4/4, `main` still `de166b6`, worktree clean, no divergence. The candidate branch is the working tree for this sprint.
- **Audit of the unaudited surface** (`ultron/agent/*`, `ultron/tools/project.py`, `ultron/cli.py`, `vision.py`, config, wake word): no new defects found. Recorded as sound: `provider.py` maps failures to typed kinds with `max_retries=0` (a failed provider call is never retried, so a consequential action cannot repeat); `race.py`/`ensemble.py`/`autotune.py` are all off-by-default, bounded by deadlines/budgets, and treat model output as untrusted; the tool registry is policy-gated with `minimum_risk` able only to *raise* risk; every subprocess call is argv-only with a timeout and no `shell=True`; `project.py` confines paths via `resolve_confined_path` and refuses sensitive names.
- **Coverage-guided hardening (24 new tests, no test weakened).** Three groups of security-critical rejection branches had no test at all: (1) `redact.py` 75%→98% — the `RedactingFilter` path that masks a secret inside a logged exception's **traceback**, the path that survives a record whose `getMessage()` raises, and `install_log_redaction()` itself (one filter per handler, idempotent, and really redacting through a live handler); (2) `tools/paths.py resolve_confined_path` — every non-relative input shape (non-string, over-length, NUL/control chars, absolute, `~`, backslash, drive letter, UNC) plus a symlink planted mid-path, with a positive control; (3) `read_confined_text` — symlink, outside-root, sensitive-name, directory, oversized and unreadable refusals. Suite: **1298 passed, 21 skipped** (was 1274).
- **The repository's own guard caught me, correctly.** The first draft of a new test fixture used an `sk-` prefixed value and `test_secret_scan` failed the suite. The fixture was changed to a non-key-shaped value passed as an extra secret — the scanner was not touched. Also fixed in the same pass: `.coverage` (a generated artifact) had been committed; it is now untracked and git-ignored.
- **Two documented backstops remain uncovered by design** (defence in depth, not reachable without a race): `read_confined_text`'s post-`fstat` oversized check, and `resolve_confined_path`'s out-of-root check behind the `..`/symlink rejections.
- **Real-device verification is now written down.** `docs/MAC_SMOKE_TEST.md` is a 9-section reproducible MacBook checklist (mic/STT, wake word, en/hi/hinglish, TTS, provider errors and timeouts, memory/history/task recovery after restart, camera and system controls, permission-denied and unavailable-device behaviour) with exact expected strings, and a final section listing what CI already proves so it is not re-tested by hand. Every claim in it was checked against the code (the first draft wrongly cited `--timeout`/`--phrase-time-limit` CLI flags that do not exist, `security --disable`, and `history show`).
- **`scripts/verify.py`** runs the whole deterministic battery (pytest, ruff, compileall, check-secrets, `git diff --check`) and prints one PASS/FAIL summary with a non-zero exit on failure, so it works as a pre-push gate on the Mac.
- **`.env.example` was documenting 8 of ~75 configuration variables.** It now documents the full surface with the *real* defaults (verified by loading it: server `127.0.0.1:5001`, agent `openai/gpt-4.1-mini`, 6 steps, 20 s timeouts, voice `en`/`observe`), grouped by area, with placeholders only — the secret scanner reports no findings against it.
- **Release status:** see `docs/RELEASE_CHECKLIST.md`. Not production-ready: the real-device rows in `MAC_SMOKE_TEST.md`, live-provider behaviour, and the candidate branch's own CI run are all still unverified. Nothing was merged, `main` was not touched, no PR was opened.

### Completion sprint — propagation and final state

- The completion-sprint tests were also pushed to **PR #9's own branch** (`integration/knowledge-voice-task-continuity`, fast-forward `a7ddc8e..e8f3f45`) so they are CI-verified across Python 3.10–3.13: run `38946165677`, **4/4 success**, 1297 passed / 21 skipped on that tree (1298 minus PR #8's one test, which that branch does not contain). PR #9 is now 12 commits / 19 files, head `e8f3f45`, still open and unmerged, and its description cites the current candidate SHA and test counts.
- The **candidate** remains `integration/pr8-pr9-integration-2026-10-16` @ `2f158a0fa367ba561da5f1756bcbf5ec17254835` (1298 passed / 21 skipped, verified locally). It and PR #9's branch now carry the same tests as separate commits — same content, different history — and the candidate additionally contains PR #8's confinement fix and test.
- The candidate still has **no CI run of its own**: `.github/workflows/ci.yml` triggers only on pushes to `main`/`arena/**` and on PRs targeting `main`. Both inputs are individually green and the exact combined tree passes the full battery locally, but the combination has not been through the matrix or `uv sync --locked`. Opening a PR for it is the remaining step and needs owner approval.
- Still unverified and disclosed as such: real microphone/STT, wake word, TTS, camera, macOS system controls, live-provider behaviour, and a genuine crash/restart on macOS — all itemized in `docs/MAC_SMOKE_TEST.md` and `docs/RELEASE_CHECKLIST.md`. The product is **not** production-ready until the P0 rows there are executed on a Mac.

### Release-readiness mission — integration PR opened, combined CI verified, two defects fixed

- **The candidate is no longer CI-less.** Opening **PR #10** (`integration/pr8-pr9-integration-2026-10-16` → `main`) gave the combined tree its own workflow runs, which the `main`/`arena/**` push triggers had denied it. Run `38947990062` on the candidate head is **4/4 success on Python 3.10–3.13**, every step green — including `Install core + dev dependencies (from uv.lock)`, i.e. `uv sync --locked --extra dev`. This retires the "combination never went through the matrix or the lock check" gap for the deterministic surface; the real-device rows stay unverified.
- **PR #10** (`https://github.com/rajaryan1111/jarvis-assistant/pull/10`): 21 commits, 26 files, +2960/−208 vs `main`, head `d20ca5f`, base `de166b6`, `state=open`, `mergeable=true`, `mergeable_state=clean`. **Not merged — merging is not authorized.** The body records the ancestry, the `tests/test_knowledge.py` conflict resolution (24 `def test_` = PR #9's 23 + PR #8's 1, proven by diffing the resolved file against each parent), the exact local results, and the explicit NOT-verified list. It was re-PATCHed after each of the two pushes that added `b7f70a5` and `d20ca5f`, because the earlier bodies quoted the superseded SHAs `a82a1b4` and `b7f70a5` — **any SHA quoted in a PR body is invalidated by the next push to that branch**.
- **Defect 1 (real, fixed test-first): `vision.py` leaked the webcam capture handle.** `_capture_frame_from_camera` called `cap.release()` only on the normal exit path, so a `cap.read()` that raised (camera unplugged mid-capture, driver fault) leaked the device handle — on macOS a leaked `AVCaptureDevice` keeps the camera light on. The existing suite only covered backend selection and gesture classification, so the leak was invisible. Fixed with `try/finally`; three tests were added to `tests/test_vision_optional.py` using a fake-`cv2` stand-in (no hardware), and the leak assertion failed `0 == 1` before the fix. `tests/test_vision_optional.py` is now 10 passing tests. **The same fake-`cv2` technique is the way to test the camera path in this environment.**
- **Defect 2 (real, fixed test-first): `main.py` printed raw exception text on 8 legacy error paths.** `print("CPU usage error:", e)` and friends echoed exception text to the terminal, which can carry URLs (including query-string credentials), paths and internal detail. They now pass through `redact()`, matching what `listen()` and the wake-word listener already did; the user-visible answer is unchanged. The regression test asserts a credential-shaped value cannot reach the terminal, and **failed pre-fix with the raw secret visible in captured stdout**. The remaining `print` sites in `main.py` are system-stat paths (psutil, vision, memory.json) that cannot carry credentials and are now uniformly redacted as defence in depth. `main.py` has no unredacted raw-exception prints left.
- **Audit surfaces checked this mission with no defect found:** `ultron/api/app.py` (host allowlist, `Origin` + `Sec-Fetch-Site` cross-site guard, CSP/`X-Frame-Options`/no-store headers, `MAX_CONTENT_LENGTH=16 KB`, message and speech length caps returning `413`, `secret_equals` constant-time token comparison, confirmation ids constant-time-compared and single-use with the expiry checked after the consume) and `ultron/security/confirmation.py` (own actions match by principal; delegated approval is restricted to actions flagged `delegated` and matches only the exact id). The API layer stays thin and delegates to the registry.
- **README documentation gaps closed:** the voice language handling (English/Hindi/Hinglish, `ULTRON_VOICE_LANGUAGE`, spoken switching, `auto`'s deliberate one-turn lag), the restart-surviving task store, `scripts/verify.py`, the Mac smoke test and release checklist links, and `ultron/voice/` + `ultron/tasks.py` in the repository structure. The install/configure/commands sections were re-checked and were already accurate.
- **Battery on the candidate tree (code commit `b7f70a5`; the branch head advances past it with documentation-only commits, so CI run ids quoted here are the ones that verified that content):** `1302 passed, 21 skipped` (21 = opt-in live-provider tests; baseline on `main` 1166); `ruff check --no-cache .` clean; `compileall` clean; `check-secrets` clean; `scripts/verify.py` 5/5 PASS; `git diff --check` clean. The push was verified a safe fast-forward against the explicit remote SHA first (`a82a1b4..b7f70a5`, one commit, five files).
- **Release status (unchanged in substance): not production-ready.** The remaining blockers are the real-device/live-provider rows — microphone/STT, wake word, TTS, camera, macOS system controls, live-provider timeouts/rate limits, and a genuine crash/restart. Voice is still mock-tested only. The repository-licensing decision is still open. PR #10 is open, CI-verified and mergeable, and **awaits owner approval to merge**.

### Final mission — integration verified, four more real defects fixed (head `ae1f003` → this work)

- **Live state re-verified before acting** (all earlier SHAs treated as historical): `main` `de166b6`; PR #8 `8c77a65`, PR #9 `e8f3f45`, PR #10 `ae1f003` — all open, unmerged, `mergeable=true`/`mergeable_state=clean`; candidate head `ae1f003` clean in the worktree. PR #10's CI run `38948540587` on that exact head: **4/4 success** (3.10–3.13), every step green including `uv sync --locked --extra dev`.
- **Integration content audited against `main` and complete.** 26 files, +2960/−208. Both confinement layers present (`_list_documents` symlink check + `_within_docs_root` read-path guard) with `MAX_FILE_CHARS`/`MAX_TOTAL_CHUNKS`/`_IndexTooLarge`/`_write_index_bounded`; `_quarantine_unreadable_memory`; `atomic_write_text`; `ultron/voice/*` (languages, pipeline, transcribe); `ultron/tasks.py` with `TaskStore`; `report_interrupted_tasks`; `vision.py` try/finally; the `.env.example`/README/checklist/smoke-test/verify-script set. **`tests/test_knowledge.py` still has exactly 24 `def test_`** = PR #9's 23 + PR #8's `test_knowledge_rebuild_skips_symlinked_files_outside_docs_root`. PR #9's three original commits (`5cc7cf5`, `ba7d092`, `16aa350`) are intact in the ancestry. No stale `/api/` route references anywhere.
- **Product smoke-tested for real, not just unit-tested.** `ultron doctor` gives a correct actionable report; the HUD server starts and answers — `/healthz` 200, `/` 302 → login, `/status` and `/ask` 401 without a token, 200 with one (a real spoken reply), 401 on a wrong token, **403 on a foreign `Origin`**. `run_jarvis.command` starts the server, waits for `/healthz` 200, and falls back to a printed login URL when no browser exists. `main.py` imports and routes with the voice extra absent.
- **Defect 3 — "what's the date" fell through to Wikipedia.** The router had `if "time" in cmd` but no `date` branch, so every date question went to the wiki/AI path and came back "Wikipedia is unavailable right now." Added `tell_date()` and an explicit branch (`"date" in cmd or "what day is it" in cmd or "what day is today" in cmd or "which day" in cmd`). **The phrasings are deliberately explicit**: a bare `"day"` also swallowed "what day is my birthday party", and a confidently wrong answer is worse than an honest one — that non-hijack behaviour has its own test.
- **Defect 4 — a fresh install could not say hello.** With no provider key — the *default* `doctor` state — `hello`, `good morning`, `thank you`, `how are you` and `flip a coin` all returned "My OpenAI key is not configured." Added `_social_reply()`: a deterministic, offline, whole-command match (wake word/"sir"/punctuation stripped first, so "open hello kitty" is never swallowed) plus coin/dice. Stdlib `random` only; no new dependency. Wired in *after* the profile branches so "what is my name" still wins.
- **Defect 5 — `VoicePipeline.run()` could raise and kill the voice loop.** Its own docstring promised a `VoiceTurn` "instead of raising", but only *returned* failures were handled. `run` drives `while True` in `main.main()` and in the wake-word listener, and `responder` is `main.handle_command` (a very large function) while `speaker` shells out to `say`. Split into `run()` (try/except → typed `error="error"`, spoken reason, **only the exception type logged** — provider/recogniser/shell text can echo a key) + `_run_stages()` + `_safe_speak()`. Three regression tests, all failed pre-fix with the raw secret visible in the traceback.
- **Defect 6 — the voice loop retried a missing microphone ~39,000 times a second.** With no `voice` extra (the first-run mistake to expect) the loop spun: measured 235,087 turns in 6 s and **874,694 log lines in 25 s**. Added `_voice_pause()` — 5 s back-off only for `unavailable`, 0 for a normal `timeout`/`no_match` — used by the loop. Verified after the fix: **3 messages in 14 s**. `run_jarvis.command` also gained `JARVIS_VOICE=1` to start the microphone loop alongside the HUD (default unchanged), and the README now states plainly which commands work with no API key.
- **Secret-scanner self-trap hit again, resolved the documented way.** A `sk-`-prefixed literal in the new pipeline test tripped the repo's own rule. The fixture is now assembled at runtime (`"sk" + "-" + ...`); the scanner rule, the test and its assertion are all unchanged.
- **Task continuity re-verified end-to-end** (not just by its tests): `pending → running → interrupted`; `recover()` turns a stale RUNNING into INTERRUPTED after a restart; `resume()` restores context and is **blocked** for an unconfirmed consequential task with the reason "this task can take real-world actions; confirm before it continues"; only `confirm_resume()` clears it (persisted, idempotent, executes nothing); non-consequential tasks resume immediately; completed/failed tasks are blocked. Corrupt `tasks.json` is quarantined to `tasks.json.unreadable-<ts>` and `TaskStore.quarantined` records it — never silently overwritten.
- **Provider layer re-verified:** `max_retries=0` (no unsafe retry of a consequential call) and a typed taxonomy (`timeout`, `not_configured`, `rate_limited`, `rejected`, `unavailable`, `bad_response`, `malformed_call`, `malformed_output`) with Groq-specific handling for 413, `tool_use_failed`, `output_parse_failed` and `insufficient_quota`. Errors log kind/type/status/code/param/request_id — **never the message or body**. `SpeakTool` is argv-only with an absolute `say` path, a voice-name regex, a 4000-char cap and the leading-`-` option-injection guard.
- **Battery on the final candidate:** **1313 passed, 21 skipped**; ruff, compileall, `check-secrets` and `scripts/verify.py` 5/5 all clean; `git diff --check` clean.
- **Release status unchanged in substance: not production-ready.** The blockers remain the real-device/live-provider rows in `docs/MAC_SMOKE_TEST.md`, the merge authorization for PR #10, and the repository-licensing decision. Everything that can be finished in this environment is finished, tested and pushed.
