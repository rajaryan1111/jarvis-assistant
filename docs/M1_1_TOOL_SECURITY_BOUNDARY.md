# M1.1: Tool security boundary

M1 put a risk-checked tool registry in front of four destructive actions (restart, shutdown, cache cleanup,
clearing reminders). Everything else in the legacy assistant was still reachable without a policy decision:
opening apps and URLs, volume, brightness, screenshots, the camera, face registration, security mode, sleep,
and every write to `memory.json`. Any *authenticated* caller could use them, and so could any *unauthenticated*
code path that called `handle_command` without a context (for example `jarvis_logic`) or imported a helper
directly.

M1.1 closes that gap. **Every state-changing legacy action is now a registered tool**, and
`ToolRegistry.request/confirm` is the only way to reach its implementation, whether the call comes from
voice, the CLI, the HUD, the API or a background service.

Not in scope (and not started): the agent runtime, an agent loop, multi-step planning, LangGraph and a new
LLM provider layer (M2 and later).

- [Centralized authorization design](#centralized-authorization-design)
- [Action / risk matrix](#action--risk-matrix)
- [Which actions require confirmation](#which-actions-require-confirmation)
- [Filesystem and subprocess safety](#filesystem-and-subprocess-safety)
- [Gaps fixed in M1.1](#gaps-fixed-in-m11)
- [Tests at the enforcement boundary](#tests-at-the-enforcement-boundary)
- [Limitations](#limitations)
- [Tested here vs needs macOS](#tested-here-vs-needs-macos)

## Centralized authorization design

```
 voice (main.py loop) ─┐  CommandContext(local-cli, LOCAL_VOICE, authenticated)
 wake-word client ─────┤  Bearer token        -> CommandContext(api-token, WAKEWORD)
 HUD (browser) ────────┤  session + CSRF      -> CommandContext(session:<id>, HUD)
 API client ───────────┤  Bearer token        -> CommandContext(api-token, API)
 jarvis_logic / import ┤  no context          -> CommandContext.untrusted(...)
 background watchers ──┘                      -> CommandContext.internal(<service>)
            │
            ▼
   handle_command(text, ctx)       keyword router: parses text into explicit arguments
            │                       (never decides whether something is allowed)
            ▼
   legacy helper wrapper            set_volume(level, ctx) / launch_any_app(name, ctx) / ...
            │                       = TOOLS.request(tool_name, args, ctx or untrusted)
            ▼
 ┌─────────────────────── ToolRegistry (ultron/tools/registry.py) ───────────────────────┐
 │ platform check -> policy.evaluate(tier, ctx, internal_ok) -> ALLOW | CONFIRM | DENY   │
 │ CONFIRM: PendingAction(tool, args, principal, code, id, 60 s TTL, 3 attempts)         │
 │ confirm(ctx, code|id): same principal, exact action, unexpired, single use            │
 │          -> confirmation gate (face check while security mode is on) -> run           │
 │ audit log: event, tool, principal, channel, status (never argument values)            │
 └───────────────────────────────────────────────────────────────────────────────────────┘
            │
            ▼
   _tool_* implementation / ultron.tools.macos / ultron.tools.system
            │  processes only via run_command (argv, absolute path, timeout, bounded, redacted)
            ▼  files only via ultron.security.paths (validated names, no symlinks, atomic)
```

Properties:

- **Outside language and keyword interpretation.** The router and (later) any model only produce a tool
  name plus arguments. The decision is made by `ultron.security.policy.evaluate` from the tool's declared
  `RiskTier` and the caller's `CommandContext`. Neither command text nor model output can change either.
- **One policy for every path.** Voice, CLI, HUD, API and background services all build a `CommandContext`
  and go through the same registry. The HUD and voice paths reach the *same* tool object (tested).
- **No side door.** Implementations are private `_tool_*` functions or tool classes, referenced only from
  `build_tool_registry()`. A static test parses `main.py` and fails if a process, browser, camera, file
  delete, `save_memory` or sleep-state change happens anywhere else (a short allowlist, explained in the
  test, covers the gate itself and internal bookkeeping). Importing `main` and calling `set_volume(50)` or
  `disable_security_mode()` without a context is treated as untrusted and does nothing.
- **Existing tiers and confirmation mechanism reused.** M1's `RiskTier`, `ConfirmationStore` and the
  confirmation gate are unchanged in behaviour. M1.1 added an `INTERNAL` channel, the `internal_ok` and
  `confirm_hints` tool fields, `invalidate_all()` and session revocation.

### Decision table

| Tier | Untrusted / no context | Internal service | Authenticated user (HUD, API, voice, wake word) |
|---|---|---|---|
| OBSERVE (read-only) | allow | allow | allow |
| ROUTINE (local, reversible) | deny | allow **only** if the tool sets `internal_ok` | allow, no prompt |
| CONSEQUENTIAL (high impact) | deny | deny | **confirm** |
| DESTRUCTIVE (irreversible) | deny | deny | **confirm** |
| FORBIDDEN | deny | deny | deny |

"Authenticated" means the HUD session cookie with CSRF and Origin checks, or the bearer API token (M1).
The local microphone loop (`python main.py`) is trusted as the person at this Mac.

## Action / risk matrix

Every registered tool. `tests/test_policy_boundary.py::test_matrix_document_matches_the_registry` parses
this table and fails if it drifts from `build_tool_registry()`.

**Required permission** means the minimum caller: *any* (also untrusted), *user* (an authenticated user
channel), *user + confirm* (that user's code-bound confirmation, plus the security-mode face gate when it is
enabled), or *user / internal* (also the listed background service).

| Tool | Tier | Entry points (phrases / routes) | Required permission | Confirmation | Effect and safeguards | Tests |
|---|---|---|---|---|---|---|
| `system.cpu_temperature` | OBSERVE | "temperature", "status report" | any | no | runs `osx-cpu-temp` from fixed Homebrew paths only (no `PATH`), 5 s timeout | B, P |
| `audio.speak` | ROUTINE | every CLI reply, `POST /speak`, reminder and intruder alerts | user / internal (reminder + intruder watchers) | no | `/usr/bin/say`, leading-`-` guard, 4000-char cap, timeout | B, P |
| `apps.open` | ROUTINE | "open <app>" | user | no | `/usr/bin/open -a <name>`; name must match a strict pattern (no `/`, no leading `-`, ≤ 64 chars) | B, P |
| `web.open_url` | ROUTINE | "open <site>" fallback, "play …", "search …" | user | no | http/https only, needs a host, no embedded credentials, no control characters, ≤ 2048 chars | B, P |
| `system.set_volume` | ROUTINE | "volume N", "increase/decrease volume" | user | no | integer 0–100 only, fixed AppleScript | B, P |
| `system.brightness` | ROUTINE | "increase/decrease brightness" | user | no | direction up/down, 1–16 steps, fixed AppleScript | B, P |
| `system.screenshot` | ROUTINE | "screenshot" | user | no | writes a new, never-overwriting file directly inside `ULTRON_SCREENSHOT_DIR` (default `~/Desktop`); refuses planted symlinks | B, P |
| `memory.add_note` | ROUTINE | "remember that …" | user | no | appends to `memory.json` (atomic write) | B |
| `profile.update` | ROUTINE | "my name is …", "my favourite X is Y" | user | no | `memory.json` | B |
| `reminders.add` | ROUTINE | "remind me …" | user | no | `memory.json` | B, L |
| `study.create_plan` | ROUTINE | "help me learn …", "create/make a study plan for …" | user | no | calls OpenAI if configured; stores the plan and a daily reminder | B |
| `study.update_progress` | ROUTINE | "update my progress for … to N" | user | no | `memory.json` | B |
| `smarthome.control` | ROUTINE | "turn on/off the … lights/fan/ac", "set ac …" | user | no | virtual state in `memory.json` only (no real devices) | B |
| `assistant.set_sleep` | ROUTINE | "go to sleep", "wake up" | user | no | in-process flag | B |
| `knowledge.reload` | ROUTINE | "reload knowledge/notes" | user | no | rebuilds `knowledge_index.json` from your documents; atomic replace | B |
| `security.enable` | ROUTINE | "enable security mode", "arm security" | user | no | makes later actions stricter, starts intruder checks; invalidates all pending actions | B |
| `security.verify_identity` | ROUTINE | "verify my identity" | user | no | camera face check; success opens the 60 s face-check window | B |
| `vision.recognize_owner` | ROUTINE | "do you see me" | user / internal (intruder watcher) | no | camera | B |
| `vision.detect_person` | ROUTINE | "do you see anyone" | user / internal (intruder watcher) | no | camera | B |
| `vision.hand_gesture` | ROUTINE | "check my hand" | user | no | camera | B |
| `security.disable` | CONSEQUENTIAL | "disable security mode", "disarm security" | user + confirm | **yes** | the confirmation must pass the face gate while the mode is still on; invalidates all pending actions | B |
| `vision.register_face` | CONSEQUENTIAL | "register/remember my face" | user + confirm | **yes** | replaces the reference face that security mode checks; gate applies; atomic, symlink-safe write | B, P |
| `reminders.clear_all` | DESTRUCTIVE | "clear reminders", "delete all reminders" | user + confirm | **yes** | deletes every reminder | B, L, A |
| `system.clear_user_caches` | DESTRUCTIVE | "clear cache", "clean my system", "clear junk" | user + confirm | **yes** | Python deletion of entries directly inside `~/Library/Caches` only; symlinks removed as links | T, L |
| `system.restart` | DESTRUCTIVE | "restart system" | user + confirm | **yes** | fixed AppleScript, timeout | T, L, B |
| `system.shutdown` | DESTRUCTIVE | "shutdown system" | user + confirm | **yes** | fixed AppleScript, timeout | T, L, A, B |

Tests: **B** `tests/test_policy_boundary.py`, **P** `tests/test_process_and_paths.py`, **T** `tests/test_tools.py`,
**L** `tests/test_legacy_commands.py`, **A** `tests/test_api_security.py`. Every ROUTINE+ tool is also covered
by the direct-call bypass matrix (B) and, where it has a phrase, by the untrusted-command matrix (B).

### Entry points that are not tools

| Entry point | What it can do | Authorization | Notes / tests |
|---|---|---|---|
| `GET /healthz` | reports `ok` and whether auth is configured | public | no secrets (A) |
| `GET /login`, `POST /login` | create a HUD session | API token or one-time ticket; Origin + JSON; throttled | (A) |
| `GET /` | HUD page with CSRF token | session | (A) |
| `POST /logout` | ends the session | session + CSRF | **M1.1:** revokes the session id server-side and cancels its pending action (B) |
| `GET /status` | time, battery, CPU/RAM | session or bearer | read-only (A) |
| `POST /ask` | `handle_command(text, ctx)` | session + CSRF, or bearer | every action inside goes through the matrix above (A, B) |
| `POST /speak` | speak text | session + CSRF, or bearer | **M1.1:** runs `audio.speak` as the caller (B) |
| `POST /confirm`, `POST /cancel` | confirm/cancel *this principal's* pending action by id | session + CSRF, or bearer | exact id, single use, expiry, gate (A, B) |
| Read-only router branches | time, battery, CPU, weather, news, cricket, Wikipedia, web-search titles, notes Q&A, lists, security status, GPT fallback | any (OBSERVE) | network reads; GPT fallback sends your text to OpenAI if configured; replies are shown or spoken, never executed (B) |
| Reminder watcher thread | speaks due reminders; drops fired one-time reminders | internal context; speech via `audio.speak` | bookkeeping writes stay internal (static allowlist) |
| Intruder watcher thread | camera checks every 10 s while security mode is on; spoken alert | internal context; only `vision.detect_person`, `vision.recognize_owner`, `audio.speak` | (B) |
| `python -m ultron security disable` | owner recovery: turn security mode off | local shell as the OS user; typed `DISABLE` or `--yes` | not reachable from voice/HUD/API (P) |
| `python -m ultron token …`, `open-hud`, `doctor`, `check-secrets`, `serve` | token management, diagnostics | local shell as the OS user | `pbcopy` and `open -a` now via `run_command` with timeouts |
| Wake-word listener | sends recognized speech to `/ask`, the reply to `/speak` | bearer token, `WAKEWORD` channel | same matrix; cannot reach internals (static test) |

## Which actions require confirmation

Only these five. Everything else is read-only (no prompt for anyone) or ROUTINE (no prompt for an
authenticated user):

1. **`security.disable`**: turning off security mode
2. **`vision.register_face`**: replacing the stored reference face
3. **`reminders.clear_all`**: deleting all reminders
4. **`system.clear_user_caches`**: deleting the contents of `~/Library/Caches`
5. **`system.restart`** and **`system.shutdown`**

Confirmation properties (unchanged from M1 except where marked):

- A request creates one `PendingAction` per principal, bound to the exact tool **and arguments**, with a
  random 4-digit code and an unguessable id. It expires after 60 s and allows 3 wrong codes.
- `confirm <code>` (voice/CLI) or the HUD button (`POST /confirm` with the action id) is accepted only from
  the **same principal**. Another HUD session, the bearer client, voice and untrusted text are all refused.
  **Superseded in M1.2:** only a signed-in HUD session (typed or clicked) can approve. Voice and bearer
  requests are approved in the HUD by exact action id, and spoken codes are no longer issued or accepted. See
  [M1_2_IDENTITY_SESSIONS_VOICE.md](M1_2_IDENTITY_SESSIONS_VOICE.md#5-voice-trust-model).
- A spoken hint must match the pending action: `confirm restart 1234` cannot confirm a pending cache cleanup.
- A bare phrase (`confirm shutdown`, `confirm disable security mode`) never authorizes anything. With nothing
  pending, it does nothing. With something pending, it asks for the code.
- A confirmation is consumed when used, even if the security gate then refuses it, so it cannot be replayed.
- **M1.1:** pending actions are invalidated when security mode is turned on or off (all principals), and
  when a HUD session logs out (that principal).
- **M1.1:** internal background services can never create or confirm a pending action.
- Text from web pages, documents, the knowledge base, emails or LLM replies is returned to the user as
  content. It is never routed back into `handle_command`, so a reply that says `confirm 1234` is displayed,
  not executed (tested).

### Disabling a protection cannot bypass it

Security mode is the legacy face check that runs before any confirmed action. Two actions could have defeated
it, and both are now protected by it:

- **Turning it off** (`security.disable`) needs a confirmation, and the confirmation runs the face gate
  *while the mode is still on*.
- **Re-registering the face** (`vision.register_face`) would let anyone swap in their own face. It now needs
  a confirmation that passes the gate against the *current* face.

If the camera or the `vision` extra is unavailable, the gate fails closed, and the owner can recover from
a shell on the Mac with `python -m ultron security disable`. This needs the same OS-account access as
reading the API token, so it is not a remote bypass.

## Filesystem and subprocess safety

**Processes** (`ultron/tools/process.py`, `run_command`) are the only way tools start a process:

- argv lists only; shell strings and `shell=True` are rejected; absolute executables only (no `PATH` lookup).
- Every call has a timeout, and the child is killed when it expires (tested with a real child process).
- stdin is closed unless input is passed explicitly.
- Output is decoded, truncated (default 4 KB) and passed through redaction.
- Timeouts, missing binaries and OS errors become a result object instead of an exception, so callers report
  a failure and leave state unchanged.
- There is no general "run a shell command" tool.

**Files** (`ultron/security/paths.py`):

- **Validated names.** Tools write only directly inside one directory, under a file name they generated.
  `child_path` rejects `/`, `..`, NUL, and names that start with `.` or `-`.
- **No symlinks.** Existing symlinks at the target are refused, never followed. The face directory must not
  be a symlink.
- **No overwrites.** New files never overwrite existing ones (`unique_child`); a dangling link counts as taken.
- **Atomic replacement** (`atomic_replace`/`atomic_write_text`): a random `O_EXCL` temp file in the same
  directory, then `os.replace`. A failure leaves the old file intact.
  - Now used for `memory.json`, the API token file, the knowledge index and the reference face.
  - The token file and `memory.json` previously used a fixed `*.tmp` name opened with `O_TRUNC`, which
    followed a planted symlink (regression-tested).
- **Cache cleanup.** Narrow Python deletion of entries directly inside the canonical `~/Library/Caches`, with
  no shell or globbing; symlinks are removed as links. The target is fixed and deliberately **not**
  configurable, because a misconfigured value could point it at the home folder.

**Configurable boundaries** (environment variables; defaults keep normal use working):

| Variable | Default | Used by |
|---|---|---|
| `ULTRON_SCREENSHOT_DIR` | `~/Desktop` (may itself be a symlink, which is your choice) | `system.screenshot` |
| `ULTRON_FACE_DIR` | `<project>/faces` (previously relative to the current directory; the launcher already runs from the project, so existing faces keep working) | face registration and recognition |
| `ULTRON_MEMORY_FILE` | `<project>/memory.json` | memory, reminders, security mode |
| `ULTRON_KNOWLEDGE_DIR`, `ULTRON_KNOWLEDGE_INDEX` | `<project>/knowledge_docs`, `<project>/knowledge_index.json` | knowledge base |

## Gaps fixed in M1.1

| # | Gap (found by reading the source) | Fix |
|---|---|---|
| 1 | Open app/URL, volume, brightness, screenshot, camera, face registration, security mode, sleep and every memory write ran for **any** caller, including untrusted/no-context callers (`jarvis_logic` default, direct imports) | all are registry tools with tiers; untrusted callers get read-only only |
| 2 | `disable security mode` was ungated: disabling the protection skipped it | CONSEQUENTIAL + confirmation + face gate |
| 3 | `register my face` replaced the reference face with no check, a full bypass of security mode | CONSEQUENTIAL + confirmation + face gate |
| 4 | Legacy helpers were directly callable with no check (`main.set_volume(50)`, `main.disable_security_mode()`) | helpers take a context and call the registry; static test forbids side effects outside tool implementations |
| 5 | `open -a <spoken text>`: a leading `-` became an `open` option; any text reached argv | strict app-name validation |
| 6 | `webbrowser.open` accepted any scheme (`file:`, `javascript:`) | http/https-only URL validation |
| 7 | No subprocess in `main.py` had a timeout; `osx-cpu-temp` was resolved via `PATH` | `run_command` everywhere; fixed paths |
| 8 | `faces/` was relative to the current directory; face writes were non-atomic and followed symlinks | anchored, validated, symlink-safe, atomic |
| 9 | `memory.json.tmp` and `api_token.tmp`: fixed temp names opened with `O_TRUNC` (followed planted symlinks) | random `O_EXCL` temp files |
| 10 | Knowledge index write was non-atomic (an interrupted rebuild corrupted it) | atomic replace |
| 11 | `/logout` only deleted the cookie in the browser; a copied cookie stayed valid, and the pending action survived | server-side revocation and the pending action is cancelled |
| 12 | Background watchers called the camera and `say` directly | internal context through the registry; they can never confirm or act beyond their opt-in tools |
| 13 | `/speak` bypassed the registry | runs `audio.speak` as the calling principal |
| 14 | `ultron token copy` and `open-hud` subprocesses had no timeout | `run_command` |

## Tests at the enforcement boundary

All tests use fake runners, a fake browser opener, fake camera functions and temp directories. The one real
child process is the test interpreter sleeping, to prove timeouts kill. Nothing shuts down, deletes user
files, opens a browser or uses the camera.

| File | Tests | Covers |
|---|---|---|
| `test_policy_boundary.py` (new) | 172 | see below |
| `test_process_and_paths.py` (new) | 68 | see below |
| `test_security_core.py` | 38 (+2) | internal-channel policy, `ConfirmationStore.clear` |
| `test_api_security.py` | 38 | M1 HTTP auth, CSRF, Origin and DNS-rebinding tests (unchanged) |
| `test_legacy_commands.py` | 32 | M1 Phase 0 regressions; reminder parsing now runs with an authenticated context |
| `test_tools.py`, `test_secret_scan.py`, `test_cli_and_startup.py`, `test_vision_optional.py`, `test_news.py` | 17, 17, 9, 7, 5 | unchanged |
| **Total** | **403** | |

`test_policy_boundary.py` covers:

- **Classification.** Exact tier and internal opt-in for every tool, and this matrix stays in sync.
- **Static no-bypass checks.** No process, browser, camera, delete, `save_memory` or sleep change outside
  tool implementations. `_tool_*` functions are referenced only from the registry. Entry-point modules
  never touch internals.
- **Direct-call bypass.** 21 helpers × 5 contexts: no context, LLM text, web text, an unauthenticated HUD
  context, and an internal service without opt-in. Each one checks that nothing runs, opens, changes
  memory or is queued.
- **Untrusted commands.** 25 action phrases sent with untrusted context, all denied. Read-only phrases still
  work.
- **Authenticated ROUTINE actions** run without a prompt, with exact argv, `shell=False` and a timeout.
- **Security mode.**
  - Disabling it needs a code. A bare phrase does nothing.
  - The face gate blocks the action when vision is missing and on a face mismatch.
  - A used code cannot be reused.
  - Re-registering the face needs confirmation and the gate.
  - A posture change invalidates other principals' pending actions.
  - A mismatched hint is rejected.
- **LLM, web and knowledge-base text** that contains `confirm <code>` is never executed.
- **Background services.**
  - The intruder check runs through the boundary.
  - Internal services are refused volume, apps, `security.disable`, shutdown and memory writes, and cannot
    confirm.
- **HUD and voice share one boundary.**
  - The same tool and argv run for both.
  - Neither can confirm the other's action.
  - HUD action ids are exact, single-use and expire after 60 s.
  - The HUD cannot disable security without confirmation and the gate.
  - Unauthenticated requests reach nothing.
  - `/speak` goes through the tool, with the injection neutralised.
  - Logout revokes the session and its pending action, and replaying the old cookie returns 401.

`test_process_and_paths.py` covers:

- **Runner.** `run_command` rejects shell strings and relative or malformed argv. It sets
  `shell=False`, a timeout and closed stdin. Timeouts are reported (and a real overrunning child is
  killed), missing or forbidden executables are reported, and output is bounded and redacted.
- **Paths.** Traversal and odd names are rejected. Writes through symlinks are refused. `unique_child`
  skips existing names and dangling links. Directory rules and symlink-aware `is_within` are covered.
  A failed atomic write keeps the old file.
- **Temp-file regressions.** `memory.json` and token writes do not follow a planted `.tmp` symlink.
- **macOS tool validation.** Argument checks for every macOS tool: `say` option injection, app-name
  injection, URL schemes, volume and brightness values. Screenshots never overwrite and refuse bad
  folders. The CPU temperature tool ignores `PATH`.
- **Face storage.** Writes are atomic, a failure keeps the previous face, unsafe names are rejected and
  symlinked faces or directories are refused.
- **Owner recovery CLI.** `security disable` needs `--yes` or a typed confirmation when not on a TTY.

**Mutation checks.** I injected five deliberate bypasses one at a time and ran the suite after each:

| Injected bypass | Tests failed |
|---|---|
| Lower `security.disable` to ROUTINE | 8 |
| Remove logout revocation | 2 |
| Route a handler branch around the context | 5 |
| Call `subprocess` directly from `speak` | 6 |
| Let internal services run any tool | 21 |

Each count includes the then-missing doc-sync test. The code was restored after each run.

## Limitations

**Authentication** (the first three items are **addressed in M1.2**; see
[M1_2_IDENTITY_SESSIONS_VOICE.md](M1_2_IDENTITY_SESSIONS_VOICE.md): approval needs a HUD session, there is a
limited client token, revocation persists across restarts, and voice can no longer approve)

- **The API token is the real security boundary, and it is all-powerful.** Anyone who can read the token
  file (your OS account) or a live HUD cookie can do anything a user can, including confirming actions.
  There are no per-client scopes yet. All bearer clients (CLI, wake word) share the principal `api-token`.
- **Session revocation and pending actions live in memory.**
  - After a server restart, pending confirmations are gone, which is safe.
  - A copied pre-logout cookie becomes valid again until it expires (12 h default), because cookies are
    signed with a key derived from the token. Run `python -m ultron token rotate` to invalidate every
    session.
- **Voice is trusted like a logged-in user.** The local microphone loop and the wake-word listener act for
  whoever is speaking in the room, or for audio playing from a speaker. Confirmation codes are random and
  expire, but they are **spoken aloud**, so someone in the room hears them. Confirmation proves intent, not
  identity.
- **Not yet built:** strong authentication for DESTRUCTIVE actions (Touch ID / LocalAuthentication). The
  audit recommends it for T3; this is still confirm-only plus the optional face gate.

**Face recognition ("security mode")**

- **It is not authentication.** It compares a raw grayscale pixel distance against one stored JPEG, so a
  photo of the owner, or lighting changes, can fool it either way. It is an extra hurdle on confirmed
  actions, not a security control.
- **The 60 s face-check window lets later actions skip the camera.** After a successful check, confirmed
  actions in the next 60 s pass the gate without a new check.
- **Privacy.** The intruder watcher turns the camera on every 10 s while security mode is enabled.
- **Owner recovery bypasses the face check by design.** `ultron security disable` turns the mode off
  without it. It requires local shell access as the OS user.

**Boundary scope**

- **In-process Python code is trusted.** The boundary protects every *request path* (voice, CLI, HUD, API,
  background services) and every *public helper*. It cannot stop code running inside the same Python
  process from calling a private `_tool_*` function, because Python has no private functions. The static
  test keeps the repository itself honest.
- **A few internal writes stay outside the registry, by design.** They are all in the static-test
  allowlist:
  - the reminder watcher's bookkeeping;
  - the gate's "recently verified" timestamp;
  - the offline recovery command.
- **Read-only branches still make network requests.** These are weather, news, cricket, Wikipedia,
  DuckDuckGo titles and the GPT fallback. They run for untrusted callers (as M1 already allowed).
  - The GPT fallback sends the user's text to OpenAI when a key is configured.
  - There is no taint tracking yet (planned for the policy engine in M3). Fetched content is only ever
    returned to the user.
- **Legacy data tools parse command text.** `reminders.add`, `smarthome.control` and
  `study.update_progress` receive the user's own command text and parse it with the same deterministic
  code as before. They are ROUTINE and only change `memory.json`.
- **Keyword routing is unchanged.** It still misroutes some phrases, for example "timer" matches the time
  handler.

## Tested here vs needs macOS

| Verified here (Linux sandbox, Python 3.11, fakes) | Needs a real Mac (not verified) |
|---|---|
| Policy decisions for every tool and context; HUD, API and voice parity; confirmation id, code, expiry, reuse and principal binding; logout revocation | `say` with the leading-space guard still speaks normally |
| Exact argv passed to `say`, `open`, `osascript` and `screencapture`; `shell=False`; timeouts; output bounds; redaction | `open -a` launches the mapped apps (including Canva by name instead of the bundle path), and a missing app falls back to the website |
| Path validation, symlink refusal and atomic writes on a real filesystem (Linux semantics) | `screencapture -x` writes into `~/Desktop` (or `ULTRON_SCREENSHOT_DIR`); needs the Screen Recording permission |
| Face-storage logic with a fake OpenCV | real camera capture, face registration and recognition; intruder watcher timing |
| Owner recovery CLI on a temp `memory.json` | brightness key codes 107/113 (kept from legacy); confirm *up* and *down* are not swapped |
| Ruff, compileall, secret scan, full test suite | volume AppleScript; restart/shutdown via System Events (Automation permission); TCC prompts |

### Manual macOS checks

Run these on the owner's Mac. They are safe: nothing destructive is confirmed.

1. `python -m ultron doctor`. All core items should be `[ok]`.
2. Start `python main.py` and say "volume 30", then "increase brightness", then "decrease brightness".
   Volume should change; brightness should move up, then down.
3. "open safari", "open vs code", "open canva", then "open facebook" (website fallback).
4. "take a screenshot". A new `jarvis_screenshot_*.png` should appear on the Desktop. A second shot in the
   same second should get a `-2` suffix.
5. "play lofi", then "search python dataclasses". The browser should open, and the reply should list titles.
6. "temperature". The CPU temperature should be reported if `osx-cpu-temp` is installed in
   `/opt/homebrew/bin` or `/usr/local/bin`.
7. With the `vision` extra installed (**updated for M1.2**: approvals happen in the HUD, not by voice):
   1. While security mode is off, type "register my face" in the HUD and press CONFIRM.
   2. Say "enable security mode".
   3. Type "disable security mode" in the HUD and press CONFIRM. The camera check should run, and the mode
      should turn off only if your face matches.
   4. Repeat with your face covered. It should be refused, and the mode should stay on.
8. Without the `vision` extra, turn security mode on and then try "disable security mode" in the HUD. It
   should be refused (fails closed). Then run `python -m ultron security disable` in Terminal, restart JARVIS and
   confirm the mode is off.
9. In the HUD:
   1. Type "shutdown system". Wait 61 s, then click Confirm. It should say *expired*.
   2. Type it again and click Cancel.
   3. Log out, then reload. The HUD should require login again.
10. Wake word: say "Jarvis, what time is it". The reply should be spoken through `/speak`.
