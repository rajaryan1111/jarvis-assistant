# ULTRON M1: Security remediation and foundation

Status: implemented on branch `arena/289eecea-jarvis-assistant`. M1 scope only: no agent loop, no
LangGraph, no vector DB, no new voice engine and no multi-agent code (see the audit's §17–§18).

Evidence levels used below:

- **Tested**: covered by the automated tests in `tests/`, run on Linux with Python 3.11.
- **Verified manually**: exercised by hand in the Linux sandbox (real server process, real HTTP).
- **Not verifiable here**: needs macOS hardware or permissions (`say`, `osascript`, camera, microphone,
  TCC). These are documented, not claimed.

## 1. Security fixes

| # | Issue (audit ref) | Fix | Evidence |
|---|---|---|---|
| P0 | Picovoice AccessKey hardcoded in `wakeword_listener.py` | Read from `PICOVOICE_ACCESS_KEY`. A clear error (exit 2) if missing. `.env.example` has placeholders only. `.gitignore` covers `.env*`, token/key files, DBs, memory, faces and knowledge data | Tested (`test_secret_scan.py`, `test_cli_and_startup.py::test_wakeword_listener_exits_cleanly_without_credentials`) |
| P0 | No guard against re-introducing secrets | `ultron check-secrets` scanner (prints only path:line:rule), run in CI and by a repository regression test | Tested |
| P0 | Keys in URLs leak into logs and errors | NewsAPI key moved to the `X-Api-Key` header. Weather and cricket use `params=`, and errors pass through `redact()`. Cricket debug dump of the full response removed. Server logs use a redaction filter | Tested (news, redaction) |
| P1 | Server bound to `0.0.0.0` with `debug=True` | Binds `127.0.0.1`, debug off. Non-loopback refused unless `ULTRON_ALLOW_NON_LOOPBACK=1`. Debug refused off loopback. No reloader | Tested (subprocess startup checks the listening address is `127.0.0.1` and the log says debug off) |
| P1 | No authentication on any endpoint | Bearer token (CLI/wake word) or HUD session cookie (HMAC-signed, HttpOnly, SameSite=Strict). The token lives in a `0600` file outside the repo (or `ULTRON_API_TOKEN`). One-time login tickets via `ultron open-hud`. Login throttling | Tested |
| P1 | Cross-site request forgery (the verified exploit: a `text/plain` POST of "confirm restart" from any website reached `osascript`) | Host allowlist (DNS rebinding), Origin allowlist, `Sec-Fetch-Site` check, per-session CSRF token, JSON-only state changes, no CORS headers | Tested (`test_csrf_regression_cross_origin_text_plain_post_cannot_trigger_shutdown`, origin/host/CSRF tests) |
| P1 | Fail-open when unconfigured | No token means protected endpoints return `503 auth_not_configured`, and the login page shows setup steps | Tested |
| P1 | Secrets in frontend | The page and JS contain only the per-session CSRF token. The token is never rendered, and the login ticket travels in the URL fragment | Tested + verified manually |
| P2 | `clear cache` ran `rm -rf ~/Library/Caches/*` via the shell immediately | `ClearUserCachesTool`: DESTRUCTIVE tier with a preview of the item count. No shell or glob. Deletes only direct children. Symlinks are removed as links, never followed. Refuses a symlinked or non-canonical cache dir | Tested (tmp dirs only) |
| P2 | Standalone "confirm shutdown"/"confirm restart" executed with no prior request | Pending-action confirmation: per-principal, 4-digit one-time code or HUD action id, 60 s expiry, single use, max 3 attempts, bound to the tool | Tested (registry, legacy router, API) |
| P2 | Restart/shutdown reachable without authentication | Only via the tool registry. Untrusted or missing context means denied | Tested |
| P2 | "clear reminders" deleted everything immediately | Registered as a DESTRUCTIVE tool with confirmation | Tested |
| P2 | Untrusted content as authorisation | `CommandContext` on every call. `jarvis_logic` and other callers default to `untrusted`. Knowledge-base text is labelled as data in the LLM prompt and is never routed to the command handler | Tested (policy, `jarvis_logic`) |
| P2 | Security mode silently "passed" or crashed without vision | Fails closed with an explanation when vision isn't installed | Tested |
| — | `say` argument injection via text starting with `-` | Leading-dash guard | Code review |

## 2. Bugs fixed

- `jarvis_logic.py` called a non-existent `main.handle()`. It now delegates to `main.handle_command(text, ctx)`.
- Importing `main` started two watcher threads (and the Flask reloader doubled them). They now start once via
  `start_background_services()`.
- The CLI loop exited on any utterance containing "stop", "exit" or "shutdown" (e.g. "stop the music",
  "shutdown system"). It now exits only on whole exit phrases.
- Reminder parsing: "in 10 minutes to drink water" stored "s to drink water", and "tomorrow at 9 am to go
  college" stored "9 am to go college". Both are fixed.
- `vision.py` crashed at import on MediaPipe ≥ 0.10.30 (no `mp.solutions`) or without OpenCV. Imports are now
  optional, hand gestures use the MediaPipe Tasks `HandLandmarker` (with a legacy fallback when an old
  MediaPipe still provides it), and the `faces/` folder is created lazily.
- `knowledge.py` required sentence-transformers, PyPDF2 and numpy at import. Imports are now lazy, pypdf is
  preferred with a PyPDF2 fallback, and a clear message is shown when the extra is missing.
- Data files (`memory.json`, knowledge index) were written relative to the current directory. They now
  default to the project folder (overridable), and memory writes are atomic (`0600`).
- `run_jarvis.command` used a hardcoded personal path and a fixed sleep. It now resolves its own
  folder, uses `.venv`, polls `/healthz` and logs in with a one-time link.

## 3. Architecture and dependency changes

```text
server.py ─┐                      ultron/api/app.py  (Flask: guards, auth, routes)
ultron CLI ┴─> ultron.api.run() ──┤
                                   └─> ultron/legacy.py (Backend protocol) ─> main.py (legacy router)
main.handle_command(text, ctx) ─> ultron/tools/registry.py ─> ultron/security/{policy,confirmation}
                                                           └─> ultron/tools/system.py (power, caches)
ultron/config.py      settings + credentials (env/.env only)
ultron/security/      tokens, sessions, policy, confirmation, redact, secret_scan
```

- Packaging: `pyproject.toml` (setuptools) with `uv.lock`, and `requires-python >=3.10,<3.14`.
  - Core dependencies: Flask, requests, python-dotenv, psutil, openai, wikipedia.
  - Extras: `voice`, `wakeword`, `vision`, `rag`, `dev`, `all`.
  - The vision extra uses `opencv-contrib-python`, the same distribution MediaPipe depends on, so two
    packages never install competing `cv2` modules.
- Flask is kept, and no new frameworks were added. The legacy `main.py` was edited in targeted places,
  not rewritten.
- CI: uv 0.13.0 on Python 3.10–3.13, running `uv sync --locked`, compileall, ruff, `check-secrets` and pytest.

## 4. Tests

161 tests in `tests/` (all passing on Linux with Python 3.11 at commit time):

| File | Tests | Covers |
|---|---|---|
| `test_security_core.py` | 36 | settings and bind validation, credentials, token file permissions, sessions, CSRF, tickets, throttle, policy, confirmations, redaction |
| `test_api_security.py` | 38 | unauthenticated / invalid credentials / forged cookie, origin, host, CSRF, JSON-only, the CSRF exploit regression, cookie flags, HUD confirm/cancel flow, 503 fail-closed, error hygiene |
| `test_tools.py` | 17 | registry policy, single use, expiry, principal binding, gate, cache-cleanup and shutdown regressions |
| `test_legacy_commands.py` | 32 | `main.handle_command` confirmation flow, standalone-confirm regression, untrusted context, reminders, CLI exit phrases, vision/knowledge degradation, `jarvis_logic` |
| `test_secret_scan.py` | 17 | repository scan, detector shapes, no values in output, `.gitignore`, `.env.example` |
| `test_cli_and_startup.py` | 9 | token CLI, doctor, wake-word missing-key exit, real `server.py` startup (loopback, debug off, 401/200), non-loopback refusal |
| `test_vision_optional.py` | 7 | backend selection (Tasks / legacy / none), gesture classification, error containment |
| `test_news.py` | 5 | NewsAPI client (moved from `news_test.py`) |

No test shuts anything down, deletes user files, reads real credentials or runs osascript or a shell.
`tests/conftest.py` isolates all data paths and fails any test that tries to run osascript.

## Remaining risks and limitations

- **Credentials remain in public Git history.** The Picovoice key was at `HEAD` until this branch, and the
  NewsAPI key is in older commits. Rotation is a manual owner action
  ([`SECURITY_CREDENTIAL_ROTATION.md`](SECURITY_CREDENTIAL_ROTATION.md)). History was not rewritten.
- ~~**Only registry tools are risk-gated.**~~ **Resolved in M1.1:** every state-changing legacy action is now
  a registered tool behind one policy boundary, and turning off security mode needs a confirmation that
  passes the face gate. See [`M1_1_TOOL_SECURITY_BOUNDARY.md`](M1_1_TOOL_SECURITY_BOUNDARY.md).
- **The face check is not authentication** (pixel distance, easy to spoof). The real boundary is the API
  token and session.
- **The bearer token is all-powerful.** Anyone who can read the token file (your user account) can control
  the assistant. No per-client scopes yet.
- **Single-user design.** All bearer clients share one principal (`api-token`). HUD sessions are separate
  principals.
- **Plain HTTP on loopback.** It is fine for `127.0.0.1`. Exposing it to a LAN (`ULTRON_ALLOW_NON_LOOPBACK`)
  would send the token and cookie unencrypted and is not supported.
- **Rate limiting** applies to login only.
- **Keyword routing** still misroutes some phrases (e.g. "timer" matches the time handler, "battery
  technology" matches battery status). Out of M1 scope.
- `open-hud` passes the one-time login URL to the browser as a process argument. It is visible to other
  local processes for a moment, but it expires in 2 minutes and works once.

## Platform verification

| Verified on Linux (sandbox, Python 3.11) | Not verifiable here (needs macOS) |
|---|---|
| install from `uv.lock` (core + dev, and the vision extra), compileall, ruff, all 161 tests | `say`, `osascript` restart/shutdown, `open`, `screencapture`, brightness/volume |
| real `server.py` process: loopback bind, debug off, 401 without auth, ticket login, CSRF-protected `/ask`, `/confirm` round trip, cross-origin 403 | camera and face/gesture inference, microphone, PortAudio/PyAudio build |
| vision extra: MediaPipe 1.1.0 has no `mp.solutions`; the Tasks backend is selected when a model path is set; missing native libraries are reported, not crashed | MediaPipe Tasks inference (needs `libEGL`, which the sandbox lacks) and the hand model download (storage.googleapis.com was not reachable) |
| | TCC prompts (Automation, Camera, Microphone), `run_jarvis.command` double-click (syntax-checked with `bash -n` only) |
| | Python 3.10 / 3.12 / 3.13 locally (interpreter downloads were blocked); covered by the CI matrix |
