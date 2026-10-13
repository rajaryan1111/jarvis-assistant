# Jarvis / ULTRON — Product Requirements Document

**Status:** Finalized v1.0  
**Product identity:** Balanced ULTRON — voice, computer control, memory, vision, and AI agents  
**Primary platform:** macOS  
**Operating principle:** Local capabilities where practical; cloud AI when useful  
**First UX priority:** Voice and microphone

## 1. Product vision
Evolve Jarvis into a reliable, modular personal AI assistant that converses naturally, listens and speaks, gathers information, uses approved tools, controls supported computer functions, works with personal knowledge, and verifies its actions. ULTRON is the long-term vision: one coherent assistant combining voice, vision, memory, reasoning, and computer interaction without surrendering user control.

## 2. Target user and problem
The initial product is a single-user assistant for its owner on macOS. The current codebase has useful voice, automation, integration, HUD, retrieval, and agent foundations, but needs a coherent interaction model, reliable recovery, clear status reporting, and a documented roadmap. Preserve useful capabilities and improve incrementally rather than rewriting everything.

## 3. Product principles
1. Reliability before novelty.
2. Voice-first interaction, with text/HUD as complementary interfaces.
3. Local-first when practical, with explicit cloud use where it adds value.
4. Every computer action uses a narrow, validated tool.
5. Security policy lives outside the language model.
6. Completion claims require evidence.
7. Modules and providers remain replaceable.
8. Build in small, testable milestones; distinguish verified from planned.

## 4. Scope
### Existing capabilities to preserve and verify
- Voice input and macOS speech output; optional wake-word support.
- OpenAI single-shot chat and study/knowledge helpers.
- Bounded multi-step agent runtime with OpenAI and Groq adapters.
- Tool catalog, schema validation, central registry, risk policy, audit, and confirmation.
- Authenticated local browser HUD/API and CLI.
- Supported macOS automation, reminders, routines, system information, and integrations.
- Optional vision and local RAG.
- Tests, linting, secret scanning, and CI.

This list describes repository capabilities, not a guarantee that every feature works on every machine. Hardware-dependent features require testing on the target Mac.

### Product improvements
- Optimize voice responsiveness, cancellation, spoken answers, and error/approval feedback first.
- Keep policy guarantees consistent across voice, CLI, HUD, and API.
- Improve bounded provider recovery without repeating completed actions.
- Make memory explicit, inspectable, and deletable.
- Improve local retrieval and distinguish user data from untrusted external content.
- Improve optional vision only when useful and privacy-aware.

## 5. Functional requirements
| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-01 | Natural language | Requests work through supported text interfaces and configured voice input. |
| FR-02 | Voice response | Useful spoken responses use the configured macOS speech path; failures are surfaced. |
| FR-03 | Multi-step work | Agent selects tools, inspects results, and continues within enforced limits. |
| FR-04 | Tool validation | Unknown, malformed, unavailable, or unauthorized calls do not execute. |
| FR-05 | Safe authorization | Every state-changing operation uses the central registry and policy. |
| FR-06 | Approval | Protected actions remain pending until authorized HUD confirmation; voice cannot approve. |
| FR-07 | Honest completion | Verified success requires evidence; uncertain/partial outcomes are reported honestly. |
| FR-08 | Provider choice | Provider selection is explicit and credentials stay isolated. |
| FR-09 | Bounded work | Model calls, tool calls, duration, context, and recovery attempts have limits. |
| FR-10 | Memory/RAG | Optional local knowledge features degrade gracefully. |
| FR-11 | Interface consistency | Voice, CLI, and HUD preserve authorization guarantees. |
| FR-12 | Local/cloud clarity | Documentation identifies local and provider-backed data flows. |
| FR-13 | Diagnostics | Safe error categories and execution summaries are available without exposing secrets/private content. |
| FR-14 | Optional vision | Vision fails clearly when hardware, models, permissions, or dependencies are unavailable. |

## 6. Non-functional requirements
**Security and privacy:** Never commit keys, tokens, recordings, screenshots, or private memory. Keep loopback/debug-off defaults, authentication, CSRF/Origin protections, risk tiers, and confirmation gates. Treat model output, web results, retrieved documents, and tool output as data, never authorization. Redact secrets. Disclose cloud data flows. Face recognition is not authentication.

**Reliability:** Use bounded timeouts/retries, preserve successful evidence during recovery, avoid repeating actions after uncertain outcomes, fail closed when authorization state is untrusted, and add regression tests for fixed bugs.

**Performance and maintainability:** Establish real Mac latency baselines before setting numerical targets. Keep heavy voice/vision/RAG dependencies optional where practical. Keep provider details in adapters, policy in the registry, and interfaces separate from business logic. Support the Python range in `pyproject.toml`.

## 7. User experience
Natural, concise, calm. English is baseline; improve Hinglish incrementally. Ask for clarification when needed, explain approvals, allow safe cancellation, and distinguish completed, failed, pending, and unverified actions. Voice/microphone is the first UX priority; HUD and CLI remain essential companion interfaces.

## 8. Non-goals
- Unrestricted autonomous computer control or arbitrary LLM-generated shell execution.
- Model-generated approval of consequential/destructive actions.
- Multi-user enterprise service in the initial product.
- Replacing the custom agent loop without measured need.
- Mandatory cloud use for every feature or a claim that every feature works offline.
- Treating face recognition as proof of identity.
- Wholesale rewrite before incremental changes are justified.

## 9. Success measures
- Deterministic regression suite, lint, compile checks, and secret scan pass.
- No unauthorized execution or duplicate completed action in tests.
- No false verified-success status in failure tests.
- Resource/retry limits are enforced.
- Live-provider results are reported separately from deterministic tests.
- Voice latency, interruption, recognition, and response quality are measured on the actual Mac.
- Each milestone has reproducible acceptance checks and documented limitations.

Set numeric latency/accuracy goals after measuring the current baseline.

## 10. Priority order
1. Protect repository and security boundaries.
2. Establish project documentation and engineering rules.
3. Verify runtime reliability and provider recovery.
4. Optimize voice/microphone interaction.
5. Improve memory and local retrieval.
6. Improve vision and contextual awareness.
7. Expand computer automation only with tests, policy review, and explicit user value.

## 11. Definition of done
Scope is clear; code matches architecture; relevant tests/security checks pass; documentation is updated; diff is reviewed; verification status is recorded. Git commits, pushes, merges, and GitHub changes require explicit authorization. Never claim remote completion without checking GitHub.

## 12. Open decisions
- Which tasks must work fully offline?
- What are acceptable voice latency/interruption targets?
- Which memory types persist by default?
- Which vision features justify permissions and resources?
- What evidence verifies each class of action?
- Which future automation features justify their complexity and risk?

Record decisions when resolved; do not silently convert proposals into implemented requirements.