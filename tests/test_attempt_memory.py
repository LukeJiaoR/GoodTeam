"""Exact-attempt public observations, with no model calls or read-time repairs."""
import json

import pytest

from goodteam_os.attempt_memory import DISPLAY_LIMIT, attempt_memory_observations
from goodteam_os.delivery_outcomes import build_delivery_summary
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.memory_handoff import MemoryHandoffStore, canonical_bytes, digest
from goodteam_os.memory_writeback import write_back_events
from goodteam_os.state import create_initial_state
from goodteam_os.workers import WorkerJobStore


@pytest.fixture
def context(tmp_path, monkeypatch):
    root = tmp_path / "target"
    root.mkdir()
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path / "operator"))
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    workers = WorkerJobStore(root)
    job = workers.create_job(run_id="run-one", project_key="DEMO", role="architect", adapter="noop",
                             task="PRIVATE_TASK", context="PRIVATE_CONTEXT")
    return root, workers, job, MemoryHandoffStore(root)


def project(context, **overrides):
    root, _, job, _ = context
    return attempt_memory_observations(root, project_key="DEMO", run_id="run-one", job={**job, **overrides})


def record(context, text="PRIVATE_MEMORY"):
    root, workers, job, store = context
    prompt = workers._worker_prompt(workers.root / job["id"], job)
    transport = store.transport(job, prompt.record)
    _, receipt = write_back_events(RoleMemoryStore(root), "architect", items=[("long-term", text)],
                                    source=job["id"], result_bytes=b"PRIVATE_RESULT")
    store.writeback(job, kind="result", receipt=receipt, prepared=prompt.record, transport_ref=transport)
    return receipt


def files(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


def rewrite_rows(store, job, change):
    root = store.job_root(job)
    rows = json.loads((root / "receipts.json").read_bytes())
    change(rows)
    raw = canonical_bytes(rows)
    (root / "receipts.json").write_bytes(raw)
    (root / "receipts-required.json").write_bytes(canonical_bytes({"count": len(rows), "digest": digest(raw)}))


def test_projects_real_preparation_and_writeback_without_private_content_or_read_claims(context):
    root, _, job, store = context
    receipt = record(context)
    before = files(root.parent)
    view = project(context)
    assert view["status"] == "observed"
    assert view["attempt_id"] == job["id"]
    assert view["preparation_count"] == view["transport_attempt_count"] == view["writeback_count"] == 1
    assert view["preparations"][0]["context_status"] == "available"
    assert view["writebacks"][0]["appended"] == receipt["appended"] == 1
    assert view["writebacks"][0]["observed_at"] == receipt["observed_at"]
    assert view["provider_receipt"] == view["provider_read"] == "unknown"
    assert view["capture"]["packet_digest"] == store.load(job)["packet_digest"]
    assert files(root.parent) == before
    raw = json.dumps(view)
    for forbidden in ("PRIVATE", str(root), "project_scope", "prepared_ref", "transport_ref"):
        assert forbidden not in raw


def test_capture_alone_and_builtin_writeback_do_not_invent_preparation_or_transport(context):
    _, workers, job, _ = context
    captured = project(context)
    assert captured["status"] == "observed" and captured["preparation_count"] == 0
    assert captured["writeback_count"] == captured["transport_attempt_count"] == 0
    workers.run_job(job["id"])
    completed = project(context)
    assert completed["preparation_count"] == completed["transport_attempt_count"] == 0
    assert {item["kind"] for item in completed["writebacks"]} == {"result", "provenance"}
    assert completed["provider_read"] == "unknown"


@pytest.mark.parametrize("identifier", ["job-historical", "job-input-any", "job-correction-any", "job-skill-any", "legacy"])
def test_missing_and_unsupported_jobs_stay_unknown_without_backfill(context, identifier):
    root, _, _, _ = context
    before = files(root.parent)
    view = project(context, id=identifier)
    assert view["status"] == view["provider_read"] == "unknown"
    assert "capture" not in view and "writebacks" not in view
    assert view["reason"] == ("not_recorded" if identifier == "job-historical" else "unsupported_attempt")
    assert files(root.parent) == before


@pytest.mark.parametrize("field,value", [
    ("run_id", "run-other"), ("project_key", "OTHER"), ("role", "planner"),
    ("adapter", "claude"), ("execution_id", "foreign"),
])
def test_wrong_scope_or_changed_job_identity_cannot_reuse_receipts(context, field, value):
    record(context)
    view = project(context, **{field: value})
    assert view["status"] == "unavailable" and "writebacks" not in view


def test_same_job_id_in_another_root_does_not_reuse_receipts(context, tmp_path):
    _, _, job, _ = context
    record(context)
    other = tmp_path / "another-target"
    other.mkdir()
    view = attempt_memory_observations(other, project_key="DEMO", run_id="run-one", job=job)
    assert view["status"] == "unknown"


@pytest.mark.parametrize("name", [".store.lock", "capture.json", "capture-required.json", "receipts.json", "receipts-required.json"])
def test_partial_history_is_unavailable_and_never_repaired(context, name):
    root, _, job, store = context
    record(context)
    (store.job_root(job) / name).unlink()
    before = files(root.parent)
    view = project(context)
    assert view["status"] == "unavailable" and view["reason"] == "records_unavailable"
    assert "capture" not in view and "writebacks" not in view
    assert files(root.parent) == before


@pytest.mark.parametrize("damage", ["foreign", "foreign_preparation", "wrong_transport", "duplicate", "invalid_count", "unknown_schema"])
def test_mismatched_receipts_fail_closed_even_with_matching_file_digest(context, damage):
    _, _, job, store = context
    record(context)

    def mutate(rows):
        if damage == "foreign":
            rows[-1]["job_id"] = "job-other"
        elif damage == "foreign_preparation":
            rows[0]["capture_ref"] = "0" * 64
        elif damage == "wrong_transport":
            rows[-1]["transport_ref"] = "0" * 64
        elif damage == "duplicate":
            rows.append(rows[-1])
        elif damage == "invalid_count":
            rows[-1]["receipt"]["appended"] = True
        else:
            rows[-1]["schema_version"] = "unrecognized"
    rewrite_rows(store, job, mutate)
    view = project(context)
    assert view["status"] == "unavailable" and "preparations" not in view


def test_replays_and_other_source_dedup_are_not_new_writes_and_partial_counts_remain_explicit(context):
    root, _, job, store = context
    record(context)
    record(context)
    memory = RoleMemoryStore(root)
    memory.append_memory_event("architect", horizon="long-term", text="FOREIGN", source="another-job")
    _, receipt = write_back_events(memory, "architect", items=[("long-term", "FOREIGN")],
                                   source=job["id"], result_bytes=b"result")
    store.writeback(job, kind="result", receipt={**receipt, "counts_complete": False, "outcome": "unknown"},
                    prepared=None, transport_ref=None)
    rows = project(context)["writebacks"]
    assert [(row["appended"], row["replayed"], row["deduplicated"]) for row in rows] == [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
    assert rows[-1]["counts_complete"] is False and rows[-1]["outcome"] == "unknown"


def test_bounded_latest_receipts_keep_total_and_omitted_counts(context):
    for _ in range(DISPLAY_LIMIT + 2):
        record(context)
    view = project(context)
    assert view["preparation_count"] == view["writeback_count"] == DISPLAY_LIMIT + 2
    assert len(view["preparations"]) == len(view["writebacks"]) == DISPLAY_LIMIT
    assert view["omitted_preparations"] == view["omitted_writebacks"] == 2


def test_delivery_projection_keeps_exact_run_and_does_not_borrow_a_newer_attempt(context):
    root, workers, job, _ = context
    record(context)
    newer = workers.create_job(run_id="run-two", project_key="DEMO", role="architect", adapter="noop", task="new", context="")
    state = create_initial_state("run-one", "DEMO", "Historical task")
    summary = build_delivery_summary(root.parent, state=state, jobs=[job, newer], worker_root=root)
    assert [row["id"] for row in summary["attempts"]] == [job["id"]]
    assert summary["attempts"][0]["memory_observations"]["writeback_count"] == 1
    state.run_id = "run-two"
    other = build_delivery_summary(root.parent, state=state, jobs=[job, newer], worker_root=root)
    assert other["attempts"][0]["memory_observations"]["writeback_count"] == 0


def test_unexpected_read_claim_and_private_extra_fields_never_reach_public_projection(context):
    _, _, job, store = context
    # A capture-only job plus a writeback needs no preparation references.
    _, receipt = write_back_events(RoleMemoryStore(context[0]), "architect", items=[], source="private", result_bytes=b"")
    store.writeback(job, kind="result", receipt=receipt, prepared=None, transport_ref=None)
    rewrite_rows(store, job, lambda rows: rows[0].update(provider_read="confirmed", private="SECRET"))
    view = project(context)
    assert view["provider_read"] == "unknown" and "SECRET" not in json.dumps(view)


def test_work_and_archived_api_use_the_same_exact_attempt_projection(context):
    from goodteam_os import web
    from goodteam_os.project_registry import ProjectRegistry
    from goodteam_os.projects import write_project_config
    from goodteam_os.state import StateStore

    root, workers, job, _ = context
    workspace = root.parent / "workspace"
    config = write_project_config(workspace / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="fake")
    ProjectRegistry().register_project(config, root=root, key="DEMO")
    states = StateStore(workspace)
    states.write_project_current(create_initial_state("run-one", "DEMO", "Original task"))
    record(context)
    current = web.build_project_work_state(workspace, "DEMO")
    original = current["delivery_summary"]["attempts"][0]["memory_observations"]
    assert original["attempt_id"] == job["id"] and original["writeback_count"] == 1
    states.archive_project_current("DEMO", reason="completed")
    states.write_project_current(create_initial_state("run-two", "DEMO", "New task"))
    newer = workers.create_job(run_id="run-two", project_key="DEMO", role="architect", adapter="noop", task="new", context="")
    code, archived = web.handle_project_api_get(workspace, "/api/projects/DEMO/runs/run-one")
    assert code == 200
    assert archived["delivery_summary"]["attempts"][0]["memory_observations"] == original
    next_view = web.build_project_work_state(workspace, "DEMO")
    next_memory = next_view["delivery_summary"]["attempts"][0]["memory_observations"]
    assert next_memory["attempt_id"] == newer["id"] and next_memory["run_id"] == "run-two"
    assert next_memory["writeback_count"] == 0


def test_symlinked_receipts_never_follow_private_target_or_repair(context):
    root, _, job, store = context
    record(context)
    path = store.job_root(job) / "receipts.json"
    path.unlink()
    secret = root.parent / "secret.txt"
    secret.write_text("PRIVATE_SECRET")
    path.symlink_to(secret)
    result = project(context)
    assert result["status"] == "unavailable"
    assert "PRIVATE" not in json.dumps(result)
    assert path.is_symlink() and secret.read_text() == "PRIVATE_SECRET"


@pytest.mark.parametrize("packet,reported_match,available", [
    ("different", True, False), ("same", False, False), (None, True, False),
    ("different", False, True), (None, False, True),
])
def test_preparation_packet_match_must_agree_with_capture_even_with_updated_proof(
        context, packet, reported_match, available):
    _, workers, job, store = context
    workers._worker_prompt(workers.root / job["id"], job)
    capture = store.load(job)

    def mutate(rows):
        assert len(rows) == 1 and rows[0]["stage"] == "worker_input_prepared"
        rows[0]["packet_digest"] = (capture["packet_digest"] if packet == "same"
                                     else "0" * 64 if packet == "different" else None)
        rows[0]["capture_packet_match"] = reported_match
    rewrite_rows(store, job, mutate)
    view = project(context)
    assert view["status"] == ("observed" if available else "unavailable")
    if available:
        assert view["preparations"][0]["capture_packet_match"] is False
    else:
        assert "preparations" not in view
