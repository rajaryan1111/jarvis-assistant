# M1.2: Identity, sessions and voice trust

M1.1 made every state-changing action go through one policy boundary. M1.2 hardens the parts *around* that
boundary: who is authenticated, how long that lasts, what a stolen credential can do, and how much a voice
is trusted. The architecture and the voice-first experience are unchanged. Routine voice commands work
exactly as before.

Everything below was checked against the code. Statements about macOS behaviour were **not** verified
here (the CI and this work run on Linux); see [Manual macOS checks](#manual-macos-checks).

## 1. What was wrong (found by reading the code, not the docs)

| # | Finding (pre-M1.2) | Effect |
|---|---|---|
| 1 | Logout revocation was an in-memory set (`UsedNonces`) | After a server restart, a copied pre-logout cookie worked again until its expiry (12 h) |
| 2 | Used one-time login-link nonces were in memory | A login link could be replayed after a restart within its 2-minute window |
| 3 | `token rotate` with `ULTRON_API_TOKEN` set only printed a note | The environment variable overrides the file, so **the old token stayed fully valid** after "rotation" |
| 4 | The server read the token once at startup | After `token rotate`, the running server kept accepting the old token until restarted |
| 5 | The session's expiry was fixed when it was issued | Lowering `ULTRON_SESSION_HOURS` did not shorten existing sessions; there was no "log out everyone" short of rotating the token |
| 6 | The bearer token could approve pending actions | Any process holding the token (a script, the wake-word listener's environment) could confirm a shutdown or turn security mode off |
| 7 | Voice could approve everything by saying `confirm <code>` | The code was spoken aloud by text-to-speech, so anyone nearby, a recording, or a speaker could replay it. The spoken code was the only barrier |
| 8 | HUD speech recognition and typing were indistinguishable | "confirm 1234" picked up by the browser microphone counted as a typed approval |
| 9 | The audit log recorded `principal=session:<session id>` | A component of the cookie appeared in logs |
| 10 | `hmac.compare_digest(str, str)` raises on non-ASCII input | A crafted bearer token, login token, cookie or CSRF header caused a `500` instead of a clean `401`/`403` (it still failed closed). Now all secret comparisons use bytes (`secret_equals`) |

## 2. Authentication vs authorization

**Authentication** answers "which credential is this?":

| Credential | Where it lives | Principal | Can log in to the HUD |
|---|---|---|---|
| Owner API token | `api_token` file (0600) or `ULTRON_API_TOKEN` | `api-token` | yes (paste at `/login`, or `ultron open-hud` link) |
| Client token (new, optional) | `client_token` file (0600) or `ULTRON_CLIENT_TOKEN` | `client-token` | **never** |
| HUD session cookie | browser (HttpOnly, SameSite=Strict) | `session:<16-hex label>` | it *is* the login |
| Local microphone loop | the `python main.py` process | `local-cli` | n/a |

**Authorization** answers "may this context do this now?", in `ultron.security.policy`:

- `can_authorize`: an authenticated user channel may **request** actions (subject to the risk policy).
- `can_confirm`: may **approve** a pending CONSEQUENTIAL/DESTRUCTIVE action. True **only** for a signed-in HUD
  session using typed or clicked input. Bearer tokens (owner or client), the wake-word listener, the local
  microphone loop, HUD speech input, background services and untrusted content are all `False`.

The reasoning: a static token in a file or environment variable is held by a *process*, not a person, and a
voice is not proof of who is speaking. Approving a high-impact action needs an interactive, authenticated
session where a person reads the exact action and clicks CONFIRM (or types its code).

Privileged administration is **not reachable over HTTP at all**:

| Operation | How | Requires |
|---|---|---|
| Create / rotate / show / copy / revoke tokens | `python -m ultron token ...` | a shell as the same OS user |
| Log out every session / reset session state | `python -m ultron sessions revoke-all` / `reset` | a shell as the same OS user |
| Turn security mode off when the camera is unavailable | `python -m ultron security disable` | a shell as the same OS user |
| Approve a pending high-impact action | HUD CONFIRM button or typed `confirm <code>` | signed-in HUD session (+ face gate when security mode is on) |

A valid session never bypasses the risk policy: a HUD request for a shutdown still creates a pending action
bound to its exact arguments, which then needs an explicit approval.

### Limits of this separation (read this)

- **It does not protect against someone who controls your OS account.** They can read the token files, the
  session state and the browser profile. The local tokens protect against *other* local users, web pages,
  other devices and accidental exposure, not against malware running as you.
- The owner token can still open a HUD session, and a HUD session can approve. So the owner token remains a
  full-power credential *for a person at a browser*. What changed is that a token sitting in a script or a
  background process can no longer approve anything by itself, and the wake-word listener can run with a
  client token that cannot log in at all.
- Approval proves that someone with a HUD session clicked CONFIRM, not who they are. While security mode is
  on, the face gate runs before execution, but the face check is a pixel comparison, not authentication
  (see [Remaining risks](#7-remaining-risks)).

## 3. Session lifecycle, expiry and persistent revocation

### Cookie

```
s2.<session id>.<generation>.<issued at>.<expires at>.<HMAC-SHA256>
```

The HMAC key is derived from the owner token. For every request, the server checks the following
**server-side** and rejects anything else with `401`:

1. The format is exact: 6 fields, known version, bounded length, numeric timestamps.
2. The signature is valid (constant-time comparison).
3. The generation equals the current generation in the session state file.
4. `issued at` is not in the future (60 s skew allowed), and `expires at` has not passed.
5. `expires at - issued at` and `now - issued at` are both within the **currently configured** lifetime.
   Lowering `ULTRON_SESSION_HOURS` shortens existing sessions, and raising it does not extend them.
6. The session id is not in the persistent revocation list (and, in this process, not in the in-memory
   logout set).

Old pre-M1.2 cookies (format v1) are rejected; log in once after upgrading.

### Persistent session state

`<data dir>/session_state.json` (mode 0600; data dir default `~/Library/Application Support/Ultron`). It holds:

| Field | Purpose | Retention |
|---|---|---|
| `generation` | random value in every cookie and login link; changing it invalidates them all | until the next revoke-all / rotation / reset |
| `revoked` | SHA-256 of logged-out session ids | until that cookie would have expired (+60 s) |
| `used_tickets` | SHA-256 of consumed login-link nonces | until the link would have expired (+60 s) |
| `retired_tokens` | SHA-256 fingerprints of rotated/revoked tokens | newest 100 |

- **No secret is stored.** Only SHA-256 digests of high-entropy random values (session ids carry 144 bits,
  ticket nonces 96 bits and tokens 256 bits), so they cannot be reversed.
- **Writes are atomic** (temp file + rename, no symlink following) and serialised across processes with an
  exclusive `flock` on `session_state.json.lock`. The server and the CLI can update it concurrently.
- **Changes from the CLI apply immediately** to a running server. It re-validates the file whenever its
  mtime, size or inode changes.
- **Deleting the file logs everybody out.** A missing file is recreated with a *new* generation, so deletion
  can never resurrect a revoked session.
- **Growth is bounded.** If more than 5000 revocations are outstanding, the generation is changed
  (everybody logs out) instead of growing the file.

### Fail closed, and owner recovery

If the file exists but is not valid, the server rejects **every** session, login and bearer request with
`503 session_state_unavailable`. `/healthz` then reports `auth_configured: false`. A file is invalid if any of
these hold:

- it is not valid JSON, or does not have exactly the expected fields and types;
- it has an unknown version;
- it is a symlink or not a regular file;
- it is owned by another user, or readable or writable by group/others;
- it is larger than 512 KB, or cannot be read.

Bearer requests fail too, because the server can no longer check whether a token was retired. The error says
what to do:

```bash
python -m ultron sessions reset          # asks you to type RESET; --yes for non-interactive use
python -m ultron open-hud                # log in again
```

`sessions reset` rebuilds the file with a new generation, which logs everyone out. It keeps the
retired-token list if the old file could still be parsed. If it could not, it says so and tells you to
rotate again if you had rotated because of a leak.

If *recording a logout* fails (for example, the disk is full), `/logout` returns `503` saying the logout
could not be recorded. The cookie is still deleted in the browser, and this server process refuses that
session from then on. Run `python -m ultron sessions revoke-all` once the disk problem is fixed.

### Login links (`ultron open-hud`)

`t2.<issued at>.<nonce>.<generation>.<HMAC>` is put in the URL **fragment**, which is never sent to the
server or written to its logs. `login.js` removes it from the address bar and history. A link is valid for
2 minutes and only once (also across restarts), and is bound to the current generation.

## 4. Rotation behaviour

`python -m ultron token rotate`:

1. Retires the fingerprint of **every token that could currently be in use**: the token file and
   `ULTRON_API_TOKEN`, if set. It also changes the session generation. Both steps are persisted *before* the
   new token is written, so a crash in between fails closed rather than open.
2. Writes the new token (0600, atomic). The token is never printed.
3. A running server picks up the new token file on the next request. Every old cookie, CSRF token and login
   link stops verifying, because the keys change and the generation changed. The old token gets `401`
   (or `503 token_retired` if it is still configured in the environment).
4. If `ULTRON_API_TOKEN` is still set to the old value, the server refuses to serve (`503 token_retired`)
   until you remove it. This closes finding 3.

If the session state is corrupt, rotation resets it first. Rotation is itself a recovery step and is
never blocked.

Client token: `token init --client`, `token rotate --client` (retires the old one; sessions are unaffected),
`token revoke --client` (retires and deletes it), and `token show|copy|path --client`. A client token equal
to the owner token is ignored.

## 5. Voice trust model

### The path from microphone to action

| Entry point | Context | Notes |
|---|---|---|
| `python main.py` (microphone loop) | `local_voice()`: channel `local_voice`, input `voice` | separate process; no HUD attached |
| `wakeword_listener.py` → `POST /ask` | client token → `client-token`/`wakeword`/voice (owner token + `X-Ultron-Client: wakeword` → same channel/input) | requests made with the client token are *always* treated as voice, whatever the request says |
| HUD speech recognition (`app.js`) → `POST /ask {"input": "voice"}` | HUD session, input `voice` | the marker can only make a request *more* restricted, so a client gains nothing by lying |
| HUD typing / buttons | HUD session, input `text` | the only context that can approve |

All of them call `handle_command(text, ctx)`, and every action is a registered tool with a risk tier
(M1.1).

### Four classes

| Class | Tiers / tools | Voice may... | Approval |
|---|---|---|---|
| 1. Conversational and read-only | OBSERVE (time, status, weather, notes Q&A, `system.cpu_temperature`, ...) | always | none |
| 2. Routine local actions | ROUTINE (open apps/sites, volume, brightness, notes, reminders, smart-home, screenshots, ...) | yes if `ULTRON_VOICE_TRUST=routine` (default); refused if `observe` | none |
| 3. Sensitive | CONSEQUENTIAL: `security.disable`, `vision.register_face` | **request only** | HUD session: CONFIRM button or typed code, action-bound, single use |
| 4. Destructive / security-critical | DESTRUCTIVE: `reminders.clear_all`, `system.clear_user_caches`, `system.restart`, `system.shutdown` (and the two class-3 tools, which are security-critical) | **request only** | same as class 3, plus the face gate while security mode is on |

Classes 3 and 4 are enforced the same way today. Voice can never approve either, and both carry the face
gate when security mode is on. The split shows where a future, stronger factor (Touch ID) belongs: class 4
first.

What happens when you ask by voice:

- **Wake word or HUD microphone, class 3/4:** "This will shut down the Mac. I can't accept a spoken or API
  confirmation for this. Press CONFIRM in the signed-in HUD within 120 seconds, or say 'cancel'." **No code
  is spoken.** The HUD shows the request (it polls `GET /pending`) with CONFIRM and CANCEL buttons, labelled
  "requested by wakeword".
- **Standalone `python main.py` loop, class 3/4:** it has no HUD attached, so a request could never be
  approved there. It is denied with guidance: "start the HUD server, open the HUD and ask there". It is not
  left as a dead pending action.
- **Any voice "confirm ..." (with or without the right code):** "For your safety I can't accept a spoken
  confirmation. Press CONFIRM in the signed-in HUD, or say 'cancel'." Nothing else is attempted.
- **"cancel"** by voice still cancels your own pending request (cancelling is always safe).

### Confirmation properties (M1.1 plus M1.2)

- Each confirmation is bound to the exact tool **and arguments**, stored server-side. `/confirm` takes only
  an `action_id`, and any `tool`/`arguments` sent with it are ignored (tested).
- It expires: 60 s for requests made in the HUD, 120 s for requests made by voice or API clients (time to
  reach the HUD). It is single use, consumed even if the gate refuses, and three wrong codes cancel it.
- A newer request from the same principal replaces the older one, whose id then stops working.
- **Delegated approval:** a request made by voice or an API client can be approved by a HUD session, but only
  by its exact `action_id` (the button). It can never be approved by a code, and the code is never revealed.
  A request made in one HUD session can't be approved or cancelled by another HUD session.
- Untrusted web, document, email, knowledge-base, transcript or model text is never routed into the command
  handler, and an `UNTRUSTED` context can neither request nor approve (M1.1, re-tested in M1.2).
- **A refusal is final.** A denied app launch does not fall back to opening a website, and a refused
  confirmation does not fall through to another handler (tested with `ULTRON_VOICE_TRUST=observe`).

### `ULTRON_VOICE_TRUST`

| Value | Spoken requests may |
|---|---|
| `routine` (default) | read-only + routine actions; request class 3/4 for HUD approval |
| `observe` | read-only answers only; everything else says "use the HUD" |
| anything else | treated as `observe` (fails towards the restrictive mode) and logged |

### What voice is and is not

- Voice is **never** identity. The microphone cannot tell the owner from a recording, a TV or someone
  nearby. Routine actions by voice are allowed because they are low impact and reversible, not because the
  speaker was recognised.
- No speaker recognition was added, and the face check is not authentication. Neither is claimed as strong
  auth.

### Recovery when the microphone, camera or biometrics are unavailable

| Situation | What still works |
|---|---|
| No microphone (or voice extra missing) | the HUD: type commands, approve with the button |
| No camera / vision extra, security mode **off** | everything (the face gate only runs while security mode is on) |
| No camera, security mode **on** | approvals are refused (fail closed) and the reply names the recovery: `python -m ultron security disable` in Terminal, then restart JARVIS |
| HUD session lost or state corrupt | `python -m ultron sessions reset`, then `python -m ultron open-hud` |
| Token leaked | `python -m ultron token rotate` (works even with corrupt session state) |
| Client token leaked | `python -m ultron token rotate --client` or `token revoke --client` |

## 6. Tests and checks

New file: `tests/test_identity_sessions.py` (113 tests; full suite 516). It uses temp dirs, a fake runner, fake browser and
fake camera, and in-process CLI calls. Token rotation only touches token files under `tmp_path`.

| Requirement | Tests |
|---|---|
| Logout → restart → replay the old cookie | `test_logout_then_restart_then_replay_of_the_old_cookie_is_rejected` |
| Rotation → (live and) restart → replay the old cookie / old token | `test_rotation_invalidates_sessions_and_the_old_token_live_and_after_restart`, `test_rotation_retires_a_token_still_configured_in_the_environment` |
| Expired, malformed, revoked and tampered sessions | `test_expiry_is_enforced_server_side`, `test_lowering_...`, `test_raising_...`, `test_cookie_from_the_future_is_rejected`, `test_malformed_cookies_...` (11 cases), `test_old_v1_cookie_format_...`, `test_tampering_with_any_cookie_field_...` (5 fields), `test_cookie_signed_with_another_token_...`, `test_revoke_all_...`, `test_deleting_the_state_file_...` |
| Login-link replay across restart | `test_login_link_cannot_be_replayed_after_a_restart`, `test_open_hud_link_logs_in_once` |
| Corrupt or unavailable revocation storage | `test_corrupt_state_fails_closed_for_every_credential` (8 corruption kinds, including recovery), `test_unsafe_or_unreadable_state_file_fails_closed` (group-readable, symlink, directory), `test_failed_logout_persistence_...`, `test_rotation_recovers_from_a_corrupt_state_file`, `test_session_reset_without_confirmation_on_a_non_tty_refuses` |
| Privileged actions with an ordinary session or token | `test_client_token_can_send_commands_but_never_log_in_or_approve`, `test_owner_bearer_token_cannot_approve_either`, `test_privileged_administration_is_not_reachable_over_http` (6 paths), `test_a_valid_session_does_not_bypass_the_risk_policy`, `test_revoked_or_duplicate_client_tokens_are_refused`, `test_a_revoked_client_token_still_set_in_the_environment_is_refused` |
| Voice routine vs sensitive | `test_voice_trust_decision_table` (19 cases), `test_only_typed_hud_sessions_can_confirm`, `test_routine_voice_commands_still_just_work`, `test_wakeword_routine_vs_sensitive_requests`, `test_standalone_voice_loop_gives_guidance_instead_of_a_dead_end`, `test_voice_trust_setting_fails_towards_the_restrictive_mode` |
| Unrelated, stale, replayed or mismatched confirmations | `test_voice_and_api_requests_are_approved_only_in_the_hud_by_exact_id`, `test_delegated_requests_expire`, `test_a_hud_session_cannot_approve_another_hud_sessions_request`, `test_hud_can_cancel_a_voice_request`, plus the M1.1 confirmation tests |
| Untrusted transcript / external content | `test_untrusted_transcripts_and_content_cannot_authorize`, `test_hud_speech_input_cannot_confirm_even_its_own_request`, M1.1 `test_llm_reply_...`, `test_web_and_document_text_...` |
| No fallback after refusal | `test_voice_refusal_does_not_fall_back_to_another_path` |
| Recovery without mic or camera | `test_recovery_without_a_microphone_uses_the_hud`, `test_recovery_without_a_camera` |
| Malformed non-ASCII credentials | `test_non_ascii_credentials_are_rejected_cleanly` (bearer, login, ticket, cookie, CSRF) |
| No secrets in logs, URLs or errors | `test_tokens_and_cookie_values_are_never_logged`, `test_tokens_are_never_accepted_in_urls`, `test_rejected_credentials_are_not_echoed`, `test_token_like_query_parameters_are_redacted_from_logs`, `test_state_file_is_private_and_holds_no_secrets`, `test_retired_fingerprints_are_not_reversible`, `test_rotate_output_never_contains_a_token` |

**Changed M1.1 tests (by design, not weakened).** About 30 M1.1 tests approved high-impact actions by voice
(`CommandContext.local_voice()` saying `confirm <code>`). M1.2 forbids exactly that, so those tests now approve
from a typed HUD context (`owner_hud()` in `tests/conftest.py`). Every original assertion is kept: single use,
argument binding, hint mismatch, per-principal binding, the face gate, posture invalidation and
untrusted-content refusal. Several also assert the new voice refusal. Three other edits:

- Two cookie/ticket format tests now use the v2 formats.
- Two exact-key checks include the new `requested_via` field.
- One test was renamed from "spoken digit words" to "digit words in typed confirmations".

**Mutation checks.** 24 targeted mutations of the auth and authz code were run one at a time against the full
suite. The script (`/tmp/mutate_m12.py`, not committed) restored each file from memory after every run.
All 24 were killed. Two survived the first round, and tests were added for them:

- removing the absolute-expiry check, which only matters after the TTL is *raised*;
- removing the retired-client-token check, which only matters when a revoked client token is still set in
  the environment.

Mutations: cookie signature check; CSRF check; cookie generation check; current-TTL check; expiry check; ticket generation check; persistent
revocation check; retired owner and client token checks; client token accepted at `/login`; client token
treated as HUD text; HUD voice marker ignored; `can_confirm` widened to `can_authorize`; `can_confirm`
ignoring voice input; voice `observe` mode removed; standalone-voice denial removed; registry confirm using
`can_authorize`; everything delegated; non-delegated actions approvable across sessions; state schema
strictness; state file permission check; persistent single-use tickets; revoke-all not changing the
generation; rotate ignoring an env token.

**Live check** (Linux, real `server.py` on 127.0.0.1, all paths in a temp dir). It confirmed:

- the cookie is format `s2`;
- the client token gets `401` at `/login`;
- a client-token routine request ran;
- a client-token `reminders.clear_all` request became a pending action with no code and a 120 s window;
- voice "confirm shutdown 1234" and bearer `/confirm` (client and owner) were refused;
- the HUD `/pending` listed it as requested by wakeword;
- a wrong id was refused, the HUD approval worked, and replaying it was refused;
- `?token=` gets `401` and is shown as `[REDACTED]` in the access log;
- logout followed by a **process restart** and replay of the old cookie gets `401`;
- `token rotate` against the *running* server: old cookie `401`, old bearer `401`, new bearer `200`;
- the state file is mode `0600` and contains no token;
- a corrupted state file gives `503 session_state_unavailable` with the recovery hint, `/healthz` shows
  `auth_configured: false`, and after `sessions reset --yes` it returns `200`;
- the server log contained no token, cookie value or session id.

## 7. Remaining risks

- **OS-account compromise is out of scope** (see §2). The same goes for a compromised browser profile while
  a HUD session is live: it can approve.
- **The owner token can still mint HUD sessions.** Keep it out of scripts and shell profiles. Use the client
  token for background clients. `ultron doctor` warns when `ULTRON_API_TOKEN` is set.
- **No strong user-presence factor yet.** Approval is a click in an authenticated session. Touch ID
  (LocalAuthentication) for class 4 is still the recommended next step and is not implemented. The face gate
  is a pixel-distance comparison and is spoofable.
- **Routine voice actions run for whoever is speaking.** Opening apps, setting volume, adding notes or
  reminders and similar can be triggered by anyone or anything the microphone hears. Set
  `ULTRON_VOICE_TRUST=observe` if that is unacceptable in your environment.
- **The `input: "voice"` marker from the HUD is self-declared.** It defends against audio picked up by the
  HUD microphone, not against a malicious page (which would already hold the session). It can only ever
  restrict.
- **Pending actions live in memory.** They disappear on restart, which is safe: nothing runs.
- **Log redaction covers the tokens known at startup plus any `*token=` query parameter.** A token rotated
  later is redacted only through those generic patterns. The server never logs tokens itself.
- **Clock changes.** Expiry uses wall-clock time. Setting the clock back can extend a session by the jump,
  with at most the configured lifetime counted from the (future) issue time and 60 s of skew allowed.
- **The `flock` locking is advisory and POSIX-only.** Windows is not a supported host.

## Manual macOS checks

These were **not** verified (no macOS in this environment). Please run them once on the Mac:

1. `uv run ultron token init && uv run ultron token init --client && uv run ultron doctor`. Expect `[ok]`
   lines for the token, client token and session state, and no secret values.
2. `uv run python server.py`, then `uv run ultron open-hud`. The HUD opens logged in, and the URL bar shows no
   `#ticket=`.
3. In the HUD: log out, then press Back or open a second tab. You are sent to `/login`. Stop the server,
   start it again, and confirm the old tab is still logged out.
4. With the HUD open, run `uv run ultron token rotate` in Terminal **without restarting the server**. The
   next HUD action redirects to `/login`. Log in again with `open-hud`.
5. Start `uv run python wakeword_listener.py` (it should *not* print the owner-token note). Say "Jarvis, what
   time is it". It answers.
6. Say "Jarvis, clear reminders". JARVIS says to press CONFIRM in the HUD and speaks **no code**. Within about
   3 s the HUD shows "CONFIRM: ... [requested by wakeword]". Press CONFIRM, and the reminders are cleared.
7. Say "Jarvis, shut down the system", then "Jarvis, confirm shutdown". JARVIS refuses the spoken
   confirmation, and nothing shuts down. Press CANCEL in the HUD.
8. Using the HUD microphone, say "Jarvis, restart system". The HUD card appears and no code is spoken.
   Saying "Jarvis, confirm ..." is refused, and only the button works. Cancel it.
9. `uv run python main.py` (standalone microphone loop): "shutdown system". JARVIS gives the HUD guidance and
   creates no pending action. "Volume 30" still works.
10. `ULTRON_VOICE_TRUST=observe uv run python main.py`: "open safari" is refused with "use the HUD", and
    "what time is it" still answers.
11. Make the state file unreadable with `chmod 644 "$(uv run ultron token path | xargs dirname)/session_state.json"`.
    The HUD and wake word report the recovery command. Then run `uv run ultron sessions reset`, type RESET,
    and log in again.
