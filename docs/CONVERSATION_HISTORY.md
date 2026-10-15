# Conversation history: local transcript, export and import

## What this is

The single-shot chat path (`main.handle_command` → `ask_gpt`, used when the keyword router does
not match and the agent loop is unavailable or off) previously started every question with an
empty history — the code itself said `# simple history for now`. It now keeps a **local,
bounded, redacted transcript** in the data directory and replays the last few turns as chat
context, so follow-up questions work.

Owner controls (`ultron history`):

```
ultron history                 # path, record count, last 5 records
ultron history --show 20       # last 20 records
ultron history --export out.jsonl
ultron history --import out.jsonl   # validated append; invalid lines are skipped and counted
ultron history --clear
```

Environment: `ULTRON_HISTORY=0` disables recording and replay entirely (default: on).
File: `<data dir>/chat_history.jsonl` — the same data directory as the API token and HUD
session state (`ULTRON_DATA_DIR`, platform defaults in `ultron.config.default_data_dir`).

## Guarantees (each one regression-tested in `tests/test_history.py`)

* **Local-only.** `ultron/history.py` contains no network code. The transcript never leaves
  the machine except through an explicit `--export` to a path the owner chooses.
* **Bounded.** At most 400 records (oldest dropped), 4000 characters per record, imports
  capped at 1000 records and 8 MB per file. The file cannot grow without limit.
* **Redacted on write and on import.** Every text passes `ultron.security.redact`, so
  credential-shaped strings (OpenAI/Groq/Picovoice keys and friends) do not accumulate.
* **Import treats the file as data, never code.** Strict JSONL; only `user`/`assistant`
  roles; non-empty string text; numeric timestamps; anything else is skipped and counted.
  Nothing in an imported file is executed, rendered as HTML, or granted any authority.
* **Fail-soft.** An unreadable, corrupt or unwritable transcript costs at most the lost
  record — never a crash and never a broken chat reply. Corrupt lines are skipped
  individually.
* **Chat context is capped.** At most 6 messages (hard cap 50 via `recent(limit=…)`) are
  handed to the model call; the transcript is context, not an unbounded prompt dump.

## What this is *not*

* **Not an audit log.** Agent-loop runs have their own append-only audit ledger
  (`docs/M2_0_AGENT_LOOP.md`); this transcript is a chat convenience and is not fed into the
  agent loop, whose first-turn-only tool contract stays exactly as designed.
* **Not a database, not synced, not multi-writer-safe beyond last-writer-wins.** Appends use
  an atomic temp-file replace; concurrent CLI and server writers can lose a record, which is
  acceptable for a convenience transcript and documented rather than papered over.
* **Not recording everything.** Only the single-shot chat fallback (question + reply) is
  recorded. Keyword-routed commands, agent runs, voice wake events and HUD interactions are
  not written to this file.

## Provenance

Local conversation history with export/import mirrors publicly documented behaviour of the
G0DM0D3 project (AGPL-3.0, inspected read-only; its history lives in browser storage). As
with M2.3, the implementation is original — no source code or content copied — and the
licensing question for any direct reuse remains an owner decision.
