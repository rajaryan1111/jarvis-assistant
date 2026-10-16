# Release checklist — JARVIS / Ultron

Prioritized. **Nothing below P0 is verified on real hardware or a live provider**, so the
product is **not** marked production-ready. Statuses are exactly what has been executed, not
what is intended.

Legend: ✅ done & verified · 🟡 done, unverified on target · ⬜ not started

## P0 — blockers to a beta release

| # | Item | Status | Evidence / what is missing |
|---|------|--------|---------------------------|
| P0.1 | Merge the integration candidate (PR #8 + PR #9 + completion sprint) | ✅ | **Merged via PR #10** into `main` at `c481fdad691afadf6d7d44340ecc6813eb2227e6`. Post-merge CI run [`38952959402`](https://github.com/rajaryan1111/jarvis-assistant/actions/runs/38952959402) passed **4/4** on Python 3.10–3.13, including `uv sync --locked --extra dev`. PR #9 was closed as superseded by PR #10. |
| P0.2 | Real-microphone speech recognition | 🟡 | Mock-tested only (38 tests). Needs §1 of `docs/MAC_SMOKE_TEST.md`. |
| P0.3 | Wake-word detection on the target Mac | 🟡 | Code path audited and mock-tested; `jarvis.ppn` bundled but never exercised with a real `PICOVOICE_ACCESS_KEY`. |
| P0.4 | Live provider behaviour (OpenAI / Groq / Ollama) | 🟡 | Typed failure kinds are tested with fakes; real timeouts, rate limits and `bad_response` bodies are not. Opt-in suite exists (`ULTRON_LIVE_*`). |
| P0.5 | TTS on the target Mac | 🟡 | `audio.speak` validated (option-injection guard, 4000-char cap) with fake runners; real `say` voices never exercised. |
| P0.6 | Task/memory recovery after a real restart and a real crash | 🟡 | Every branch is tested with injected failures; a genuine kill -9 mid-write has not been performed on macOS. |

## P1 — should be done before beta

| # | Item | Status | Evidence |
|---|------|--------|----------|
| P1.1 | Camera / vision prototype on the target Mac | 🟡 | Optional extra; face check is explicitly **not** authentication. |
| P1.2 | macOS system controls (volume, brightness, screenshot, open app/URL) | 🟡 | Argument validation fully tested; real `osascript` calls not exercised. |
| P1.3 | Permission-denied paths on macOS (Microphone, Camera, Accessibility) | 🟡 | Failure taxonomy tested; the real permission dialogs are not. |
| P1.4 | Hindi / Hinglish recognition accuracy with real speech | 🟡 | Detection and switching tested with fixtures; there is no Hinglish locale, so romanised Hindi rides on `en-IN` and needs real-speech validation. |
| P1.5 | `uv sync --locked` on the target platform | 🟡 | Cannot run in the sandbox (no `uv` binary). CI's `Install core + dev dependencies (from uv.lock)` step passed on all four Python jobs for the candidate, and neither `pyproject.toml` nor `uv.lock` is modified by this branch. |
| P1.6 | Multi-Python matrix on the final integrated `main` commit | ✅ | Post-merge CI run [`38952959402`](https://github.com/rajaryan1111/jarvis-assistant/actions/runs/38952959402) on `c481fdad691afadf6d7d44340ecc6813eb2227e6`: Python 3.10, 3.11, 3.12, 3.13 all `success`, every step green. |

## P2 — hardening and polish

| # | Item | Status | Evidence |
|---|------|--------|----------|
| P2.1 | Coverage of security-critical rejection branches | ✅ | `redact.py` 75%→98%, `tools/paths.py` 81%→90%; 24 new tests this sprint. |
| P2.1b | Release-readiness audit defects | ✅ | Fixed test-first: `vision.py` leaked the webcam handle when `cap.read()` raised (`b7f70a5`); `main.py` echoed raw exception text on 8 legacy error paths (`b7f70a5`); the date router had no `date` branch so "what's the date" fell through to Wikipedia; greetings needed an API key so a fresh install could not say hello; `VoicePipeline.run()` could raise and kill the voice loop; and the loop retried a missing microphone ~39,000 times a second (~875,000 log lines in 25 s), now backed off to ~1 per 5 s. Every one has a regression test that failed pre-fix. |
| P2.2 | Configuration surface documented | ✅ | `.env.example` went from 8 to ~75 variables with verified real defaults. |
| P2.3 | Real-device checklist | ✅ | `docs/MAC_SMOKE_TEST.md`, every claim checked against the code. |
| P2.4 | One-command verification | ✅ | `scripts/verify.py` (5 checks, non-zero exit on failure). |
| P2.5 | Index locking / concurrent-rebuild races | ⬜ | Documented limitation: writes are atomic, last writer wins. Same convention as the task store. |
| P2.6 | `_load_index()` whole-file materialization | ⬜ | Documented limitation, bounded by the 64 MiB cap. |
| P2.7 | Basename-only source labels | ⬜ | Documented limitation: same-named files in different directories can collide. |
| P2.8 | Repository license decision | ⬜ | Blocked on an owner decision (the reference project is AGPL-3.0); no license is assumed. |

## How to re-verify everything that *is* verified

```bash
python scripts/verify.py          # pytest + ruff + compileall + check-secrets + diff --check
```

Expected: `1313 passed, 21 skipped` (the 21 are the opt-in live-provider tests) and five
PASS lines. CI runs the same set plus `uv sync --locked` across Python 3.10–3.13.

The deterministic battery for the candidate tree (code commit `b7f70a5`), exactly as executed:

```bash
ULTRON_SKIP_DOTENV=1 python -B -m pytest -p no:cacheprovider -q   # 1313 passed, 21 skipped
ruff check --no-cache .                                            # clean
python -m compileall -q -x '/\.venv/' .                            # clean
python -B -m ultron check-secrets                                   # no hardcoded credentials
python scripts/verify.py                                            # 5/5 PASS
git diff --check; git status                                        # clean
```

## Explicit non-claims

- No live provider, no macOS hardware, no microphone, no camera and no wake word has been
  exercised by this work. Voice results are **mock-tested only**.
- Integration PR #10 was merged into `main` at `c481fdad691afadf6d7d44340ecc6813eb2227e6`; post-merge CI run `38952959402` passed 4/4. No deployment or force-push was performed.
- "1313 passed" describes the deterministic suite on the candidate tree, not device
  behaviour.
