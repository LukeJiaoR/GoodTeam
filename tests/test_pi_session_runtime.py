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
    assert metadata["last_entry_id"] == "entry-2"
    assert "Fake Pi completed" in (job_root / "result.md").read_text(encoding="utf-8")
    assert "agent_settled" in (job_root / "pi_protocol.jsonl").read_text(encoding="utf-8")
    assert "tool_started" in (job_root / "pi_events.jsonl").read_text(encoding="utf-8")
    assert len((job_root / "pi_entries.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert status["invocation_evidence"]["kind"] == "session_runtime"
    assert json.loads((job_root / "status.json").read_text(encoding="utf-8"))["status"] == "completed"


def test_pi_resume_switches_explicit_same_lineage_without_reprompt(monkeypatch, tmp_path):
    configure_fake_pi(monkeypatch, tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    store = WorkerJobStore(project)
    first = create_pi_job(store)
    assert store.run_job(first["id"])["status"] == "completed"
    resumed = create_pi_job(store, session_policy="resume", worker_session_ref=first["id"])
    status = store.run_job(resumed["id"])
    events = (store.root / resumed["id"] / "pi_events.jsonl").read_text(encoding="utf-8")
    assert status["status"] == "completed"
    assert "session_resumed" in events
    assert "prompt_accepted" not in events


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
    resumed = create_pi_job(store, session_policy="resume", worker_session_ref=job["id"])
    assert store.run_job(resumed["id"])["status"] == "completed"
    resumed_root = store.root / resumed["id"]
    entries = (resumed_root / "pi_entries.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(entries) == len({json.loads(line)["id"] for line in entries})


def test_running_session_accepts_cli_steer_followup_and_abort(monkeypatch, tmp_path, capsys):
    configure_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_PI_SETTLE_DELAY", "0.5")
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
    job_path = store.root / job["id"] / "job.json"
    payload = json.loads(job_path.read_text(encoding="utf-8"))
    payload["pi_path"] = str(FAKE_PI)
    payload["pi_request_timeout"] = 0.05
    job_path.write_text(json.dumps(payload), encoding="utf-8")
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
