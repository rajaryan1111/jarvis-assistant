# Jarvis / ULTRON — Product Requirements Document

**Status:** Revised v2.0 — collaborative personal AI assistant  
**Product identity:** Balanced ULTRON: a voice-first AI partner for study, research, coding, and creative building  
**Primary platform:** macOS  
**Execution model:** Local capabilities where practical; cloud AI when useful

## 1. Product vision

Build a capable, dependable voice assistant inspired by the *working relationship* Tony Stark has with JARVIS: a natural, proactive-feeling technical partner that helps its owner think, learn, build, and get work done. This is a product inspiration, not a promise of fictional autonomy or unrestricted control.

ULTRON should be able to collaborate with the user on:
- **Assignments and studying:** understand a prompt, explain concepts at the right level, outline answers, work through problems, use the user's notes as the primary source when requested, and help format deliverables.
- **Research projects:** refine research questions, review literature supplied or found through permitted sources, compare claims, track citations and uncertainty, plan methodology, inspect datasets, and help build reproducible experiments.
- **Coding and debugging:** understand an existing repository, explain code, plan changes, write and review patches, run tests when authorized, diagnose errors, and avoid unrelated changes.
- **Website and app creation:** turn an idea into requirements, UI/UX, architecture, implementation tasks, working code, responsive layouts, tests, and deployment guidance.
- **Everyday computer assistance:** use voice, memory, vision, and narrowly scoped computer tools to gather context and carry out approved actions.

The aim is not simply a chatbot that speaks. It is a collaborative assistant that understands the current task, works across tools and files, keeps track of decisions, shows its work through evidence, and asks the owner when a decision or permission is needed.

## 2. Target user and operating context

The initial product is a single-user assistant for its owner on macOS. It should support natural spoken interaction with text/HUD/CLI fallbacks. It should adapt between tutor, research partner, coding partner, and website-building partner without pretending to be infallible.

## 3. Product principles

1. **Collaborate, don't merely answer:** help plan, execute, verify, and iterate.
2. **Voice first, not voice only:** speech is the preferred interaction; text and HUD remain reliable controls.
3. **Reliability before spectacle:** report what happened, what failed, and what remains.
4. **Local first where practical:** prefer local tools and storage; use cloud AI when it adds value and disclose relevant data flow.
5. **User remains in control:** ask before consequential actions, external publication, deployment, destructive edits, purchases, or sending messages.
6. **Security outside the model:** model output is a proposal, never authorization.
7. **Evidence-led work:** distinguish sourced facts, inference, generated drafts, and verified results.
8. **Respect project context:** inspect the existing repo, notes, constraints, and style before changing anything.
9. **Incremental and reversible:** small diffs, tests, checkpoints, and clear rollback paths.
10. **Honest academic and research support:** help the user understand and produce their own work; preserve citations and source fidelity.

## 4. Core collaboration workflows

### 4.1 Assignments and study
- Accept a spoken or typed question, assignment prompt, rubric, lecture note, PDF, image, or question bank.
- When asked to use supplied notes, ground the answer in those notes, preserve their terminology and mark unsupported gaps instead of silently substituting general knowledge.
- Explain difficult ideas step by step, provide examples and exam-oriented structure when requested, and create revision plans, flashcards, or practice questions.
- Help draft and revise reports, presentations, lab records, and code exercises.
- Flag uncertain facts, missing inputs, and citation needs. Do not fabricate quotations, experiments, references, or results.

### 4.2 Research projects
- Help turn a broad idea into a research question, objectives, scope, hypotheses, and evaluation criteria.
- Search permitted sources when requested; summarize and compare evidence with links/citations and publication context.
- Maintain a literature matrix, dataset notes, experiment plan, reproducibility checklist, and decision log.
- Assist with data cleaning, analysis scripts, visualizations, and paper/report structure.
- Separate source-supported statements from inference and proposed work. Do not invent sources or claim experiments were run when they were not.

### 4.3 Coding and repository work
- Inspect repository URL, branch, HEAD, working tree, tests, and relevant docs before edits.
- Explain existing code and errors, propose a small plan, make focused changes, and add regression tests.
- Run available checks and report exact results, failures, and skips.
- Avoid unrelated refactors, secret exposure, unsafe shell execution, and duplicate side effects.
- Never commit, push, merge, open/update a PR, or modify remote Git state unless the user explicitly authorizes that action. When authorized, preserve unrelated branches and PRs.

### 4.4 Website and app creation
- Convert the idea into audience, content, user flows, requirements, visual direction, architecture, and acceptance criteria.
- Help create responsive accessible UI, backend/API, persistence, tests, and setup/deployment documentation.
- Prefer maintainable, incremental implementation over impressive but fragile demos.
- Test mobile and desktop layouts; verify links, forms, error/loading states, and keyboard/accessibility basics.
- Do not deploy, purchase domains, expose secrets, or publish user content without explicit approval.

### 4.5 Voice and computer assistance
- Accept natural speech and provide useful spoken answers.
- Give concise listening/processing/approval/failure feedback.
- Use relevant local context only with appropriate permissions.
- Offer previews before consequential computer actions and support safe cancellation where feasible.
- Never let voice recognition alone approve protected actions.

## 5. Functional requirements

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-01 | Voice conversation | Configured microphone input and macOS speech output work with clear fallback/error states. |
| FR-02 | Task collaboration | Assistant can clarify, plan, execute permitted steps, verify evidence, and summarize next actions. |
| FR-03 | Assignment support | Can work from supplied prompts/notes and identify when a point is unsupported by the source. |
| FR-04 | Research support | Source-aware summaries, citations, uncertainty, and reproducible experiment plans. |
| FR-05 | Coding support | Repo-aware focused changes, tests, diagnostics, and no unapproved remote Git mutations. |
| FR-06 | Website building | Requirements-to-implementation workflow with responsive/accessibility checks and deployment guidance. |
| FR-07 | Bounded agent loop | Tool use, model calls, context, duration, output, and retries have enforced limits. |
| FR-08 | Tool validation | Unknown, malformed, unavailable, or unauthorized calls do not execute. |
| FR-09 | Safe authorization | All state-changing operations pass through central registry and policy. |
| FR-10 | Protected approvals | Consequential/destructive actions require authorized HUD confirmation; voice cannot approve. |
| FR-11 | Honest completion | Success is reported only when supported by evidence; otherwise status is partial/unverified/failed. |
| FR-12 | Memory and continuity | User-approved project decisions and task state can be inspected, updated, and deleted. |
| FR-13 | Provider choice | Provider selection is explicit; credentials remain isolated and secret. |
| FR-14 | Local/cloud clarity | Explain relevant provider/external-service data flow; don't promise full offline operation. |
| FR-15 | Diagnostics | Safe, content-minimizing error and execution summaries. |
| FR-16 | Optional vision | Useful visual context only with permission, clear retention rules, and graceful failure. |

## 6. Non-functional requirements

**Security and privacy:** Never commit keys, tokens, recordings, screenshots, or private memory. Keep loopback/debug-off defaults, authentication, CSRF/Origin protections, risk tiers, and confirmation gates. Treat model output, web results, retrieved documents, and tool output as untrusted. Face recognition is not authentication.

**Reliability:** Use bounded timeouts/retries; preserve completed evidence; avoid repeating actions after uncertain outcomes; fail closed when authorization is unclear.

**Maintainability:** Keep providers replaceable, tools narrow, optional heavy dependencies optional, and milestones testable. Measure actual Mac voice performance before setting targets.

**Research quality:** Preserve provenance, links, dates, and uncertainty. Do not fabricate citations or empirical results.

**Academic integrity:** Help the user learn, reason, draft, and debug. Follow the user's institution/course rules; never claim the user personally performed work that did not occur.

## 7. Interaction model

The assistant should feel like a calm, technically capable teammate. It should remember relevant approved project context, explain its plan briefly, work in manageable steps, ask one focused question when blocked, and report outcomes in plain language. English is baseline; Hinglish can improve incrementally. Users should be able to switch naturally between voice and text.

For larger tasks, use a loop: **understand → plan → do one bounded step → inspect evidence → continue or ask → summarize**.

## 8. Scope boundaries and non-goals

- No unrestricted autonomous computer control or arbitrary model-generated shell execution.
- No model-generated approval of consequential/destructive actions.
- No fabricated citations, test outcomes, screenshots, deployments, or completed actions.
- No unapproved Git commit/push/merge, PR changes, external publication, or deployment.
- No promise that all features work offline or that every generated program is correct.
- No multi-user enterprise service in the initial product.
- No wholesale rewrite or agent framework adoption without a measured need.

## 9. Success measures

- Deterministic tests, lint, compile checks, and secret scans pass.
- No unauthorized execution, duplicate completed action, or false verified-success status in regression tests.
- Assignment answers respect provided-source constraints when requested.
- Research summaries preserve traceable citations and distinguish evidence from inference.
- Coding tasks have reviewable diffs and accurately reported test outcomes.
- Website tasks pass agreed responsive, functional, and accessibility checks.
- Actual Mac measurements cover voice latency, recognition failures, interruption, and speech playback.
- Each milestone records reproducible acceptance checks and known limitations.

Set numeric latency/accuracy goals only after measuring a baseline.

## 10. Priority order

1. Protect repository and security boundaries; keep the current foundation stable.
2. Establish this product/architecture/engineering documentation.
3. Verify runtime reliability and truthful status reporting.
4. Make voice/microphone interaction reliable.
5. Build a task workspace for assignments, research, coding, and website projects.
6. Add inspectable project memory and local retrieval.
7. Improve vision and contextual awareness.
8. Expand automation only when useful, testable, and authorized.

## 11. Definition of done

Scope and acceptance criteria are clear; code matches architecture; relevant checks are run and reported; source/citations and limitations are preserved; diff is reviewed; docs are updated. Remote Git actions require explicit authorization and must be verified afterward.

## 12. Open decisions

- Which workflows must work fully offline?
- Which project files/notes may persist in memory, and for how long?
- What voice latency/interruption targets are acceptable?
- Which research sources and citation formats should be preferred?
- Which website frameworks/templates should be defaulted to?
- What evidence verifies each class of computer action?
- Which proactive reminders or follow-ups are useful without being intrusive?
