# MacBook smoke test — JARVIS / Ultron

Reproducible checklist for the **real device**. Everything here needs hardware, macOS
permissions, or live provider credentials that a CI runner does not have, so none of it
is claimed as verified by the test suite.

Run it top to bottom on a clean install. Record the result of every row; a row that
cannot run is `N/A` with the reason, never a silent pass.

## 0. Before you touch the microphone

| # | Command | Expect |
|---|---------|--------|
| 0.1 | `uv sync` (or `uv sync --extra voice,wakeword,vision,rag`) | resolves from the committed `uv.lock`; no lock change |
| 0.2 | `python -m ultron doctor` | `[ok]` for server bind, API token, session state; credentials listed as configured/not set |
| 0.3 | `python -m ultron token init` then `python -m ultron token init --client` | token files written `0600` under the data directory |
| 0.4 | `python -m ultron check-secrets` | `No hardcoded credentials found...` |
| 0.5 | `pytest -q` | `1298 passed, 21 skipped` (the 21 are opt-in live-provider tests) |
| 0.6 | `python -m ultron agent "what is two plus two"` | a bounded agent turn completes; no tool runs |
| 0.7 | `./run_jarvis.command` | server binds `127.0.0.1:5001`, `/healthz` answers, HUD opens with a one-time login link |

`doctor` is the fastest signal: if a credential or the session state is broken, nothing
downstream is worth testing.

## 1. Microphone and speech recognition

Requires the `voice` extra and macOS **Microphone** permission for the terminal.

| # | Step | Expect |
|---|------|--------|
| 1.1 | `ULTRON_SKIP_DOTENV=1 python main.py`, wait for the boot line | `JARVIS system initializing...` is spoken; then `Listening...` |
| 1.2 | Say "what is the weather today" | `You: what is the weather today` then a spoken answer |
| 1.3 | Stay silent for ~10 s | a spoken `I did not hear anything, sir.` (the timeout path; a failed turn is audible, not silent) |
| 1.3b | Speak gibberish / very quietly | a spoken `I could not make that out, sir.` (the no-match path) |
| 1.4 | Deny Microphone permission in System Settings -> Privacy & Security, then rerun | a typed failure message (`unavailable`/`error`), **no traceback and no raw recogniser text**, and the loop keeps running |
| 1.5 | Uninstall/rename `speech_recognition` and rerun | the install hint prints and the process does not crash |

The recogniser needs outbound network access (Google's endpoint). On a network with no
route out, expect the `unavailable`/`timeout` message, not a hang: `listen()` bounds every
call with a 6 s timeout and an 8 s phrase limit (there are no CLI flags for these; they
are the function defaults).

## 2. Wake word

Requires the `wakeword` extra, `PICOVOICE_ACCESS_KEY`, and the bundled `jarvis.ppn`.

| # | Step | Expect |
|---|------|--------|
| 2.1 | `python -m ultron doctor` | client token line `[ok]` |
| 2.2 | Start the server, then `python wakeword_listener.py` | `Listening for wake word 'jarvis'...`; no crash on missing mic |
| 2.3 | Say "jarvis" | `Wake word detected`, then `Listening for command...` |
| 2.4 | Say a command, e.g. "open Safari" | `Heard command: open safari`, routed, and the confirmation policy applies |
| 2.5 | Say "jarvis" then nothing | times out with a typed message and returns to wake-word listening |
| 2.6 | Run the listener with the server stopped | `Error talking to server:` - a redacted reason, no token, no traceback |

## 3. English / Hindi / Hinglish

Locale comes from `ULTRON_VOICE_LANGUAGE` (`en` default, `hi`, `hinglish`, `auto`).

| # | Step | Expect |
|---|------|--------|
| 3.1 | Default (unset) | English, locale `en-IN` - identical to the historical behaviour |
| 3.2 | `ULTRON_VOICE_LANGUAGE=hi` + Hindi speech | recognised with `hi-IN` |
| 3.3 | `ULTRON_VOICE_LANGUAGE=auto` + Hindi speech | detected as Hindi from the next turn (a deliberate one-turn locale lag) |
| 3.4 | `auto` + romanised Hindi ("kya haal hai bhai") | classified `hinglish`, still recognised with `en-IN` (there is no Hinglish locale) |
| 3.5 | Say "switch to hindi" | the switch is confirmed and the command router is **not** invoked |
| 3.6 | Ask "how do I say water in hindi" | reaches the normal command handler (strict switch parsing, not a language change) |
| 3.7 | `ULTRON_VOICE_LANGUAGE=klingon` | falls back to English with a warning, no crash |

## 4. Spoken response / TTS

| # | Step | Expect |
|---|------|--------|
| 4.1 | Any spoken answer | audible via `say`; the voice is the owner's configured voice (default `Daniel`), **not** auto-selected per language |
| 4.2 | Very long answer | capped at `max_chars` (4000) and still spoken, never truncated mid-word |
| 4.3 | `audio.speak` with a voice name starting with `-` | rejected (option-injection guard) |
| 4.4 | TTS unavailable (e.g. `say` removed from PATH) | a typed failure, the CLI loop continues |

## 5. Provider errors and network timeouts

Do these with a real key present, then simulate failure:

| # | Step | Expect |
|---|------|--------|
| 5.1 | Valid `OPENAI_API_KEY`, ask a question | a real answer, token usage logged |
| 5.2 | Point `ULTRON_OLLAMA_BASE_URL` at a dead port | `unavailable` with a plain-language reason; no traceback, no key |
| 5.3 | Block outbound traffic (or set a tiny `ULTRON_AGENT_MODEL_TIMEOUT`) | `timeout`; the run ends within the configured bound |
| 5.4 | Invalid key | `rejected`, and the message never contains the key |
| 5.5 | Garbage/empty model reply | `bad_response`, no crash, no fabricated answer |
| 5.6 | `python -m ultron race "question"` with two models | bounded by `ULTRON_RACE_DEADLINE`; slow racers are reported, not awaited forever |
| 5.7 | `python -m ultron autotune --models ...` | respects `ULTRON_AUTOTUNE_BUDGET`; remaining tasks marked `budget_skipped` |

`max_retries=0` everywhere: a failed provider call is **never** retried automatically, so
a consequential action cannot be repeated by a retry.

## 6. Memory, history and task recovery after restart

| # | Step | Expect |
|---|------|--------|
| 6.1 | "remember that my wifi password hint is the blue mug" | stored; `memory.json` is `0600` and the note is utf-8 clean |
| 6.2 | Restart, ask "what did I ask you to remember" | the note is recalled |
| 6.3 | Corrupt `memory.json` (write `{ not json`), restart | it is moved to `memory.json.unreadable-<timestamp>` with a printed notice; notes are **not** silently wiped |
| 6.4 | Same for `tasks.json` | moved aside; startup prints `Saved task state was unusable...`; JARVIS still starts |
| 6.5 | Start a task, kill the process mid-task, restart | `Interrupted task: <title> (nothing was re-run...)`; the task is `interrupted` |
| 6.6 | Try to resume a **consequential** interrupted task | blocked until confirmed; confirming records `confirmed_at`; a fresh interruption clears it |
| 6.7 | `python -m ultron history --show 10` after several turns | transcript is `0600`; `--export FILE` / `--import FILE` round-trip |
| 6.8 | Check file modes: `ls -l` on `memory.json`, `tasks.json`, the transcript, the knowledge index | all `0600`; the data directory `0700` |

## 7. Camera / vision and system controls

Vision is an optional extra and a prototype face check, **not** authentication.

| # | Step | Expect |
|---|------|--------|
| 7.1 | `python -m ultron security` | shows the current face-check mode |
| 7.2 | Trigger a face check with the camera covered | denied/unknown; it does not silently pass |
| 7.3 | Deny Camera permission, retry | a typed failure, no crash |
| 7.4 | `python -m ultron security disable --yes` (owner recovery) | mode disabled, recorded |
| 7.5 | "set volume to 30" / "set brightness to 50" | applied; a voice channel asks for spoken confirmation per policy |
| 7.6 | "take a screenshot" | saved under `ULTRON_SCREENSHOT_DIR`; path is confined |
| 7.7 | "open Safari" / "open https://example.com" | app opens / default browser opens https only |
| 7.8 | "clear user caches" | requires confirmation on a signed-in HUD session; voice can never approve |

## 8. Permission-denied and unavailable-device behaviour

| # | Condition | Expect |
|---|------|--------|
| 8.1 | No microphone | typed `unavailable`; the CLI loop keeps running, and backs off ~5 s between attempts so it does not spin the CPU or flood the terminal |
| 8.2 | No camera | typed failure; no crash, no bypass |
| 8.3 | No Porcupine access key | the wake-word listener exits with a setup hint |
| 8.4 | Server bound off-loopback without `ULTRON_ALLOW_NON_LOOPBACK` | refused at startup |
| 8.5 | `ULTRON_DEBUG=1` off loopback | refused |
| 8.6 | Read-only data directory | a typed failure; the previous state is preserved, no partial file |
| 8.7 | Full disk during a task write | the old `tasks.json` is intact and no `*.tmp` remains |

## 9. What CI already proves (do not re-test by hand)

These are verified on every push/PR across Python 3.10-3.13, with mocked providers,
fake runners and no hardware:

- policy tiers, confirmation single-use/expiry/principal binding, login throttle, CSRF
- argv-only subprocess use, timeouts, real child-kill on overrun
- path confinement: traversal, absolute/`~`/backslash/drive-letter/UNC, control
  characters, over-length, mid-path symlinks, out-of-root reads, sensitive names,
  oversized and unreadable files
- redaction of query keys, headers, URL userinfo, token shapes, logged tracebacks, and
  the install step itself
- atomic `0600` writes with failure injection (old file preserved, no temp leftovers)
- knowledge confinement (both layers), bounded indexing/retrieval, corrupt-index handling
- memory/history utf-8 and quarantine behaviour
- the voice pipeline: detection, switch parsing, failure taxonomy, empty/malformed
  replies, auto-mode lag
- task store: validation, quarantine, interrupted writes, duplicate resumes, bounds,
  redaction, permissions, confirmation rules

Only the rows above are unverified without a Mac.
