# ULTRON — Phase 0: Repository Audit & Engineering Plan

| | |
|---|---|
| **Audit date** | 2026-10-12 |
| **Repository** | `github.com/rajaryan1111/jarvis-assistant` (the brief's URL, `github.com/a1creator284/jarvis-assistant`, redirects here through the GitHub API) |
| **Audited commit** | `35b2a33` (`main`, "chore: remove generated knowledge index") |
| **Audit branch** | `arena/289eecea-jarvis-assistant` |
| **Audit environment** | Linux sandbox (Debian 12, x86-64, Python 3.11.2). **No macOS, microphone, camera, speakers, or display.** |
| **Scope** | Read-only audit plus a plan. No application code changed. |

---

## 0. Executive summary

JARVIS is a **single-user macOS voice assistant prototype**, about 3,800 lines in total. Most of it is one 1,504-line module (`main.py`). It sends free text through a long chain of **substring `if` checks** (55 top-level branches) to hardcoded skills. Anything that doesn't match goes to a single OpenAI chat call with no history and no tools. A Flask app serves a browser HUD. Voice input exists in **three separate, inconsistent paths**. TTS is the macOS `say` command, which blocks and can't be interrupted.

It is **not an agent**. There is no tool-calling, no planning, no observe/act/verify loop, no conversation memory, and no provider abstraction. The useful parts to keep are the **skill ideas** (reminders, system info, app launching, weather, cricket, and so on), the **HUD's visual design**, the **local RAG idea**, and the **test and CI scaffolding**. The core logic should not become ULTRON's foundation. It should be **wrapped, then retired** behind a new modular runtime (see §12–§17).

### Top findings (details in §5–§11)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| 1 | **Critical** | A **live-format Picovoice AccessKey is hardcoded** in `wakeword_listener.py:12` at HEAD of a **public** repo. | Verified (secret scan, value withheld) |
| 2 | **Critical** | A **NewsAPI key stays in Git history** (`news.py` and `news_test.py` before commit `a3bdbd2`). Removing it from HEAD did not revoke it. | Verified (history scan, value withheld) |
| 3 | **Critical** | **Unauthenticated command API exposed to the LAN and to any website.** `/ask` and `/speak` have no auth. The server binds `0.0.0.0` with `debug=True`. `get_json(force=True)` accepts `text/plain`, so a **cross-origin "simple" POST from any web page can trigger `confirm restart`, `confirm shutdown`, or `clear cache`**. | Verified (Flask test client: cross-origin `text/plain` POST reached the restart `osascript` call, which was intercepted) |
| 4 | **High** | `clear cache` runs `rm -rf "~/Library/Caches"/*` through `shell=True` **with no confirmation**, because security mode is off by default. `confirm shutdown` and `confirm restart` run **with no pending-confirmation state**: saying the confirm phrase alone is enough. | Verified (side effects intercepted, not executed) |
| 5 | **High** | "Security mode" face check is a **raw L2 pixel difference between grayscale crops** against one stored JPEG. It is not authentication, and a photo of the owner would defeat it. | Code inspection |
| 6 | **High** | **A fresh install is broken.** README says `pip install -r requirements.txt`, but there is **no `requirements.txt` or `pyproject.toml`**. Unpinned `mediapipe` now resolves to **1.1.0, which no longer ships `mp.solutions`**. `vision.py` uses that API at import time, and `main.py` → `server.py` import `vision` unconditionally, so the **whole app fails to start**. | Verified (wheel contents inspected for 1.1.0 vs 0.10.21) |
| 7 | **High** | `jarvis_logic.py` calls `main.handle(...)`, which doesn't exist, so it always returns "Sorry, something went wrong in my brain." | Verified |
| 8 | Medium | Routing by substring misfires: "set a timer…" → tells the time; "tell me about the latest battery technology" → battery status; "research X" → web search; "turn on the backlight" → virtual smart-home "light". The CLI quits on any utterance containing `stop`, `exit`, or `shutdown` (e.g., "stop the music", "shutdown system"). | Verified |
| 9 | Medium | Reminder parser bugs: "in 10 minutes to drink water" stores the text **"s to drink water"** (regex alternation order). "tomorrow at 9 am to go college" stores **"9 am to go college"**. Same for "on monday at 7 am …". | Verified |
| 10 | Medium | With Flask `debug=True`, the reloader **parent and child processes both import `main`**, so **both start the reminder and intruder watcher threads**. Two in-memory copies of `memory.json` then race, which can produce duplicate reminders and lost writes. | Verified (process and thread inspection) |
| 11 | Medium | The only tested module (`news.py`) **isn't used by the app**. `main.get_news()` is a separate, untested reimplementation. Effective test coverage of the running app is **0%**. | Verified (grep + tests) |
| 12 | Medium | The wake-word path (`wakeword_listener.py`) posts to `/ask` and **only prints** the reply. `/ask` doesn't speak, so wake-word commands get **no spoken answer**. | Code inspection |

### Immediate owner actions (before any feature work)

1. **Revoke and rotate the Picovoice AccessKey** in the Picovoice Console. It is public in HEAD and in history.
2. **Revoke and rotate the NewsAPI key.** It is public in history.
3. Until M1 lands, **don't run `server.py` on untrusted networks**, and close the HUD tab when you're not using it. (Any website you visit can POST to `127.0.0.1:5001`.)
4. Decide whether to rewrite Git history (`git filter-repo`) after rotation. This is optional once the keys are revoked, and it needs a force-push to `main`, so it is **the owner's decision**.

---

## 1. Method and evidence levels

Every claim in this document carries one of three evidence levels:

- **[V] Verified.** Observed by running something in this sandbox (tests, linters, the import/startup probe, or the routing harness).
- **[I] Inspection.** Read directly in the source code. Logic was traced but not run.
- **[U] Unverified.** Claimed by the README, or depends on macOS or hardware that this sandbox doesn't have.

**What the sandbox could not verify:** anything that needs macOS (`say`, `osascript`, `open`, `screencapture`, `osx-cpu-temp`), audio devices (PyAudio, Porcupine, SpeechRecognition microphone), the webcam (OpenCV capture, MediaPipe), browser speech recognition, or real API calls. Outbound network was limited to GitHub and PyPI. Heavy ML dependencies (torch, sentence-transformers, mediapipe, opencv) were **deliberately not installed**.

**How runtime behaviour was still verified safely:** a harness (see Appendix A) imported `main` and `server` with *only the unavailable hardware/ML modules stubbed*. It **intercepted `subprocess.run`, `subprocess.check_output`, and `webbrowser.open` so nothing actually ran**, and it ran from a temp directory so no `memory.json` or `faces/` touched the repo. Real Flask, requests, psutil, and openai packages were used.

---

## 2. Repository identity and Git state [V]

- `git status` showed a clean tree on `arena/289eecea-jarvis-assistant`. There was no uncommitted user work to preserve.
- Remote: `origin → https://github.com/rajaryan1111/jarvis-assistant.git`. The authenticated user has `ADMIN` permission.
- `a1creator284/jarvis-assistant` resolves to `rajaryan1111/jarvis-assistant` (same repo, created 2025-12-01). It is public.
- The clone was **shallow** (1 commit). It was unshallowed with `git fetch --unshallow`, which is non-destructive, so the full history (**18 commits**) could be scanned.
- Branches on the remote: `main` only. No PRs or issues. The last 5 CI runs on `main` all **passed** (CI only runs `compileall`).
- History shape: one big "Initial commit of JARVIS project" (2025-12-01, 3,795 lines), then docs, CI, and hygiene commits in Sept/Oct 2026. It also removed a bundled third-party PDF (1.7 MB, still in history) and a generated `knowledge_index.json`.

---

## 3. Repository map [V]

| Path | Lines | Role | Status |
|---|---:|---|---|
| `main.py` | 1,504 | **Everything**: config, `speak`/`listen`, JSON memory, profile learning, GPT call, YouTube/weather/news/wiki/DDG/cricket, system info, cache cleaner, app launcher, brightness/volume/screenshot, virtual smart home, reminders and watcher thread, "security mode" and intruder watcher thread, study planner, the `handle_command` router, and the CLI loop | Works for the happy path; many issues |
| `server.py` | 42 | Flask HUD server: `/`, `/ask`, `/status`, `/speak` (port 5001, `0.0.0.0`, `debug=True`) | Works [V]; insecure |
| `templates/index.html` | 252 | HUD markup. Status labels are **hardcoded** ("FACE ID LINKED", "Knowledge base: READY") | Cosmetic |
| `static/app.js` | 308 | HUD logic: chat, `/status` polling, browser `webkitSpeechRecognition` wake word, red-alert mode, `/speak` | Inspection only |
| `static/style.css` | 939 | HUD styling | Keep the design |
| `static/red-alert.mp3` | 110 KB | Alert sound | — |
| `vision.py` | 226 | Webcam capture, Haar face crop, L2 "face match", MediaPipe hand "open palm", person detection | **Broken with current mediapipe** [V] |
| `knowledge.py` | 268 | RAG: walk `knowledge_docs/`, PyPDF2/text extraction, fixed-size chunks, MiniLM embeddings, JSON index, brute-force cosine, GPT answer | Inspection only (heavy deps) |
| `wakeword_listener.py` | 107 | Porcupine wake word → Google STT → POST `/ask` | **Hardcoded secret**; reply not spoken |
| `jarvis.ppn` | 3.6 KB | Porcupine keyword model "jarvis" (tied to a platform and SDK version) | Compatibility unknown [U] |
| `jarvis_logic.py` | 20 | Wrapper that calls the missing `main.handle` | **Broken** [V], unused |
| `client.py` | 22 | OpenAI "haiku" demo; makes a **network call at import time** | Scratch file |
| `news.py` / `news_test.py` | 28 / 37 | NewsAPI helper + 3 unit tests | Tests pass [V]; **unused by the app** |
| `musicLibrary.py` | 7 | Dict of 5 YouTube URLs | **Unused** [V] |
| `run_jarvis.command` | 27 | macOS double-click launcher: `cd` to a **hardcoded path on the author's machine**, starts `server.py`, opens Chrome | Not portable |
| `.github/workflows/ci.yml` | 22 | `python -m compileall` on Ubuntu, Python 3.11 | Passes; doesn't run tests |
| `.env.example`, `.gitignore`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md` | — | Docs and config | `.gitignore` is reasonable |

Missing entirely: dependency manifest, lockfile, `tests/` directory, linter/type-checker config, logging config, license file, and the `docs/` folder (until this audit).

---

## 4. Current architecture

### 4.1 Runtime topology [I, partially V]

```text
                          ┌──────────────────────── Browser (Chrome) ───────────────────────┐
                          │ index.html + app.js                                             │
                          │  • webkitSpeechRecognition (cloud STT, continuous, en-IN)       │
                          │  • substring "jarvis" wake word; "red alert" handled client-side│
                          │  • POST /ask  → shows reply → POST /speak (Mac speaks)          │
                          └──────────────┬──────────────────────────────────────────────────┘
                                         │ HTTP, no auth, 0.0.0.0:5001
 wakeword_listener.py ──POST /ask──────► │
 (Porcupine + Google STT,                ▼
  reply only printed)          server.py (Flask, debug=True, reloader)
                                         │  from main import handle_command, get_status, speak
                                         ▼
                 main.py  (module import has side effects: .env, OpenAI client, vision import,
                          knowledge import, memory.json load, 2 daemon threads started)
   ┌──────────────┬───────────────┬─────────────────┬──────────────────┬───────────────────┐
   │handle_command│ skills (≈40   │ memory.json     │ reminder_watcher │ intruder_watcher  │
   │ 55 substring │ functions)    │ (cwd-relative,  │ (30 s poll,      │ (10 s poll, camera│
   │ if-branches  │ subprocess/   │ no lock, non-   │ speaks via say)  │ when security on) │
   │ → GPT fallbk │ osascript/web │ atomic writes)  │                  │                   │
   └──────────────┴───────────────┴─────────────────┴──────────────────┴───────────────────┘
          │                │                                   │
          ▼                ▼                                   ▼
   OpenAI chat       vision.py (OpenCV/MediaPipe)       knowledge.py (PyPDF2 + sentence-
   (gpt-4.1-mini,    webcam, faces/<name>.jpg            transformers, knowledge_index.json)
   max 200 tokens,
   no history)

 python main.py  →  separate CLI loop: SpeechRecognition(Google) → handle_command → say
                    (its own process ⇒ its own copy of memory + its own watcher threads)
```

### 4.2 Entry points and startup commands

| Command | What happens | Evidence |
|---|---|---|
| `python server.py` (or `run_jarvis.command`) | Imports `main`, which loads `.env`, builds the OpenAI client (or `None`), creates `sr.Recognizer`, imports `vision` (creates `faces/` in the cwd and initialises MediaPipe), imports `knowledge` (imports sentence-transformers/torch, which is slow), loads `memory.json`, and **starts 2 daemon threads**. Flask then starts in debug mode with the reloader, so this all happens **again** in the child process. Serves `0.0.0.0:5001`. | [V] with stubs: 2 Python processes, both with watcher threads; `/status` and `/ask` return 200; `faces/` created in the cwd |
| `python main.py` | Same import side effects. Speaks a boot line, then loops `listen()` → `handle_command()` → `speak()`. | [I] |
| `python wakeword_listener.py` | Porcupine on PyAudio input. On "jarvis", records one command with Google STT and POSTs it to `/ask`. **Needs `server.py` running.** | [I] |
| `python client.py` | Demo OpenAI call. Raises if no key. | [V] (raises `RuntimeError` without a key) |
| Start at login | **None.** No LaunchAgent, no app bundle. | [V] |

### 4.3 State and data

- `memory.json` (cwd-relative, plaintext) holds notes, profile, favourites, virtual smart-home state, reminders, security state, and study plans. It is written on every mutation with `open('w')` and no lock or atomic rename. It is read once at import, so separate processes (CLI, the server's reloader parent and child) **diverge and overwrite each other**. [I], process duplication [V]
- `faces/raj.jpg`: one plaintext face crop. **The owner's name is hardcoded in 4 places.** [V]
- `knowledge_docs/` and `knowledge_index.json`: a JSON file with every chunk's embedding, loaded fully on every query. [I]
- All paths are **relative to the cwd**, so where data lives depends on the directory you launched from. [I]

---

## 5. Feature inventory

Legend: ✅ works (as far as could be checked) · ⚠️ partial or buggy · ❌ broken · ❓ unverifiable here (macOS/hardware).

| Area | Feature | Status | Evidence | Notes |
|---|---|---|---|---|
| Text I/O | HUD text box → `/ask` → reply | ✅ | V | No history: each request is stateless apart from `memory.json` side effects |
| Text I/O | `jarvis_logic.handle_command` | ❌ | V | Calls the missing `main.handle` |
| Voice in | Browser `webkitSpeechRecognition` + "jarvis" substring | ❓ | I | Chromium/WebKit only; audio goes to the browser vendor's cloud STT; restart/watchdog hacks |
| Voice in | CLI `listen()` with Google Web Speech (`en-IN`) | ❓ | I | Unofficial free endpoint; English (India) only; 6 s timeout, 8 s phrase limit |
| Wake word | Porcupine `jarvis.ppn` | ❓ | I | Hardcoded key; `.ppn` compatibility with pvporcupine 4.x is unknown; reply not spoken |
| TTS | `/usr/bin/say -v Daniel` | ❓ | I | **Blocking** `subprocess.run`; can't interrupt; the voice can hear itself (no echo handling) |
| LLM | OpenAI Chat Completions `gpt-4.1-mini`, 200 tokens | ⚠️ | V (no-key path) | Model hardcoded in 4 places; no history; no system prompt; no tools; no streaming; "not configured" reply when there's no key |
| Hinglish | — | ❌ | V | Hinglish input falls through to the GPT fallback; STT is fixed to `en-IN`; keyword routing is English only |
| Memory | "my name is", favourites, "remember that", "what do you know about me" | ⚠️ | V (happy path) / I | Works, but triggers on any substring, so e.g. "…why my name is not showing" would set the name (inspection) |
| Reminders | Relative, absolute, tomorrow, weekday, daily | ⚠️ | V | Text corruption bugs (§0 #9); "remind me to X" without a time is rejected; only fires while the process runs; duplicated under the Flask reloader |
| Study planner | GPT 7-day plan + daily 8 pm reminder + progress | ⚠️ | V (offline template path) | Hardcodes "college student" persona |
| Web | YouTube play (scrapes the results page), DuckDuckGo HTML scrape, Wikipedia | ⚠️ | V (fallback paths) | HTML scraping is fragile; `wikipedia` package last released in 2014 |
| APIs | Weather (OpenWeather over **http**), news, cricket | ⚠️ | V (no-key paths) | Keys in query strings; weather uses plain HTTP; cricket prints the whole API response |
| System | Time, battery, CPU, RAM, temperature (`osx-cpu-temp` via Homebrew) | ⚠️ | V (Linux psutil) | Temperature needs a third-party binary; battery is `None` on desktops |
| System | Open app (`open -a`) → else open `https://<words>.com` | ⚠️ | V | "open the pod bay doors" → opens `thepodbaydoors.com` (arbitrary domain guess) |
| System | Brightness (System Events key codes), volume, screenshot to Desktop | ❓ | V (call shape) | Needs Accessibility/Automation permission; "increase volume" jumps to 100% |
| System | Clear cache (`rm -rf ~/Library/Caches/*`, shell) | ⚠️ dangerous | V | No confirmation by default |
| System | Restart / shutdown | ⚠️ dangerous | V | "confirm …" works without a prior request |
| Smart home | Virtual on/off and AC temperature stored in JSON | ✅ (virtual) | V | Substring device match ("backlight" → light) |
| Vision | Register face / "do you see me" / hand gesture / person detection | ❌ on fresh install | V (dependency) | `mp.solutions` is absent from mediapipe 1.1.0 (verified) and present in 0.10.21 (verified). Pin a verified version to run |
| Security mode | Face-gated sensitive commands; intruder voice alert | ⚠️ not real security | I | Pixel-L2 match; threshold is a magic number |
| HUD | Boot animation, chat, time/battery polling, red-alert mode | ✅ / ❓ | V (served 200) | Many status labels are fake; loads Google Fonts from a CDN |
| RAG | Index `knowledge_docs/*.pdf,txt,md`; answer with sources | ❓ | I | Fixed 1,200-char chunks; English-only MiniLM; full JSON scan; PyPDF2 is deprecated |
| Browser automation | — | ❌ none | — | Only `webbrowser.open` |
| Computer control (mouse, keyboard, windows, AX) | — | ❌ none | — | Only brightness key codes |
| Terminal / coding / filesystem tools | — | ❌ none | — | — |
| Task planning / agent loop | — | ❌ none | — | — |
| Startup at login / packaging | — | ❌ none | — | — |

---

## 6. Subsystem findings

### 6.1 Voice, wake word, TTS [I]
- **Three independent input pipelines** (browser Web Speech, CLI SpeechRecognition, Porcupine listener) use different wake-word logic, different STT, and different reply handling. None of them share state.
- No VAD, no endpointing control, no streaming STT, no barge-in. `say` blocks the calling thread (the Flask request thread on `/speak`, or the reminder thread).
- No echo handling. With speakers on, the browser recogniser hears JARVIS speaking. Only the wake-word requirement stops feedback loops.
- Wake word "jarvis" would have to be **retrained for "ULTRON"** whichever engine is chosen.

### 6.2 LLM integration [I/V]
- One `ask_gpt()` call with `history=[]`. There is no system prompt, so there's no persona, language policy, or safety policy. Replies are capped at 200 tokens.
- RAG and the study planner each create their own completions with the same hardcoded model. There is no retry, timeout config, cost tracking, or streaming.
- **No tool calling.** The model can't act; only the regex router can.

### 6.3 Tool execution [I]
- "Tools" are plain functions picked by substring order inside `handle_command`. Order is significant and fragile (e.g., "time" is checked before "temperature" and "timer").
- The functions mix concerns: they parse natural language, take the action, and phrase the spoken reply. They return only prose strings, with no structured result and no success or failure signal.
- `subprocess` is used both safely (argv lists) and unsafely (`shell=True` in `clean_system`). Ruff flags 8 × S607 (partial executable path), 5 × S603, and 1 × S602. [V]

### 6.4 Browser and computer control [I]
- Browser: `webbrowser.open` only. YouTube and DDG results are regex-scraped from HTML.
- Desktop: `open -a`, `osascript` for volume and System Events key codes, and `screencapture -x`. No window management, no Accessibility API, no mouse or keyboard synthesis beyond brightness keys, no clipboard, and no observation of screen state.

### 6.5 Memory, reminders, RAG, documents [I/V]
- Memory is one JSON blob shared by threads without locks (§4.3). Facts have no provenance or timestamps, and there's no conversation log or task history.
- The reminder scheduler is an in-process 30 s polling loop. Missed reminders fire late when the process restarts, which is fine, but each process duplicates them (§0 #10).
- RAG uses an English-only embedding model (`all-MiniLM-L6-v2`), which is unsuitable for Hindi/Hinglish. Chunks are fixed-size with no structure awareness. There's no keyword search and no incremental indexing (full rebuild each time).

### 6.6 Vision / security mode [I/V]
- `recognize_face` compares 200×200 grayscale crops by L2 norm against a threshold of 20000. That is sensitive to lighting and pose and easy to spoof. It must not gate destructive actions in ULTRON. Replace it with **Touch ID / LocalAuthentication** for high-risk confirmations (§15).
- `intruder_watcher` opens the camera every 10 s while security mode is on. That's a privacy concern, and the camera indicator turns on repeatedly.

### 6.7 HUD [I/V]
- Vanilla HTML/CSS/JS, no build step, served by Flask. The visual design is worth keeping.
- It's request/response only. There's no event stream, so it can't show agent progress, tool calls, approvals, or listening/speaking state, and its status chips are static text.

---

## 7. macOS dependencies and permissions (current) [I]

| Dependency / capability | Used for | macOS permission (TCC) that will be required |
|---|---|---|
| `/usr/bin/say` | TTS | none |
| `open`, `open -a` | apps, URLs | none (per-app Automation only if scripting the app) |
| `osascript` → `System Events` key codes, restart, shut down | brightness, power | **Accessibility** (key events) + **Automation → System Events** |
| `osascript set volume` | volume | none |
| `screencapture -x` | screenshot | **Screen Recording** |
| `osx-cpu-temp` (Homebrew, third-party) | temperature | none; not installed by default |
| Microphone (PyAudio / SpeechRecognition / browser) | voice | **Microphone** (granted to Terminal/Python or to Chrome) |
| Webcam (OpenCV) | face, hand, intruder | **Camera** |
| `~/Library/Caches` deletion | cleaner | may hit protected containers; can break running apps |

Important for ULTRON: TCC grants attach to the **responsible process** (Terminal/iTerm/VS Code/Python launcher, or an `.app` bundle). Running from different terminals means granting permissions again. That's the main technical reason to eventually ship a **signed `.app`** (M11).

---

## 8. Secrets and security issues (severity-ranked)

Secret values are **intentionally not reproduced** here.

| Sev | Issue | Location | Recommendation |
|---|---|---|---|
| Critical | Hardcoded Picovoice AccessKey (live format, public) | `wakeword_listener.py:12` (HEAD + history) | Revoke and rotate now; load from Keychain or env; add secret scanning (gitleaks) to CI and pre-commit |
| Critical | NewsAPI key in history | `news.py`/`news_test.py` in commits before `a3bdbd2`/`24a397c` | Revoke and rotate; optional history rewrite (owner decision) |
| Critical | Unauthenticated `/ask` + `/speak`, `0.0.0.0`, `debug=True`, `force=True` JSON → CSRF from any site, LAN access | `server.py` | Bind `127.0.0.1`; per-launch bearer token; strict `Origin` + `Content-Type` checks; turn off debug |
| High | Destructive actions without real confirmation (`rm -rf` caches via shell, shutdown/restart with a stateless "confirm" phrase) | `main.py` `clean_system`, `handle_command` | Remove or route through the policy engine with explicit, stateful approval |
| High | Pseudo-biometric "security mode" presented as security | `vision.py`, `main.py` | Downgrade to a demo; use LocalAuthentication (Touch ID) |
| Medium | API keys in URL query strings (weather, news, cricket), which end up in logs and proxies | `main.py` | Use headers where the API allows; never log URLs that carry keys |
| Medium | Weather over plain HTTP | `main.py:234` | HTTPS |
| Medium | Debug prints of full API responses and every command | `main.py` | Structured logging with redaction |
| Medium | Personal data in plaintext under the cwd (`memory.json`, `faces/`) | — | `~/Library/Application Support/Ultron/` with `0700` permissions; consider FileVault-only plus optional encryption |
| Medium | Browser STT sends audio to the vendor's cloud; Google Web Speech in the CLI | `app.js`, `main.py` | Local STT by default (M7) |
| Low | `client.py` makes a paid API call at import, with `store=True` | `client.py` | Delete |
| Low | Third-party fonts loaded from a CDN in a local tool | `index.html` | Bundle locally |

---

## 9. Technical debt

**Duplication [V/I]**
- News: `news.py` (tested, unused) vs `main.get_news` (used, untested).
- STT and wake word: three pipelines (§6.1). `speak()` is defined in Python and as a JS wrapper around `/speak`.
- Memory defaults dict repeated three times in `load_memory`. GPT call code repeated three times.
- `musicLibrary.py` and `jarvis_logic.py` are dead code.

**Tight coupling [I]**
- `main.py` is a god module: import has side effects (threads, camera module, ML model import, file I/O), and globals (`memory`, `jarvis_sleep`, `client`) are mutated from threads and Flask handlers.
- `server.py` imports `main` directly, so you can't start the HUD without loading vision, torch, and the audio libraries.
- Parsing, acting, and phrasing are fused in every skill, so skills can't be reused by a planner.

**Obsolete or fragile dependencies [V via PyPI metadata, 2026-10-12]**

| Package | Status | Action |
|---|---|---|
| `mediapipe` (unpinned) | Latest 1.1.0 **removed `mp.solutions`** (verified in the wheel) | Pin or replace; vision is out of ULTRON's core scope anyway |
| `PyPDF2` | Last release 2022 (3.0.1); succeeded by `pypdf` (6.x) | Migrate to `pypdf` |
| `wikipedia` | Last release 2014 | Use the Wikipedia REST API with `httpx`, or treat it as generic web research |
| `SpeechRecognition.recognize_google` | Uses an unofficial, keyless Google endpoint | Replace with local Whisper + optional cloud STT |
| `pvporcupine` | 4.x current; needs an AccessKey; `.ppn` files are tied to version and platform | Keep only behind an interface (M7 decision) |
| Flask dev server + `debug=True` | Not for long-running local daemons | FastAPI + uvicorn (M1) |

**Code quality [V]:** pyflakes reports 2 unused imports and 4 useless `global` statements. Ruff (`E,F,B,S`) reports 81 findings: 55 long lines, 13 subprocess flags (S603/S607), 1 `shell=True` (S602), 1 Flask debug, 1 bind-all, 1 request without a timeout (`wakeword_listener.py`), and 9 minor (asserts in tests, unused imports and loop variables, `zip` without `strict`).

---

## 10. Baseline: commands executed and results

| # | Command (from repo root unless noted) | Result |
|---|---|---|
| 1 | `git status`, `git branch -a`, `git remote -v`, `git log` | Clean; 1 local commit (shallow) |
| 2 | `gh repo view …`, `gh api repos/a1creator284/jarvis-assistant` | Same repo; public; viewer has ADMIN |
| 3 | `git fetch --unshallow origin` | 18 commits available |
| 4 | Redacted regex secret scan over every blob in every commit (Python script) | 2 credential exposures (§8) |
| 5 | `python3 -m compileall -q -x '/\.git/' .` (same as CI) | **Pass** (exit 0) |
| 6 | Isolated venv in `/tmp` (outside the repo): `pip install pytest ruff pyflakes requests flask openai python-dotenv psutil numpy` | OK. **No** torch, opencv, mediapipe, pyaudio, porcupine, or sentence-transformers installed |
| 7 | `pytest -q -p no:cacheprovider news_test.py` and `pytest -q` (whole repo) | **3 passed, 0 failed** (only test file) |
| 8 | `pyflakes *.py` | 6 warnings (unused imports and `global`s) |
| 9 | `ruff check --select E,F,B,S --statistics .` | 81 findings (§9) |
| 10 | Real import of each module (no stubs) | `main`, `server`, `jarvis_logic` → `ModuleNotFoundError: wikipedia`; `knowledge` → `PyPDF2`; `vision` → `cv2`; `wakeword_listener` → `pvporcupine`; `client` → `RuntimeError` (no key). **Expected: no manifest, deps not installed** |
| 11 | `pip download mediapipe==1.1.0` and `==0.10.21`, then inspect the wheels | 1.1.0 has **no** `python/solutions/hands.py`; 0.10.21 has it |
| 12 | Routing harness (Appendix A) against `main.handle_command`, `jarvis_logic`, and the Flask test client | Findings #3, #4, #7, #8, #9 reproduced; GET `/` 200; `/status` 200; malformed JSON → 400 |
| 13 | `python server.py` with file-based stubs, from a temp cwd, for about 7 s; `curl /status` and `/ask "what time is it"` | Served on `0.0.0.0:5001`, "Debug mode: on", "Restarting with stat". **Reloader parent (4 threads) and child (5 threads) both imported `main`** (§0 #10). Responses correct |

**Environmental blockers:** no macOS (so no `say`, `osascript`, `open`, `screencapture`, TCC, AppleScript, or Accessibility), no audio or video devices, no display, and outbound network limited to GitHub and PyPI (so every third-party API call failed, which exercised the error-handling paths). Nothing in §5 marked ❓ should be considered working until it's checked on the owner's Mac.

**Reproducible errors (copy/paste):** see Appendix A. All side effects are intercepted, so the harness is safe to run on a Mac too.

---

## 11. Deployment and startup behaviour [V/I]

- Manual launch only. `run_jarvis.command` has a hardcoded absolute path from the author's machine. It checks `jarvis_env/` while `.gitignore` lists `.jarvis_env/`. It opens Chrome specifically and backgrounds the server without a PID file or log file.
- No LaunchAgent, no restart on crash, no log rotation, no health endpoint, no single-instance guard (running twice → port conflict or duplicate watchers).
- No packaging. TCC permissions attach to whatever terminal launched Python.

---

## 12. Target architecture

### 12.1 Design principles

1. **Capabilities, not commands.** The model chooses among typed, composable tools. There's no growing chain of string matches. The only deterministic intents are **safety intents** (stop, cancel, pause, kill switch), and those must work even if the LLM is down.
2. **Structured first, pixels last.** Order of preference: official API or CLI → AppleScript/JXA for scriptable apps → Accessibility tree → Playwright DOM/ARIA → screenshot + vision-model coordinates.
3. **Every action is observable and verifiable.** Tools return structured results and declare post-conditions. The runtime verifies before moving on.
4. **Policy is enforced outside the model.** The LLM proposes, the policy engine decides, and the user approves when required. Content from the web, files, email, or repos is **data, never instructions**.
5. **Local-first, provider-agnostic.** All state lives in local SQLite. Any model provider sits behind one interface, and a local model is a first-class option.
6. **One process, clean seams.** A single daemon (`ultrond`) at first, with module boundaries that allow splitting voice or browser workers into separate processes later.
7. **Small dependency surface.** This process can control the whole computer, so every dependency is attack surface. Each one needs a reason (§13).

### 12.2 Component diagram

```text
 ┌──────────────── Interfaces ────────────────┐
 │ HUD (web, 127.0.0.1 + token)  │ Voice I/O │ CLI │ (later) menu-bar app
 └───────────────┬───────────────┴─────┬─────┴─────┘
                 │ WebSocket events / REST          │ utterances, barge-in
                 ▼                                  ▼
 ┌─────────────────────────── API layer (FastAPI) ──────────────────────────┐
 │ auth (per-launch token, Origin check) · sessions · approvals · event bus  │
 └───────────────────────────────┬──────────────────────────────────────────┘
                                 ▼
 ┌────────────────────────── Agent Runtime ─────────────────────────────────┐
 │ Conversation Manager → Task Orchestrator (state machine, checkpoints)     │
 │   ├─ Planner (LLM → structured Plan; fast path for single-step requests)  │
 │   ├─ Executor (tool-call loop per step, budgets, cancellation)            │
 │   ├─ Verifier (tool post-conditions → state probes → LLM/vision judge)    │
 │   └─ Recovery (retry w/ backoff, re-plan, ask user, abort & report)       │
 └──────┬──────────────────┬────────────────────┬───────────────────┬────────┘
        ▼                  ▼                    ▼                   ▼
 ┌────────────┐   ┌─────────────────┐   ┌──────────────┐   ┌─────────────────┐
 │ Model      │   │ Policy Engine   │   │ Tool Registry│   │ Memory Service  │
 │ Providers  │   │ risk tiers,taint│◄──┤ typed tools, │   │ SQLite: convos, │
 │ (adapters) │   │ approvals,audit │   │ schemas, risk│   │ tasks, facts,   │
 └────────────┘   └─────────────────┘   └──────┬───────┘   │ FTS5+vector RAG │
                                               │           └─────────────────┘
      ┌──────────────┬──────────────┬──────────┼───────────┬──────────────┬─────────────┐
      ▼              ▼              ▼          ▼           ▼              ▼             ▼
 macOS control   Shell/Terminal  Filesystem  Browser    Web research   Code tools   Integrations
 (AX, Quartz,    (argv, PTY,     (roots,     (Playwright (fetch,       (repo, edit, (MCP client,
 AppleScript,    timeouts,       Trash,      persistent  search API,   test, git,   calendar,
 screen, OCR,    env scrub)      documents)  profile)    extraction)   ext. agents) mail, legacy
 clipboard)                                                                         JARVIS skills)
```

### 12.3 Core interfaces (contract sketch, Python typing)

These are illustrative signatures for the M1/M2 design review, **not files to create yet**.

```python
# --- core types -----------------------------------------------------------
class Message(BaseModel):            # provider-neutral
    role: Literal["system", "user", "assistant", "tool"]
    content: list[ContentPart]       # text | image | tool_call | tool_result
    provenance: Provenance           # user | model | tool:<name> | untrusted:<source>

class ToolCall(BaseModel):
    id: str; name: str; arguments: dict[str, Any]

class ToolResult(BaseModel):
    call_id: str
    ok: bool
    data: dict[str, Any] | None      # structured output (validated by output schema)
    observation: str                 # compact text for the model
    artifacts: list[ArtifactRef]     # screenshots, files, diffs
    untrusted: bool                  # true if data came from web/files/email/repos
    error: ToolError | None          # typed: NotPermitted | Timeout | NotFound | Precondition | ...

# --- model providers ------------------------------------------------------
class ModelProvider(Protocol):
    id: str
    capabilities: ModelCaps          # tools, vision, json_schema, streaming, context_window
    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...
    def stream(self, req: CompletionRequest) -> AsyncIterator[StreamEvent]: ...

# --- tools ----------------------------------------------------------------
class RiskTier(IntEnum): OBSERVE=0; ROUTINE=1; CONSEQUENTIAL=2; DESTRUCTIVE=3; FORBIDDEN=4

class ToolSpec(BaseModel):
    name: str                        # "fs.read", "app.open", "shell.run", "browser.click"
    description: str
    input_model: type[BaseModel]; output_model: type[BaseModel]
    base_risk: RiskTier
    side_effects: set[Literal["none","local_fs","process","network","ui","external_comm","system"]]
    permissions: set[MacPermission]  # Accessibility, ScreenRecording, Automation(app), Mic...
    idempotent: bool; timeout_s: float
    platforms: set[str] = {"darwin"}

class Tool(Protocol):
    spec: ToolSpec
    def assess(self, args: BaseModel, ctx: ExecContext) -> RiskAssessment: ...   # dynamic risk
    async def run(self, args: BaseModel, ctx: ExecContext) -> ToolResult: ...
    async def verify(self, args: BaseModel, result: ToolResult, ctx: ExecContext) -> Verification: ...

class ToolRegistry(Protocol):
    def register(self, tool: Tool) -> None: ...
    def list_for(self, ctx: ExecContext) -> list[ToolSpec]: ...       # filtered by platform/permission/profile
    async def invoke(self, call: ToolCall, ctx: ExecContext) -> ToolResult: ...  # validates → policy → run → verify → audit

# --- policy ---------------------------------------------------------------
class PolicyDecision(BaseModel):
    action: Literal["allow", "confirm", "authenticate", "deny"]
    tier: RiskTier; reasons: list[str]; summary_for_user: str

class PolicyEngine(Protocol):
    def decide(self, call: ToolCall, assessment: RiskAssessment, task: TaskContext) -> PolicyDecision: ...

class ApprovalBroker(Protocol):       # UI/voice channel only — never the model
    async def request(self, decision: PolicyDecision, task_id: str) -> ApprovalOutcome: ...

# --- runtime --------------------------------------------------------------
class TaskOrchestrator(Protocol):
    async def submit(self, objective: Objective, session: SessionId) -> TaskId: ...
    async def cancel(self, task_id: TaskId, reason: str) -> None: ...
    async def resume(self, task_id: TaskId) -> None: ...
    def events(self, task_id: TaskId) -> AsyncIterator[TaskEvent]: ...

# --- memory ---------------------------------------------------------------
class MemoryService(Protocol):
    async def append_message(self, session: SessionId, msg: Message) -> None: ...
    async def recall(self, query: str, k: int, scopes: set[str]) -> list[MemoryHit]: ...  # hybrid FTS5 + vector
    async def propose_fact(self, fact: FactCandidate) -> FactId: ...   # provenance + confidence; user-reviewable
    async def ingest(self, path: Path) -> IngestReport: ...

# --- voice ----------------------------------------------------------------
class WakeWordDetector(Protocol):  def process(self, pcm: bytes) -> bool: ...
class SpeechToText(Protocol):      async def transcribe(self, audio: AudioClip, hints: LangHints) -> Transcript: ...
class TextToSpeech(Protocol):      def speak(self, text_stream: AsyncIterator[str], voice: VoiceSel) -> PlaybackHandle: ...
class PlaybackHandle(Protocol):    def stop(self) -> None: ...; async def done(self) -> None: ...

# --- macOS platform layer (pyobjc wrappers; the only place that imports AppKit/Quartz) ----
class MacPlatform(Protocol):
    def permissions(self) -> dict[MacPermission, PermissionState]: ...
    def frontmost_app(self) -> AppInfo: ...
    def ax_tree(self, app: AppRef, depth: int) -> AXNode: ...
    def ax_perform(self, node: AXRef, action: str) -> None: ...
    def key(self, chord: str) -> None: ...; def type_text(self, s: str) -> None: ...
    def click(self, x: int, y: int, button: str = "left") -> None: ...
    def screenshot(self, region: Rect | None) -> ImageRef: ...; def ocr(self, img: ImageRef) -> list[TextBox]: ...
```

### 12.4 Execution loop

```text
Understand ─► (fast path?) ─yes─► single tool call ─► verify ─► report
    │ no
    ▼
  Plan (structured: steps[], each with goal, success_criteria, expected tools, risk estimate)
    ▼
  for step in plan:
     Select tools (registry filtered by context + permissions)
     ┌─► Model proposes ToolCall
     │   Policy.decide → allow | confirm (ApprovalBroker) | authenticate (Touch ID) | deny
     │   Execute (timeout, cancellation token, budget accounting)
     │   Observe (structured result + optional screenshot/AX snapshot; untrusted flag)
     │   Verify (tool post-condition → state probe → model/vision judge)
     │   ├─ ok        → next step
     │   ├─ retryable → retry with backoff (bounded)
     │   ├─ wrong     → re-plan remaining steps (bounded re-plans)
     │   └─ blocked   → ask user / abort with partial report
     └── (loop until step success or budget exhausted)
    ▼
  Report (what was done, evidence, what failed, what needs the user) → memory (task history)
```

**Task state machine** (persisted per transition in SQLite):
`created → planning → running ⇄ waiting_approval → (paused) → succeeded | failed | cancelled | interrupted`.
After a crash or restart, `running` tasks become `interrupted`. They are **never auto-resumed**. The user is offered "resume", which replays from the last checkpoint and skips non-idempotent steps already marked done.

**Budgets per task:** max steps, max wall time, max tokens/cost, max re-plans, max consecutive failures. All are configurable, and breaching any of them stops the task with a report.

---

## 13. Technology decisions

| Concern | Decision | Why (requirement-driven) | Alternatives considered | Revisit if |
|---|---|---|---|---|
| Language/runtime | **Python 3.12**, `pyproject.toml` + **`uv`** lockfile | The existing code and the best macOS bridges (pyobjc), Playwright, Whisper ports, and SQLite tooling are all Python. 3.12 fits current wheels (e.g., kokoro needs `<3.13`, pydantic-ai needs `≥3.11`). `uv` gives reproducible installs, which fixes finding #6 | Poetry (slower, no extra benefit); Swift (better native integration but rewrites everything) | Native UI needs grow (M11 may add a thin Swift shell) |
| API server | **FastAPI + uvicorn**, bound to `127.0.0.1` | Needs **WebSockets** for streaming tokens, agent events, approvals, and voice state, plus async concurrency with long tasks. Pydantic models are shared with the tool schemas | Keep Flask (no native async or WebSocket; extensions needed); Starlette only (FastAPI adds validation and docs for little cost) | — |
| Agent runtime | **Custom, thin runtime** (orchestrator + executor + verifier, roughly 1–2 kLOC) | The loop is simple but needs **tight control** of policy gates, cancellation from voice barge-in, approval pauses, and SQLite checkpoints. A framework would hide exactly the parts that matter for safety | **LangGraph** (good checkpointing and interrupts, but a heavy abstraction and dependency churn for one user); **Pydantic AI** (typed tools, provider-neutral; viable fallback for the provider layer) | Multi-agent graphs get complex (M9) → re-evaluate LangGraph for the orchestrator only |
| Model providers | Own `ModelProvider` protocol + adapters on **official SDKs**: OpenAI, Anthropic, Google Gemini, and **OpenAI-compatible** endpoints (covers Ollama, LM Studio, vLLM, OpenRouter, and others). A `FakeProvider` for tests | Provider independence without one giant dependency. Explicit capability flags (tools, vision, JSON schema). Easy to pin exactly what runs inside a process with full computer access | LiteLLM (broad coverage, but a large, fast-moving dependency surface; keep it as an optional adapter behind the same protocol) | Adapter upkeep becomes costly |
| Tool schemas | **Pydantic v2 models → JSON Schema** for each provider's tool format | One source of truth for validation, docs, UI forms, and tests | Hand-written JSON | — |
| External tool ecosystem | **MCP client** (Model Context Protocol) in M9 | Standard way to add integrations without bespoke plugins. MCP tools still pass through the same policy engine | Custom plugin API | — |
| macOS control | **pyobjc** (ApplicationServices/AX, Quartz CGEvent, AppKit NSWorkspace/NSPasteboard, Vision OCR, LocalAuthentication) + `osascript`/JXA for scriptable apps + the `shortcuts` CLI | Native, deterministic, inspectable (AX tree), and verifiable. No extra layers | pyautogui (wraps the same calls, less control, no AX); Hammerspoon (external app dependency) | — |
| Screen understanding | AX tree first → Apple Vision OCR (local) → screenshot to a vision-capable model as the last resort | Cheap and precise first, generic last | Pure screenshot agents (slow, costly, brittle) | — |
| Browser | **Playwright (Python)** with a dedicated persistent Chromium profile for the agent; optional CDP attach to the user's Chrome **only with opt-in** | Reliable DOM/ARIA observations, downloads, uploads, forms; isolates the agent from the user's main cookies by default | Selenium (weaker API); pure AppleScript Safari (limited) | — |
| Web research | `httpx` + a search API (owner chooses: Brave, Tavily, SearXNG self-hosted, or a provider's built-in search) + article extraction (e.g., `trafilatura`, to evaluate) | Replaces fragile HTML scraping; results tagged **untrusted** | DDG HTML scraping (current; fragile, may break ToS) | — |
| Shell | Own `shell.run` tool: argv form by default, PTY sessions for interactive tools, cwd/env control, **secret-scrubbed env**, timeouts, output caps, command risk classifier | Broad power with auditable, policy-gated execution | Blind `shell=True` passthrough (rejected) | Need stronger isolation → optional containers for untrusted code |
| Storage | **SQLite** (WAL) in `~/Library/Application Support/Ultron/ultron.db`; **FTS5** for keyword search; **`sqlite-vec`** for vectors (pre-1.0, so pin it and keep a NumPy brute-force fallback; personal-scale corpora are small) | One local file, transactional, zero services, easy backup | Postgres/pgvector, Chroma, LanceDB, Qdrant (extra services or deps without need at this scale) | Corpus > ~1M chunks or measured latency issues |
| Embeddings | Local **multilingual** model (candidates: `multilingual-e5-small`, `paraphrase-multilingual-MiniLM-L12-v2`), chosen by a small Hindi/Hinglish/English retrieval benchmark in M8; provider embeddings optional | The current English-only MiniLM fails the Hindi/Hinglish requirement | Cloud-only embeddings (privacy, cost) | — |
| Documents | `pypdf` (replaces PyPDF2), `python-docx`, `openpyxl`, Markdown/text; OCR via Apple Vision for scanned PDFs | Light, permissive licences | PyMuPDF (fast but **AGPL**); heavier converters (docling, markitdown) evaluated only if quality demands it | — |
| Wake word | `WakeWordDetector` interface; **owner decides** between Porcupine (accurate, low CPU, needs AccessKey and licence review, version-locked `.ppn`) and **openWakeWord** (Apache-2.0, offline, custom-trainable; last release 2024-02, a maintenance risk). Either way "ULTRON" needs a new keyword model | Keeps the engine swappable | Browser substring wake word (current; cloud, unreliable) | — |
| VAD / endpointing | **Silero VAD** | Small, accurate, offline | WebRTC VAD (less accurate) | — |
| STT | **Local Whisper** on Apple Silicon (`mlx-whisper` or `whisper.cpp`/Metal); cloud STT optional. **Hindi/Hinglish accuracy must be benchmarked** on the owner's voice (code-switching is the hard case) | Privacy, latency, offline | Google Web Speech (current, unofficial); `faster-whisper` (strong on CPU/CUDA, no Metal) | Benchmark shows a cloud STT is needed for Hinglish quality |
| TTS | `TextToSpeech` interface; **v1 = macOS `say` via `Popen`** (interruptible, offline; Hindi and Indian-English system voices exist; confirm with `say -v '?'`); neural upgrade candidates: Piper, Kokoro (local) or a cloud TTS | Interruptibility first, quality second | Blocking `say` (current) | — |
| Barge-in / echo | Phase 1: half-duplex + wake word / hotkey / "stop" during playback. Phase 2: **AVAudioEngine voice-processing I/O** (system AEC) via pyobjc | Real barge-in on laptop speakers needs echo cancellation | Headphones-only assumption | — |
| HUD | Port the existing HTML/CSS; **vanilla JS/TypeScript + WebSocket**, no SPA framework at first; assets bundled locally | The current HUD is vanilla and good-looking; the requirements are event display and approvals, not a complex app | React/Svelte (add later only if complexity demands it) | Approval/task UIs outgrow vanilla |
| Config & secrets | `pydantic-settings` (TOML file + env) for config; **macOS Keychain** (`keyring`) for secrets; `.env` only as a dev fallback | Removes plaintext keys; matches macOS norms | `.env` only (current) | — |
| Logging/observability | Stdlib `logging` with a JSON formatter + redaction filter; task/step/tool-call traces in SQLite, shown in the HUD | No extra dependencies; traces double as the audit log | structlog, OpenTelemetry (later if needed) | — |
| Quality gates | **ruff** (lint + format), **mypy** (strict on the new package), **pytest** (+ pytest-asyncio), **gitleaks** in pre-commit and CI | Catches the classes of issues found in this audit | — | — |
| Startup at login | **LaunchAgent** plist first (M10); signed `.app` + `SMAppService` later (M11) | Fastest path to reliable autostart; the `.app` gives a stable TCC identity | — | — |
| Vision (face/hand) | **Out of core scope.** Keep it as an optional legacy plugin, pinned, never used for authentication | Not required by the ULTRON vision; heavy, fragile dependency | — | Owner wants presence detection |

---

## 14. Proposed folder structure

The layout below is created **incrementally** in M1–M2, only when each piece gets real code (no placeholder modules now).

```text
jarvis-assistant/                 (repo name can stay; package is `ultron`)
├── pyproject.toml                # deps, extras: [voice] [browser] [rag] [legacy-vision] [dev]
├── uv.lock
├── src/ultron/
│   ├── app.py                    # composition root: build registry, providers, services; lifecycle
│   ├── config/                   # settings.py (pydantic-settings), secrets.py (Keychain), paths.py
│   ├── api/                      # FastAPI app, auth.py, routes/, ws.py (event stream), approvals.py
│   ├── core/
│   │   ├── types.py              # Message, ToolCall, ToolResult, TaskEvent, Provenance …
│   │   ├── events.py             # in-process async event bus
│   │   └── runtime/              # orchestrator.py, planner.py, executor.py, verifier.py, recovery.py, budgets.py
│   ├── providers/                # base.py, openai.py, anthropic.py, gemini.py, openai_compat.py, fake.py
│   ├── policy/                   # tiers.py, engine.py, classifiers/ (shell, paths, urls), taint.py, audit.py
│   ├── tools/
│   │   ├── registry.py, base.py
│   │   ├── system/               # apps, windows, input, clipboard, screen, notifications, power, audio
│   │   ├── shell/                # run, pty sessions
│   │   ├── fs/                   # read, write, search, move, trash
│   │   ├── documents/            # extract (pdf/docx/xlsx/md), summarize helpers
│   │   ├── browser/              # Playwright session, navigate, snapshot, click, fill, download, upload
│   │   ├── web/                  # search, fetch, extract
│   │   ├── code/                 # repo map, patch apply, test runner, git, external coding agents
│   │   ├── memory/               # remember, recall, reminders/schedules
│   │   ├── integrations/         # mcp_client.py, calendar, mail, messages … (opt-in)
│   │   └── legacy/               # ported JARVIS skills: weather, news, cricket, system_report, smart_home
│   ├── platform/macos/           # ax.py, quartz.py, appscript.py, screen.py, ocr.py, pasteboard.py,
│   │                             # permissions.py, localauth.py  (ONLY place importing pyobjc)
│   ├── voice/                    # audio_io.py, vad.py, wakeword/, stt/, tts/, dialog.py (turn-taking,
│   │                             # barge-in), language.py (hi/en/Hinglish detection & style)
│   ├── memory/                   # db.py, migrations/, store.py, retrieval.py (FTS5+vec, RRF),
│   │                             # embeddings.py, ingest.py, scheduler.py
│   └── observability/            # logging.py (JSON + redaction), tracing.py
├── ui/                           # HUD (ported from templates/ + static/), local fonts
├── tests/
│   ├── unit/  integration/  macos/ (marker: requires real Mac + permissions)
│   ├── evals/                    # scripted multi-step tasks + prompt-injection cases
│   └── fixtures/                 # fake web pages, fake repos, sample docs (hi/en/Hinglish)
├── scripts/                      # dev helpers, launchd plist generator, permission checker
├── docs/                         # this audit, ADRs (docs/adr/NNNN-*.md), runbooks
└── legacy/                       # (M1) current JARVIS files moved here unchanged until retired
```

### Legacy migration map

| Current | Destination | When |
|---|---|---|
| `main.py` skills (weather, news, cricket, system report, smart home, study plan) | `tools/legacy/*` as typed tools (parsing logic deleted; the model supplies the arguments) | M3 |
| `main.py` reminders | `memory/scheduler.py` (SQLite-backed, single-instance) + `tools/memory/reminders` | M3/M8 |
| `main.py` app launch / volume / brightness / screenshot / power | `tools/system/*` via `platform/macos` | M4 |
| `main.py` `clean_system` | Delete. Re-add only as a policy-gated, Trash-based tool if wanted | M1 |
| `knowledge.py` | `memory/ingest.py` + `memory/retrieval.py` | M8 |
| `vision.py` | `legacy/` optional extra; never used for auth | M1 (move) |
| `wakeword_listener.py`, browser STT, CLI `listen()` | Replaced by `voice/` | M7 |
| `server.py`, `templates/`, `static/` | `api/` + `ui/` | M1 |
| `news.py` + test | Merge into `tools/legacy/news.py`, keep the tests | M3 |
| `jarvis_logic.py`, `client.py`, `musicLibrary.py` | Delete | M1 |

---

## 15. Security and permission model

### 15.1 Threat model (single-user local agent)
- **Prompt injection** from web pages, emails, documents, repo files, issue text, or tool output trying to make the agent run commands, exfiltrate data, or message people.
- **Local network or browser attackers** reaching the control API (finding #3).
- **Model error**: a destructive action caused by misunderstanding.
- **Secret leakage** into logs, prompts, or subprocess environments.
- **Supply-chain risk** in dependencies of a process with full computer access.

### 15.2 Risk tiers and default decisions

| Tier | Examples | Default |
|---|---|---|
| **T0 Observe** | read screen/AX tree, list windows, read files in allowed roots, web search/fetch, `git status`, system info | **Allow**, logged |
| **T1 Routine, reversible, local** | open/focus apps, type or click in the **task's own** target, create/edit files inside workspace roots, read-only or allowlisted shell commands (`ls`, `rg`, `pytest`, `git diff`), browser navigation, set volume | **Allow**, logged, visible in the HUD timeline |
| **T2 Consequential** | send a message or email, submit a web form, upload a file, `git push` (non-force), install packages, run unclassified shell commands, modify files outside workspace roots, move to Trash, change system settings, start a long-running background process | **Confirm** (per action by default; "approve similar actions for this task" option) |
| **T3 Destructive / irreversible / high-impact** | permanent delete, `rm -rf`, `sudo`, disk utilities, `git push --force` / history rewrite, payments or purchases, deleting cloud data, changing security or privacy settings, sending to many recipients, shutdown/restart, credential access | **Confirm + authenticate** (Touch ID via LocalAuthentication) with a plain-language summary of exact effects; never "remember" approval |
| **T4 Forbidden** | disable SIP/Gatekeeper/firewall, read or dump Keychain secrets into the model context, exfiltrate secrets, modify ULTRON's own policy files at runtime | **Deny**, always |

**Dynamic risk.** `Tool.assess()` can raise (never lower) the tier based on the arguments: path classifier (workspace vs home vs system), shell classifier (pipes to `sh`, `curl | bash`, `chmod -R`, `launchctl`, `defaults write`, `kill`), recipient count, URL domain, and so on.

### 15.3 Untrusted content and taint
- Every `ToolResult` from web, files, email, repos, or OCR is marked `untrusted` and wrapped in delimiters in the prompt with an explicit "this is data" label.
- **Taint propagation.** If a proposed tool call's arguments are derived from untrusted content (e.g., a command string, URL, or recipient copied from a page), the policy engine **raises it one tier** and shows the provenance in the approval prompt.
- Approvals come **only** from the ApprovalBroker (a HUD click, a spoken confirmation tied to the specific pending action ID with a short-lived nonce, or Touch ID). A model message saying "the user approved" counts for nothing.
- The current stateless "confirm shutdown" phrase pattern is explicitly rejected.

### 15.4 Filesystem roots (configurable)
- **Workspace roots** (e.g., `~/Ultron/Workspace`, chosen project dirs): read/write at T1.
- **Home**: read at T0 (excluding `~/Library/Keychains`, browser profile stores, `~/.ssh`, and `.env`-style secrets, which are T4 for reading into context); writes at T2.
- **System paths**: write at T3, or denied.
- Delete = **move to Trash** (NSFileManager) at T2. Permanent delete is T3.

### 15.5 Secrets
- Stored in the macOS Keychain. Config refers to them by name.
- Never placed in prompts. Subprocess environments are built from an allowlist. Logs pass through a redaction filter (known secret values + patterns).
- CI and pre-commit run **gitleaks**, and the existing `.gitignore` is kept and extended (`*.db`, `ultron-data/`).

### 15.6 Control API
- Binds `127.0.0.1` only. A random per-launch token is required for REST and WebSocket (delivered to the HUD through the launcher URL fragment).
- Strict `Origin` check. JSON content-type required. No CORS. No debug mode.
- Remote/phone access is an **explicit opt-in** later (e.g., over Tailscale with auth), not the default.

### 15.7 Kill switch and budgets
- A global hotkey and the voice phrase "ULTRON stop" are handled **deterministically** and cancel all running tasks and TTS within 500 ms.
- Per-task budgets (§12.4). The HUD always shows which task is acting.

### 15.8 macOS permissions (TCC) plan
- A `ultron doctor` command / HUD page reports the state of: **Accessibility, Screen Recording, Microphone, Camera (optional), Automation (per target app), Full Disk Access (optional), Input Monitoring (only if a global key listener is used)**.
- Each tool declares the permissions it needs. Tools that are missing permissions are hidden from the model, with an explanation, rather than failing at runtime.

### 15.9 Audit log
- Append-only SQLite table: every tool call, its arguments (redacted), the policy decision, approver, result, and timestamps. Viewable and exportable from the HUD.

---

## 16. Testing, logging, task state and recovery strategy

| Layer | What | How |
|---|---|---|
| Unit | policy classifiers, tool argument validation, provider adapters (recorded fixtures), reminder parsing, retrieval ranking | pytest, no network, `FakeProvider` |
| Runtime | planner/executor/verifier loops, budgets, cancellation, crash-resume | Scripted `FakeProvider` emitting tool-call sequences against fake tools; assert state transitions in SQLite |
| Integration (Linux CI) | FastAPI auth/Origin/token, WebSocket events, Playwright against local fixture pages, shell/fs tools in temp dirs, memory/RAG with a small corpus | pytest + Playwright, GitHub Actions `ubuntu-latest` |
| macOS integration | AX tree reads, app open/focus, typing into TextEdit, screenshot+OCR, `say` interruption, permission doctor | `@pytest.mark.macos`, run **locally on the owner's Mac** (hosted macOS runners can't be granted Accessibility/Screen Recording reliably); an optional `macos-latest` CI job for non-TCC tests |
| Evals | 30→100 end-to-end objectives (files, browser forms, coding fixes, research summaries) + **prompt-injection suite** (malicious page/email/README instructing deletion or exfiltration) | Scored runs per provider; track success rate, steps, cost, and policy violations (must be 0) |
| Voice | WER on a recorded Hindi/English/Hinglish set from the owner; wake-word false accepts per hour; barge-in stop latency | Offline scripts + manual protocol |
| Logging | JSON logs with redaction under `~/Library/Logs/Ultron/`, rotated | stdlib `logging` |
| Recovery | Checkpoint per step; `interrupted` state after a crash; idempotency keys for non-idempotent tools | Covered by runtime tests |

---

## 17. Incremental roadmap

Sizes: S ≈ 1–3 days, M ≈ 1–2 weeks, L ≈ 2–4 weeks of focused work (rough, single developer with AI assistance).

### M0 — Audit (this document) ✅
Deliverable: `docs/ULTRON_ARCHITECTURE_AUDIT.md`. Acceptance: reviewed by the owner; §19 decisions answered.

### M1 — Repository cleanup, security hotfixes, and foundation (M) · depends on M0
**Implement**
- Remove the hardcoded Picovoice key from source (after the owner rotates it); load from env/Keychain. Add **gitleaks** pre-commit + CI.
- `pyproject.toml` + `uv.lock` with extras; pin legacy dependencies so the legacy app installs reproducibly (`mediapipe` pinned to a version with `mp.solutions`, or vision made optional and lazily imported).
- Move legacy files to `legacy/` **unchanged** except for minimal security fixes: bind `127.0.0.1`, `debug=False`, require JSON content-type and a token, remove `clean_system` and stateless power confirms. Delete `jarvis_logic.py`, `client.py`, `musicLibrary.py`.
- Create `src/ultron/` with: `config` (pydantic-settings, Keychain secrets, standard app paths), `observability/logging`, `api` skeleton (FastAPI, token + Origin auth, health endpoint, WebSocket echo of events), and `core/types.py`.
- CI: ruff, mypy (new package), pytest; keep the `compileall` job for legacy.
- ADRs for the key decisions in §13.

**Tests:** API auth (no token → 401; wrong Origin → 403; `text/plain` → 415), settings loading, secret redaction, legacy routing **characterization tests** (pinning current behaviour, including the known bugs, marked `xfail` with issue links).
**Acceptance:** see §18.

### M2 — Agent runtime and provider integration (M–L) · depends on M1
**Implement:** `ModelProvider` protocol + OpenAI, Anthropic, Gemini, and OpenAI-compatible adapters (streaming, tool calls, vision input) + `FakeProvider`. Conversation manager with SQLite-persisted history. A minimal tool registry with two T0 tools (`time.now`, `system.info`) to prove the loop. A system prompt with persona and **language policy** (reply in the user's language/register). Token and cost accounting.
**Tests:** adapter contract tests using recorded fixtures for each provider; tool-call round trip with `FakeProvider`; history persistence across restart; provider switching via config with no code changes.
**Acceptance:** the same scripted conversation ("what time is it, then summarise our chat") passes with ≥2 real providers and the fake one; a Hinglish prompt gets a Hinglish reply; no provider SDK is imported outside `providers/`.

### M3 — Tool registry, policy engine v1, and task execution loop (L) · depends on M2
**Implement:** full `ToolSpec`/`Tool`/`ToolRegistry`; policy engine (tiers, dynamic assessment, taint flag, audit log); ApprovalBroker over WebSocket + HUD approval card; orchestrator state machine with plan → execute → verify → recover, budgets, cancellation, and crash → `interrupted`. Port legacy skills (weather, news, cricket, system report, reminders with a fixed parser, study plan) as typed tools. The HUD is ported to the new API and shows a live task timeline.
**Tests:** state-machine transitions; budget exhaustion; cancellation mid-tool; approval allow/deny/timeout; T4 always denied; taint raising a tier; reminder-parser regression tests for every bug in §0.
**Acceptance:** a three-step objective with a T2 step pauses for approval, resumes, verifies, and reports; killing the process mid-task leaves it `interrupted` with a resumable checkpoint; legacy features reachable through natural language **without** any substring router.

### M4 — macOS computer control and terminal (L) · depends on M3
**Implement:** `platform/macos` (pyobjc): permission doctor, NSWorkspace apps, AX tree read/act, CGEvent keyboard/mouse, window list and focus, NSPasteboard clipboard, screenshots (`screencapture`/ScreenCaptureKit), Apple Vision OCR, LocalAuthentication. Tools: `app.*`, `window.*`, `input.*`, `clipboard.*`, `screen.*`, `system.volume/brightness/notify`. `shell.run` + PTY sessions with classifier, env scrubbing, timeouts, and output caps.
**Tests:** unit tests for classifiers and argument validation (Linux); `@macos` tests: open TextEdit → type → read back via AX → verify; screenshot + OCR finds known text; Touch ID prompt for a T3 dry run; shell classifier corpus (≥200 commands labelled by tier).
**Acceptance:** "Open TextEdit, write a two-line note, save it to the workspace as note.txt" succeeds end to end and the verifier confirms the file contents; missing Accessibility permission is reported clearly; `rm -rf ~/x` proposed by the model triggers a T3 auth prompt.

### M5 — Browser automation and filesystem/document tools (L) · depends on M3 (M4 is helpful but not required)
**Implement:** Playwright session manager (dedicated profile, downloads quarantine dir), navigate, ARIA/DOM snapshot, click, fill, select, upload, download, screenshot; web search provider + fetch/extract; filesystem tools with roots and Trash; document extraction (pdf/docx/xlsx/md/txt, OCR fallback).
**Tests:** Playwright against local fixture sites (login form, multi-page form, file upload/download, a **prompt-injection page**); fs root enforcement; extraction golden files including Hindi text.
**Acceptance:** "Find the X form on fixture site, fill it from this document, stop before submit" stops for T2 approval; the injection page cannot cause any tool call above T0 without approval; downloads land only in quarantine.

### M6 — Autonomous coding and verification (M–L) · depends on M4 (shell), M5 (fs)
**Implement:** repo map/search (ripgrep), patch-based edit tool with diff preview, test-runner tool (detects pytest/npm/etc.), git tools (branch/worktree per task, commit; push = T2; force = T3); optional **external coding-agent adapters** (e.g., Claude Code / Codex CLI / Gemini CLI / Aider, as the owner chooses) run as policy-gated subprocesses in a worktree.
**Tests:** fixture repos with failing tests → agent fixes them; verify the agent never commits to `main`; repo files containing injection text are treated as data.
**Acceptance:** on ≥5 fixture bugs, the agent produces a branch where the tests pass, with a summary and diff, and no T2+ action happens without approval.

### M7 — Voice: wake word, STT, TTS, Hinglish, interruption (L) · depends on M3 (runtime events); in parallel with M4–M6
**Implement:** audio I/O, Silero VAD endpointing, wake-word engine (owner choice) with an "ULTRON" model, local Whisper STT with language hints, `say`-based interruptible TTS with sentence-streaming from the LLM, language detection + voice selection (Devanagari → hi-IN voice; Latin Hinglish → en-IN voice), barge-in phase 1 (half-duplex + wake word / hotkey), deterministic "stop" intent; phase 2 AEC spike (AVAudioEngine voice processing).
**Tests:** recorded test set (owner's voice: 50 English, 50 Hindi, 50 Hinglish utterances) → WER per class; wake-word false accepts over 1 h of background audio; stop latency; reply-language match rate.
**Acceptance:** wake → command → spoken reply starts < 2.5 s on Apple Silicon (local STT, cloud LLM); "ULTRON stop" halts speech < 500 ms; WER targets agreed after the first benchmark; no audio leaves the machine unless a cloud STT is explicitly enabled.

### M8 — Memory, RAG, and task history (M) · depends on M2/M3
**Implement:** SQLite schema + migrations (sessions, messages, tasks, steps, tool_calls, facts with provenance/confidence, documents, chunks, schedules); hybrid retrieval (FTS5 BM25 + sqlite-vec, reciprocal-rank fusion); structure-aware chunking; incremental ingest with a file watcher on chosen folders; multilingual embedding chosen by benchmark; memory review UI (view/edit/delete facts); task-history search ("what did you do yesterday?").
**Tests:** retrieval benchmark (hi/en/Hinglish Q&A over fixture docs, recall@5); migration tests; facts from untrusted sources flagged and never auto-promoted; deleting a fact removes it from retrieval.
**Acceptance:** recall@5 ≥ agreed target; answers cite sources; the legacy `memory.json` is imported once with a report.

### M9 — Integrations and optional specialized agents (M, ongoing) · depends on M3, M5, M8
**Implement:** MCP client (tools pass through the policy engine); owner-selected integrations (e.g., Calendar/Reminders via EventKit or AppleScript, Mail, Messages, Spotify, Home via Shortcuts); optional specialist sub-agents (researcher, coder, desktop operator) as *configurations of the same runtime* with restricted tool sets, not separate frameworks.
**Tests:** per-integration contract tests with fakes; an MCP server fixture; sub-agent tool-scope enforcement.
**Acceptance:** adding an MCP server needs config only; every external send action is T2+.

### M10 — Security hardening, testing, and local deployment (M) · depends on M1–M8
**Implement:** threat-model review; full prompt-injection eval suite; fuzzing of the policy classifiers; dependency audit (`pip-audit`), pinned hashes; LaunchAgent install/uninstall script (`ultron service install`), single-instance lock, health checks, log rotation, backup/export of the data dir; `ultron doctor`.
**Tests:** eval suite with 0 policy violations; service survives a crash (launchd restart) and reboot; restore from backup.
**Acceptance:** ULTRON starts at login via LaunchAgent, the HUD is reachable only on localhost with the token, and all §15 controls are covered by tests.

### M11 — Optional macOS app packaging and startup at login (M) · depends on M10
**Implement:** signed and notarized `.app` (Briefcase or py2app, or a thin Swift launcher embedding the Python runtime) for a stable TCC identity; menu-bar status item; `SMAppService` login item; optional native webview for the HUD; auto-update strategy.
**Tests:** clean-machine install; permissions granted once and kept across updates; login-item toggle.
**Acceptance:** double-click install, one-time permission onboarding, and ULTRON is running after reboot.

### Dependency graph

```text
M0 → M1 → M2 → M3 ─┬─► M4 ─┬─► M6
                   ├─► M5 ─┘
                   ├─► M7 (parallel with M4–M6)
                   └─► M8 ─► M9
          M1…M8 ─────────► M10 ─► M11
```

---

## 18. Initial acceptance criteria (the next implementation phase: M1)

M1 is done when **all** of the following hold:

1. **No secrets in HEAD.** A `gitleaks` scan of the working tree passes in CI and pre-commit. The owner confirms the Picovoice and NewsAPI keys have been revoked.
2. **Reproducible install.** `uv sync` (or `pip install -e .[dev]`) on a clean Python 3.12 venv works, and the `legacy` HUD starts on macOS from the README instructions alone.
3. **Control API hardened.** The server binds `127.0.0.1`, runs without debug, and rejects missing or wrong tokens (401), foreign `Origin` (403), and non-JSON bodies (415). A test reproduces the §0 #3 cross-origin attack and shows it blocked.
4. **Dangerous legacy paths removed or gated.** `clean_system` is removed. Shutdown/restart need a stateful, expiring confirmation tied to a prior request (or are removed until M3).
5. **Dead code removed.** `jarvis_logic.py`, `client.py`, and `musicLibrary.py` are deleted, and no remaining import refers to them.
6. **Single watcher instance.** The reminder watcher runs exactly once per running app (no reloader duplication), and a single-instance lock prevents two daemons.
7. **Quality gates in CI.** ruff (lint + format check), mypy on `src/ultron`, and pytest all run on every PR. Characterization tests for legacy routing exist (known bugs `xfail`). `news_test.py` still passes.
8. **Skeleton runs.** `uv run ultron serve` starts FastAPI on localhost with `/health` and an authenticated WebSocket event stream. Settings load from a TOML file + Keychain.
9. **Docs.** README is updated with accurate setup and status, and ADRs record the §13 decisions the owner accepted.

---

## 19. Decisions and access needed from the owner

1. **Key rotation (urgent).** Revoke the Picovoice and NewsAPI keys. Should Git history be rewritten afterwards? (That means a force-push to `main`, which only the owner should authorise.)
2. **Repository identity.** The brief names `a1creator284/jarvis-assistant`, but the remote and authenticated account are `rajaryan1111/jarvis-assistant`. GitHub resolves the former to the latter. Confirm this is the intended repo and whether it should **stay public** while it holds personal-agent code.
3. **Target hardware.** Mac model (Apple Silicon or Intel), macOS version, RAM. This decides local STT/LLM feasibility (`mlx-whisper`, local models).
4. **LLM providers.** Which providers and models must be supported first (OpenAI, Anthropic, Google, local via Ollama/LM Studio, OpenRouter, other)? Monthly budget ceiling? Is offline-only operation required?
5. **Coding agents.** Should ULTRON delegate coding to external CLIs you already use (Claude Code, Codex CLI, Gemini CLI, Aider), or only use its own tools?
6. **Wake word engine.** Porcupine (needs AccessKey and licence review) or openWakeWord (open source)? Exact wake phrase ("Ultron", "Hey Ultron")?
7. **STT/TTS privacy stance.** Local-only, or cloud STT/TTS allowed for better Hindi/Hinglish quality? Preferred voice/persona? Should replies say "sir"?
8. **Web search provider.** Brave, Tavily, self-hosted SearXNG, provider-native search, or something else?
9. **Approval defaults.** Are the §15.2 tier defaults acceptable? Which actions do you want auto-approved inside a workspace (e.g., `pip install` in a project venv)?
10. **Workspace roots.** Which folders may ULTRON write to without asking?
11. **Legacy features.** Keep face/hand vision, cricket, virtual smart home, study planner? (Recommendation: keep cricket/weather/news/study as legacy tools; move vision to an optional extra.)
12. **Packaging.** Do you have (or plan) an Apple Developer ID for signing and notarizing the M11 `.app`?

---

## Appendix A — Safe reproduction harness (side effects intercepted)

Run from **any temp directory** with Flask, requests, psutil, openai, and python-dotenv installed. It stubs unavailable hardware/ML modules and **replaces `subprocess.run`, `subprocess.check_output`, and `webbrowser.open` with recorders**, so nothing executes.

```python
import os, subprocess, sys, types, webbrowser
sys.path.insert(0, "/path/to/jarvis-assistant")
for k in ("OPENAI_API_KEY", "WEATHER_API_KEY", "NEWS_API_KEY", "CRICKET_API_KEY"):
    os.environ.pop(k, None)

class _Any:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return _Any()
    def __call__(self, *a, **k): return _Any()

def stub(name, **attrs):
    m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m

stub("speech_recognition", Recognizer=_Any, Microphone=_Any)
stub("wikipedia", summary=lambda t, sentences=2: f"[wiki:{t}]")
stub("cv2", VideoCapture=_Any, data=types.SimpleNamespace(haarcascades="/nonexistent/"))
stub("mediapipe", solutions=types.SimpleNamespace(hands=_Any(), drawing_utils=_Any()))
stub("PyPDF2", PdfReader=_Any); stub("sentence_transformers", SentenceTransformer=_Any)

CALLS = []
subprocess.run = lambda *a, **k: CALLS.append((a[0], k.get("shell", False))) or subprocess.CompletedProcess(a[0], 1)
subprocess.check_output = lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
webbrowser.open = lambda url, *a, **k: CALLS.append(("browser", url)) or True

import main; main.speak = lambda t: None
for c in ["set a timer for 5 minutes", "tell me about the latest battery technology",
          "remind me in 10 minutes to drink water", "remind me tomorrow at 9 am to go college",
          "clear cache", "confirm shutdown", "open the pod bay doors"]:
    CALLS.clear(); print(repr(c), "->", main.handle_command(c), CALLS)

import jarvis_logic; print(jarvis_logic.handle_command("hello"))   # -> "Sorry, something went wrong…"

import server
r = server.app.test_client().post("/ask", data='{"message":"confirm restart"}',
        content_type="text/plain", headers={"Origin": "https://evil.example"})
print(r.status_code, r.get_json(), CALLS)   # 200, "Restarting now, sir.", osascript restart recorded
```

Observed output (abridged) in the audit sandbox:

```text
'set a timer for 5 minutes' -> It is 06:57 PM on Monday, 12 October 2026.
'tell me about the latest battery technology' -> I cannot read the battery status.
'remind me in 10 minutes to drink water' -> … I will remind you to s to drink water.
'remind me tomorrow at 9 am to go college' -> … I will remind you to 9 am to go college.
'clear cache' -> Cleanup complete … [('rm -rf "/home/user/Library/Caches"/*', True)]
'confirm shutdown' -> Shutting down now, sir. [(['osascript','-e','tell application "System Events" to shut down'], False)]
'open the pod bay doors' -> Opening thepodbaydoors.com in your browser, sir.
Sorry, something went wrong in my brain.
200 {'reply': 'Restarting now, sir.'} [(['osascript','-e','tell application "System Events" to restart'], False)]
```

## Appendix B — Dependency facts used (PyPI, queried 2026-10-12)

`mediapipe 1.1.0` (2026-10-06; no `mp.solutions` in the wheel) · `PyPDF2 3.0.1` (2022-12-31) → `pypdf 6.20.1` · `wikipedia 1.4.0` (2014-11-15) · `pvporcupine 4.0.3` · `SpeechRecognition 3.17.0` · `sentence-transformers 6.1.0` · `openai 3.29.0` · `Flask 3.1.3` · `fastapi 0.143.0` · `playwright 1.63.0` · `pyobjc-framework-ApplicationServices 12.2.2` · `sqlite-vec 0.1.9` (pre-1.0) · `openwakeword 0.6.0` (2024-02-11) · `faster-whisper 1.2.1` · `mlx-whisper 0.4.3` · `silero-vad 6.2.3` · `piper-tts 1.8.0` · `kokoro 0.9.4` (Python <3.13) · `langgraph 1.2.14` · `pydantic-ai 2.55.0` · `litellm 1.105.0` · `pymupdf 1.28.2` (AGPL).
