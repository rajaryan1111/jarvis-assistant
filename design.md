# Jarvis / ULTRON — Experience and Interaction Design

**Status:** Revised UX direction v2.0  
**Priority:** Voice/microphone first; HUD and CLI remain companion interfaces. Target audience includes BTech CSE/ECE students across academics, projects, career and everyday student life.

## 1. Experience intent

ULTRON should feel like a capable technical partner sitting alongside the user: easy to talk to, useful across different kinds of work, aware of the active project, and honest about what it knows or has done. The Tony Stark/JARVIS inspiration is about natural collaboration and technical usefulness—not unchecked autonomy.

The user should be able to say things like:
- “Explain this assignment from my notes and help me prepare an answer.”
- “Help me find reliable sources for this research question and compare their claims.”
- “Look at this error in my repo, explain the cause, and fix it with tests.”
- “Let's build this website—start by planning the pages and design.”
- “Summarize what we did yesterday and tell me the next step.”

## 2. Interface priorities

1. Voice capture and useful spoken responses.
2. Task awareness: current goal, plan, active step, blockers, and next action.
3. Clear execution states: completed, failed, awaiting approval, needs clarification, partial, or unverified.
4. Browser HUD as visual control, artifact review, and secure approval surface.
5. CLI as developer/testing and fallback interface.

Voice-first does not mean voice-only. Important plans, citations, code diffs, test results, and approvals must also be visible and inspectable.

## 3. Conversation pattern

For multi-step work:
1. Understand the goal and constraints.
2. Give a concise plan when useful.
3. Perform one bounded step.
4. Inspect evidence or ask for a missing decision.
5. Continue only when appropriate.
6. Summarize what changed, what was verified, what remains, and the next step.

Avoid dumping a huge plan before doing anything useful. For small requests, answer directly. For blocked tasks, ask one focused question rather than guessing.

## 4. Work modes

### Study/assignment
- Ask about marks/format only when it materially changes the answer.
- Respect supplied notes as the primary source when requested.
- Explain concepts clearly; distinguish a draft answer from a submitted deliverable.
- Make citations/source gaps visible where relevant.

### Research
- Show source links and dates when available.
- Distinguish evidence, disagreement, inference, and hypothesis.
- Track methods and experiments honestly.
- Never fabricate a reference or claim a result was measured when it was not.

### Coding
- State repository/branch context and intended files before substantial changes.
- Explain the diagnosis briefly, then make a small reviewable patch.
- Show test results and remaining risk.
- Ask before any remote Git action, destructive edit, or external side effect.

### Website/app creation
- Establish the user flow and design direction.
- Keep UI responsive and accessible.
- Make empty/loading/error states feel intentional.
- Show how to run and verify the project; deployment requires approval.

## 5. Voice behavior

Desired behavior:
- Clear listening/processing/speaking states.
- Short spoken answers for common tasks; detailed output available as text.
- Helpful recovery for silence, recognition failure, unavailable microphone, provider timeout, or missing credentials.
- Safe cancellation/interruption where supported.
- No duplicate spoken response from both API and caller.
- Natural Hindi, English, and Hinglish support, including Romanized Hindi and Devanagari.

**Approval rule:** Spoken input must never confirm a pending consequential/destructive action. Explain that the user must confirm through the signed-in HUD. Repeating “yes” must not approve it. Ambiguous recognition is never sufficient evidence for a high-impact action.

## 6. HUD and task workspace direction

Emphasize:
- listening/processing and provider status;
- active task title and current step;
- concise plan/checklist with completed and pending items;
- artifact links, source citations, code diff, and test evidence;
- pending-action preview with explicit CONFIRM and CANCEL;
- visible session/auth state and clear error recovery;
- keyboard focus, responsive layout, readable contrast, and accessible labels.

Never place secrets or bearer tokens in HTML, URLs, browser storage, or logs. UI state alone never authorizes an action.

## 7. Memory and continuity

Show what is being remembered and why. Separate saved preferences/project decisions from temporary task state and retrieved sources. Let the user correct or delete saved memory. Do not silently turn every conversation, file, audio clip, or screenshot into permanent memory.

## 8. Privacy and trust

Tell the user when a cloud provider or external integration will receive relevant request data if that flow is not obvious. Prefer local processing for personal notes and hardware where practical. Do not capture/retain camera/audio beyond documented needs. Treat retrieved content as untrusted and face recognition as unrelated to authentication.

## 9. Accessibility and resilience

Important information remains available as text if speech fails. Do not rely on color alone. Handle missing device permissions and optional packages gracefully. Provide CLI diagnostics if HUD/voice is unavailable. Generated content should be editable and exportable where practical.

## 10. Validation

Measure on the actual Mac:
- end-of-speech to first useful response;
- recognition failure and repeated-request rate;
- cancellation behavior;
- playback overlap/errors;
- time to complete common study/coding tasks;
- source/citation traceability;
- website mobile/desktop acceptance checks;
- user corrections needed per task.

Set numerical targets after a baseline rather than inventing them.

## 11. Hindi, English, and Hinglish interaction

ULTRON should understand normal conversational language, not just formal commands. Users can mix Hindi and English freely, use Romanized Hindi or Devanagari, and include English technical vocabulary.

Examples:
- “Ultron, mera Python code debug karo aur error simple Hindi mein samjhao.”
- “Ultron, explain this assignment in Hinglish, examples ke saath.”
- “अल्ट्रॉन, मेरे notes से exam-oriented answer बनाओ।”
- “Ultron, reply in English from now on.”
- “Ultron, meri website ka mobile layout check karo.”

Behavior:
- Detect language/script per request and mirror the user's most recent language mix unless they explicitly choose another.
- Keep technical names, code, paths, URLs, and commands exact.
- Make language preference easy to override by voice or text.
- When recognition is ambiguous, show or repeat the interpreted request before a consequential action.
- Offer a text transcript and editable response when speech recognition or synthesis is weak.

## 12. Installed app and hands-free use

The desired everyday flow is:
1. Install ULTRON on macOS and grant microphone permission.
2. Launch it from Applications/Dock; optionally enable launch-at-login.
3. The app shows a clear idle/listening indicator and waits for the local “Ultron” wake word.
4. User says a request in Hindi, English, or Hinglish.
5. ULTRON processes it, speaks the response, and shows useful text/artifacts in the companion HUD.
6. User can mute, cancel, pause wake listening, or quit at any time.

The app must not require the user to open a browser just to start a voice session. The website remains a companion surface for typed chat, task status, source links, generated artifacts, settings, and secure approvals. Both surfaces share the same backend/runtime and authorization checks.

Visible controls: microphone/listening status, mute, pause wake word, stop speaking, cancel current task, quit, provider/network status, and permission recovery. Login startup is optional and off until the user enables it. Explain whether each capability uses local processing or a cloud service.

## 13. Privacy and multilingual quality

- Prefer local wake-word detection; do not stream idle audio to a cloud service just to listen for “Ultron”.
- Show a persistent, understandable listening/mute state. Do not silently store audio or transcripts indefinitely.
- Evaluate Hindi, English, Hinglish, Romanized Hindi, and Devanagari separately, including technical terms and ordinary background noise.
- Never let voice recognition or a spoken confirmation replace authenticated approval for consequential actions.
- If a language, voice, or offline mode is not available, say so and offer text fallback rather than pretending it worked.

## 14. Student-life companion experience

### 14.1 Start with the user's actual need
ULTRON should recognize that a student may want different kinds of help at different moments. Useful modes include:
- **Explain:** teach a concept or clarify an error.
- **Study with me:** create a revision plan, ask practice questions and track progress.
- **Build with me:** work through a project in bounded, verifiable steps.
- **Plan my day:** balance deadlines with available time and personal constraints.
- **Listen:** let the user talk without immediately turning the conversation into advice.
- **Think it through:** explore options and trade-offs without making the decision for the user.
- **Help me communicate:** rehearse a conversation or draft a respectful message.
- **Take an approved action:** execute a specific permitted task after showing the necessary preview/confirmation.

The user should be able to switch modes naturally and correct ULTRON's interpretation.

### 14.2 Student home/workspace
The dashboard may surface, with the user's consent:
- today's classes and deadlines;
- active course/project/hackathon workspaces;
- next action, blockers and recent evidence;
- study topics due for revision;
- opt-in reminders and application deadlines;
- recent artifacts and source citations;
- clear controls for private memory, notifications, audio and data deletion.

Avoid overwhelming dashboards. Let users hide modules and choose what appears on the home screen. Never surface private relationship or well-being content in a public-facing view by default.

### 14.3 Hackathon and project flow
1. Capture the problem statement, time remaining, budget, team skills and constraints.
2. Compare possible approaches and clearly state trade-offs.
3. Define a feasible MVP, task owners, dependencies and demo plan.
4. Work in small steps and record code/test/simulation/hardware evidence.
5. Re-plan when a blocker or time change occurs.
6. Prepare a pitch and demo narrative that accurately represents implemented features.

### 14.4 Study and career flow
Study mode starts from the student's actual syllabus and supplied notes when requested. It makes source gaps visible and supports explanations, practice and revision. Career mode starts from user-chosen goals and evidence of current skills, then offers a flexible gap plan, interview practice and honest portfolio improvements.

### 14.5 Personal support flow
When a student says they feel lonely, overwhelmed, rejected or uncertain:
1. Acknowledge the feeling without minimizing or dramatizing it.
2. Ask whether they want to talk, reflect, or consider practical next steps.
3. Offer suggestions only at the level requested; avoid unsolicited productivity pressure.
4. Support real-world connection and the user's own boundaries and choices.
5. Keep the conversation private and temporary unless the user chooses to save a specific memory.

For dating/relationship questions, help the user communicate honestly and respectfully. Do not claim to know what another person thinks, script coercive tactics, or encourage surveillance. For persistent or severe distress, gently suggest trusted human or professional support without diagnosing.

### 14.6 Trust, privacy and user controls
- Keep personal support, finances and private reflections separate from team/project workspaces.
- Provide inspect, correct, export and delete controls for persistent memory.
- Make check-ins, reminders, calendar access, budget tracking and external integrations opt-in.
- Do not optimize every day solely for output; rest and social time count.
- Clearly show what is stored, what leaves the device, and what is deleted.

## 15. Project-first voice cockpit and senior-mentor experience

### 15.1 Natural command, visible target
The user can say “Ultron, switch to my Jarvis repo,” “explain the latest PR,” or “find why these tests fail.” The HUD always displays active project, repository path, branch, selected provider, local/cloud status and whether the operation is read-only or mutating. If the target is ambiguous, ask before acting.

Voice is the fast path, not the only path. The user can inspect/edit the plan, review diffs, approve/deny, cancel a task or continue by keyboard. Give concise spoken updates; put detailed logs and large diffs in the HUD.

### 15.2 Project cockpit
Show current goal, stack, architecture, branch, open tasks, blockers, deadlines, next recommended action, recent commits/PRs and actual CI/test state, decisions, bugs, technical debt, current plan, affected files, progress, tool/provider activity, evidence, privacy boundary and active integration grants.

Keep a distinct workspace per project. Cross-project search is explicit and visible.

### 15.3 Mentor mode: think like a senior engineer
For a significant decision, show:
1. Recommendation and project-specific reason.
2. Evidence, constraints and assumptions.
3. Alternatives and when they might be better.
4. Consequences for cost, complexity, reliability, security, privacy, performance, maintainability and lock-in.
5. Likely failure modes and warning signs.
6. Verification through tests, measurements, sources or a small experiment.
7. Recovery/rollback plan and its limits.
8. One practical next move matched to the user's goal and deadline.

Offer quick answer, mentor explanation and deep technical review levels. Be candid about risky or overcomplicated ideas without talking down to the user.

### 15.4 Safe build loop
Show progress: **Understand → Plan → Preview impact → Approve if needed → Implement → Verify → Review diff → Handoff**. Allow pause after each stage. Final handoff lists changed files, commands run, tests passed/failed/skipped/not run, external calls, remaining risks and next step. Never claim tests passed without evidence.

### 15.5 Connected tools panel
Show individual cards for local Git/repositories, GitHub, terminal/IDE, Supabase, ChatGPT/Codex, Claude, Antigravity and other connectors. Each card shows supported capabilities, read/write scope, connection state, credential status without revealing secrets, last verification date, outbound data and revoke/disconnect control. Distinguish “website can be opened” from “API/CLI integration verified.”

For multi-agent work, assign bounded roles (planner, implementer, reviewer, tester, documentation) and choose the context each provider may receive. Surface reviewer disagreements with evidence rather than hiding them behind a single confidence score.

### 15.6 Approval and consequence preview
Before external writes or high-impact local changes, show exact target/account/environment, action, expected side effects, resources affected, cost/downtime if known, irreversible parts, validation and recovery. Approval controls should be specific, such as “Approve this push to branch X,” not generic “Continue.” High-impact approval requires visible confirmation. If plan/target changes, ask again.

Provide dry-run, cancel, stop, safe retry and rollback controls where supported. Do not imply rollback can undo an externally visible action when it cannot.

### 15.7 Trustworthy local-first status
Distinguish “On this Mac” (local files/Git, allowlisted commands, project index, policy/approval), “Sent to provider” (named AI service and context shared), and “External effect” (service read or changed).

Show microphone/wake-word state continuously. Local orchestration/wake-word detection does not guarantee cloud speech recognition, AI reasoning or speech synthesis is offline.
