import pytest

from goodteam_os.graph import _worker_job_fields, resolve_role_adapter
from goodteam_os.projects import load_project_target, write_project_config
from goodteam_os.state import create_initial_state
from goodteam_os.team_control_plane import ensure_team_state, execute_team_commands, TeamDispatchBlocked


def test_intake_uses_research_identity_and_preserves_capability_gate(tmp_path, monkeypatch):
    # Isolate dispatch mapping from the host's installed/authenticated Codex CLI.
    monkeypatch.setattr(
        "goodteam_os.team_control_plane.worker_backend_doctor",
        lambda mode: {"mode": mode, "ready": True},
    )
    root = tmp_path / "repo"
    root.mkdir()
    config = write_project_config(tmp_path / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="codex")
    team = ensure_team_state(tmp_path, "DEMO", config)
    researcher = next(worker for worker in team["workers"] if worker["role"] == "research")
    state = create_initial_state("run", "DEMO", "Inspect existing project")
    state.role_models["research"] = {"primary": "codex:gpt-5.5"}
    fields = _worker_job_fields(load_project_target(config), "project_intake", state)
    assert researcher["id"] == fields["worker_id"]
    assert "codex" == resolve_role_adapter(load_project_target(config), "project_intake")
    execute_team_commands(
        tmp_path,
        "DEMO",
        [{"kind": "disable_role", "role": "research"}],
        actor="user",
        surface="team_ui",
        config_path=config,
    )
    with pytest.raises(TeamDispatchBlocked):
        _worker_job_fields(load_project_target(config), "project_intake", state)


def test_intake_preserves_full_research_runtime_and_unavailable_gate(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.team_control_plane.worker_backend_doctor", lambda _: {"ready": True})
    root = tmp_path / "repo"
    root.mkdir()
    config = write_project_config(tmp_path / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="codex")
    team = ensure_team_state(tmp_path, "DEMO", config)
    researcher = next(worker for worker in team["workers"] if worker["role"] == "research")
    execute_team_commands(tmp_path, "DEMO", [{
        "kind": "change_worker_runtime", "worker_id": researcher["id"],
        "provider": "claude", "model": "research-model", "reasoning_depth": "max",
        "additional_prompt": "Preserve the intake constraints.",
    }], actor="user", surface="team_ui", config_path=config)
    target = load_project_target(config)
    state = create_initial_state("run", "DEMO", "Inspect existing project")
    fields = _worker_job_fields(target, "project_intake", state)
    assert fields["worker_id"] == researcher["id"]
    assert fields["adapter"] == "claude"
    assert fields["model"] == "research-model"
    assert fields["reasoning_depth"] == "max"
    assert fields["additional_prompt"] == "Preserve the intake constraints."
    monkeypatch.setattr("goodteam_os.team_control_plane.worker_backend_doctor", lambda _: {"ready": False})
    with pytest.raises(TeamDispatchBlocked):
        _worker_job_fields(target, "project_intake", state)


def test_legacy_intake_keeps_its_own_role_configuration():
    target = {"worker_backend": {"mode": "codex"}, "workers": {"project_intake": "noop", "research": "fake"}}
    state = create_initial_state("run", "DEMO", "Legacy intake")
    assert resolve_role_adapter(target, "project_intake") == "noop"
    fields = _worker_job_fields(target, "project_intake", state)
    assert fields["adapter"] == "noop"
    assert "worker_id" not in fields


@pytest.mark.parametrize("available,enabled", [(True, True), (False, True), (True, False)])
def test_intake_artifact_dispatch_records_research_worker_and_fails_closed(tmp_path, monkeypatch, available, enabled):
    """Exercise real job creation/artifact reading; only provider execution is fake."""
    from goodteam_os.graph import generate_artifact_via_worker
    from goodteam_os.workers import WorkerJobStore

    # Synthetic selection evidence, separate from the dispatch readiness gate
    # exercised below. Never probe a host CLI for this fake provider fixture.
    monkeypatch.setattr("goodteam_os.model_discovery.discover", lambda _: [{
        "model_capabilities": {"fixture-research": {"reasoning_depths": ["high"]}},
    }])
    monkeypatch.setattr("goodteam_os.team_control_plane.worker_backend_doctor", lambda _: {"ready": True})
    root = tmp_path / "target"
    root.mkdir()
    config = write_project_config(tmp_path / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="codex")
    team = ensure_team_state(tmp_path, "DEMO", config)
    researcher = next(worker for worker in team["workers"] if worker["role"] == "research")
    commands = [{"kind": "change_worker_runtime", "worker_id": researcher["id"],
                 "provider": "codex", "model": "fixture-research", "reasoning_depth": "high",
                 "additional_prompt": "Keep all recorded project constraints."}]
    if not enabled:
        commands.append({"kind": "disable_role", "role": "research"})
    execute_team_commands(tmp_path, "DEMO", commands, actor="user", surface="team_ui", config_path=config)
    monkeypatch.setattr("goodteam_os.team_control_plane.worker_backend_doctor", lambda _: {"ready": available})
    executed = []

    def fake_provider_execution(store, job_id):
        executed.append(job_id)
        result_path = store.root / job_id / "result.md"
        result_path.write_text("# Intake assessment\n\nThe exact Research worker inspected this isolated target.\n", encoding="utf-8")
        return {"status": "completed", "result_path": str(result_path)}

    monkeypatch.setattr(WorkerJobStore, "run_job", fake_provider_execution)
    state = create_initial_state("intake-run", "DEMO", "Inspect existing project")
    failure = {}
    result = generate_artifact_via_worker(state=state, workspace=tmp_path, project_config=config,
                                         role="project_intake", artifact_key="takeover",
                                         task_text="Inspect this isolated existing project.", failure_status=failure)
    jobs = list((root / ".goodteam" / "workers" / "jobs").glob("*/job.json"))
    if available and enabled:
        import json
        assert len(executed) == len(jobs) == 1
        job = json.loads(jobs[0].read_text(encoding="utf-8"))
        assert job["id"] == executed[0]
        assert job["role"] == "project_intake"
        assert job["worker_id"] == researcher["id"]
        assert job["adapter"] == "codex"
        assert job["model"] == "fixture-research"
        assert job["reasoning_depth"] == "high"
        assert job["additional_prompt"] == "Keep all recorded project constraints."
        assert result.startswith("# Intake assessment")
        assert failure == {}
    else:
        assert result is None
        assert failure["status"] == "failed"
        assert executed == jobs == []
    assert state.approvals == create_initial_state("intake-run", "DEMO", "Inspect existing project").approvals
