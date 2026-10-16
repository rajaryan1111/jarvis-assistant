"""Bounded, redacted, local-only conversation transcript for the single-shot chat path.

The legacy chat fallback (``main.handle_command`` → ``ask_gpt``) used to start every question
with an empty history. This module gives it the last few turns as context and keeps a local
transcript the owner can inspect, export and import:

* **local-only** - one JSONL file under the data directory (``ULTRON_DATA_DIR``); nothing is
  uploaded, synced or sent anywhere, and there is no network code in this module;
* **bounded** - at most ``MAX_RECORDS`` records (oldest dropped), ``MAX_TEXT_CHARS`` per
  record, imports capped per record and per file;
* **redacted** - every text passes :func:`ultron.security.redact` on the way in *and* on
  import, so credential-shaped strings do not accumulate in the file;
* **owner-only** - the file is written 0600 through the shared atomic writer (unique
  temp file, ``fsync``, ``os.replace``, temp cleanup on failure), so the transcript is
  never world-readable and a failed write can never truncate it;
* **opt-out** - ``ULTRON_HISTORY=0`` disables recording and replay completely;
* **fail-soft** - a broken or unwritable transcript never breaks a chat reply.

This is deliberately **not** an audit log: the agent loop has its own append-only ledger, and
this transcript is a convenience for chat context and owner review. Concurrent writers are
last-writer-wins (atomic replace); a lost record is acceptable, a crash is not.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping
from pathlib import Path

from ultron.config import default_data_dir
from ultron.security.paths import atomic_write_text
from ultron.security.redact import redact

log = logging.getLogger("ultron.history")

HISTORY_FILENAME = "chat_history.jsonl"
MAX_RECORDS = 400  # whole transcript cap; oldest records are dropped on append
MAX_TEXT_CHARS = 4000  # per record, applied after redaction
MAX_CONTEXT_MESSAGES = 6  # messages handed to the single-shot chat call
MAX_CONTEXT_LIMIT = 50  # hard cap for recent(limit=...)
MAX_IMPORT_RECORDS = 1000
MAX_IMPORT_BYTES = 8 * 1024 * 1024
_ROLES = ("user", "assistant")
_DISABLED = ("0", "false", "no", "off")


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """``ULTRON_HISTORY`` - on by default; any of 0/false/no/off disables recording."""
    env = os.environ if env is None else env
    return (env.get("ULTRON_HISTORY") or "").strip().lower() not in _DISABLED


def history_path(env: Mapping[str, str] | None = None) -> Path:
    return default_data_dir(env) / HISTORY_FILENAME


def _record(role: str, text: str, ts: float | None = None) -> dict[str, object]:
    return {"ts": round(time.time(), 3) if ts is None else ts,
            "role": role,
            "text": redact(str(text))[:MAX_TEXT_CHARS]}


def _valid(item: object) -> bool:
    return (isinstance(item, dict) and item.get("role") in _ROLES
            and isinstance(item.get("text"), str) and bool(item["text"])
            and isinstance(item.get("ts"), (int, float)) and not isinstance(item.get("ts"), bool))


def _read(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue  # a corrupt line costs that line, never the transcript
            if _valid(item):
                records.append(item)
    return records


def _write(path: Path, records: list[dict]) -> None:
    """Replace ``path`` with ``records`` as JSONL, owner-only, atomically.

    Goes through the shared atomic writer: a uniquely named temp file created with
    ``O_EXCL`` in the same directory (a planted link is never followed), mode 0600,
    ``fsync`` and ``os.replace``. A failed write removes the temp file and leaves the
    previous transcript intact, so a crash or a full disk can never truncate history.
    """
    payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records)
    atomic_write_text(path, payload)


def records(env: Mapping[str, str] | None = None) -> list[dict]:
    """All valid stored records, oldest first ([] when disabled or unreadable)."""
    if not enabled(env):
        return []
    try:
        return _read(history_path(env))
    except OSError as exc:
        log.warning("chat history unreadable (%s); continuing without it", type(exc).__name__)
        return []


def recent(limit: int = MAX_CONTEXT_MESSAGES, env: Mapping[str, str] | None = None) -> list[dict]:
    """The last ``limit`` messages as ``{"role", "content"}`` dicts for the chat call."""
    limit = max(0, min(int(limit), MAX_CONTEXT_LIMIT))
    if limit == 0:
        return []  # [-0:] would slice everything; zero means zero
    return [{"role": r["role"], "content": r["text"]} for r in records(env)[-limit:]]


def record_exchange(user_text: str, assistant_text: str, env: Mapping[str, str] | None = None) -> None:
    """Append one question/answer pair. Never raises: history must not break a chat reply."""
    if not enabled(env):
        return
    try:
        path = history_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        stored = _read(path)
        stored.append(_record("user", user_text))
        stored.append(_record("assistant", assistant_text))
        _write(path, stored[-MAX_RECORDS:])
    except OSError as exc:
        log.warning("chat history could not be written (%s); reply unaffected", type(exc).__name__)


def clear(env: Mapping[str, str] | None = None) -> bool:
    """Delete the transcript file. True when a file was removed."""
    try:
        history_path(env).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("chat history could not be cleared (%s)", type(exc).__name__)
        return False


def export_to(dest: str | os.PathLike, env: Mapping[str, str] | None = None) -> int:
    """Write the (already redacted) transcript to ``dest`` as JSONL; returns the record count.

    Export works even when recording is disabled: the owner can always read out what exists.
    """
    stored = _read(history_path(env))
    dest_path = Path(dest).expanduser()
    if dest_path.parent and str(dest_path.parent) not in ("", "."):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
    _write(dest_path, stored)
    return len(stored)


def import_from(source: str | os.PathLike, env: Mapping[str, str] | None = None) -> tuple[int, int]:
    """Append validated records from a JSONL file; returns ``(imported, skipped)``.

    The file is data, never code: strict JSONL, known roles only, non-empty string text only,
    numeric timestamps, per-record and per-file caps, and every imported text is redacted
    again. Invalid lines are skipped and counted, not executed or trusted.
    """
    src = Path(source).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"no such file: {src.name}")
    if src.stat().st_size > MAX_IMPORT_BYTES:
        raise ValueError(f"import file is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB")
    incoming: list[dict] = []
    skipped = 0
    with src.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            if len(incoming) >= MAX_IMPORT_RECORDS:
                skipped += 1
                continue
            try:
                item = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if not _valid(item):
                skipped += 1
                continue
            incoming.append(_record(str(item["role"]), str(item["text"]), ts=float(item["ts"])))
    if incoming:
        path = history_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        stored = _read(path)
        stored.extend(incoming)
        _write(path, stored[-MAX_RECORDS:])
    return len(incoming), skipped
