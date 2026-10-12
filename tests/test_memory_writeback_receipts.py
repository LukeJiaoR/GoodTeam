"""Synthetic coverage for Worker WAL observations; no provider or daemon."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from goodteam_os import memory
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.memory_writeback import MAX_RECEIPT_OBSERVATIONS, write_back_events


ROLE = "implementation"
SOURCE = "job-one/result.md"
RESULT = b"# Result\r\n\r\n## Learned\r\n- SECRET_CONTENT_marker\r\n"
ITEMS = [("mid-term", "SECRET_CONTENT_marker")]


def write(store, *, items=None, source=SOURCE, result_bytes=RESULT, include_parsed=True):
    return write_back_events(store, ROLE, items=ITEMS if items is None else items,
                             source=source, result_bytes=result_bytes, include_parsed=include_parsed)


def assert_private(receipt, tmp_path):
    serialized = json.dumps(receipt)
    for forbidden in ("SECRET_CONTENT_marker", "SECRET_EXCEPTION_marker", "result.md", "task.md",
                      "provenance", str(tmp_path), SOURCE):
        assert forbidden not in serialized
    assert receipt["result_sha256"] == hashlib.sha256(RESULT).hexdigest()
    assert datetime.fromisoformat(receipt["observed_at"]).utcoffset().total_seconds() == 0


def test_observes_actual_returned_ids_exact_bytes_and_time(tmp_path, monkeypatch):
    store = RoleMemoryStore(tmp_path)
    returned = []
    original = store.append_memory_event

    def append(*args, **kwargs):
        event = original(*args, **kwargs)
        returned.append(event)
        return event

    monkeypatch.setattr(store, "append_memory_event", append)
    start = datetime.now(UTC)
    status, receipt = write(store)
    end = datetime.now(UTC)

    assert status == {"status": "ok", "source": SOURCE, "parsed": 1, "appended": 1, "skipped_duplicates": 0}
    assert receipt["new_event_ids"] == [store._event_id(returned[0])]
    assert receipt["observations"][0]["event_timestamp"] == returned[0]["timestamp"]
    assert receipt["outcome"] == "completed"
    assert receipt["counts_complete"] is True
    assert receipt["observations"][0]["projection"] == "complete"
    assert start <= datetime.fromisoformat(receipt["observed_at"]) <= end
    assert_private(receipt, tmp_path)


def test_empty_parse_preserves_status_and_does_not_initialize_memory(tmp_path):
    store = RoleMemoryStore(tmp_path)
    status, receipt = write(store, items=[])
    assert status == {"status": "skipped", "reason": "no_sections", "source": SOURCE,
                      "parsed": 0, "appended": 0, "skipped_duplicates": 0}
    assert receipt["outcome"] == "skipped"
    assert receipt["counts_complete"] is True
    assert not store.role_root(ROLE).exists()
    assert_private(receipt, tmp_path)


def test_same_job_replay_retains_own_ids_without_append_or_projection_repair(tmp_path, monkeypatch):
    store = RoleMemoryStore(tmp_path)
    _, first = write(store)
    wal = store.event_path(ROLE).read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("Replay must not append or repair derived files")

    monkeypatch.setattr(store, "append_memory_event", forbidden)
    monkeypatch.setattr(store, "rebuild_role_memory", forbidden)
    status, receipt = write(store)
    assert status == {"status": "skipped", "source": SOURCE, "parsed": 1, "appended": 0,
                      "skipped_duplicates": 1}
    assert receipt["replayed_event_ids"] == first["new_event_ids"]
    assert receipt["new_event_ids"] == receipt["duplicate_event_ids"] == []
    assert receipt["observations"][0]["outcome"] == "replayed"
    assert receipt["observations"][0]["event_timestamp"] == first["observations"][0]["event_timestamp"]
    assert datetime.fromisoformat(receipt["observations"][0]["event_timestamp"]) < datetime.fromisoformat(receipt["observed_at"])
    assert store.event_path(ROLE).read_bytes() == wal


def test_foreign_source_text_dedup_never_attributes_existing_id_to_new_job(tmp_path):
    store = RoleMemoryStore(tmp_path)
    original = store.append_memory_event(ROLE, horizon="long-term", text=ITEMS[0][1], source="job-other/result.md")
    status, receipt = write(store)
    assert status["appended"] == 0
    assert status["skipped_duplicates"] == 1
    assert receipt["deduplicated"] == 1
    assert receipt["duplicate_event_ids"] == [store._event_id(original)]
    assert receipt["replayed_event_ids"] == receipt["new_event_ids"] == []
    assert receipt["observations"][0]["outcome"] == "deduplicated"
    assert receipt["observations"][0]["relation"] == "other_source_text_dedup"
    assert receipt["observations"][0]["requested_horizon"] == "mid-term"
    assert receipt["observations"][0]["event_horizon"] == "long-term"
    assert len(store._read_events(ROLE)) == 1
    assert store._read_events(ROLE)[0]["source"] == "job-other/result.md"


def test_same_job_replay_prefers_own_source_over_earlier_foreign_text(tmp_path):
    store = RoleMemoryStore(tmp_path)
    foreign = store.append_memory_event(ROLE, horizon="mid-term", text=ITEMS[0][1], source="job-other/result.md")
    own = store.append_memory_event(ROLE, horizon="mid-term", text=ITEMS[0][1], source=SOURCE)
    _, receipt = write(store)
    assert receipt["replayed_event_ids"] == [store._event_id(own)]
    assert store._event_id(foreign) not in receipt["replayed_event_ids"]
    assert receipt["duplicate_event_ids"] == receipt["new_event_ids"] == []


def test_same_batch_duplicate_retains_original_horizon_and_counts(tmp_path):
    store = RoleMemoryStore(tmp_path)
    status, receipt = write(store, items=[*ITEMS, ("short-term", ITEMS[0][1])])
    assert status["appended"] == status["skipped_duplicates"] == 1
    assert receipt["new_event_ids"] == receipt["replayed_event_ids"]
    assert store._read_events(ROLE)[0]["horizon"] == "mid-term"
    assert receipt["observations"][1]["relation"] == "same_source_text_replay"
    assert receipt["observations"][1]["requested_horizon"] == "short-term"
    assert receipt["observations"][1]["event_horizon"] == "mid-term"


@pytest.mark.parametrize("failure_boundary", ["before_wal", "after_wal", "checkpoint", "projection"])
def test_failure_boundaries_retain_exact_commit_facts(tmp_path, monkeypatch, failure_boundary):
    store = RoleMemoryStore(tmp_path)
    real_atomic = memory.atomic_write
    real_checkpoint = store._write_checkpoint

    def atomic(path, data, **kwargs):
        if path == store.event_path(ROLE):
            if failure_boundary == "before_wal":
                raise OSError(f"SECRET_EXCEPTION_marker {tmp_path}")
            real_atomic(path, data, **kwargs)
            if failure_boundary == "after_wal":
                raise OSError(f"SECRET_EXCEPTION_marker {tmp_path}")
            return
        return real_atomic(path, data, **kwargs)

    def checkpoint(role, events, raw):
        if events and failure_boundary == "checkpoint":
            raise OSError(f"SECRET_EXCEPTION_marker {tmp_path}")
        return real_checkpoint(role, events, raw)

    def projection(*args, **kwargs):
        raise OSError(f"SECRET_EXCEPTION_marker {tmp_path}")

    monkeypatch.setattr(memory, "atomic_write", atomic)
    monkeypatch.setattr(store, "_write_checkpoint", checkpoint)
    if failure_boundary == "projection":
        monkeypatch.setattr(store, "rebuild_role_memory", projection)
    status, receipt = write(store)

    assert status["status"] == "error"
    assert status["error"] == "memory_writeback_incomplete"
    assert receipt["attempted"] == 1
    assert receipt["counts_complete"] is True
    if failure_boundary == "before_wal":
        assert status["appended"] == 0
        assert receipt["new_event_ids"] == []
        assert receipt["failure_outcome"] == "known_not_committed"
    else:
        committed = json.loads(store.event_path(ROLE).read_bytes())
        assert status["appended"] == 1
        assert receipt["new_event_ids"] == [store._event_id(committed)]
        assert receipt["failure_outcome"] == "committed"
        assert receipt["observations"][0]["projection"] == "unobserved"
    assert_private(receipt, tmp_path)


def test_checkpoint_corruption_after_commit_does_not_erase_wal_observation(tmp_path, monkeypatch):
    store = RoleMemoryStore(tmp_path)
    original = store._write_checkpoint

    def checkpoint(role, events, raw):
        if events:
            store.checkpoint_path(role).write_bytes(b"invalid checkpoint")
            raise OSError("SECRET_EXCEPTION_marker")
        return original(role, events, raw)

    monkeypatch.setattr(store, "_write_checkpoint", checkpoint)
    status, receipt = write(store)
    assert status["appended"] == 1
    assert receipt["failure_outcome"] == "committed"
    with pytest.raises(ValueError):
        store._wal_snapshot(ROLE)
    assert_private(receipt, tmp_path)


def test_unknown_wal_outcome_is_explicit_and_never_retried(tmp_path, monkeypatch):
    store = RoleMemoryStore(tmp_path)
    attempts = []
    original = store.append_memory_event

    def append(*args, **kwargs):
        attempts.append(1)
        original(*args, **kwargs)
        # Uncooperative external modification cannot be claimed as our exact
        # append even though a matching event appears earlier in the bytes.
        with store.event_path(ROLE).open("ab") as handle:
            handle.write(b"unknown suffix")
        raise OSError("SECRET_EXCEPTION_marker")

    monkeypatch.setattr(store, "append_memory_event", append)
    status, receipt = write(store, items=[*ITEMS, ("long-term", "second item")])
    assert len(attempts) == 1
    assert status["status"] == "error"
    assert receipt["outcome"] == receipt["failure_outcome"] == "unknown"
    assert receipt["counts_complete"] is False
    assert receipt["observations"][0]["outcome"] == "unknown"
    assert receipt["unattempted"] == 1
    assert receipt["new_event_ids"] == []
    assert_private(receipt, tmp_path)


def test_partial_batch_preserves_prior_commit_and_stops_on_failed_append(tmp_path, monkeypatch):
    store = RoleMemoryStore(tmp_path)
    original = store.append_memory_event
    attempts = []

    def append(*args, **kwargs):
        attempts.append(kwargs["text"])
        if len(attempts) == 2:
            raise OSError("SECRET_EXCEPTION_marker")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "append_memory_event", append)
    status, receipt = write(store, items=[*ITEMS, ("long-term", "second"), ("short-term", "third")])
    assert len(attempts) == 2
    assert status["appended"] == 1
    assert receipt["outcome"] == "partial"
    assert receipt["failure_outcome"] == "known_not_committed"
    assert receipt["counts_complete"] is True
    assert receipt["unattempted"] == 1
    assert receipt["new_event_ids"] == [store._event_id(store._read_events(ROLE)[0])]


def test_unreadable_observation_preserves_prior_returned_event_id(tmp_path, monkeypatch):
    store = RoleMemoryStore(tmp_path)
    original_append = store.append_memory_event
    original_read = store._wal_bytes
    committed = []

    def append(*args, **kwargs):
        event = original_append(*args, **kwargs)
        committed.append(event)
        return event

    def read(role):
        if committed:
            raise OSError("SECRET_EXCEPTION_marker")
        return original_read(role)

    monkeypatch.setattr(store, "append_memory_event", append)
    monkeypatch.setattr(store, "_wal_bytes", read)
    status, receipt = write(store)
    assert status["appended"] == 1
    assert receipt["new_event_ids"] == [store._event_id(committed[0])]
    assert receipt["outcome"] == "unknown"
    assert receipt["counts_complete"] is False


def test_provenance_returns_full_event_identity_without_raw_provenance(tmp_path):
    store = RoleMemoryStore(tmp_path)
    source = "job-one/provenance"
    text = "Dispatched job one for task: SECRET_CONTENT_marker. Sources: task.md."
    status, receipt = write(store, items=[("short-term", text)], source=source, include_parsed=False)
    assert status == {"status": "ok", "source": source, "appended": 1, "skipped_duplicates": 0}
    assert receipt["new_event_ids"] == [store._event_id(store._read_events(ROLE)[0])]
    _, replay = write(store, items=[("short-term", text)], source=source, include_parsed=False)
    assert replay["replayed_event_ids"] == receipt["new_event_ids"]
    assert_private(receipt, tmp_path)


def test_receipt_persistence_failure_cannot_turn_replay_into_another_wal_write(tmp_path):
    store = RoleMemoryStore(tmp_path)
    _, first = write(store)
    # Persistence is owned by the handoff store, outside this helper. Losing
    # that receipt does not authorize an automatic append retry.
    with pytest.raises(OSError):
        raise OSError("synthetic receipt persistence failure")
    _, replay = write(store)
    assert replay["new_event_ids"] == []
    assert replay["replayed_event_ids"] == first["new_event_ids"]
    assert len(store._read_events(ROLE)) == 1


def test_receipt_bound_does_not_change_legacy_writing_budget(tmp_path):
    store = RoleMemoryStore(tmp_path)
    count = MAX_RECEIPT_OBSERVATIONS + 7
    status, receipt = write(store, items=ITEMS * count)
    assert status["parsed"] == count
    assert status["appended"] == 1
    assert status["skipped_duplicates"] == count - 1
    assert len(receipt["observations"]) == MAX_RECEIPT_OBSERVATIONS
    assert receipt["omitted_observations"] == 7
    assert receipt["replayed"] == count - 1
    assert len(receipt["observations_sha256"]) == 64
    assert receipt["counts_complete"] is True


def test_concurrent_jobs_serialize_text_dedup_with_commit(tmp_path):
    def run(source):
        return write(RoleMemoryStore(tmp_path), source=source)

    with ThreadPoolExecutor(max_workers=2) as executor:
        pairs = list(executor.map(run, ["job-one/result.md", "job-two/result.md"]))
    assert sorted(status["appended"] for status, _ in pairs) == [0, 1]
    appended = next(receipt for _, receipt in pairs if receipt["appended"])
    dedup = next(receipt for _, receipt in pairs if not receipt["appended"])
    assert dedup["duplicate_event_ids"] == appended["new_event_ids"]
    assert dedup["replayed_event_ids"] == []
    assert len(RoleMemoryStore(tmp_path)._read_events(ROLE)) == 1


@pytest.mark.parametrize("timestamp, expected", [
    ("2020-01-02T03:04:05+05:30", "2020-01-02T03:04:05+05:30"),
    ("2020-01-02T03:04:05", None),
    ("SECRET_EXCEPTION_marker /private/path", None),
])
def test_replayed_event_timestamp_is_only_an_aware_parsed_date(tmp_path, monkeypatch, timestamp, expected):
    store = RoleMemoryStore(tmp_path)
    write(store)
    events, digest, raw = store._wal_snapshot(ROLE)
    events[0]["timestamp"] = timestamp
    monkeypatch.setattr(store, "_wal_snapshot", lambda role: (events, digest, raw))
    _, receipt = write(store)
    observation = receipt["observations"][0]
    assert observation["outcome"] == "replayed"
    assert observation.get("event_timestamp") == expected
    assert_private(receipt, tmp_path)
