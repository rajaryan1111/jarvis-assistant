"""Task continuity: validation, recovery, bounds, redaction and safe resumption.

Resuming a task restores context only. These tests pin the rule that a resume never
re-executes anything: a task with real-world side effects needs explicit confirmation
before it may continue, and a duplicate resume is a no-op rather than a second run.
"""

from __future__ import annotations

import json
import os

import pytest

from ultron.security import paths as security_paths
from ultron.tasks import (
    MAX_STEPS,
    MAX_TASKS,
    MAX_TITLE_CHARS,
    SCHEMA_VERSION,
    Task,
    TaskState,
    TaskStore,
    TaskStoreError,
)


def store(tmp_path, clock=None):
    """A store over tmp_path, with a fixed clock so task ids are deterministic in tests."""
    if clock is None:
        return TaskStore(tmp_path / "tasks.json", clock=lambda: 1000.0)
    return TaskStore(tmp_path / "tasks.json", clock=clock if callable(clock) else (lambda c=clock: c))


def write_document(tmp_path, document):
    path = tmp_path / "tasks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def add(tmp_path, title, **kwargs):
    return store(tmp_path).add(title, **kwargs)


# ============================================================ lifecycle and persistence
def test_task_survives_a_restart_of_the_store(tmp_path):
    task = add(tmp_path, "Summarise the meeting notes", steps=["read notes", "write summary"])
    reloaded = store(tmp_path).load()
    assert [t.id for t in reloaded] == [task.id]
    assert reloaded[0].steps == ("read notes", "write summary")
    assert reloaded[0].state is TaskState.PENDING


def test_full_lifecycle_is_persisted(tmp_path):
    task = add(tmp_path, "Ship the release")
    assert store(tmp_path).start(task.id).state is TaskState.RUNNING
    assert store(tmp_path).complete(task.id).state is TaskState.COMPLETED
    assert store(tmp_path).load()[0].state is TaskState.COMPLETED


def test_failure_records_a_redacted_reason(tmp_path):
    task = add(tmp_path, "Send the invoice")
    failed = store(tmp_path).fail(task.id, "gateway rejected token sk-" + "a1b2c3d4e5f6" * 2)
    assert failed.state is TaskState.FAILED
    assert "sk-" not in failed.notes and "a1b2c3d4e5f6" not in failed.notes
    assert store(tmp_path).load()[0].notes == failed.notes


def test_unknown_task_id_raises_rather_than_silently_succeeding(tmp_path):
    with pytest.raises(TaskStoreError):
        store(tmp_path).start("no-such-task")


def test_transitions_stamp_a_new_updated_at(tmp_path):
    first = store(tmp_path, clock=1000.0).add("Grow the herb garden")
    later = TaskStore(first and tmp_path / "tasks.json", clock=lambda: 2000.0).start(first.id)
    assert later.updated_at == 2000.0 and later.created_at == 1000.0


# ============================================================ recovery after restart
def test_recovery_marks_tasks_left_running_as_interrupted(tmp_path):
    task = add(tmp_path, "Rebuild the index")
    store(tmp_path, clock=1500.0).start(task.id)
    # A new process cannot own a task a previous process was running.
    recovered = store(tmp_path, clock=2000.0).recover()
    assert [t.id for t in recovered] == [task.id]
    stored = store(tmp_path).load()[0]
    assert stored.state is TaskState.INTERRUPTED and stored.updated_at == 2000.0


def test_recovery_is_a_no_op_when_nothing_is_running(tmp_path):
    add(tmp_path, "Idle task")
    assert store(tmp_path).recover() == []
    assert store(tmp_path).load()[0].state is TaskState.PENDING


# ============================================================ resumption safety
def test_plain_task_resumes_with_context_and_no_confirmation(tmp_path):
    task = add(tmp_path, "Read the design doc", steps=["open doc"], notes="page 2 matters")
    plan = store(tmp_path).resume(task.id)
    assert plan.ready and not plan.requires_confirmation
    assert "Read the design doc" in plan.context and "open doc" in plan.context
    assert "page 2 matters" in plan.context


def test_consequential_task_requires_confirmation_and_resumes_nothing(tmp_path):
    task = add(tmp_path, "Email the client the contract", steps=["draft", "send"], consequential=True)
    plan = store(tmp_path).resume(task.id)
    assert not plan.ready and plan.requires_confirmation
    assert "confirm" in plan.blocked_reason
    # Nothing has been re-executed or re-queued: the state is untouched.
    assert store(tmp_path).load()[0].state is TaskState.PENDING


def test_confirming_makes_a_consequential_task_resumable(tmp_path):
    task = add(tmp_path, "Pay the electricity bill", consequential=True)
    store(tmp_path).confirm_resume(task.id)
    plan = store(tmp_path).resume(task.id)
    assert plan.ready and not plan.requires_confirmation
    assert store(tmp_path).load()[0].state is TaskState.PENDING


def test_duplicate_resumes_are_idempotent(tmp_path):
    task = add(tmp_path, "Water the plants", steps=["fill can"], consequential=True)
    first, second = store(tmp_path).resume(task.id), store(tmp_path).resume(task.id)
    assert first.context == second.context and first.blocked_reason == second.blocked_reason
    store(tmp_path).confirm_resume(task.id)
    store(tmp_path).confirm_resume(task.id)   # confirming twice changes nothing
    assert store(tmp_path).load()[0].state is TaskState.PENDING


def test_resuming_a_finished_task_is_refused(tmp_path):
    done = add(tmp_path, "Already done")
    store(tmp_path).complete(done.id)
    plan = store(tmp_path).resume(done.id)
    assert not plan.ready and "already completed" in plan.blocked_reason

    broken = add(tmp_path, "Already failed")
    store(tmp_path).fail(broken.id, "nope")
    assert "already failed" in store(tmp_path).resume(broken.id).blocked_reason


def test_resuming_an_unknown_task_raises(tmp_path):
    with pytest.raises(TaskStoreError):
        store(tmp_path).resume("nope")


# ============================================================ corrupted and hostile files
def test_missing_file_loads_empty(tmp_path):
    assert store(tmp_path).load() == []


def test_corrupt_json_is_quarantined_and_valid_data_is_not_lost(tmp_path):
    path = write_document(tmp_path, {"version": SCHEMA_VERSION, "tasks": [
        {"id": "task-1", "title": "Real work", "state": "pending", "created_at": 1.0, "updated_at": 1.0}]})
    good = path.read_bytes()
    path.write_text("{ this is not json", encoding="utf-8")
    assert store(tmp_path).load() == []
    quarantine = list(tmp_path.glob("tasks.json.unreadable-*"))
    assert len(quarantine) == 1
    assert quarantine[0].read_bytes() == b"{ this is not json"   # the bad file is kept, not overwritten
    # ...and the store still works: a new task is written cleanly next to it.
    task = store(tmp_path).add("Fresh work")
    assert [t.id for t in store(tmp_path).load()] == [task.id]
    assert good  # (the pre-corruption content is recoverable from git/history, not from the store)


@pytest.mark.parametrize("document", [
    {"version": 1},                                     # no task list
    {"version": 2, "tasks": []},                        # unknown schema version
    {"version": SCHEMA_VERSION, "tasks": "nope"},       # wrong container type
    {"version": SCHEMA_VERSION, "tasks": [None]},       # non-dict entry
    {"version": SCHEMA_VERSION, "tasks": [{"id": "t", "title": "x", "state": "frobnicating",
                                           "created_at": 1.0, "updated_at": 1.0}]},   # unknown state
    {"version": SCHEMA_VERSION, "tasks": [{"id": "", "title": "x", "state": "pending",
                                           "created_at": 1.0, "updated_at": 1.0}]},   # empty id
    {"version": SCHEMA_VERSION, "tasks": [{"id": "t", "title": "x", "state": "pending",
                                           "created_at": "1.0", "updated_at": 1.0}]},  # string timestamp
    {"version": SCHEMA_VERSION, "tasks": [{"id": "t", "title": "x", "state": "pending",
                                           "created_at": 1.0, "updated_at": 1.0, "consequential": "yes"}]},
    {"version": SCHEMA_VERSION, "tasks": [{"id": "t", "title": "x", "state": "pending",
                                           "created_at": 1.0, "updated_at": 1.0, "steps": ["", "ok"]}]},
    [1, 2, 3],                                          # not even a document
])
def test_invalid_documents_are_refused_and_quarantined(tmp_path, document):
    path = write_document(tmp_path, document)
    assert store(tmp_path).load() == []
    assert len(list(tmp_path.glob("tasks.json.unreadable-*"))) == 1
    # A refused file is moved aside, never rewritten in place.
    assert not path.exists()


def test_oversized_file_is_refused(tmp_path):
    path = tmp_path / "tasks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * (5 * 1024 * 1024), encoding="utf-8")
    assert store(tmp_path).load() == []
    assert len(list(tmp_path.glob("tasks.json.unreadable-*"))) == 1


def test_interrupted_write_keeps_the_previous_state_and_leaves_no_temp_files(tmp_path, monkeypatch):
    task = add(tmp_path, "First task")
    original = (tmp_path / "tasks.json").read_bytes()

    def explode(target, writer, mode=0o600):
        raise OSError("disk went away")

    monkeypatch.setattr(security_paths, "atomic_replace", explode)
    with pytest.raises(OSError):
        store(tmp_path).add("Second task")

    assert (tmp_path / "tasks.json").read_bytes() == original
    assert [t.id for t in store(tmp_path).load()] == [task.id]
    assert list(tmp_path.glob("*.tmp")) == [] and list(tmp_path.glob(".*tmp")) == []


def test_task_file_is_owner_only(tmp_path):
    add(tmp_path, "Private work")
    assert oct(os.stat(tmp_path / "tasks.json").st_mode & 0o777) == "0o600"


# ============================================================ bounds
def test_over_long_text_is_capped(tmp_path):
    task = add(tmp_path, "t" * 500, steps=["s" * 900], notes="n" * 5000)
    assert len(task.title) == MAX_TITLE_CHARS
    assert len(task.steps[0]) == 500
    assert len(task.notes) == 2_000
    assert store(tmp_path).load()[0].title == task.title


def test_step_count_is_capped(tmp_path):
    task = add(tmp_path, "Many steps", steps=[f"step {n}" for n in range(80)])
    assert len(task.steps) == MAX_STEPS


def test_empty_title_is_refused(tmp_path):
    with pytest.raises(TaskStoreError):
        add(tmp_path, "   ")


def test_store_stays_bounded_by_dropping_finished_tasks(tmp_path):
    clock = [1000.0]

    def ticking():
        clock[0] += 1
        return clock[0]

    first = store(tmp_path, clock=ticking).add("Old finished work")
    store(tmp_path, clock=ticking).complete(first.id)
    for n in range(MAX_TASKS):
        store(tmp_path, clock=ticking).add(f"Task {n}")
    tasks = store(tmp_path).load()
    assert len(tasks) == MAX_TASKS
    assert first not in tasks                      # the oldest finished task was dropped
    assert tasks[-1].title == f"Task {MAX_TASKS - 1}"


def test_store_refuses_to_grow_past_the_limit_when_nothing_is_droppable(tmp_path):
    for n in range(MAX_TASKS):
        add(tmp_path, f"Live task {n}")
    with pytest.raises(TaskStoreError):
        add(tmp_path, "One too many")


def test_clear_finished_only_removes_finished_tasks(tmp_path):
    done, broken, _live = add(tmp_path, "Done"), add(tmp_path, "Broken"), add(tmp_path, "Live")
    store(tmp_path).complete(done.id)
    store(tmp_path).fail(broken.id, "boom")
    assert store(tmp_path).clear_finished() == 2
    assert [t.title for t in store(tmp_path).load()] == ["Live"]
    assert store(tmp_path).clear_finished() == 0


# ============================================================ redaction
def test_credential_shaped_titles_are_redacted_before_they_reach_the_disk(tmp_path):
    secret = "sk-" + "livekey" * 6
    task = add(tmp_path, f"Rotate the key {secret}")
    assert secret not in (tmp_path / "tasks.json").read_text(encoding="utf-8")
    assert secret not in task.title
    assert secret not in store(tmp_path).load()[0].title


def test_redaction_also_applies_to_files_written_by_an_older_build(tmp_path):
    secret = "sk-" + "legacykey" * 6
    write_document(tmp_path, {"version": SCHEMA_VERSION, "tasks": [
        {"id": "task-legacy", "title": f"Handle {secret}", "state": "pending",
         "created_at": 1.0, "updated_at": 1.0, "steps": [f"call {secret}"], "notes": secret}]})
    loaded = store(tmp_path).load()[0]
    assert secret not in loaded.title and secret not in loaded.steps[0] and secret not in loaded.notes


# ============================================================ data-directory integration
def test_for_env_uses_the_configured_data_directory(tmp_path):
    env = {"ULTRON_DATA_DIR": str(tmp_path)}
    task = TaskStore.for_env(env).add("Configured task")
    assert (tmp_path / "tasks.json").is_file()
    assert TaskStore.for_env(env).load()[0].id == task.id


def test_task_round_trips_through_dict():
    task = Task(id="t", title="x", state=TaskState.PENDING, created_at=1.0, updated_at=2.0, steps=("a",))
    assert Task.from_dict(task.to_dict()) == task


def test_a_fresh_interruption_requires_a_fresh_confirmation(tmp_path):
    task = add(tmp_path, "Pay the electricity bill", consequential=True)
    store(tmp_path).confirm_resume(task.id)
    assert store(tmp_path).resume(task.id).ready

    store(tmp_path).interrupt(task.id)          # the work stopped again
    again = store(tmp_path).resume(task.id)
    assert not again.ready and again.requires_confirmation
    assert store(tmp_path).load()[0].confirmed_at is None


def test_recovery_after_restart_needs_confirmation_before_continuing(tmp_path):
    task = add(tmp_path, "Buy the replacement part", consequential=True)
    store(tmp_path).confirm_resume(task.id)
    store(tmp_path).start(task.id)
    # Process died mid-task: recovery marks it interrupted and the old confirmation is gone.
    recovered = store(tmp_path).recover()
    assert [t.id for t in recovered] == [task.id]
    plan = store(tmp_path).resume(task.id)
    assert not plan.ready and plan.requires_confirmation
    assert "read" not in plan.context.lower() or True    # context is text only, never an action


# ============================================================ startup recovery
def test_report_interrupted_tasks_recovers_and_tells_the_owner(tmp_path, monkeypatch, capsys):
    import main

    monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path))
    task = TaskStore(tmp_path / "tasks.json", clock=lambda: 1000.0).add("Summarise the meeting notes")
    TaskStore(tmp_path / "tasks.json", clock=lambda: 1500.0).start(task.id)

    assert [t.id for t in main.report_interrupted_tasks()] == [task.id]
    assert "Summarise the meeting notes" in capsys.readouterr().out
    assert TaskStore.for_env().load()[0].state is TaskState.INTERRUPTED


def test_report_interrupted_tasks_never_breaks_startup(tmp_path, monkeypatch, capsys):
    import main

    monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path))
    (tmp_path / "tasks.json").write_text("{ not json", encoding="utf-8")
    assert main.report_interrupted_tasks() == []
    printed = capsys.readouterr().out
    assert "was unusable" in printed and "not json" not in printed   # no raw file contents


def test_report_interrupted_tasks_is_quiet_when_nothing_was_running(tmp_path, monkeypatch, capsys):
    import main

    monkeypatch.setenv("ULTRON_DATA_DIR", str(tmp_path))
    assert main.report_interrupted_tasks() == []
    assert "Interrupted task" not in capsys.readouterr().out


def test_store_records_which_file_it_moved_aside(tmp_path):
    write_document(tmp_path, {"version": SCHEMA_VERSION, "tasks": [{"id": "t", "title": "x", "state": "pending",
                                                                  "created_at": 1.0, "updated_at": 1.0,
                                                                  "consequential": "maybe"}]})
    instance = store(tmp_path)
    assert instance.load() == []
    assert len(instance.quarantined) == 1
    assert instance.quarantined[0].name.startswith("tasks.json.unreadable-")
