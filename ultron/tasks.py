"""Bounded, validated, resumable task state for the assistant.

Long-running work the owner starts and may come back to is persisted as one small JSON
document under the data directory (``ULTRON_DATA_DIR``). The store is deliberately small
and strict:

* **bounded** - at most :data:`MAX_TASKS` tasks and length-capped text fields, so a
  corrupted or hostile file cannot make the assistant allocate without limit;
* **validated** - a versioned schema is enforced exactly; anything that does not match is
  refused and quarantined, never silently "repaired";
* **redacted** - every text field passes through :func:`ultron.security.redact` on the way
  in and on the way out, so credential-shaped strings never reach the file;
* **atomic** - written 0600 through the shared atomic writer, so an interrupted write can
  never truncate or corrupt the previous state;
* **safe to resume** - resuming restores *context only*. A task whose steps could take a
  consequential action (send a message, change or delete files, spend money) must be
  confirmed before anything runs again; :meth:`TaskStore.resume` never re-executes a step.

An unreadable or schema-invalid file is moved aside (``tasks.json.unreadable-<ts>``),
exactly like ``memory.json``: bad data can never overwrite good data.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ultron.config import default_data_dir
from ultron.security.paths import atomic_write_text
from ultron.security.redact import redact

log = logging.getLogger("ultron.tasks")

FILENAME = "tasks.json"
SCHEMA_VERSION = 1
MAX_TASKS = 200
MAX_TITLE_CHARS = 200
MAX_STEP_CHARS = 500
MAX_STEPS = 50
MAX_NOTE_CHARS = 2_000
MAX_FILE_BYTES = 4 * 1024 * 1024


class TaskState(str, Enum):
    """Lifecycle of one task. A fresh process can never own a RUNNING task."""

    PENDING = "pending"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStoreError(RuntimeError):
    """Raised for caller mistakes (unknown id, illegal transition). Never for bad files."""


@dataclass(frozen=True)
class Task:
    """One resumable unit of work. ``consequential`` marks steps with real side effects."""

    id: str
    title: str
    state: TaskState
    created_at: float
    updated_at: float
    steps: tuple[str, ...] = ()
    notes: str = ""
    consequential: bool = False
    #: When the owner explicitly allowed this task to continue after an interruption.
    confirmed_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "steps": list(self.steps),
            "notes": self.notes,
            "consequential": self.consequential,
            "confirmed_at": self.confirmed_at,
        }

    @classmethod
    def from_dict(cls, raw: object) -> Task | None:
        """Strict validation. Returns ``None`` for anything that is not exactly this shape."""
        if not isinstance(raw, dict):
            return None
        task_id, title, state = raw.get("id"), raw.get("title"), raw.get("state")
        created, updated = raw.get("created_at"), raw.get("updated_at")
        steps, notes = raw.get("steps", []), raw.get("notes", "")
        consequential = raw.get("consequential", False)
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 64:
            return None
        if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_CHARS:
            return None
        if not isinstance(state, str) or state not in {member.value for member in TaskState}:
            return None
        for stamp in (created, updated):
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
                return None
        if not isinstance(steps, list) or len(steps) > MAX_STEPS:
            return None
        if not all(isinstance(step, str) and step.strip() and len(step) <= MAX_STEP_CHARS for step in steps):
            return None
        if not isinstance(notes, str) or len(notes) > MAX_NOTE_CHARS:
            return None
        if not isinstance(consequential, bool):
            return None
        confirmed = raw.get("confirmed_at")
        if confirmed is not None and (isinstance(confirmed, bool) or not isinstance(confirmed, (int, float))):
            return None
        return cls(id=task_id, title=title, state=TaskState(state), created_at=float(created),
                   updated_at=float(updated), steps=tuple(steps), notes=notes, consequential=consequential,
                   confirmed_at=None if confirmed is None else float(confirmed))


@dataclass(frozen=True)
class ResumePlan:
    """What resuming a task restores. Confirmation is required before consequential steps."""

    task: Task
    context: str
    requires_confirmation: bool
    blocked_reason: str = ""

    @property
    def ready(self) -> bool:
        return not self.blocked_reason


class TaskStore:
    """The persisted task list. Every method is safe to call on a broken or missing file."""

    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self._path = Path(path)
        self._clock = clock
        #: Files this instance moved aside because they were unusable (see _quarantine).
        self.quarantined: list[Path] = []

    @classmethod
    def for_env(cls, env: dict[str, str] | None = None, *, clock=time.time) -> TaskStore:
        """The store under the configured data directory (same place as the transcript)."""
        return cls(default_data_dir(env) / FILENAME, clock=clock)

    # ---------------------------------------------------------------- reading
    def load(self) -> list[Task]:
        """All stored tasks, oldest first. ``[]`` when missing, unreadable or invalid."""
        try:
            if self._path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("task file is too large")
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            self._quarantine(exc)
            return []
        tasks = self._validate_document(raw)
        if tasks is None:
            self._quarantine(ValueError("task file does not match the schema"))
            return []
        return [self._redact(task) for task in tasks]

    def _validate_document(self, raw: object) -> list[Task] | None:
        if not isinstance(raw, dict) or raw.get("version") != SCHEMA_VERSION:
            return None
        entries = raw.get("tasks")
        if not isinstance(entries, list) or len(entries) > MAX_TASKS:
            return None
        tasks = []
        for entry in entries:
            task = Task.from_dict(entry)
            if task is None:
                return None  # one invalid entry invalidates the document: strict, never partial
            tasks.append(task)
        return tasks

    @staticmethod
    def _redact(task: Task) -> Task:
        """Defence in depth: files written by an older build are redacted on read too."""
        return Task(
            id=task.id, title=redact(task.title), state=task.state,
            created_at=task.created_at, updated_at=task.updated_at,
            steps=tuple(redact(step) for step in task.steps),
            notes=redact(task.notes), consequential=task.consequential, confirmed_at=task.confirmed_at,
        )

    def _quarantine(self, exc: Exception) -> None:
        """Move an unusable file aside so its content survives and is never overwritten."""
        backup = self._path.with_name(f"{self._path.name}.unreadable-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            self._path.replace(backup)
        except OSError:
            log.warning("task state unreadable (%s) and could not be set aside", type(exc).__name__)
            return
        self.quarantined.append(backup)
        log.warning("task state was unusable (%s); moved it to %s", type(exc).__name__, backup.name)

    # ---------------------------------------------------------------- writing
    def _save(self, tasks: list[Task]) -> None:
        document = {"version": SCHEMA_VERSION, "tasks": [task.to_dict() for task in tasks]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._path, json.dumps(document, indent=2))

    def _mutate(self, task_id: str, change) -> Task:
        tasks = self.load()
        for index, task in enumerate(tasks):
            if task.id == task_id:
                updated = change(task)
                tasks[index] = updated
                self._save(tasks)
                return updated
        raise TaskStoreError(f"no task with id {task_id!r}")

    # ---------------------------------------------------------------- lifecycle
    def add(self, title: str, *, steps=(), notes: str = "", consequential: bool = False) -> Task:
        """Create a pending task. Text is capped and redacted before it is stored."""
        title = redact(str(title)).strip()[:MAX_TITLE_CHARS]
        if not title:
            raise TaskStoreError("a task needs a title")
        capped_steps = tuple(redact(str(step)).strip()[:MAX_STEP_CHARS] for step in list(steps)[:MAX_STEPS])
        now = float(self._clock())
        task = Task(id=f"task-{int(now)}-{secrets.token_hex(3)}", title=title, state=TaskState.PENDING,
                    created_at=now, updated_at=now, steps=capped_steps,
                    notes=redact(str(notes)).strip()[:MAX_NOTE_CHARS], consequential=bool(consequential))
        tasks = self.load()
        tasks.append(task)
        # Stay bounded without losing live work: drop the oldest finished tasks first.
        droppable = [t for t in tasks if t.state in (TaskState.COMPLETED, TaskState.FAILED)]
        while len(tasks) > MAX_TASKS and droppable:
            tasks.remove(droppable.pop(0))
        if len(tasks) > MAX_TASKS:
            raise TaskStoreError(f"too many unfinished tasks (limit {MAX_TASKS}); finish or clear some first")
        self._save(tasks)
        return task

    def start(self, task_id: str) -> Task:
        return self._mutate(task_id, lambda t: self._transition(t, TaskState.RUNNING))

    def interrupt(self, task_id: str) -> Task:
        """Mark a task interrupted (the process stopped mid-way)."""
        return self._mutate(task_id, lambda t: self._transition(t, TaskState.INTERRUPTED))

    def complete(self, task_id: str) -> Task:
        return self._mutate(task_id, lambda t: self._transition(t, TaskState.COMPLETED))

    def fail(self, task_id: str, reason: str = "") -> Task:
        note = redact(str(reason)).strip()[:MAX_NOTE_CHARS]
        return self._mutate(task_id, lambda t: self._transition(t, TaskState.FAILED, note))

    def _transition(self, task: Task, state: TaskState, note: str | None = None) -> Task:
        # A fresh interruption needs a fresh confirmation: the flag is cleared on the way in.
        confirmed = None if state is TaskState.INTERRUPTED else task.confirmed_at
        return Task(id=task.id, title=task.title, state=state, created_at=task.created_at,
                    updated_at=float(self._clock()), steps=task.steps,
                    notes=note if note is not None else task.notes, consequential=task.consequential,
                    confirmed_at=confirmed)

    # ---------------------------------------------------------------- recovery
    def recover(self) -> list[Task]:
        """Persist the post-restart view: tasks left RUNNING are INTERRUPTED.

        A new process cannot own a task a previous process was running, so recovery marks
        them interrupted (their steps did not necessarily finish). Returns every task that
        was interrupted, so the caller can tell the owner what needs attention.
        """
        tasks = self.load()
        interrupted = [t for t in tasks if t.state is TaskState.RUNNING]
        if not interrupted:
            return []
        now = float(self._clock())
        normalised = [
            Task(id=t.id, title=t.title, state=TaskState.INTERRUPTED, created_at=t.created_at,
                 updated_at=now, steps=t.steps, notes=t.notes, consequential=t.consequential,
                 confirmed_at=None)
            if t.state is TaskState.RUNNING else t
            for t in tasks
        ]
        self._save(normalised)
        return interrupted

    def resume(self, task_id: str) -> ResumePlan:
        """Restore a task's context. Never re-executes anything by itself.

        A consequential task is *not* ready until :meth:`confirm_resume` is called: the
        caller must show the owner what will happen and get an explicit go-ahead.
        """
        tasks = self.load()
        task = next((t for t in tasks if t.id == task_id), None)
        if task is None:
            raise TaskStoreError(f"no task with id {task_id!r}")
        if task.state in (TaskState.COMPLETED, TaskState.FAILED):
            return ResumePlan(task, "", False, f"this task is already {task.state.value}; nothing to resume")
        if task.consequential and task.confirmed_at is None:
            return ResumePlan(task, self._context_for(task), True,
                              "this task can take real-world actions; confirm before it continues")
        return ResumePlan(task, self._context_for(task), False)

    def confirm_resume(self, task_id: str) -> Task:
        """Record the owner's explicit go-ahead for a consequential task (idempotent).

        The task becomes pending again; the caller still decides what to run. Nothing is
        executed here, and the confirmation is persisted so it can be audited later.
        """
        def confirm(task: Task) -> Task:
            if task.confirmed_at is not None:
                return task
            now = float(self._clock())
            return Task(id=task.id, title=task.title, state=TaskState.PENDING, created_at=task.created_at,
                        updated_at=now, steps=task.steps, notes=task.notes, consequential=task.consequential,
                        confirmed_at=now)

        return self._mutate(task_id, confirm)

    @staticmethod
    def _context_for(task: Task) -> str:
        lines = [task.title]
        if task.steps:
            lines.append("Steps: " + " | ".join(task.steps))
        if task.notes:
            lines.append("Notes: " + task.notes)
        return "\n".join(lines)

    def clear_finished(self) -> int:
        """Drop completed and failed tasks. Returns how many were removed."""
        tasks = self.load()
        kept = [t for t in tasks if t.state not in (TaskState.COMPLETED, TaskState.FAILED)]
        removed = len(tasks) - len(kept)
        if removed:
            self._save(kept)
        return removed
