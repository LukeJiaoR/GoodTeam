"""Correction-safe machine context; all fixtures are synthetic and local."""
import hashlib
import json

import pytest

from goodteam_os.memory import RoleMemoryStore
from goodteam_os.workers import WorkerJobStore


@pytest.fixture(autouse=True)
def offline_memory(monkeypatch, tmp_path, tmp_path_factory):
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path_factory.mktemp("operator")))
    monkeypatch.setenv("GOODTEAM_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)


def append(store, text, horizon="long-term", **kwargs):
    return store.append_memory_event("architect", horizon=horizon, text=text, source="synthetic", **kwargs)


def worker(store, run="test"):
    job = store.create_job(run_id=run, role="architect", adapter="noop", task="database migration", context="")
    return job, store.root / job["id"]


@pytest.mark.parametrize("horizon", ["long-term", "mid-term", "short-term"])
def test_correction_generates_safe_context_without_rewriting_legacy_notes(tmp_path, horizon):
    store = RoleMemoryStore(tmp_path)
    old = append(store, "database OLD_RULE", horizon)
    append(store, "database KEEP_VALID", horizon)
    append(store, "database NEW_RULE", horizon, supersedes=store._event_id(old))
    legacy = store.memory_path("architect", horizon)
    with legacy.open("a") as stream:
        stream.write("\n# Handwritten\n- KEEP_ORIGINAL_HANDWRITTEN_NOTE\n")
    original = legacy.read_bytes()
    packet = store.build_pickup_packet("architect", query="database")
    assert "OLD_RULE" not in json.dumps(packet)
    assert "KEEP_VALID" in json.dumps(packet) and "NEW_RULE" in json.dumps(packet)
    assert packet["context_status"] == "partial"
    assert "legacy_mixed_after_correction" in packet["context_reasons"]
    assert legacy.read_bytes() == original
    assert packet["provenance"]["wal_sha256"] == hashlib.sha256(store.event_path("architect").read_bytes()).hexdigest()
    assert not packet["provenance"]["legacy_sources"][horizon]["path"].startswith("/")
    workers = WorkerJobStore(tmp_path)
    job, root = worker(workers)
    prompt = workers._worker_prompt(root, job)
    assert "OLD_RULE" not in prompt and "KEEP_VALID" in prompt and "NEW_RULE" in prompt
    assert "Memory context: partial" in prompt and "legacy_mixed_after_correction" in prompt


def test_frozen_packet_filters_invalidated_ids_without_introducing_new_facts(tmp_path):
    store = RoleMemoryStore(tmp_path)
    old = append(store, "database OLD_RULE")
    keep = append(store, "database KEEP_VALID")
    workers = WorkerJobStore(tmp_path)
    job, root = worker(workers)
    original = (root / "role_pickup_packet.json").read_bytes()
    append(store, "database NEW_RULE", supersedes=store._event_id(old))
    prompt = workers._worker_prompt(root, job)
    assert "OLD_RULE" not in prompt and "NEW_RULE" not in prompt
    assert "KEEP_VALID" in prompt
    assert "Memory context: partial" in prompt
    assert "wal_changed_after_capture" in prompt
    assert (root / "role_pickup_packet.json").read_bytes() == original
    assert workers._memory_citations_from_pickup_packet(root) == [store._event_id(keep)]


def test_invalid_wal_does_not_fall_back_to_cached_context(tmp_path):
    store = RoleMemoryStore(tmp_path)
    append(store, "database OLD_RULE")
    workers = WorkerJobStore(tmp_path)
    job, root = worker(workers)
    with store.event_path("architect").open("a") as stream:
        stream.write('{"broken":')
    packet = store.build_pickup_packet("architect", query="database")
    assert packet["context_status"] == "unavailable"
    assert "wal_unreadable" in packet["context_reasons"]
    assert "OLD_RULE" not in json.dumps(packet)
    prompt = workers._worker_prompt(root, job)
    assert "OLD_RULE" not in prompt and "Memory context: unavailable" in prompt


def test_invalid_index_cannot_block_generated_context_or_destroy_handwritten_notes(tmp_path):
    store = RoleMemoryStore(tmp_path)
    old = append(store, "database OLD_RULE")
    append(store, "database NEW_RULE", supersedes=store._event_id(old))
    store.index_path("architect").write_text("{")
    legacy = store.memory_path("architect", "long-term")
    original = legacy.read_bytes()
    packet = store.build_pickup_packet("architect", query="database")
    assert "NEW_RULE" in json.dumps(packet) and "OLD_RULE" not in json.dumps(packet)
    assert legacy.read_bytes() == original
    assert store.index_path("architect").read_text() == "{"


def test_new_wal_fact_does_not_rewrite_a_frozen_job(tmp_path):
    store = RoleMemoryStore(tmp_path)
    append(store, "database KEEP_VALID")
    workers = WorkerJobStore(tmp_path)
    job, root = worker(workers)
    append(store, "database NEW_AFTER_CAPTURE")
    prompt = workers._worker_prompt(root, job)
    assert "KEEP_VALID" in prompt and "NEW_AFTER_CAPTURE" not in prompt
    assert "wal_changed_after_capture" in prompt


def test_source_edit_invalidates_frozen_legacy_summary(tmp_path):
    store = RoleMemoryStore(tmp_path)
    paths = store.ensure_role_memory("architect")
    paths["long-term"].write_text("# Notes\n- OLD_HANDWRITTEN_FACT\n")
    workers = WorkerJobStore(tmp_path)
    job, root = worker(workers)
    paths["long-term"].write_text("# Notes\n- NEW_HANDWRITTEN_FACT\n")
    prompt = workers._worker_prompt(root, job)
    assert "OLD_HANDWRITTEN_FACT" not in prompt and "NEW_HANDWRITTEN_FACT" not in prompt
    assert "legacy_changed_after_capture" in prompt


@pytest.mark.parametrize("corrupt", [[], {"role_key": None}, {"role_key": "architect", "context_reasons": [{}], "relevant_events": [{"id": []}]}])
def test_valid_json_but_invalid_pickup_is_explicitly_unavailable_or_partial(tmp_path, corrupt):
    workers = WorkerJobStore(tmp_path)
    job, root = worker(workers)
    (root / "role_pickup_packet.json").write_text(json.dumps(corrupt))
    prompt = workers._worker_prompt(root, job)
    assert "pickup_unverifiable" in prompt


def test_valid_wal_survives_projection_rebuild_failure(tmp_path, monkeypatch):
    store = RoleMemoryStore(tmp_path)
    old = append(store, "database OLD_RULE")
    append(store, "database NEW_RULE", supersedes=store._event_id(old))
    def fail(*args, **kwargs):
        raise OSError("synthetic unavailable projection; must not reach prompt")
    monkeypatch.setattr(store, "rebuild_role_memory", fail)
    packet = store.build_pickup_packet("architect", query="database")
    assert "NEW_RULE" in json.dumps(packet) and "OLD_RULE" not in json.dumps(packet)


def test_retrieval_failure_keeps_bounded_generated_summaries_and_stable_reason(tmp_path, monkeypatch):
    store = RoleMemoryStore(tmp_path)
    append(store, "database KEEP_VALID")
    def fail(*args, **kwargs):
        raise OSError("/private/synthetic/path should never be disclosed")
    monkeypatch.setattr(store, "_rank_events", fail)
    packet = store.build_pickup_packet("architect", query="database")
    assert packet["context_status"] == "partial"
    assert "retrieval_unavailable" in packet["context_reasons"]
    assert packet["relevant_events"] == []
    assert "KEEP_VALID" in packet["summaries"]["long-term"]
    assert "/private/synthetic" not in json.dumps(packet)


def test_generated_prompt_context_has_hard_bounds_and_dependency_ids(tmp_path):
    store = RoleMemoryStore(tmp_path)
    for horizon in ("long-term", "mid-term", "short-term"):
        for number in range(14):
            append(store, f"database {number} " + "中" * 2200, horizon)
    packet = store.build_pickup_packet("architect", query="database", top_k=100)
    assert all(len(summary) <= 600 and len(summary.encode()) <= 2400 for summary in packet["summaries"].values())
    assert len(packet["short_term"]) <= 1600 and len(packet["short_term"].encode()) <= 6400
    assert sum(len(event["text"]) for event in packet["relevant_events"]) <= 4000
    assert all(len(event["text"]) <= 1600 for event in packet["relevant_events"])
    assert len(packet["provenance"]["generated_sources"]["long-term"]["event_ids"]) == 12
    assert store.validate_pickup_packet(packet)["relevant_events"] == packet["relevant_events"]


def test_cross_process_append_during_capture_returns_unavailable(tmp_path, monkeypatch):
    import subprocess
    import sys
    store = RoleMemoryStore(tmp_path)
    append(store, "database SNAPSHOT_BEFORE")
    original = store._rank_events
    def append_while_ranking(*args, **kwargs):
        subprocess.run([sys.executable, "-c", "from pathlib import Path; import sys; from goodteam_os.memory import RoleMemoryStore; RoleMemoryStore(Path(sys.argv[1])).append_memory_event('architect', horizon='long-term', text='database SNAPSHOT_AFTER', source='process-test')", str(tmp_path)], check=True)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "_rank_events", append_while_ranking)
    packet = store.build_pickup_packet("architect", query="database")
    assert packet["context_status"] == "unavailable"
    assert packet["context_reasons"] == ["wal_changed_during_read"]
    assert "SNAPSHOT_BEFORE" not in json.dumps(packet) and "SNAPSHOT_AFTER" not in json.dumps(packet)
    assert len(store._read_events("architect")) == 2


def test_overview_exposes_correction_context_status(tmp_path):
    from goodteam_os.web import build_memory_overview
    store = RoleMemoryStore(tmp_path)
    old = append(store, "database OLD_RULE")
    append(store, "database NEW_RULE", supersedes=store._event_id(old))
    role = build_memory_overview(tmp_path, "SYNTHETIC")["roles"][0]
    assert role["context_status"] == "partial"
    assert "legacy_mixed_after_correction" in role["context_reasons"]
    assert "OLD_RULE" not in json.dumps(role)


def test_cross_process_correction_during_worker_consumption_is_visible(tmp_path, monkeypatch):
    import subprocess
    import sys
    memory = RoleMemoryStore(tmp_path)
    old = append(memory, "database OLD_DURING_READ")
    workers = WorkerJobStore(tmp_path)
    job, root = worker(workers)
    original = RoleMemoryStore._wal_snapshot_matches
    def correct_during_validation(self, role, digest):
        subprocess.run([sys.executable, "-c", "from pathlib import Path; import sys; from goodteam_os.memory import RoleMemoryStore; RoleMemoryStore(Path(sys.argv[1])).append_memory_event('architect', horizon='long-term', text='database NEW_DURING_READ', source='process-test', supersedes=sys.argv[2])", str(tmp_path), memory._event_id(old)], check=True)
        return original(self, role, digest)
    monkeypatch.setattr(RoleMemoryStore, "_wal_snapshot_matches", correct_during_validation)
    prompt = workers._worker_prompt(root, job)
    assert "OLD_DURING_READ" not in prompt and "NEW_DURING_READ" not in prompt
    assert "Memory context: unavailable" in prompt and "wal_changed_during_read" in prompt


def test_unversioned_packet_retains_only_matching_active_bodies(tmp_path):
    memory = RoleMemoryStore(tmp_path)
    first = append(memory, "database VALID_FACT")
    other = append(memory, "database OTHER_FACT")
    workers = WorkerJobStore(tmp_path)
    job, root = worker(workers)
    path = root / "role_pickup_packet.json"
    path.write_text(json.dumps({"role_key": "architect", "summaries": {"long-term": "UNPROVEN_OLD_SUMMARY"},
                               "relevant_events": [{"id": memory._event_id(first), "text": first["text"]},
                                                   {"id": memory._event_id(other), "text": "FORGED_BODY"}]}))
    original = path.read_bytes()
    prompt = workers._worker_prompt(root, job)
    assert "VALID_FACT" in prompt and "FORGED_BODY" not in prompt and "UNPROVEN_OLD_SUMMARY" not in prompt
    assert "pickup_unverifiable" in prompt
    assert path.read_bytes() == original


@pytest.mark.parametrize("bad_line", ['{"text":"missing fields"}', 'null', '{"text": ["not a string"]}'])
def test_malformed_wal_schema_is_unavailable_without_exception_details(tmp_path, bad_line):
    memory = RoleMemoryStore(tmp_path)
    append(memory, "database SECRET_OLD_BODY")
    with memory.event_path("architect").open("a") as stream:
        stream.write(bad_line + "\n")
    packet = memory.build_pickup_packet("architect", query="database")
    assert packet["context_status"] == "unavailable"
    assert packet["context_reasons"] == ["wal_unreadable"]
    assert "SECRET_OLD_BODY" not in json.dumps(packet)


def test_correction_chain_retains_only_final_valid_fact(tmp_path):
    memory = RoleMemoryStore(tmp_path)
    first = append(memory, "database RULE_FIRST")
    second = append(memory, "database RULE_SECOND", "mid-term", supersedes=memory._event_id(first))
    append(memory, "database RULE_FINAL", "short-term", supersedes=memory._event_id(second))
    packet = memory.build_pickup_packet("architect", query="database")
    assert "RULE_FIRST" not in json.dumps(packet) and "RULE_SECOND" not in json.dumps(packet)
    assert "RULE_FINAL" in json.dumps(packet)
    assert len(memory.list_events("architect")) == 3  # history is still inspectable


@pytest.mark.parametrize("raw", [b"{", b"\xff"])
def test_unreadable_pickup_is_explicit_in_real_worker_prompt(tmp_path, raw):
    workers = WorkerJobStore(tmp_path)
    job, root = worker(workers)
    (root / "role_pickup_packet.json").write_bytes(raw)
    prompt = workers._worker_prompt(root, job)
    assert "Memory context: unavailable" in prompt and "pickup_unreadable" in prompt


@pytest.mark.parametrize("loss", ["empty", "whitespace", "missing", "directory"])
def test_lost_wal_never_resurrects_corrected_legacy_context(tmp_path, loss):
    from goodteam_os.web import build_memory_overview
    memory = RoleMemoryStore(tmp_path)
    old = append(memory, "database OLD_RULE")
    append(memory, "database NEW_RULE", supersedes=memory._event_id(old))
    workers = WorkerJobStore(tmp_path)
    frozen_job, frozen_root = worker(workers, "frozen")
    original_legacy = memory.memory_path("architect", "long-term").read_bytes()
    wal = memory.event_path("architect")
    if loss in {"empty", "whitespace"}:
        wal.write_text("" if loss == "empty" else "\n \n")
    else:
        wal.unlink()
        if loss == "directory":
            wal.mkdir()
    packet = memory.build_pickup_packet("architect", query="database")
    assert packet["context_status"] == "unavailable"
    assert packet["context_reasons"] == ["wal_unreadable"]
    assert "OLD_RULE" not in json.dumps(packet) and "NEW_RULE" not in json.dumps(packet)
    next_job, next_root = worker(workers, "new")
    for job, root in [(frozen_job, frozen_root), (next_job, next_root)]:
        prompt = workers._worker_prompt(root, job)
        assert "OLD_RULE" not in prompt and "NEW_RULE" not in prompt
        assert "Memory context: unavailable" in prompt
    overview = build_memory_overview(tmp_path, "SYNTHETIC")
    role = overview["roles"][0]
    assert role["context_status"] == "unavailable"
    assert "OLD_RULE" not in json.dumps(role["summaries"])
    assert memory.memory_path("architect", "long-term").read_bytes() == original_legacy


def test_generated_markdown_is_loss_evidence_when_index_is_also_missing(tmp_path):
    memory = RoleMemoryStore(tmp_path)
    old = append(memory, "database OLD_RULE")
    append(memory, "database NEW_RULE", supersedes=memory._event_id(old))
    memory.event_path("architect").unlink()
    memory.index_path("architect").unlink()
    packet = memory.build_pickup_packet("architect")
    assert packet["context_status"] == "unavailable"
    assert "OLD_RULE" not in json.dumps(packet)


def test_frozen_digest_is_loss_evidence_without_index_or_markdown(tmp_path):
    memory = RoleMemoryStore(tmp_path)
    append(memory, "database OLD_RULE")
    packet = memory.build_pickup_packet("architect", query="database")
    memory.event_path("architect").unlink()
    memory.index_path("architect").unlink()
    for horizon in ("long-term", "mid-term", "short-term"):
        memory.memory_path("architect", horizon).unlink()
    consumed = memory.validate_pickup_packet(packet)
    assert consumed["context_status"] == "unavailable"
    assert "OLD_RULE" not in json.dumps(consumed)


@pytest.mark.parametrize("empty_index", [False, True])
def test_genuinely_legacy_only_notes_remain_available(tmp_path, empty_index):
    memory = RoleMemoryStore(tmp_path)
    paths = memory.ensure_role_memory("architect")
    paths["long-term"].write_text("# Handwritten\n- KEEP_LEGACY_ONLY\n")
    if empty_index:
        memory.refresh_memory_index("architect")
    packet = memory.build_pickup_packet("architect")
    assert packet["context_status"] == "available"
    assert "KEEP_LEGACY_ONLY" in packet["summaries"]["long-term"]


@pytest.mark.parametrize("rewrite", [False, True])
def test_known_wal_prefix_cannot_be_rolled_back_or_rewritten(tmp_path, rewrite):
    from goodteam_os.web import build_memory_overview
    memory = RoleMemoryStore(tmp_path)
    old = append(memory, "database OLD_RULE")
    append(memory, "database NEW_RULE", supersedes=memory._event_id(old))
    workers = WorkerJobStore(tmp_path)
    frozen_job, frozen_root = worker(workers, "frozen")
    wal = memory.event_path("architect")
    original = wal.read_bytes()
    wal.write_bytes(original.replace(b"NEW_RULE", b"BAD_RULE") if rewrite else original.splitlines(keepends=True)[0])
    packet = memory.build_pickup_packet("architect", query="database")
    assert packet["context_status"] == "unavailable"
    next_job, next_root = worker(workers, "new")
    for job, root in [(frozen_job, frozen_root), (next_job, next_root)]:
        prompt = workers._worker_prompt(root, job)
        assert "OLD_RULE" not in prompt and "BAD_RULE" not in prompt
        assert "Memory context: unavailable" in prompt
    role = build_memory_overview(tmp_path, "SYNTHETIC")["roles"][0]
    assert role["context_status"] == "unavailable"


def test_older_index_checkpoint_allows_genuine_append(tmp_path):
    memory = RoleMemoryStore(tmp_path)
    append(memory, "database PREVIOUS_VALID")
    index = memory.index_path("architect").read_bytes()
    append(memory, "database APPENDED_VALID")
    memory.index_path("architect").write_bytes(index)  # WAL committed before the newer index.
    packet = memory.build_pickup_packet("architect", query="database")
    assert packet["context_status"] == "available"
    assert "PREVIOUS_VALID" in json.dumps(packet) and "APPENDED_VALID" in json.dumps(packet)
    assert memory.index_path("architect").read_bytes() == index


@pytest.mark.parametrize("status", [None, "unknown", "unavailable", "withheld_after_correction"])
def test_source_manifest_and_body_are_bound_together(tmp_path, status):
    memory = RoleMemoryStore(tmp_path)
    valid = append(memory, "database RETAIN_VALID")
    path = memory.memory_path("architect", "long-term")
    path.write_text("# Notes\n- OLD_LEGACY\n")
    packet = memory.build_pickup_packet("architect", query="database")
    source = packet["provenance"]["legacy_sources"]["long-term"]
    if status is None:
        source.pop("status")
    else:
        source["status"] = status
    path.write_text("# Notes\n- NEW_LEGACY\n")
    consumed = memory.validate_pickup_packet(packet)
    assert consumed["context_status"] == "partial"
    assert "pickup_unverifiable" in consumed["context_reasons"]
    assert consumed["summaries"] == {"long-term": "", "mid-term": ""}
    assert [event["id"] for event in consumed["relevant_events"]] == [memory._event_id(valid)]
