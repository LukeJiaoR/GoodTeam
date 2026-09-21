"""Fault-injection coverage for the binding-specific audit publication path."""

import json
import os
import stat
from pathlib import Path

import pytest

from goodteam_os import team_control_plane as tcp


def committed_binding(tmp_path, monkeypatch):
    store = tcp.TeamControlPlaneStore(tmp_path / "workspace", "DEMO")
    initial = store.load_or_migrate(legacy_target={"worker_backend": {"mode": "fake"}})
    previous = store.events_path.read_bytes()
    command = {
        "kind": "set_role_skills",
        "subject": "role:qa",
        "mode": "custom",
        "bindings": [],
        "expected_binding_revision": 0,
    }

    def stop_before_audit(_events):
        raise OSError("stop after binding commit")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_append_events_idempotent", stop_before_audit)
        with pytest.raises(OSError, match="stop after binding commit"):
            store.execute(
                [command], actor="user", surface="team_ui",
                command_key="atomic-binding", expected_revision=initial["revision"],
            )
    committed = store.read()
    assert committed["skill_bindings"]["role:qa"]["binding_revision"] == 1
    assert len(committed["pending_events"]) == 1
    assert store.events_path.read_bytes() == previous
    return store, committed, previous, command, initial["revision"]


def assert_recovered_once(store, committed, command, initial_revision):
    recovered = store.reconcile_pending_audit()
    assert recovered == {**committed, "pending_events": []}
    assert store.reconcile_pending_audit() == recovered
    binding_events = [event for event in store.read_events() if event["event"] == "role_skills.changed"]
    assert binding_events == committed["pending_events"]
    replay = store.execute(
        [command], actor="user", surface="team_ui",
        command_key="atomic-binding", expected_revision=initial_revision,
    )
    assert replay["replayed"] is True
    assert replay["events"] == binding_events
    assert store.read() == recovered
    assert len([event for event in store.read_events() if event["event"] == "role_skills.changed"]) == 1


def test_partial_temporary_write_preserves_visible_ledger_and_pending_binding(tmp_path, monkeypatch):
    store, committed, previous, command, initial_revision = committed_binding(tmp_path, monkeypatch)
    real_temporary = tcp.NamedTemporaryFile
    interrupted = []

    class PartialWriter:
        def __init__(self, handle):
            self.handle = handle
            self.name = handle.name

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, data):
            self.handle.write(data[:len(data) // 2])
            self.handle.flush()
            interrupted.append(True)
            raise OSError("partial temporary write")

    def torn_temporary(*args, **kwargs):
        handle = real_temporary(*args, **kwargs)
        return PartialWriter(handle) if kwargs.get("prefix") == ".team-events-" else handle

    with monkeypatch.context() as patch:
        patch.setattr(tcp, "NamedTemporaryFile", torn_temporary)
        with pytest.raises(OSError, match="partial temporary write"):
            store.reconcile_pending_audit()
    assert interrupted == [True]
    assert store.events_path.read_bytes() == previous
    assert store.read() == committed
    assert not list(store.state_dir.glob(".team-events-*"))
    assert_recovered_once(store, committed, command, initial_revision)


@pytest.mark.parametrize("phase", ["before", "after"])
def test_interrupted_publication_recovers_without_reapplying_binding(tmp_path, monkeypatch, phase):
    store, committed, previous, command, initial_revision = committed_binding(tmp_path, monkeypatch)
    real_replace = tcp.os.replace

    def interrupted_replace(source, destination):
        if Path(destination) == store.events_path:
            if phase == "after":
                real_replace(source, destination)
            raise OSError(f"{phase} ledger replace")
        return real_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(tcp.os, "replace", interrupted_replace)
        with pytest.raises(OSError, match=f"{phase} ledger replace"):
            store.reconcile_pending_audit()
    assert store.read() == committed
    if phase == "before":
        assert store.events_path.read_bytes() == previous
    else:
        assert store.events_path.read_bytes().startswith(previous)
        assert store.read_events()[-1] == committed["pending_events"][0]
    assert_recovered_once(store, committed, command, initial_revision)


def test_repeated_directory_sync_failure_does_not_clear_pending_on_deduplication(tmp_path, monkeypatch):
    store, committed, _previous, command, initial_revision = committed_binding(tmp_path, monkeypatch)
    real_fsync = tcp.os.fsync

    def fail_directory_sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory sync interrupted")
        return real_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(tcp.os, "fsync", fail_directory_sync)
        for _attempt in range(2):
            with pytest.raises(OSError, match="directory sync interrupted"):
                store.reconcile_pending_audit()
            assert store.read() == committed
            matches = [event for event in store.read_events() if event["event"] == "role_skills.changed"]
            assert matches == committed["pending_events"]
    assert_recovered_once(store, committed, command, initial_revision)


def test_complete_nonterminated_tail_is_preserved_and_separated(tmp_path):
    store = tcp.TeamControlPlaneStore(tmp_path / "workspace", "DEMO")
    store.state_dir.mkdir(parents=True)
    previous = b'{ "id": "evt-old", "event": "existing" }'
    store.events_path.write_bytes(previous)
    event = {"id": "evt-new", "event": "role_skills.changed", "reason": "自定义方法"}
    with store._locked():
        store._append_events_idempotent([event, event])
    assert store.events_path.read_bytes().startswith(previous + b"\n")
    assert store.read_events() == [json.loads(previous), event]
    with store._locked():
        store._append_events_idempotent([event])
    assert store.read_events() == [json.loads(previous), event]


def test_unrelated_existing_corruption_is_not_silently_truncated(tmp_path, monkeypatch):
    store, committed, previous, _command, _revision = committed_binding(tmp_path, monkeypatch)
    corrupt = previous + b'{"id":"unrelated-torn-record"'
    store.events_path.write_bytes(corrupt)
    with pytest.raises(tcp.TeamControlPlaneError, match="ledger is corrupt"):
        store.reconcile_pending_audit()
    assert store.events_path.read_bytes() == corrupt
    assert store.read() == committed


def test_nonbinding_mutation_keeps_legacy_append_path(tmp_path, monkeypatch):
    store = tcp.TeamControlPlaneStore(tmp_path / "workspace", "DEMO")
    state = store.load_or_migrate(legacy_target={"worker_backend": {"mode": "fake"}})

    def binding_only_path(_events):
        raise AssertionError("ordinary Team commands must not use binding audit publication")

    monkeypatch.setattr(store, "_append_events_idempotent", binding_only_path)
    outcome = store.execute(
        [{"kind": "disable_role", "role": "qa"}], actor="user", surface="team_ui",
        command_key="ordinary-team-command", expected_revision=state["revision"],
    )
    assert outcome["changed"] is True
    assert store.read()["roles"]["qa"]["state"] == "disabled"
    assert store.read()["pending_events"] == []
