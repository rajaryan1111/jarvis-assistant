# Jarvis / ULTRON — Experience and Interaction Design

**Status:** Revised UX direction v2.0  
**Priority:** Voice/microphone first; HUD and CLI remain companion interfaces.

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
- Natural English baseline with incremental Hinglish support.

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
