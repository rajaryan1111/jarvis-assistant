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

The initial product is a single-user assistant for its owner on macOS, designed around the BTech CSE/ECE student journey. It supports natural spoken interaction with text/HUD/CLI fallbacks and adapts between tutor, research partner, coding/hardware partner, hackathon teammate, career coach, life organizer and bounded personal companion without pretending to be infallible.

## 3. Product principles

1. **Collaborate, don't merely answer:** help plan, execute, verify, and iterate.
2. **Voice first, not voice only:** speech is the preferred interaction; text and HUD remain reliable controls.
3. **Reliability before spectacle:** report what happened, what failed, and what remains.
4. **Local first where practical:** prefer local tools and storage; use cloud AI when it adds value and disclose relevant data flow.
11. **Whole-student support:** help with engineering and everyday student challenges while preserving autonomy, privacy, and real-world relationships.
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

The assistant should feel like a calm, technically capable teammate. It should remember relevant approved project context, explain its plan briefly, work in manageable steps, ask one focused question when blocked, and report outcomes in plain language. Hindi, English, and Hinglish are first-class requirements, including Romanized Hindi and Devanagari. Language-specific accuracy must be measured. Users should be able to switch naturally between voice and text.

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
2. Verify runtime reliability and truthful status reporting.
3. Build reliable Hindi/English/Hinglish voice interaction and local wake-word activation.
4. Package an installable macOS desktop app while retaining the companion website/HUD.
5. Build a shared task workspace for assignments, research, coding, and website projects.
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

## 13. Trilingual conversation and installable desktop product (v3 decision)

### Language behavior
ULTRON must understand and respond naturally in **Hindi, English, and Hinglish**. Hinglish includes Hindi-English code-switching in the same sentence, Romanized Hindi (for example, “Ultron, mera Python code debug karo”) and Hindi written in Devanagari (for example, “अल्ट्रॉन, मेरा कोड समझाओ”). The user should not have to select a language before every request.

- Detect the language and script per utterance; support mixed-language requests and technical English terms inside Hindi sentences.
- Reply in the language/style the user used most recently, unless they request another language. Allow explicit commands such as “reply in English”, “Hindi mein samjhao”, or “Hinglish mein bolo”.
- Preserve meaning, names, code, file paths, commands, and technical identifiers during transcription and translation.
- Handle accents, natural pauses, and common speech variations; ask a short clarification if confidence is too low rather than executing a risky action.
- Speech recognition, language identification, text normalization, and speech synthesis are separate replaceable capabilities. Measure Hindi, English, and Hinglish quality independently on the target Mac. Do not claim equal accuracy before testing.
- Wake-word detection for “Ultron” should work independently of the conversational language.

### Installable macOS desktop app plus website
The primary experience must be an **installable native-feeling macOS desktop app** that the owner can launch from Applications/Dock and use hands-free by saying “Ultron”, without first opening a browser or manually starting a web server. A website/HUD is also required as a companion interface for visible plans, sources, code diffs, task artifacts, settings, and secure approval flows.

- Package the local assistant and its required runtime into a versioned macOS app/installer with clear setup, permissions, upgrade, and uninstall instructions.
- After the user enables it, an optional login item may start the app at login. Do not silently enable startup or microphone access.
- Use a lightweight, privacy-conscious local wake-word listener. Keep the microphone indicator/status visible; provide mute/pause/quit controls and clear permission recovery. Prefer local wake-word detection; do not continuously stream microphone audio to a cloud provider just to detect “Ultron”.
- After wake-word detection, capture the request, process it through the configured local/cloud pipeline, and speak the response. Define timeout, false-wake, interruption, and no-network behavior.
- The desktop app and website must share the same core/task state and policy boundaries rather than becoming two divergent assistants. The website must remain usable independently for typed interaction and visual review.
- Bind any local web service to loopback by default, require authentication for protected operations, and retain existing Host/Origin/CSRF protections. Do not expose a local control API to the LAN by default.
- Local-first does not mean fully offline: disclose which speech/LLM features need internet or cloud credentials and which operations remain local.
- Website-only access is not an acceptable substitute for the installed voice/wake-word experience. Packaging and wake-word support are roadmap requirements until validated on the actual Mac.

### New acceptance criteria
- User can speak in Hindi, English, or Hinglish, including Romanized Hindi and Devanagari; the response follows the user's chosen language.
- Wake-word behavior is tested for all three language contexts, with recognition and false-wake metrics recorded.
- After installation and explicit microphone permission, saying “Ultron” can start a session without opening a browser. The app exposes visible listening/mute/quit state.
- The companion website supports typed interaction and visual inspection while using the same authorization policy and task context.
- Installer, launch-at-login opt-in, upgrade, and uninstall paths are documented and tested on a supported macOS version.
- Network requirements, audio handling, and local/cloud boundaries are clearly disclosed.

## 14. Student-life problem map: the BTech journey

ULTRON is also a **student-life companion** for the broad BTech CSE/ECE experience, not only an engineering tutor. It should help with academic, technical, career, social, practical, and personal challenges through useful workflows—not claim to solve every problem or replace human relationships and professional services.

### 14.1 Problem domains and proposed capabilities

| Domain | Common student problems | Proposed ULTRON workflows |
|---|---|---|
| Academics and exams | Large syllabus, scattered notes, backlogs, exam anxiety, confusing concepts, teacher-specific answer formats | Source-grounded notes/PDF/QB/PYQ analysis; revision prioritization; easy-language explanations; worked examples; quizzes, flashcards, viva practice and progress tracking |
| Labs and practicals | Lab records, viva questions, instrument confusion, debugging practicals, incomplete reports | Prepare from lab manuals; explain safe measurement procedures; help interpret errors and readings; organize lab-report drafts; distinguish simulated results from measurements actually taken |
| CSE learning | DSA confusion, debugging, Git conflicts, databases, APIs, web development, AI/ML and deployment issues | Guided problem solving, repo-aware diagnosis, code review, focused patches, tests, explanations and portfolio-ready documentation |
| ECE learning | Circuit analysis, analog/digital electronics, 8051/ARM, Verilog/VHDL, FPGA, PCB, sensors, embedded systems, DSP/MATLAB | Explain schematics and datasheets; help calculate and simulate; troubleshoot from supplied evidence; assist with firmware and HDL; distinguish simulation from real hardware verification |
| Projects and building | Unclear scope, poor architecture, limited budget, integration failures, weak documentation | Idea-to-requirements flow; stack/component choices; task breakdown; project memory; implementation/test loop; budget-aware alternatives; demo and report preparation |
| Hackathons and competitions | Choosing an idea, tight deadlines, team coordination, hardware/software integration, weak pitches | Compare ideas against novelty, feasibility, impact, time and budget; define MVP; assign owners/dependencies; track blockers; prepare demos, architecture and pitch practice |
| Teamwork | Unequal contributions, missed deadlines, merge conflicts, disagreements | Shared task/decision log, clear ownership, integration checklist, neutral communication drafts and escalation options |
| Career and placements | Unclear career direction, skill gaps, DSA/interview pressure, weak resume, missed internships | Role exploration; skill-gap plan; interview practice; truthful resume/project review; opportunity tracker; deadline reminders and application preparation |
| Research and innovation | Finding trustworthy sources, citations, literature gaps, dataset/method choices, reproducibility | Source-aware literature matrix; citation/provenance tracking; research-question refinement; dataset and experiment logs; transparent distinction between evidence, inference and hypothesis |
| Productivity | Procrastination, deadline collisions, unrealistic plans, context switching | Time-aware task plans; small next steps; opt-in reminders; progress reviews; safe rescheduling and resume-from-last-state |
| Friendship and loneliness | Feeling left out, homesickness, social awkwardness, lack of community | Empathetic conversation; low-pressure ways to meet people through clubs/interests; conversation practice; encouragement of real human connection |
| Dating and relationships | Expressing feelings, conflict, rejection, jealousy, breakups, boundaries | Respectful message drafting; perspective-taking; communication rehearsal; boundary reflection and emotional processing without claiming to know another person's private thoughts |
| Well-being | Stress, burnout, comparison, fear of failure, confidence dips | Optional check-ins/journaling, grounding exercises, workload reflection and support-seeking prompts; no diagnosis or claim to replace professional care |
| Money and student living | Limited allowance, hostel routines, food planning, travel and component costs | Opt-in expense/budget planning; affordable options; shopping/packing lists; meal or trip planning based on stated constraints; no unapproved purchases |
| Everyday administration | Forms, scholarships, registration, attendance, documents and deadlines | Checklist and calendar support, document organization and reminders; verify rules from authoritative sources when needed |
| Communication and creativity | Viva, presentations, public speaking, English confidence, writing, guitar, photography, video creation | Role-play, presentation/pitch rehearsal, language switching, feedback, practice plans and creative project assistance |
| Digital safety and accessibility | Phishing, account risks, information overload, accessibility barriers, different learning needs | Safety education, source evaluation, permission explanations, alternative learning formats and user-configured workflows |
| Life after college | Jobs vs higher study vs research/entrepreneurship, relocation and uncertainty | Compare options against user-stated goals, costs, constraints and evidence; help form experiments and decision plans without pretending to predict the future |

### 14.2 CSE/ECE and interdisciplinary focus

ULTRON should support both shared engineering workflows and branch-specific tools. CSE examples include algorithms, software engineering, APIs, databases, AI/ML, cybersecurity and deployment. ECE examples include circuits, electronics, microcontrollers, embedded firmware, HDL/FPGA, DSP, signal processing, sensors and PCB workflows. Interdisciplinary projects can connect software with robotics, IoT, computer vision, edge AI and sensor systems.

For hardware and lab work, ULTRON must label whether a result is theoretical, simulated, inspected from logs, or physically measured. It must never claim a board, circuit, or instrument works without appropriate evidence.

### 14.3 Student context and consent

- Support separate contexts for courses, projects, hackathons, career preparation, personal planning and private reflection.
- Store only approved useful memory; let the student inspect, correct, export and delete it.
- Make calendar, reminders, expense tracking, mood check-ins, audio, screenshots and other sensitive integrations opt-in.
- Keep private relationship and well-being conversations out of shared project/team workspaces unless the user explicitly moves selected content.
- Let the student switch between “just listen”, “help me think”, “make a plan”, and “take an approved action”.
- Do not optimize every interaction for productivity; rest, social connection and doing nothing can be valid user goals.

### 14.4 Trust and well-being boundaries

ULTRON may offer supportive conversation, reflection and practical options, but must not present itself as a human, exclusive relationship, therapist, doctor, financial adviser or guaranteed career authority. Encourage appropriate trusted-person or professional support for persistent/severe distress. Never foster emotional dependency, shame the user, or claim to know what another person secretly feels. Relationship help must respect consent and privacy and must not assist stalking, manipulation or surveillance.

Financial planning must be transparent and based on user-provided assumptions; purchases or financial transactions require explicit authorization. Academic help should support learning and follow course rules; do not fabricate experiments, citations, credentials or work performed.

### 14.5 Student-life acceptance criteria

- A student can create distinct course/project/personal contexts and inspect or delete saved memory.
- The assistant can turn a deadline list into a realistic plan and revise it when time or priorities change.
- Course answers can be grounded in supplied notes, with source gaps called out.
- Hackathon planning captures time, budget, team skills, dependencies, MVP and demo evidence.
- CSE/ECE workflows distinguish proposed code/simulations from tests and physical measurements.
- Career advice states assumptions and uncertainty and never invents achievements or guarantees placements.
- Optional personal features are opt-in, private by default and easy to disable.
- Relationship and well-being workflows preserve autonomy, consent and real-world support.
- Each workflow has tests for honest status, privacy, cancellation and permission boundaries.

## 15. Expanded success measures

In addition to the engineering acceptance criteria, measure task completion and student value: time required to find the next study step; correctness and source fidelity of notes-based answers; hackathon milestone visibility; test/verification accuracy; reminder usefulness; student-rated clarity and emotional tone; and ease of correcting/deleting memory. Use opt-in feedback and privacy-preserving aggregate metrics. Do not infer sensitive traits or score a student's emotional state without explicit consent.
