# Jarvis — AI Voice Assistant

[![CI](https://github.com/rajaryan1111/jarvis-assistant/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rajaryan1111/jarvis-assistant/actions/workflows/ci.yml)

A macOS-focused personal AI assistant that combines **voice interaction, vision, retrieval, LLM tools and desktop automation** in one Python application, with a browser-based HUD.

> This is a personal engineering project, not a production security product. Face recognition is a
> prototype convenience feature, **not authentication**. The project is being evolved into
> "ULTRON", a local-first computer-use agent. See [`docs/ULTRON_ARCHITECTURE_AUDIT.md`](docs/ULTRON_ARCHITECTURE_AUDIT.md)
> , [`docs/M1_SECURITY_AND_FOUNDATION.md`](docs/M1_SECURITY_AND_FOUNDATION.md)
> and [`docs/M1_1_TOOL_SECURITY_BOUNDARY.md`](docs/M1_1_TOOL_SECURITY_BOUNDARY.md).

## Features

- 🎙️ **Voice**: microphone loop (`python main.py`), an optional Porcupine wake word, and macOS `say` for speech.
- 🧠 **LLM + RAG**: OpenAI-backed answers and study plans, plus an offline knowledge base over your notes/PDFs.
- 👁️ **Vision (optional)**: face registration/check, person detection and an open-palm gesture (MediaPipe Tasks).
- 🖥️ **macOS automation**: open apps/sites, screenshots, brightness/volume, diagnostics, and restart/shutdown (with confirmation).
- ⏰ **Routines**: reminders, a study planner, local memory, sleep mode and a virtual smart-home state.
- 🌐 **Integrations**: weather, news, Wikipedia, web/YouTube search and cricket scores.
- 🖥️ **Web HUD**: a Flask server on `127.0.0.1` with an authenticated browser control surface.

## Requirements

| | |
|---|---|
| OS | **macOS** for the full feature set (`say`, `open`, `osascript`, `screencapture`, camera, microphone). The server, CLI, text commands and tests also run on Linux, where the macOS-only actions report "only available on macOS". |
| Python | 3.10 – 3.13 (`requires-python = ">=3.10,<3.14"`) |
| Package manager | [uv](https://docs.astral.sh/uv/) recommended (CI uses uv 0.13.0); plain `pip` also works |

## Install

```bash
git clone https://github.com/rajaryan1111/jarvis-assistant.git
cd jarvis-assistant
uv sync                       # core only: HUD server, CLI, text commands (reproducible from uv.lock)
```

Optional features are separate extras, so the core install stays light:

| Extra | Adds | Install |
|---|---|---|
| `voice` | microphone input for `python main.py` (SpeechRecognition, PyAudio) | `brew install portaudio` then `uv sync --extra voice` |
| `wakeword` | Porcupine wake word (`wakeword_listener.py`) | `brew install portaudio` then `uv sync --extra wakeword` |
| `vision` | OpenCV face utilities and MediaPipe hand gestures | `uv sync --extra vision` |
| `rag` | offline knowledge base (sentence-transformers, pypdf) | `uv sync --extra rag` |
| `dev` | pytest, ruff | `uv sync --extra dev` |
| `all` | every runtime extra | `uv sync --extra all` |

Combine extras as needed, for example `uv sync --extra voice --extra vision`. Without uv, use
`python -m venv .venv && source .venv/bin/activate && pip install -e ".[voice]"`. This isn't
pinned by the lock file, so the installed versions may differ.

**Hand gestures** use MediaPipe's Tasks API (recent MediaPipe releases no longer include the old
`mp.solutions` API). Download the official model and point the assistant at it:

```bash
mkdir -p models
curl -L -o models/hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
echo "ULTRON_HAND_LANDMARKER_MODEL=$PWD/models/hand_landmarker.task" >> .env
```

## Configure

1. **API keys (all optional).** Copy `.env.example` to `.env` and add only the keys you use. A missing key
   disables only that feature, with a clear message. `.env` is git-ignored. Never commit it.

   | Variable | Provider | Used for |
   |---|---|---|
   | `OPENAI_API_KEY` | OpenAI | LLM replies, knowledge-base answers, study plans |
   | `WEATHER_API_KEY` | OpenWeather | weather |
   | `NEWS_API_KEY` | NewsAPI | headlines (sent in the `X-Api-Key` header, never in URLs) |
   | `CRICKET_API_KEY` | CricketData | live scores |
   | `PICOVOICE_ACCESS_KEY` | Picovoice | wake-word listener |

2. **Local API token (required for the HUD).** This token authorises control of your computer through the
   local server. It is generated locally and stored **outside the repository** in a file only you can read:

   ```bash
   uv run ultron token init     # creates ~/Library/Application Support/Ultron/api_token (chmod 600); not printed
   uv run ultron doctor         # checks config and optional features; never prints secret values
   ```

   Other commands: `token path`, `token show` (prints it only when you ask), `token copy` (macOS clipboard),
   and `token rotate` (replaces it, retires the old one permanently and logs out every HUD session; a running
   server picks it up immediately). `sessions revoke-all` logs out every HUD session; `sessions reset` is the
   owner recovery if the session state file is ever corrupt. As an alternative to the file, you can set
   `ULTRON_API_TOKEN` (32+ random characters) in your environment, but the file is preferred because
   rotation can replace it.

   For the wake-word listener, create a separate, limited **client token**: `uv run ultron token init --client`.
   It can send commands but can never log in to the HUD or approve anything.

## Start

```bash
uv run python server.py        # HUD/API server on http://127.0.0.1:5001 (same as: uv run ultron serve)
uv run ultron open-hud         # opens the HUD with a one-time login link (valid 2 minutes)
```

You can also open <http://127.0.0.1:5001/login> and paste the token (`uv run ultron token copy`).
The HUD then uses an HttpOnly session cookie, so the token is never stored in the page or in browser
storage. On macOS, `run_jarvis.command` (double-click) does all of the above using `.venv`.
Set `ULTRON_BROWSER_APP="Google Chrome"` to choose a browser.

The other entry points:

```bash
uv run python main.py              # microphone loop (needs the voice extra); say "exit" or "goodbye" to quit
uv run python wakeword_listener.py # wake word; needs the server running, PICOVOICE_ACCESS_KEY and the client token
```

Voice stays the main way to use JARVIS: read-only and routine commands (time, weather, open apps, volume,
notes, reminders, ...) just work. **High-impact actions** (shut down, restart, clear caches or all reminders,
turn off security mode, replace your face) can be *requested* by voice but must be **approved in the
signed-in HUD** with the CONFIRM button. No code is spoken, and "confirm" by voice is refused, because a voice
(or a recording of one) is not proof of who is speaking. `ULTRON_VOICE_TRUST=observe` limits voice to read-only
answers. Details: [voice trust model](docs/M1_2_IDENTITY_SESSIONS_VOICE.md#5-voice-trust-model).

## Security model

The assistant can control this computer, so the server is built to fail closed:

- **Loopback only.** It binds to `127.0.0.1`. Other addresses are refused unless you set
  `ULTRON_ALLOW_NON_LOOPBACK=1` (not recommended: traffic is plain HTTP). Debug mode is off by default and is
  never allowed on a non-loopback address.
- **Authentication on every sensitive endpoint.** `/status`, `/ask`, `/speak`, `/confirm`, `/cancel`,
  `/pending` and `/logout` require either `Authorization: Bearer <token>` (wake-word and other clients) or the
  HUD session cookie. If no token is configured, these endpoints return `503`, and the login page explains the
  setup. Tokens are never accepted in URLs.
- **Sessions that stay revoked.** Logout, `sessions revoke-all` and `token rotate` are recorded in a private
  session state file (`0600`, no secrets, only hashes), so they survive restarts. Expiry is enforced by the
  server against the current `ULTRON_SESSION_HOURS`. If that file is ever corrupt or unsafe, sign-in fails
  closed (`503`) and tells you to run `uv run ultron sessions reset`.
- **Authentication is not authorization.** Bearer tokens and voice can *request* actions, but only a
  signed-in HUD session (typed or clicked) can *approve* one. The client token can't even log in. Token and
  session administration is CLI-only. This does not protect against someone who controls your OS account.
- **Cross-site protection.** The server checks the `Host` header against an allowlist (DNS-rebinding
  defence) and rejects disallowed `Origin` values. It never sends CORS headers. State-changing browser
  requests need an allowed `Origin`/`Referer`, a per-session `X-CSRF-Token` and `Content-Type:
  application/json`. The cookie is `SameSite=Strict`. CORS is not treated as authorisation.
- **One policy boundary for every action.** Every state-changing legacy action (apps, browser, volume,
  brightness, screenshots, camera, memory, reminders, security mode, power, caches) is a registered tool
  with a risk tier, and voice, CLI, HUD, API and background services all reach it through the same tool
  registry. Read-only commands work for anyone. Routine actions run without prompts for an authenticated
  user. See the [action/risk matrix](docs/M1_1_TOOL_SECURITY_BOUNDARY.md#action--risk-matrix).
- **Confirmation for high-impact actions.** Turning off security mode, re-registering your face, deleting
  all reminders, clearing user caches, restart and shut down create a *pending action* bound to the exact
  arguments. It runs only after the signed-in HUD approves it: the CONFIRM button, or typing the one-time
  code shown for requests made in the HUD. It must happen within 60 seconds (120 for requests made by voice).
  It is single use, and three wrong codes cancel it. A bare "confirm shutdown" does nothing, and **no spoken
  confirmation is ever accepted**. While security mode is on, the approval also needs the face check, so
  turning the mode off can't skip it (owner recovery: `uv run ultron security disable` on the Mac).
- **Untrusted content never authorises anything.** Commands without an authenticated context (such as
  `jarvis_logic.handle_command(text)` or a direct call to a helper in `main.py`) get read-only answers only.
  Knowledge-base text, web pages and LLM output can't request or confirm actions and are never executed.
- **Narrow processes and files.** Tools start processes only through one runner (argv lists, absolute
  paths, timeouts, bounded and redacted output; no shell). They write files only inside their configured
  folder, never through symlinks, and atomically.
- **No secrets in the browser or logs.** Pages and JS contain only a per-session CSRF token. Log
  output passes through a redaction filter, and `uv run ultron check-secrets` scans files that could be
  committed for hardcoded credentials (CI runs it).
- Security headers include a strict CSP (no inline script), `frame-ancestors 'none'`, `nosniff` and `no-store`.

Known limits are listed in [`docs/M1_1_TOOL_SECURITY_BOUNDARY.md`](docs/M1_1_TOOL_SECURITY_BOUNDARY.md#limitations)
and [`docs/M1_SECURITY_AND_FOUNDATION.md`](docs/M1_SECURITY_AND_FOUNDATION.md#remaining-risks-and-limitations).
For example, the API token is all-powerful, voice is trusted like a logged-in user, and the face check is
not authentication.

## Tests and checks

```bash
uv sync --extra dev
uv run pytest -q                      # unit + HTTP security tests; never shuts down, deletes user files or uses real keys
uv run ruff check .
uv run python -m ultron check-secrets
uv run python -m compileall -q -x '/\.venv/' .
```

CI runs these on Python 3.10–3.13 (Ubuntu). The tests use fake command runners and temporary
directories. They can't verify macOS behaviour (`say`, `osascript`, camera, microphone, TCC permissions).
See the M1 report for what was verified where.

## Troubleshooting

| Symptom | Fix |
|---|---|
| HUD shows "Authentication is not configured" | `uv run ultron token init`, then restart the server |
| `401` / redirected to `/login` | Session expired, you logged out, or the token was rotated. Run `uv run ultron open-hud` |
| `503 session_state_unavailable` | The session state file is corrupt or has unsafe permissions: `uv run ultron sessions reset`, then `open-hud` |
| `503 token_retired` | You rotated the token but `ULTRON_API_TOKEN` still holds the old one. Remove it from your shell/`.env` |
| "I can't accept a spoken confirmation" | By design: press CONFIRM in the signed-in HUD |
| `403 forbidden_origin` / `csrf_failed` | Use the HUD from `http://127.0.0.1:5001` (or `localhost:5001`) and reload the page |
| `400 invalid_host` | Open the HUD at `127.0.0.1` or `localhost`, not via another hostname |
| "The API token file … is readable by other users" | `chmod 600 "$(uv run ultron token path)"` |
| "Refusing to listen on non-loopback address" | Unset `ULTRON_HOST`, or read the security model first |
| "That action is only available on macOS" | Restart, shutdown and cache cleanup use macOS APIs |
| "Vision features are not installed" / hand model message | `uv sync --extra vision`, and set `ULTRON_HAND_LANDMARKER_MODEL` (see Install) |
| `PyAudio` fails to build | `brew install portaudio`, then reinstall the `voice` extra |
| Restart/shutdown confirmed but nothing happens | Grant your terminal Automation permission for System Events (System Settings → Privacy & Security → Automation) |
| A feature says its key is missing | Add it to `.env`. Run `uv run ultron doctor` to see which keys are set |

## Repository structure

```text
server.py             starts the HUD/API server (ultron.api.app)
main.py               legacy JARVIS command router, voice loop and integrations
ultron/
  config.py           settings + credential loading (env/.env only)
  api/app.py          Flask app: auth, origin/host/CSRF guards, routes
  security/           tokens, sessions, risk policy, confirmations, redaction, secret scanner
  tools/              tool interface with risk metadata, registry, macOS system tools
  legacy.py           adapter between the API and main.py
  cli.py              `ultron` command (serve, token, open-hud, doctor, check-secrets)
templates/, static/   HUD + login page
vision.py             optional OpenCV/MediaPipe utilities
knowledge.py          optional offline knowledge base
news.py               NewsAPI integration
wakeword_listener.py  optional Porcupine wake word
jarvis_logic.py       thin wrapper around main.handle_command
tests/                pytest suite
docs/                 architecture audit, M1 report, credential rotation guide
```

## Limitations

- The macOS system commands don't work on Windows or Linux.
- Integrations need your own API keys, and the free tiers have limits.
- The face check is a pixel-distance prototype. It is easy to fool, and it is not authentication.
- The smart-home layer is a virtual state model, not a device controller.
- Natural-language routing is keyword-based, so some phrases go to the wrong handler (see the audit).

## License / attribution

Personal portfolio project by **Raj Aryan**. Third-party services and libraries retain their respective licenses.
