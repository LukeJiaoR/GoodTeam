import json
import threading
import time
from pathlib import Path

import pytest

from goodteam_os.cli import main
from goodteam_os.session_runtimes import PI_CAPABILITIES, pi_backend_doctor
from goodteam_os.workers import WorkerJobStore, _pid_alive, discover_installed_agents, worker_backend_doctor


FAKE_PI = Path(__file__).parent / "fixtures" / "fake_pi_rpc.py"


def configure_fake_pi(monkeypatch, tmp_path: Path) -> Path:
    sessions = tmp_path / "pi-sessions"
    monkeypatch.setenv("GOODTEAM_PI_PATH", str(FAKE_PI))
    monkeypatch.setenv("GOODTEAM_PI_SESSION_DIR", str(sessions))
    monkeypatch.setenv("GOODTEAM_PI_PROVIDER", "fake-provider")
    monkeypatch.setenv("GOODTEAM_PI_MODEL", "fake-model")
    monkeypatch.setenv("GOODTEAM_PI_THINKING_LEVEL", "low")
    return sessions


def create_pi_job(store: WorkerJobStore, **overrides):
    values = {
        "run_id": "execution-1",
        "execution_id": "execution-1",
        "project_key": "DEMO",
        "role": "implementation",
        "adapter": "pi",
        "task": "Read README and report.",
        "context": "Local isolated test only.",
    }
    values.update(overrides)
    return store.create_job(**values)


def set_pi_request_timeout(store: WorkerJobStore, job_id: str, timeout: float) -> None:
    job_path = store.root / job_id / "job.json"
    payload = json.loads(job_path.read_text(encoding="utf-8"))
    payload["pi_path"] = str(FAKE_PI)
    payload["pi_request_timeout"] = timeout
    job_path.write_text(json.dumps(payload), encoding="utf-8")


def test_pi_doctor_missing_does_not_affect_builtin(monkeypatch, tmp_path):
    monkeypatch.setenv("GOODTEAM_PI_PATH", str(tmp_path / "missing-pi"))
    monkeypatch.setenv("GOODTEAM_PI_SESSION_DIR", str(tmp_path / "sessions"))
    report = pi_backend_doctor(tmp_path)
    assert report["ready"] is False
    assert report["runtime_kind"] == "persistent_session"
    assert set(report["capabilities"]) == set(PI_CAPABILITIES)
    assert worker_backend_doctor("fake")["ready"] is True


def test_pi_doctor_available_reports_safe_configuration(monkeypatch, tmp_path):
    sessions = configure_fake_pi(monkeypatch, tmp_path)
    report = pi_backend_doctor(tmp_path)
    assert report["ready"] is True
    assert report["version"] == "pi 0.99.0-fake"
    assert report["provider"] == "fake-provider"
    assert report["model"] == "fake-model"
    assert report["thinking_level"] == "low"
    assert report["session_directory"] == str(sessions.resolve())
    assert "secret" not in json.dumps(report).lower()


def test_pi_doctor_allows_pi_defaults_and_rejects_unwritable_session_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GOODTEAM_PI_PATH", str(FAKE_PI))
    monkeypatch.setenv("GOODTEAM_PI_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("GOODTEAM_PI_PROVIDER", raising=False)
    monkeypatch.delenv("GOODTEAM_PI_MODEL", raising=False)
    monkeypatch.delenv("GOODTEAM_PI_THINKING_LEVEL", raising=False)
    report = pi_backend_doctor(tmp_path)
    assert report["provider"] == ""
    assert report["model"] == "auto"
    assert report["thinking_level"] == "default"
    monkeypatch.setattr("goodteam_os.session_runtimes._directory_writable", lambda _path: False)
    report = pi_backend_doctor(tmp_path)
    assert report["session_directory_writable"] is False
    assert report["ready"] is False


def test_pi_initial_run_persists_result_events_entries_and_runtime_cwd(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    project = tmp_path / "project"
    workspace = tmp_path / "sandbox" / "workspace"
    project.mkdir()
    workspace.mkdir(parents=True)
    (workspace / ".git").mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    status = store.run_job(
        job["id"],
        sandbox_context={"available": True, "workspace_path": str(workspace), "mode": "worktree", "status": "ready"},
    )
    job_root = store.root / job["id"]
    metadata = json.loads((job_root / "pi_session.json").read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["runtime_kind"] == "persistent_session"
    assert status["runtime_cwd"] == str(workspace)
    assert metadata["session_id"] == "fake-session-1"
    assert metadata["status"] == "settled"
    assert metadata["active_operation"]["state"] == "settled"
    assert metadata["active_operation"]["turn_started_at"]
    assert metadata["settled_operation_id"] == metadata["active_operation"]["id"]
    assert metadata["last_entry_id"] == "entry-2"
    assert "Fake Pi completed" in (job_root / "result.md").read_text(encoding="utf-8")
    assert "agent_settled" in (job_root / "pi_protocol.jsonl").read_text(encoding="utf-8")
    assert "tool_started" in (job_root / "pi_events.jsonl").read_text(encoding="utf-8")
    assert len((job_root / "pi_entries.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert status["invocation_evidence"]["kind"] == "session_runtime"
    assert json.loads((job_root / "status.json").read_text(encoding="utf-8"))["status"] == "completed"


def test_pi_resume_switches_explicit_same_lineage_without_reprompt(monkeypatch, tmp_path):
    sessions = configure_fake_pi(monkeypatch, tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    first = create_pi_job(store)
    assert store.run_job(first["id"])["status"] == "completed"
    resumed = create_pi_job(
        store,
        session_policy="resume",
        worker_session_ref=first["id"],
        task="Create RESUMED.md and verify the existing tests.",
    )
    status = store.run_job(resumed["id"])
    events = (store.root / resumed["id"] / "pi_events.jsonl").read_text(encoding="utf-8")
    protocol = (store.root / resumed["id"] / "pi_protocol.jsonl").read_text(encoding="utf-8")
    assert status["status"] == "completed"
    assert "session_resumed" in events
    assert "resume_prompt_accepted" in events
    assert '"command":"switch_session"' in protocol
    assert protocol.count('"command":"prompt"') == 1
    event_names = {json.loads(line)["event"] for line in events.splitlines()}
    assert "prompt_accepted" not in event_names
    session_file = sessions / "fake-session-1.jsonl"
    entries = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines()]
    user_messages = [entry["message"]["content"] for entry in entries if entry.get("message", {}).get("role") == "user"]
    assert len(user_messages) == 2
    assert len(set(user_messages)) == 2
    assert sum("Create RESUMED.md and verify the existing tests." in message for message in user_messages) == 1


def test_pi_resume_rejects_cross_project_reference(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    first = create_pi_job(store)
    assert store.run_job(first["id"])["status"] == "completed"
    resumed = create_pi_job(
        store,
        project_key="OTHER",
        session_policy="resume",
        worker_session_ref=first["id"],
    )
    status = store.run_job(resumed["id"])
    assert status["status"] == "failed"
    assert "different project" in status["summary"]


def test_pi_lost_prompt_response_recovers_only_from_bound_settled_marker(monkeypatch, tmp_path):
    sessions = configure_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_PI_STARTUP_DELAY", "0.1")
    monkeypatch.setenv("FAKE_PI_DROP_PROMPT_RESPONSE", "1")
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    set_pi_request_timeout(store, job["id"], 0.05)

    first = store.run_job(job["id"])
    assert first["status"] == "failed"
    assert first["failure_class"] == "pi_request_timeout"
    metadata = json.loads((store.root / job["id"] / "pi_session.json").read_text(encoding="utf-8"))
    assert metadata["active_operation"]["state"] == "settled"

    monkeypatch.delenv("FAKE_PI_DROP_PROMPT_RESPONSE")
    recovered = store.run_job(job["id"])
    assert recovered["status"] == "completed"
    entries = [json.loads(line) for line in (sessions / "fake-session-1.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(entry.get("message", {}).get("role") == "user" for entry in entries) == 1


def test_pi_recovery_fails_closed_when_prompt_settlement_is_ambiguous(monkeypatch, tmp_path):
    sessions = configure_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_PI_DROP_PROMPT_RESPONSE", "1")
    monkeypatch.setenv("FAKE_PI_NO_SETTLED", "1")
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    set_pi_request_timeout(store, job["id"], 0.05)

    first = store.run_job(job["id"])
    assert first["failure_class"] == "pi_request_timeout"
    metadata = json.loads((store.root / job["id"] / "pi_session.json").read_text(encoding="utf-8"))
    assert metadata["active_operation"]["state"] == "dispatching"
    assert (sessions / "fake-session-1.jsonl").read_text(encoding="utf-8")

    monkeypatch.delenv("FAKE_PI_DROP_PROMPT_RESPONSE")
    monkeypatch.delenv("FAKE_PI_NO_SETTLED")
    recovered = store.run_job(job["id"])
    assert recovered["status"] == "failed"
    assert recovered["failure_class"] == "pi_resume_failed"
    entries = [json.loads(line) for line in (sessions / "fake-session-1.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(entry.get("message", {}).get("role") == "user" for entry in entries) == 1


def test_pi_resume_prompt_uses_same_fail_closed_operation_marker(monkeypatch, tmp_path):
    sessions = configure_fake_pi(monkeypatch, tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    first = create_pi_job(store)
    assert store.run_job(first["id"])["status"] == "completed"
    resumed = create_pi_job(
        store,
        session_policy="resume",
        worker_session_ref=first["id"],
        task="Continue exactly once.",
    )
    set_pi_request_timeout(store, resumed["id"], 0.05)
    monkeypatch.setenv("FAKE_PI_DROP_PROMPT_RESPONSE", "1")
    monkeypatch.setenv("FAKE_PI_NO_SETTLED", "1")
    assert store.run_job(resumed["id"])["failure_class"] == "pi_request_timeout"

    monkeypatch.delenv("FAKE_PI_DROP_PROMPT_RESPONSE")
    monkeypatch.delenv("FAKE_PI_NO_SETTLED")
    recovered = store.run_job(resumed["id"])
    assert recovered["failure_class"] == "pi_resume_failed"
    entries = [json.loads(line) for line in (sessions / "fake-session-1.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(entry.get("message", {}).get("role") == "user" for entry in entries) == 2


def test_pi_invalid_cursor_full_replays_without_duplicate_entries(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    assert store.run_job(job["id"])["status"] == "completed"
    root = store.root / job["id"]
    metadata = json.loads((root / "pi_session.json").read_text(encoding="utf-8"))
    metadata["last_entry_id"] = "invalid-cursor"
    (root / "pi_session.json").write_text(json.dumps(metadata), encoding="utf-8")
    resumed = create_pi_job(
        store,
        session_policy="resume",
        worker_session_ref=job["id"],
        task="Continue after cursor recovery.",
    )
    assert store.run_job(resumed["id"])["status"] == "completed"
    resumed_root = store.root / resumed["id"]
    entries = (resumed_root / "pi_entries.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(entries) == len({json.loads(line)["id"] for line in entries})


def test_running_session_accepts_cli_steer_followup_and_abort(monkeypatch, tmp_path, capsys):
    configure_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_PI_SETTLE_DELAY", "2")
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    outcome: dict[str, object] = {}

    def run() -> None:
        outcome.update(store.run_job(job["id"]))

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if store.session_status(job["id"])["process_alive"]:
            break
        time.sleep(0.01)
    assert main(["worker-session", "steer", "--project-root", str(project), "--job-id", job["id"], "--message", "Focus tests"]) == 0
    assert json.loads(capsys.readouterr().out)["accepted"] is True
    assert store.session_control(job["id"], "follow_up", message="Summarize") ["accepted"] is True
    assert store.session_control(job["id"], "abort", reason="test stop")["accepted"] is True
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert outcome["status"] == "failed"
    assert outcome["failure_class"] == "pi_aborted"
    events = (store.root / job["id"] / "pi_events.jsonl").read_text(encoding="utf-8")
    assert "steer_accepted" in events
    assert "follow_up_accepted" in events
    assert "abort" in events
    acknowledgements = [
        json.loads(line)
        for line in (store.root / job["id"] / "pi_control_acks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    latest = {record["control_id"]: record["state"] for record in acknowledgements}
    assert set(latest.values()) == {"rpc_accepted"}


def test_session_control_timeout_reports_queued_instead_of_accepted(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("GOODTEAM_PI_CONTROL_ACK_TIMEOUT", "0.05")
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    active = {
        "job_id": job["id"],
        "session_status": "running",
        "process_alive": True,
    }
    monkeypatch.setattr(store, "session_status", lambda _job_id: active)

    result = store.session_control(job["id"], "steer", message="queued command")
    assert result["accepted"] is False
    assert result["control_state"] == "queued"


def test_processing_control_is_failed_not_replayed_after_restart(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    root = store.root / job["id"]
    control_id = "control-restart-window"
    store._append_jsonl(
        root / "pi_control_queue.jsonl",
        {"id": control_id, "action": "steer", "message": "must not replay"},
    )
    store._append_pi_control_ack(root, control_id, "processing")

    store._process_pi_controls(root, job, None)  # type: ignore[arg-type]
    acknowledgement = store._pi_control_ack_states(root)[control_id]
    assert acknowledgement["state"] == "failed"
    assert "ambiguous after worker restart" in acknowledgement["error"]


def test_settled_session_rejects_steer_and_abort_is_idempotent(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    assert store.run_job(job["id"])["status"] == "completed"
    with pytest.raises(ValueError, match="not active"):
        store.session_control(job["id"], "steer", message="late")
    root = store.root / job["id"]
    metadata = json.loads((root / "pi_session.json").read_text(encoding="utf-8"))
    metadata["status"] = "aborted"
    (root / "pi_session.json").write_text(json.dumps(metadata), encoding="utf-8")
    assert store.session_control(job["id"], "abort")["idempotent"] is True


def test_discovery_lists_pi_separately_from_one_shot_registry(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    # Keep the fake-Pi probe without calling unrelated provider CLIs installed
    # on the test host or querying their live model catalogs.
    monkeypatch.setattr(
        "goodteam_os.workers.worker_backend_doctor",
        lambda mode: pi_backend_doctor(tmp_path) if mode == "pi" else {"available": False, "ready": False},
    )
    pi = next(item for item in discover_installed_agents() if item["backend"] == "pi")
    assert pi["runtime_kind"] == "persistent_session"
    assert pi["aliases"] == ["pi", "pi-rpc"]


def test_pi_process_crash_is_classified_and_cleaned_up(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_PI_CRASH", "1")
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    status = store.run_job(job["id"])
    assert status["status"] == "failed"
    assert status["failure_class"] == "pi_process_exited"
    assert not _pid_alive(status["pid"])


def test_pi_rpc_timeout_is_classified(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_PI_TIMEOUT_COMMAND", "get_state")
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    set_pi_request_timeout(store, job["id"], 0.05)
    status = store.run_job(job["id"])
    assert status["status"] == "failed"
    assert status["failure_class"] == "pi_request_timeout"


def test_pi_overall_timeout_aborts_and_kills_process(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_PI_SETTLE_DELAY", "1")
    monkeypatch.setattr("goodteam_os.workers.PI_OVERALL_TIMEOUT_SECONDS", 0.05)
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    status = store.run_job(job["id"])
    assert status["status"] == "failed"
    assert status["failure_class"] == "pi_overall_timeout"
    assert not _pid_alive(status["pid"])


def test_pi_agent_error_is_not_reported_as_success(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_PI_AGENT_ERROR", "1")
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    status = store.run_job(job["id"])
    assert status["status"] == "failed"
    assert status["failure_class"] == "pi_agent_failed"


def test_pi_missing_assistant_text_writes_explicit_fallback(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_PI_NO_TEXT", "1")
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    job = create_pi_job(store)
    status = store.run_job(job["id"])
    result = (store.root / job["id"] / "result.md").read_text(encoding="utf-8")
    assert status["status"] == "completed"
    assert "completed-without-assistant-text" in result
