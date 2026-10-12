"""Ordinary-worker observations through isolated actual host call boundaries."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sys

import pytest

from goodteam_os.memory import RoleMemoryStore
from goodteam_os.memory_handoff import MemoryHandoffError, MemoryHandoffStore, canonical_bytes, digest
from goodteam_os.workers import WorkerJobStore


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path / "operator"))
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    monkeypatch.setattr("goodteam_os.workers._sleep", lambda _seconds: None)
    workers = WorkerJobStore(project)
    return workers, MemoryHandoffStore(project)


def create(workers, **kwargs):
    return workers.create_job(**{"run_id": "run-synthetic", "project_key": "SYNTHETIC", "role": "architect",
                                 "adapter": "claude", "task": "database SECRET_TASK", "context": "SECRET_CONTEXT", **kwargs})


def receipts(handoff, job, stage):
    return [item for item in handoff.receipts(job) if item["stage"] == stage]


def offline_cli(monkeypatch, handoff, *, transient_first=False, timeout=False):
    # The child verifies the receipt was committed before its actual start and
    # the digest matches the exact stdin it received. No daemon/provider/network.
    script = '''import hashlib,json,pathlib,sys,time
root = pathlib.Path(sys.argv[1])
rows = json.loads((root / 'receipts.json').read_text())
transport = [r for r in rows if r['stage'] == 'transport_attempt'][-1]
prepared = [r for r in rows if r['stage'] == 'worker_input_prepared'][-1]
assert transport['prepared_id'] == prepared['prepared_id']
assert transport['provider_receipt'] == transport['provider_read'] == 'unknown'
prompt = sys.stdin.read()
assert hashlib.sha256(prompt.encode()).hexdigest() == prepared['input_digest']
if sys.argv[2] == 'timeout':
    time.sleep(3)
if sys.argv[2] == 'transient' and len([r for r in rows if r['stage'] == 'transport_attempt']) == 1:
    sys.stderr.write('429 rate limit')
    sys.exit(1)
print('## Learned\\n- A synthetic durable lesson.')
'''

    def run(self, job_root, job, spec):
        return self._run_cli_worker(job_root, job,
            command=[sys.executable, "-c", script, str(handoff.job_root(job)),
                     "transient" if transient_first else "timeout" if timeout else "ok"],
            model="synthetic", preflight={"claude_available": True}, label="Synthetic CLI", capture="stdout",
            summarize=lambda code, stderr: "Synthetic complete" if code == 0 else stderr,
            timeout_seconds=0.05 if timeout else 10)

    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", run)


def test_actual_subprocess_receives_exact_prepared_input_and_metadata_stays_private(ctx, monkeypatch):
    workers, handoff = ctx
    memory = RoleMemoryStore(workers.project_root)
    event = memory.append_memory_event("architect", horizon="long-term", text="database SECRET_MEMORY", source="SECRET_SOURCE")
    job = create(workers)
    capture = handoff.load(job)
    offline_cli(monkeypatch, handoff)
    status = workers.run_job(job["id"])
    assert status["status"] == "completed"
    prepared = receipts(handoff, job, "worker_input_prepared")
    transport = receipts(handoff, job, "transport_attempt")
    written = receipts(handoff, job, "memory_writeback")
    assert len(prepared) == len(transport) == 1 and len(written) == 2
    assert transport[0]["prepared_ref"] == digest(canonical_bytes(prepared[0]))
    assert prepared[0]["capture_ref"] == digest(canonical_bytes(capture))
    assert prepared[0]["capture_packet_match"] is True
    assert prepared[0]["citations"] == status["memory_citations"] == [memory._event_id(event)]
    assert all(item["transport_ref"] == digest(canonical_bytes(transport[0])) for item in written)
    assert written[0]["receipt"]["new_event_ids"]
    assert handoff.load(job) == capture
    raw = b"\n".join(path.read_bytes() for path in handoff.job_root(job).glob("*.json"))
    for forbidden in (b"SECRET_TASK", b"SECRET_CONTEXT", b"SECRET_MEMORY", b"SECRET_SOURCE", str(workers.project_root).encode()):
        assert forbidden not in raw
    persisted = json.loads((workers.root / job["id"] / "status.json").read_text())
    assert persisted == dict(status)
    assert not {"prepared_prompt", "transport_ref", "receipt"} & set(persisted)


def test_actual_transient_retry_has_unique_preparation_and_transport_identity(ctx, monkeypatch):
    workers, handoff = ctx
    job = create(workers)
    offline_cli(monkeypatch, handoff, transient_first=True)
    status = workers.run_job(job["id"])
    assert status["status"] == "completed" and status["attempts"] == 2
    assert status["disposition"] == "recovered"
    prepared = receipts(handoff, job, "worker_input_prepared")
    transport = receipts(handoff, job, "transport_attempt")
    assert len(prepared) == len(transport) == 2
    assert prepared[0]["input_digest"] == prepared[1]["input_digest"]
    assert prepared[0]["prepared_id"] != prepared[1]["prepared_id"]
    assert transport[0]["transport_id"] != transport[1]["transport_id"]
    assert transport[0]["prepared_ref"] != transport[1]["prepared_ref"]
    assert receipts(handoff, job, "memory_writeback")[0]["transport_ref"] == digest(canonical_bytes(transport[-1]))


@pytest.mark.parametrize("boundary", ["worker_input_prepared", "transport_attempt"])
def test_required_receipt_failure_prevents_external_call_and_retry(ctx, monkeypatch, boundary):
    workers, handoff = ctx
    job = create(workers)
    offline_cli(monkeypatch, handoff)
    original = MemoryHandoffStore._append
    calls = []

    def fail(self, root, value):
        if value["stage"] == boundary:
            raise OSError("network timeout in metadata filesystem")
        return original(self, root, value)

    monkeypatch.setattr(MemoryHandoffStore, "_append", fail)
    monkeypatch.setattr("goodteam_os.workers.subprocess.Popen", lambda *args, **kwargs: calls.append(True))
    with pytest.raises((OSError, ValueError)):
        workers.run_job(job["id"])
    assert calls == []


def test_writeback_receipt_loss_preserves_committed_counts_and_never_retries_transport(ctx, monkeypatch):
    workers, handoff = ctx
    job = create(workers)
    offline_cli(monkeypatch, handoff)
    original = MemoryHandoffStore._append

    def fail(self, root, value):
        if value["stage"] == "memory_writeback":
            raise OSError("network timeout in metadata filesystem")
        return original(self, root, value)

    monkeypatch.setattr(MemoryHandoffStore, "_append", fail)
    status = workers.run_job(job["id"])
    assert status["status"] == "completed" and status["attempts"] == 1
    assert status["memory_writeback"]["status"] == "error"
    assert status["memory_writeback"]["appended"] == 1
    assert status["memory_provenance"]["appended"] == 1
    assert status["memory_writeback"]["error"] == "memory_handoff_writeback_receipt_unavailable"
    assert len(receipts(handoff, job, "transport_attempt")) == 1
    memory = RoleMemoryStore(workers.project_root)
    assert len(memory.list_events("architect")) == 2
    original_ids = [event["id"] for event in memory.list_events("architect")
                    if event["source"] == f"{job['id']}/result.md"]
    replay = workers._write_back_memory(workers.root / job["id"], job)
    assert replay["appended"] == 0 and replay.receipt["replayed_event_ids"] == original_ids[:1]


@pytest.mark.parametrize("leaf", ["required.json", "capture.json", "capture-required.json", "receipts.json", "receipts-required.json"])
def test_retained_enrollment_never_downgrades_after_metadata_loss(ctx, monkeypatch, leaf):
    workers, handoff = ctx
    job = create(workers)
    (handoff.job_root(job) / leaf).unlink()
    calls = []
    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", lambda *_args: calls.append(True))
    with pytest.raises(ValueError):
        workers.run_job(job["id"])
    assert not calls


def test_receipt_history_rollback_and_tamper_fail_closed(ctx):
    workers, handoff = ctx
    job = create(workers)
    path = handoff.job_root(job) / "receipts.json"
    initial = path.read_bytes()
    workers._worker_prompt(workers.root / job["id"], job)
    path.write_bytes(initial)
    with pytest.raises(MemoryHandoffError, match="receipts_mismatched"):
        workers.run_job(job["id"])


def test_capture_failure_partial_job_cannot_run(ctx, monkeypatch):
    workers, handoff = ctx
    monkeypatch.setattr(MemoryHandoffStore, "capture", lambda *_a: (_ for _ in ()).throw(OSError("capture fault")))
    with pytest.raises(OSError):
        create(workers)
    job_file, = workers.root.glob("*/job.json")
    job = json.loads(job_file.read_text())
    assert (handoff.job_root(job) / "required.json").is_file()
    with pytest.raises(MemoryHandoffError, match="capture_missing"):
        workers.run_job(job["id"])


@pytest.mark.parametrize("key,value", [("role", "planner"), ("run_id", "other-run"), ("project_key", "OTHER"),
                                       ("execution_id", "other-execution"), ("adapter", "noop")])
def test_frozen_identity_rejects_mutated_job(ctx, key, value):
    workers, _handoff = ctx
    job = create(workers)
    path = workers.root / job["id"] / "job.json"
    path.write_text(json.dumps({**job, key: value}))
    with pytest.raises(MemoryHandoffError, match="mismatched"):
        workers.run_job(job["id"])


def test_job_json_from_another_enrolled_job_cannot_borrow_capture(ctx):
    workers, _handoff = ctx
    first, second = create(workers), create(workers)
    (workers.root / first["id"] / "job.json").write_text(json.dumps(second))
    with pytest.raises(ValueError, match="job_root_mismatch"):
        workers.run_job(first["id"])


def test_packet_role_swap_is_withheld_without_reading_other_role(ctx, monkeypatch):
    workers, handoff = ctx
    job = create(workers)
    other = RoleMemoryStore(workers.project_root)
    other.append_memory_event("planner", horizon="long-term", text="FOREIGN_ROLE_SECRET", source="test")
    root = workers.root / job["id"]
    (root / "role_pickup_packet.json").write_text(json.dumps(other.build_pickup_packet("planner", query="FOREIGN")))
    monkeypatch.setattr(RoleMemoryStore, "validate_pickup_packet", lambda *_a: pytest.fail("must reject role before reading foreign WAL"))
    prompt = workers._worker_prompt(root, job)
    assert "FOREIGN_ROLE_SECRET" not in prompt and "pickup_role_mismatch" in prompt
    prepared, = receipts(handoff, job, "worker_input_prepared")
    assert prepared["citations"] == [] and prepared["capture_packet_match"] is False
    assert prepared["validation"]["context_reasons"] == ["pickup_role_mismatch"]


def test_one_validation_supplies_actual_fragment_and_citations(ctx, monkeypatch):
    workers, handoff = ctx
    memory = RoleMemoryStore(workers.project_root)
    old = memory.append_memory_event("architect", horizon="long-term", text="database OLD_FACT", source="test")
    job = create(workers)
    memory.append_memory_event("architect", horizon="long-term", text="database NEW_FACT", source="test",
                               supersedes=memory._event_id(old))
    calls = []
    original = RoleMemoryStore.validate_pickup_packet

    def validate(self, packet):
        calls.append(True)
        return original(self, packet)

    monkeypatch.setattr(RoleMemoryStore, "validate_pickup_packet", validate)
    offline_cli(monkeypatch, handoff)
    status = workers.run_job(job["id"])
    assert calls == [True]
    assert status["memory_citations"] == []
    prepared, = receipts(handoff, job, "worker_input_prepared")
    assert prepared["validation"]["context_reasons"]
    assert prepared["citations"] == [] and prepared["capture_packet_match"] is True


def test_short_term_policy_is_unchanged_and_summaries_are_dependency_metadata(ctx):
    workers, handoff = ctx
    memory = RoleMemoryStore(workers.project_root)
    memory.append_memory_event("architect", horizon="short-term", text="DO_NOT_INJECT_SHORT_TERM", source="test")
    memory.append_memory_event("architect", horizon="long-term", text="A displayed summary.", source="test")
    job = create(workers, task="")  # No query means no relevant-event retrieval.
    prompt = workers._worker_prompt(workers.root / job["id"], job)
    assert "DO_NOT_INJECT_SHORT_TERM" not in prompt
    prepared, = receipts(handoff, job, "worker_input_prepared")
    assert prepared["citations"] == []
    sources = prepared["validation"]["rendered_sources"]
    assert sources and all(source["source_kind"] == "horizon_summary" for source in sources)
    assert all(source["horizon"] != "short-term" for source in sources)
    assert all("generated_dependency_ids" in source for source in sources)


def test_historical_job_remains_unknown_without_retroactive_capture(ctx):
    workers, handoff = ctx
    job = create(workers, adapter="noop")
    # Simulate a pre-feature fixture: project protocol exists but no external
    # enrollment has ever been retained. Complete authority loss is also unknown.
    shutil.rmtree(handoff.job_root(job))
    status = workers.run_job(job["id"])
    assert status["status"] == "completed" and handoff.load(job) is None
    assert handoff.receipts(job) == []


def test_builtin_host_references_do_not_claim_preparation_or_transport(ctx):
    workers, handoff = ctx
    memory = RoleMemoryStore(workers.project_root)
    event = memory.append_memory_event("architect", horizon="long-term", text="database fact", source="test")
    job = create(workers, adapter="noop")
    status = workers.run_job(job["id"])
    assert status["memory_citations"] == [memory._event_id(event)]
    assert not receipts(handoff, job, "worker_input_prepared") and not receipts(handoff, job, "transport_attempt")
    assert all(row["prepared_ref"] is None and row["transport_ref"] is None
               for row in receipts(handoff, job, "memory_writeback"))


def test_parallel_jobs_keep_local_prepared_identity(ctx, monkeypatch):
    workers, handoff = ctx
    jobs = [create(workers, task=f"synthetic task {number}") for number in range(2)]
    offline_cli(monkeypatch, handoff)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda job: workers.run_job(job["id"]), jobs))
    assert all(status["status"] == "completed" for status in statuses)
    prepared = [receipts(handoff, job, "worker_input_prepared")[0] for job in jobs]
    assert prepared[0]["input_digest"] != prepared[1]["input_digest"]
    for job, prompt in zip(jobs, prepared):
        assert prompt["job_id"] == job["id"]
        assert receipts(handoff, job, "transport_attempt")[0]["prepared_ref"] == digest(canonical_bytes(prompt))


def test_operator_store_cannot_be_inside_project_or_follow_symlink(ctx, monkeypatch, tmp_path):
    workers, _handoff = ctx
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(workers.project_root / "operator"))
    with pytest.raises(MemoryHandoffError, match="unsafe_store_root"):
        create(workers)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(link))
    with pytest.raises(ValueError):
        create(workers)
    assert list(outside.iterdir()) == []


def test_symlinked_receipt_leaf_is_never_read_or_replaced(ctx, tmp_path):
    workers, handoff = ctx
    job = create(workers)
    target = tmp_path / "target"
    target.write_text("DO_NOT_TOUCH")
    leaf = handoff.job_root(job) / "receipts.json"
    leaf.unlink()
    leaf.symlink_to(target)
    with pytest.raises(ValueError):
        workers.run_job(job["id"])
    assert target.read_text() == "DO_NOT_TOUCH"


def test_transport_exception_retains_attempt_without_fabricated_success(ctx, monkeypatch):
    workers, handoff = ctx
    job = create(workers)
    offline_cli(monkeypatch, handoff)
    monkeypatch.setattr("goodteam_os.workers.subprocess.Popen", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic spawn failure")))
    with pytest.raises(OSError, match="spawn failure"):
        workers.run_job(job["id"])
    transport, = receipts(handoff, job, "transport_attempt")
    assert transport["observation"] == "host_call_entry" and transport["provider_receipt"] == "unknown"
    assert not receipts(handoff, job, "memory_writeback")


def test_pi_actual_rpc_prompt_has_matching_preparation_and_host_attempt(ctx, monkeypatch, tmp_path):
    workers, handoff = ctx
    fake_pi = Path(__file__).parent / "fixtures" / "fake_pi_rpc.py"
    monkeypatch.setenv("GOODTEAM_PI_PATH", str(fake_pi))
    monkeypatch.setenv("GOODTEAM_PI_SESSION_DIR", str(tmp_path / "sessions"))
    job = create(workers, adapter="pi")
    status = workers.run_job(job["id"])
    assert status["status"] == "completed"
    prepared, = receipts(handoff, job, "worker_input_prepared")
    transport, = receipts(handoff, job, "transport_attempt")
    metadata = json.loads((workers.root / job["id"] / "pi_session.json").read_text())
    assert metadata["active_operation"]["prompt_sha256"] == prepared["input_digest"]
    assert transport["prepared_ref"] == digest(canonical_bytes(prepared))
    assert transport["provider_receipt"] == "unknown"


def test_receipt_bounded_history_never_discards_prior_attempt(ctx, monkeypatch):
    import goodteam_os.memory_handoff as module
    workers, handoff = ctx
    job = create(workers)
    root = workers.root / job["id"]
    first = workers._worker_prompt(root, job)
    monkeypatch.setattr(module, "MAX_RECEIPTS", 1)
    with pytest.raises(MemoryHandoffError, match="receipts_limit"):
        workers._memory_transport(root, job, first)
    assert len(handoff.receipts(job)) == 1


def test_cross_job_transport_cannot_be_used_as_writeback_evidence(ctx):
    workers, handoff = ctx
    first, second = create(workers), create(workers)
    a = workers._worker_prompt(workers.root / first["id"], first)
    b = workers._worker_prompt(workers.root / second["id"], second)
    transport = workers._memory_transport(workers.root / first["id"], first, a)
    with pytest.raises(MemoryHandoffError, match="transport_mismatch"):
        handoff.writeback(second, kind="result", receipt={}, prepared=b.record, transport_ref=transport)


def test_interrupted_receipt_write_cannot_be_repaired_into_a_dispatch(ctx, monkeypatch):
    import goodteam_os.memory_handoff as module
    workers, handoff = ctx
    job = create(workers)
    original = module.atomic_write

    def fail(path, data, **kwargs):
        if path.name == "receipts.json":
            raise OSError("synthetic disk failure")
        return original(path, data, **kwargs)

    monkeypatch.setattr(module, "atomic_write", fail)
    with pytest.raises(ValueError):
        workers._worker_prompt(workers.root / job["id"], job)
    monkeypatch.setattr(module, "atomic_write", original)
    with pytest.raises(MemoryHandoffError, match="receipts_mismatched"):
        workers.run_job(job["id"])
    assert json.loads((handoff.job_root(job) / "receipts.json").read_text()) == []
    assert json.loads((handoff.job_root(job) / "receipts-required.json").read_text())["count"] == 1


def test_recapture_cannot_clear_known_transport_after_capture_loss(ctx):
    workers, handoff = ctx
    job = create(workers)
    root = workers.root / job["id"]
    prompt = workers._worker_prompt(root, job)
    workers._memory_transport(root, job, prompt)
    rows = (handoff.job_root(job) / "receipts.json").read_bytes()
    (handoff.job_root(job) / "capture.json").unlink()
    with pytest.raises(MemoryHandoffError, match="capture_missing"):
        handoff.capture(job, b"{}")
    assert (handoff.job_root(job) / "receipts.json").read_bytes() == rows


def test_capture_content_mutation_does_not_match_independent_commit_marker(ctx):
    workers, handoff = ctx
    job = create(workers)
    path = handoff.job_root(job) / "capture.json"
    value = json.loads(path.read_text())
    value["packet_digest"] = "0" * 64
    path.write_bytes(canonical_bytes(value))
    with pytest.raises(MemoryHandoffError, match="capture_mismatched"):
        workers.run_job(job["id"])


def test_unavailable_cli_preflight_does_not_invent_preparation_or_transport(ctx, monkeypatch):
    workers, handoff = ctx
    job = create(workers)
    monkeypatch.setattr("goodteam_os.workers._cli_preflight", lambda *_a: {"claude_available": False})
    monkeypatch.setattr("goodteam_os.workers._cli_runtime_evidence", lambda **_kw: {})
    monkeypatch.setattr("goodteam_os.workers.which", lambda _name: None)
    status = workers.run_job(job["id"])
    assert status["status"] == "failed" and status["attempts"] == 1
    assert handoff.receipts(job) == [] and status["memory_citations"] == []


def test_pi_receipt_failure_prevents_prompt_send_and_is_not_retried(ctx, monkeypatch, tmp_path):
    from goodteam_os.session_runtimes import PiSessionRuntime
    workers, handoff = ctx
    fake_pi = Path(__file__).parent / "fixtures" / "fake_pi_rpc.py"
    monkeypatch.setenv("GOODTEAM_PI_PATH", str(fake_pi))
    monkeypatch.setenv("GOODTEAM_PI_SESSION_DIR", str(tmp_path / "sessions"))
    job = create(workers, adapter="pi")
    calls = []
    monkeypatch.setattr(PiSessionRuntime, "send_prompt", lambda *_a: calls.append(True))
    monkeypatch.setattr(MemoryHandoffStore, "transport", lambda *_a: (_ for _ in ()).throw(ValueError("memory_handoff_unavailable")))
    status = workers.run_job(job["id"])
    assert status["status"] == "failed" and calls == []
    assert len(receipts(handoff, job, "worker_input_prepared")) == 1
    assert not receipts(handoff, job, "transport_attempt")


def test_pty_boundary_has_durable_transport_before_entering_runner(ctx, monkeypatch):
    workers, handoff = ctx
    job = create(workers)
    calls = []

    def runner(command, **kwargs):
        prompt = kwargs["process_input"]
        transport, = receipts(handoff, job, "transport_attempt")
        prepared, = receipts(handoff, job, "worker_input_prepared")
        assert digest(prompt.encode()) == prepared["input_digest"]
        assert transport["prepared_ref"] == digest(canonical_bytes(prepared))
        calls.append(True)
        return 0, "Synthetic PTY done.", ""

    monkeypatch.setattr("goodteam_os.workers._run_command_in_pty", runner)
    status = workers._run_cli_worker(workers.root / job["id"], job, command=["synthetic-pty"], model="synthetic",
        preflight={"claude_available": True}, label="Synthetic PTY", capture="stdout",
        summarize=lambda *_a: "Synthetic complete", use_pty=True)
    assert status["status"] == "completed" and calls == [True]


def test_malformed_summary_shape_does_not_change_prompt_policy_or_crash_metadata(ctx):
    from goodteam_os.memory_core.context import _pickup_content_digest
    workers, handoff = ctx
    job = create(workers)
    root = workers.root / job["id"]
    path = root / "role_pickup_packet.json"
    value = json.loads(path.read_text())
    value["summaries"] = ["DO_NOT_RENDER_MALFORMED_SUMMARY"]
    value["provenance"]["content_sha256"] = _pickup_content_digest(value)
    path.write_text(json.dumps(value))
    prompt = workers._worker_prompt(root, job)
    assert "DO_NOT_RENDER_MALFORMED_SUMMARY" not in prompt
    prepared, = receipts(handoff, job, "worker_input_prepared")
    assert prepared["validation"]["rendered_sources"] == []


@pytest.mark.parametrize("malformed_role", [[], {}, ["FOREIGN_ROLE"], 17, False, None])
def test_malformed_packet_role_is_unavailable_without_type_error(ctx, malformed_role):
    workers, handoff = ctx
    job = create(workers)
    root = workers.root / job["id"]
    path = root / "role_pickup_packet.json"
    value = json.loads(path.read_text())
    value["role_key"] = malformed_role
    path.write_text(json.dumps(value))
    prompt = workers._worker_prompt(root, job)
    assert "Memory context: unavailable" in prompt
    prepared, = receipts(handoff, job, "worker_input_prepared")
    assert prepared["citations"] == []
    assert prepared["validation"]["context_status"] == "unavailable"
    assert prepared["validation"]["rendered_sources"] == []



def test_builtin_host_refs_keep_admitted_role_after_mutable_job_swap(ctx, monkeypatch):
    workers, handoff = ctx
    job = create(workers, adapter="noop")
    memory = RoleMemoryStore(workers.project_root)
    memory.append_memory_event("planner", horizon="long-term", text="FOREIGN_ROLE_FACT", source="test")
    foreign = memory.build_pickup_packet("planner", query="FOREIGN_ROLE_FACT")
    root = workers.root / job["id"]
    original = MemoryHandoffStore.load
    swapped = []

    def load_then_swap(self, admitted_job):
        value = original(self, admitted_job)
        if not swapped:
            (root / "job.json").write_text(json.dumps({**job, "role": "planner"}))
            (root / "role_pickup_packet.json").write_text(json.dumps(foreign))
            swapped.append(True)
        return value

    monkeypatch.setattr(MemoryHandoffStore, "load", load_then_swap)
    status = workers.run_job(job["id"])
    assert status["status"] == "completed" and swapped == [True]
    assert status["memory_citations"] == []
    assert handoff.load(job)["role"] == "architect"
