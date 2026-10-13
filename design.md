# Jarvis / ULTRON — Experience and Interaction Design

**Status:** UX direction v1.0  
**Priority:** Voice and microphone first; HUD and CLI remain companion interfaces.

## 1. Experience intent
Jarvis should feel like a capable, calm, responsive assistant—not a terminal with a personality. Interaction should be natural, transparent about limitations, and conservative when controlling the computer.

## 2. Interface priorities
1. Voice capture and spoken responses.
2. Clear execution states: completed, failed, awaiting approval, needs clarification, or unverified.
3. Browser HUD as visual control and approval surface.
4. CLI as developer/testing and fallback interface.

Voice-first does not mean voice-only. Important approval/recovery flows must remain available through the secure HUD.

## 3. Voice interaction
Desired behavior:
- Clear listening/processing state.
- Short, natural spoken answers for common requests.
- Longer details available as text when appropriate.
- Helpful recovery for no speech, recognition failure, unavailable microphone, provider timeout, or missing credentials.
- Safe cancellation/interruption where supported.
- No duplicate spoken response from both API and caller.

**Approval rule:** Spoken input must never confirm a pending consequential/destructive action. Explain that the user must confirm through the signed-in HUD. Repeating “yes” must not approve it.

English is the baseline; improve Hinglish incrementally. Ambiguous recognition is never sufficient evidence for a high-impact action.

## 4. Conversation style
Lead with the result or next action. Ask one concise clarification when needed. Do not claim success before a tool completes. For partial tasks, describe what succeeded and what remains. Explain approvals in plain language. Keep errors useful but free of secrets, raw provider bodies, and stack traces. Do not expose hidden model reasoning.

## 5. Action lifecycle
| State | User-facing meaning | Expected behavior |
|---|---|---|
| Listening | Capturing voice | Clear indicator; avoid duplicate submission |
| Processing | Interpreting/executing | Lightweight feedback; honor limits |
| Needs clarification | Ambiguous/missing input | Ask directly; do not guess high-impact details |
| Awaiting approval | Protected action needs owner | Show exact action and secure HUD path |
| Completed | Evidence supports success | State result clearly |
| Partial/unverified | Completion not proven | Describe known results and uncertainty |
| Failed | Operation failed | Explain useful next step without fabricating success |
| Cancelled | User/runtime stopped task | Confirm cancellation when known |

## 6. HUD direction
Emphasize listening/status feedback, readable conversation, separate pending-action preview, explicit CONFIRM and CANCEL, visible session/auth state, keyboard focus, responsive layout, and accessible contrast. Never place secrets or bearer tokens in HTML, URLs, browser storage, or logs. UI state alone never authorizes an action.

## 7. Privacy and trust
Tell the user when a cloud provider or external integration will be used when the flow is not obvious. Prefer local processing for personal notes and hardware where practical. Do not capture/retain camera/audio beyond documented needs. Distinguish retrieved facts from assumptions when it matters. Face recognition is not login or authorization.

## 8. Accessibility and resilience
Important information must remain available as text if speech fails. Do not rely on color alone. Handle missing device permissions and optional packages gracefully. Provide CLI diagnostics if HUD/voice is unavailable.

## 9. Validation
Measure on the actual Mac: end-of-speech to first useful response, recognition failure rate, duplicate/missed requests, cancellation, playback errors/overlap, and repetition/clarification rate. Set numeric targets after baseline measurement.