"""Real-process storage races/crashes using only owned synthetic directories."""
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

from goodteam_os.memory import RoleMemoryStore
from goodteam_os.memory_storage import MemoryStorageError, role_lock


ROLE = "architect"
CHILD = r'''
import json, os, sys, time
from pathlib import Path
import goodteam_os.memory as memory
import goodteam_os.memory_storage as storage
root, operation, text = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
store = memory.RoleMemoryStore(root)
def wait(path):
    deadline = time.monotonic() + 8
    while not path.exists():
        if time.monotonic() > deadline: raise RuntimeError("synthetic barrier timeout")
        time.sleep(.01)
if operation == "hold":
    with storage.role_lock(store.role_root("architect")):
        (root / "holder-ready").touch()
        wait(root / "release")
        store.append_memory_event("architect", horizon="long-term", text=text, source="synthetic")
elif operation == "append":
    (root / (text + "-started")).touch()
    event = store.append_memory_event("architect", horizon="long-term", text=text, source="synthetic")
    (root / (text + "-done")).write_text(json.dumps(event))
elif operation == "read":
    (root / "reader-started").touch()
    index = store.read_memory_index("architect")
    (root / "reader-done").write_text(json.dumps(index))
else:
    real_write = memory.atomic_write
    real_replace = storage.os.replace
    def fault_write(path, data, **kwargs):
        if path.name == "events.jsonl" and operation == "before_wal": os._exit(77)
        if path.name == "index.json" and operation == "before_index": os._exit(77)
        real_write(path, data, **kwargs)
        if path.name == "events.jsonl" and operation == "after_wal": os._exit(77)
        if path.name == "checkpoint.json" and operation == "after_checkpoint" and json.loads(data)["event_count"] > 0: os._exit(77)
    def fault_replace(source, dest, **kwargs):
        if dest == "events.jsonl" and operation == "before_replace": os._exit(77)
        return real_replace(source, dest, **kwargs)
    memory.atomic_write = fault_write
    storage.os.replace = fault_replace
    store.append_memory_event("architect", horizon="long-term", text=text, source="synthetic")
'''


def append(store, text, **kwargs):
    return store.append_memory_event(ROLE, horizon="long-term", text=text, source="synthetic", **kwargs)


def wait_file(path):
    deadline = time.monotonic() + 8
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"Synthetic process did not reach {path.name}")
        time.sleep(.01)


def start(root, operation, text="FACT"):
    return subprocess.Popen([sys.executable, "-c", CHILD, str(root), operation, text], stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def finish(process, code=0):
    try:
        out, err = process.communicate(timeout=12)
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate()
        pytest.fail(f"Owned synthetic process timed out: {err!r}")
    assert process.returncode == code, (out, err)


def stop(processes):
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.communicate()


@pytest.mark.parametrize("same", [False, True])
def test_two_process_writers_serialize_dedup_wal_and_projection(tmp_path, same):
    store = RoleMemoryStore(tmp_path)
    paths = store.ensure_role_memory(ROLE)
    handwritten = b"# Handwritten\r\n\r\nKEEP_ALL_BYTES  \t\n"
    paths["long-term"].write_bytes(handwritten)
    first = start(tmp_path, "hold", "SAME" if same else "FIRST")
    processes = [first]
    try:
        wait_file(tmp_path / "holder-ready")
        lock_inode = (store.role_root(ROLE) / ".store.lock").stat().st_ino
        second_text = "SAME" if same else "SECOND"
        second = start(tmp_path, "append", second_text)
        reader = start(tmp_path, "read")
        processes += [second, reader]
        wait_file(tmp_path / (second_text + "-started"))
        wait_file(tmp_path / "reader-started")
        time.sleep(.1)
        assert not (tmp_path / (second_text + "-done")).exists()
        assert not (tmp_path / "reader-done").exists()
        (tmp_path / "release").touch()
        for process in processes:
            finish(process)
        events = store._read_events(ROLE)
        assert len(events) == (1 if same else 2)
        content = paths["long-term"].read_bytes()
        assert content.startswith(handwritten)
        assert content.count(b"- SAME Source:") == 1 if same else b"FIRST" in content and b"SECOND" in content
        assert store.read_memory_index(ROLE)["event_count"] == len(events)
        assert json.loads((tmp_path / "reader-done").read_text())["event_count"] in (1, len(events))
        assert (store.role_root(ROLE) / ".store.lock").stat().st_ino == lock_inode
    finally:
        stop(processes)


@pytest.mark.parametrize("phase,committed", [("before_wal", False), ("before_replace", False), ("after_wal", True), ("after_checkpoint", True), ("before_index", True)])
def test_process_death_recovers_committed_or_aborted_append_once(tmp_path, phase, committed):
    store = RoleMemoryStore(tmp_path)
    paths = store.ensure_role_memory(ROLE)
    note = b"# Manual\nKEEP_TRAILING_SPACE  \n"
    paths["long-term"].write_bytes(note)
    finish(start(tmp_path, phase), 77)
    assert len(store._read_events(ROLE)) == int(committed)
    index = store.read_memory_index(ROLE)
    assert index["event_count"] == int(committed)
    result = append(store, "FACT")
    assert store._read_events(ROLE) == [result]
    assert paths["long-term"].read_bytes().startswith(note)
    assert paths["long-term"].read_text().count("- FACT Source:") == 1
    before = paths["long-term"].read_bytes()
    assert append(store, "FACT") == result
    assert paths["long-term"].read_bytes() == before
    assert store.read_memory_index(ROLE)["event_count"] == 1


@pytest.mark.parametrize("index_state", ["missing", "broken"])
def test_independent_checkpoint_repairs_bad_index_but_rejects_rollback(tmp_path, index_state):
    store = RoleMemoryStore(tmp_path)
    old = append(store, "database OLD_RULE")
    prefix = store.event_path(ROLE).read_bytes()
    append(store, "database NEW_RULE", supersedes=store._event_id(old))
    if index_state == "missing":
        store.index_path(ROLE).unlink()
    else:
        store.index_path(ROLE).write_text("{")
    assert store.read_memory_index(ROLE)["event_count"] == 2
    checkpoint = store.checkpoint_path(ROLE).read_bytes()
    store.event_path(ROLE).write_bytes(prefix)
    store.index_path(ROLE).unlink()
    for action in [lambda: store.read_memory_index(ROLE), lambda: store.rebuild_role_memory(ROLE), lambda: append(store, "MORE"), lambda: store.list_events(ROLE), lambda: store.retrieve_relevant_events(ROLE, "database")]:
        with pytest.raises(ValueError):
            action()
    assert store.checkpoint_path(ROLE).read_bytes() == checkpoint
    assert not store.index_path(ROLE).exists()
    assert store.build_pickup_packet(ROLE)["context_status"] == "unavailable"


@pytest.mark.parametrize("index_state", ["missing", "broken"])
def test_legacy_wal_without_any_checkpoint_requires_explicit_recovery(tmp_path, index_state, monkeypatch):
    from goodteam_os.web import build_memory_overview
    from goodteam_os.workers import WorkerJobStore
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path / "operator"))
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    store = RoleMemoryStore(tmp_path)
    append(store, "database OLD_RULE")
    store.checkpoint_path(ROLE).unlink()  # Simulate a pre-upgrade role.
    if index_state == "missing":
        store.index_path(ROLE).unlink()
    else:
        store.index_path(ROLE).write_text("{")
    before = {path.name: path.read_bytes() for path in store.role_root(ROLE).iterdir() if path.is_file()}
    packet = store.build_pickup_packet(ROLE)
    assert packet["context_status"] == "unavailable" and packet["context_reasons"] == ["history_unverifiable"]
    with pytest.raises(MemoryStorageError, match="restore_checkpoint_or_confirm_import"):
        store.read_memory_index(ROLE)
    overview = build_memory_overview(tmp_path, "SYNTHETIC")
    assert overview["roles"][0]["context_reasons"] == ["history_unverifiable"]
    workers = WorkerJobStore(tmp_path)
    job = workers.create_job(run_id="synthetic", role=ROLE, adapter="noop", task="database", context="")
    prompt = workers._worker_prompt(workers.root / job["id"], job)
    assert "OLD_RULE" not in prompt and "history_unverifiable" in prompt
    assert {path.name: path.read_bytes() for path in store.role_root(ROLE).iterdir() if path.is_file()} == before


def test_valid_legacy_index_migrates_checkpoint_without_rewriting_notes(tmp_path):
    store = RoleMemoryStore(tmp_path)
    append(store, "database VERIFIED")
    store.checkpoint_path(ROLE).unlink()
    old = json.loads(store.index_path(ROLE).read_bytes())
    old.pop("projection_policy")
    store.index_path(ROLE).write_text(json.dumps(old))
    before = store.memory_path(ROLE, "long-term").read_bytes()
    assert store.read_memory_index(ROLE)["event_count"] == 1
    assert store.checkpoint_path(ROLE).is_file()
    assert store.memory_path(ROLE, "long-term").read_bytes() == before


def test_new_append_preserves_legacy_exact_bytes_without_final_newline(tmp_path):
    store = RoleMemoryStore(tmp_path)
    append(store, "OLD")
    wal = store.event_path(ROLE)
    old = wal.read_bytes().rstrip(b"\n")
    wal.write_bytes(old)
    store.checkpoint_path(ROLE).unlink()
    index = json.loads(store.index_path(ROLE).read_bytes())
    index["integrity"]["events_sha256"] = hashlib.sha256(old).hexdigest()
    store.index_path(ROLE).write_text(json.dumps(index))
    append(store, "NEW")
    assert wal.read_bytes().startswith(old + b"\n")
    assert len(store._read_events(ROLE)) == 2


@pytest.mark.parametrize("target", ["events.jsonl", "index.json", "checkpoint.json", "long-term.md", ".store.lock"])
def test_unsafe_symlink_is_not_read_or_replaced(tmp_path, target):
    store = RoleMemoryStore(tmp_path)
    append(store, "FACT")
    outside = tmp_path / "untouched"
    outside.write_bytes(b"DO_NOT_TOUCH")
    path = store.role_root(ROLE) / target
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError):
        append(store, "NEW")
    assert outside.read_bytes() == b"DO_NOT_TOUCH" and path.is_symlink()


def test_unsupported_lock_platform_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.memory_storage.fcntl", None)
    store = RoleMemoryStore(tmp_path)
    with pytest.raises(MemoryStorageError, match="unsupported"):
        append(store, "FACT")
    assert store.build_pickup_packet(ROLE)["context_status"] == "unavailable"
    assert not store.event_path(ROLE).exists()


def test_multiline_replay_keeps_manual_bytes_and_does_not_duplicate(tmp_path):
    store = RoleMemoryStore(tmp_path)
    paths = store.ensure_role_memory(ROLE)
    note = b"# Manual\nKeep  \n\n"
    paths["long-term"].write_bytes(note)
    event = append(store, "line one\nline two")
    before = paths["long-term"].read_bytes()
    store.index_path(ROLE).unlink()
    store.rebuild_role_memory(ROLE)
    assert append(store, event["text"]) == event
    assert paths["long-term"].read_bytes() == before
    assert before.startswith(note)


def test_wal_size_limit_rejects_before_commit_and_keeps_prior_history(tmp_path, monkeypatch):
    store = RoleMemoryStore(tmp_path)
    append(store, "small")
    before = store.event_path(ROLE).read_bytes()
    monkeypatch.setattr("goodteam_os.memory.MAX_WAL_BYTES", len(before) + 5)
    with pytest.raises(MemoryStorageError, match="size_limit"):
        append(store, "too large")
    assert store.event_path(ROLE).read_bytes() == before
    assert len(store._read_events(ROLE)) == 1


@pytest.mark.parametrize("method", ["list_events", "retrieve_relevant_events"])
def test_lock_free_model_work_rechecks_current_revision(tmp_path, monkeypatch, method):
    store = RoleMemoryStore(tmp_path)
    old = append(store, "database OLD")
    original = store._rank_events
    def concurrent_correction(*args, **kwargs):
        code = "from pathlib import Path; import sys; from goodteam_os.memory import RoleMemoryStore; RoleMemoryStore(Path(sys.argv[1])).append_memory_event('architect',horizon='long-term',text='database NEW',source='synthetic',supersedes=sys.argv[2])"
        subprocess.run([sys.executable, "-c", code, str(tmp_path), store._event_id(old)], check=True, timeout=8)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "_rank_events", concurrent_correction)
    with pytest.raises(MemoryStorageError, match="revision_changed"):
        getattr(store, method)(ROLE, query="database")


def test_role_lock_is_reentrant_but_other_roles_are_independent(tmp_path):
    store = RoleMemoryStore(tmp_path)
    with role_lock(store.role_root(ROLE)):
        with role_lock(store.role_root(ROLE)):
            append(store, "REENTRANT")
        # A different role in a new process must proceed while this role is held.
        code = "from pathlib import Path; import sys; from goodteam_os.memory import RoleMemoryStore; RoleMemoryStore(Path(sys.argv[1])).append_memory_event('planner',horizon='long-term',text='OTHER')"
        subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=True, timeout=8)


@pytest.mark.parametrize("field", ["text", "source", "supersedes"])
def test_invalid_unicode_is_rejected_before_durable_write(tmp_path, field):
    store = RoleMemoryStore(tmp_path)
    append(store, "VALID")
    wal = store.event_path(ROLE).read_bytes()
    fields = {"text": "NEW", "source": "synthetic", "supersedes": ""}
    fields[field] = "\ud800"
    with pytest.raises(UnicodeError):
        store.append_memory_event(ROLE, horizon="long-term", **fields)
    assert store.event_path(ROLE).read_bytes() == wal
    assert len(store._read_events(ROLE)) == 1


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is POSIX-only")
def test_fork_child_does_not_inherit_parent_lock_ownership(tmp_path):
    store = RoleMemoryStore(tmp_path)
    child = None
    try:
        with role_lock(store.role_root(ROLE)):
            child = os.fork()
            if child == 0:
                try:
                    (tmp_path / "fork-started").touch()
                    append(store, "FORK_CHILD")
                    (tmp_path / "fork-done").touch()
                    os._exit(0)
                except BaseException:
                    os._exit(1)
            wait_file(tmp_path / "fork-started")
            time.sleep(.1)
            assert not (tmp_path / "fork-done").exists()
        wait_file(tmp_path / "fork-done")
        _, status = os.waitpid(child, 0)
        child = None
        assert os.waitstatus_to_exitcode(status) == 0
        assert len(store._read_events(ROLE)) == 1
    finally:
        if child is not None:
            os.kill(child, 9)
            os.waitpid(child, 0)


@pytest.mark.parametrize("read", [
    lambda store: store._read_events("missing"),
    lambda store: store.list_events("missing"),
    lambda store: store.retrieve_relevant_events("missing", "database"),
    lambda store: store.build_pickup_packet("missing"),
    lambda store: store.read_role_memory("missing"),
    lambda store: store.read_memory_index("missing"),
])
def test_absent_role_reads_do_not_create_business_state(tmp_path, read):
    from goodteam_os.web import build_memory_overview
    store = RoleMemoryStore(tmp_path)
    assert build_memory_overview(tmp_path, "SYNTHETIC")["roles"] == []
    read(store)
    assert not store.role_root("missing").exists()
    assert not (tmp_path / ".goodteam").exists()
    assert build_memory_overview(tmp_path, "SYNTHETIC")["roles"] == []
    store.append_memory_event("missing", horizon="long-term", text="FIRST")
    assert len(store._read_events("missing")) == 1


def test_missing_role_read_linearizes_before_concurrent_first_append(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import goodteam_os.memory_storage as storage
    from goodteam_os.skills.models import SkillError
    store = RoleMemoryStore(tmp_path)
    real_directory = storage.directory
    raced = False
    @contextmanager
    def create_after_observed_absence(path, *, create=False):
        nonlocal raced
        try:
            with real_directory(path, create=create) as fd:
                yield fd
        except SkillError as exc:
            if not raced and not create and isinstance(exc.__cause__, FileNotFoundError):
                raced = True
                # Another process initializes after this reader observed absence.
                finish(start(tmp_path, "append", "FIRST"))
            raise
    monkeypatch.setattr(storage, "directory", create_after_observed_absence)
    assert store._read_events(ROLE) == []
    assert raced
    assert [event["text"] for event in store._read_events(ROLE)] == ["FIRST"]


def test_existing_legacy_role_gets_stable_lock_on_first_read(tmp_path):
    store = RoleMemoryStore(tmp_path)
    role = store.role_root(ROLE)
    role.mkdir(parents=True)
    note = b"# Handwritten\nKEEP\n"
    store.memory_path(ROLE, "long-term").write_bytes(note)
    assert store._read_events(ROLE) == []
    lock = role / ".store.lock"
    inode = lock.stat().st_ino
    append(store, "FIRST")
    assert lock.stat().st_ino == inode
    assert store.memory_path(ROLE, "long-term").read_bytes().startswith(note)


def test_missing_role_cannot_satisfy_frozen_nonempty_history(tmp_path):
    store = RoleMemoryStore(tmp_path)
    append(store, "OLD")
    packet = store.build_pickup_packet(ROLE, query="OLD")
    # A never-initialized store cannot attest a packet from a different history.
    other = RoleMemoryStore(tmp_path / "other")
    validated = other.validate_pickup_packet(packet)
    assert validated["context_status"] == "unavailable"
    assert "OLD" not in json.dumps(validated)
    assert not other.role_root(ROLE).exists()


def test_absent_role_empty_capture_and_consumption_stay_available_without_creation(tmp_path):
    store = RoleMemoryStore(tmp_path)
    packet = store.build_pickup_packet("missing")
    assert packet["context_status"] == "available" and packet["event_count"] == 0
    assert store.validate_pickup_packet(packet)["context_status"] == "available"
    assert not store.role_root("missing").exists()
