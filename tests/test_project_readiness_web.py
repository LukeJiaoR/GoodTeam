from __future__ import annotations

import json
from pathlib import Path

import pytest

from goodteam_os import web
from goodteam_os.project_import import ProjectImportError, import_project


def _sync_dispatch(
    _executor,
    key: str,
    root: Path,
    execution_id: str,
    attempt: int,
    runner,
    _background: bool,
) -> None:
    runner(key, root, execution_id, attempt)


@pytest.fixture
def synchronous_ready_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web.ProjectReadinessExecutor, "_dispatch", _sync_dispatch)
    monkeypatch.setattr(
        web.ProjectReadinessExecutor,
        "_default_preflight",
        lambda _self, _key: {
            "status": "ready",
            "mode": "fake",
            "local_only": True,
            "external_invocation": False,
            "message": "ready",
        },
    )


def test_existing_readiness_api_runs_confirms_and_unlocks_real_workflow_bridge(
    tmp_path: Path,
    synchronous_ready_executor: None,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite", "test": "vitest"}}),
        encoding="utf-8",
    )
    imported = import_project(workspace, str(root))
    key = imported["project"]["project_key"]

    start_status, started = web.handle_project_api_post(
        workspace,
        f"/api/projects/{key}/readiness/start",
        {"inputs": {}, "idempotency_key": "browser-action-1"},
    )
    assert start_status == 202
    assert started["readiness"]["execution"]["status"] == "needs_input"
    command_step = next(
        step
        for step in started["readiness"]["steps"]
        if step["id"] == "command_verification"
    )
    command_ids = [item["id"] for item in command_step["input_requirements"]["commands"]]

    _status, awaiting_constraints = web.handle_project_api_post(
        workspace,
        f"/api/projects/{key}/readiness/input",
        {
            "step_id": "command_verification",
            "values": {"confirmed_command_ids": command_ids},
        },
    )
    assert awaiting_constraints["readiness"]["execution"]["current_step_id"] == "constraints_confirmation"

    _status, ready = web.handle_project_api_post(
        workspace,
        f"/api/projects/{key}/readiness/input",
        {
            "step_id": "constraints_confirmation",
            "values": {"confirmed": True},
        },
    )
    assert ready["readiness"]["lifecycle"] == "ready"
    assert ready["capabilities"]["start_workflow"] is True

    bridge_status, bridge = web.handle_project_api_post(
        workspace,
        f"/api/projects/{key}/workflow",
        {},
    )
    assert bridge_status == 200
    assert bridge == {
        "project_key": key,
        "workflow_url": f"/projects/{key}/work",
        "bridge": "product_project_workflow",
    }


def test_new_project_api_persists_brief_and_never_writes_target(
    tmp_path: Path,
    synchronous_ready_executor: None,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "empty"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]

    status, home = web.handle_project_api_post(
        workspace,
        f"/api/projects/{key}/readiness/start",
        {
            "idempotency_key": "new-1",
            "inputs": {
                "goal": "Build GoodTeam",
                "brief": "A project-ready workspace",
                "tech_preferences": "Python, React",
                "constraints": "Local first",
                "prohibited_operations": "No install without approval",
            },
        },
    )

    assert status == 202
    assert home["readiness"]["lifecycle"] == "ready"
    assert home["readiness"]["inputs"]["brief"] == "A project-ready workspace"
    assert list(root.iterdir()) == []


def test_readiness_step_artifact_api_uses_step_id_not_a_filesystem_path(
    tmp_path: Path,
    synchronous_ready_executor: None,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    web.handle_project_api_post(
        workspace,
        f"/api/projects/{key}/readiness/start",
        {"inputs": {}, "idempotency_key": "artifact-1"},
    )

    status, artifact = web.handle_project_api_get(
        workspace,
        f"/api/projects/{key}/readiness/steps/repository_understanding/artifact",
    )

    assert status == 200
    assert artifact["project_key"] == key
    assert artifact["step_id"] == "repository_understanding"
    assert artifact["content"]["scan"]["read_only"] is True


def test_workflow_bridge_rechecks_server_readiness_and_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "empty"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]

    with pytest.raises(web.ReadinessExecutionError) as captured:
        web.handle_project_api_post(workspace, f"/api/projects/{key}/workflow", {})

    assert captured.value.code == "readiness_not_ready"
    status, payload = web._project_api_error_response(captured.value)
    assert status == 409
    assert set(payload) == {"code", "message", "details", "recovery", "request_id"}
    assert payload["code"] == "readiness_not_ready"


def test_ready_workflow_bridge_rejects_same_path_root_replacement(
    tmp_path: Path,
    synchronous_ready_executor: None,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "empty"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]
    web.handle_project_api_post(
        workspace,
        f"/api/projects/{key}/readiness/start",
        {"inputs": {"goal": "Demo", "brief": "Ready project"}},
    )
    original = tmp_path / "original-root"
    root.rename(original)
    root.mkdir()
    marker = root / "replacement.txt"
    marker.write_text("must remain untouched", encoding="utf-8")

    with pytest.raises(web.ReadinessExecutionError) as captured:
        web.handle_project_api_post(workspace, f"/api/projects/{key}/workflow", {})

    assert captured.value.code == "project_root_changed"
    assert marker.read_text(encoding="utf-8") == "must remain untouched"


@pytest.mark.parametrize("mutation", ["wrong_key", "wrong_root", "missing_key", "missing_root"])
def test_home_get_rejects_cross_project_readiness_binding(
    tmp_path: Path,
    mutation: str,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]
    readiness_path = workspace / "state" / "projects" / key / "readiness.json"
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    if mutation == "wrong_key":
        readiness["project_key"] = "OTHER"
    elif mutation == "wrong_root":
        readiness["canonical_root"] = str(tmp_path / "other-private-project")
    elif mutation == "missing_key":
        readiness.pop("project_key")
    else:
        readiness.pop("canonical_root")
    readiness_path.write_text(json.dumps(readiness), encoding="utf-8")

    with pytest.raises(ProjectImportError) as captured:
        web.build_project_home(workspace, key)

    assert captured.value.code == "project_readiness_mismatch"
    assert "other-private-project" not in str(captured.value.details)


def test_project_config_key_change_between_lookup_and_load_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]
    real_load = web.load_project_target
    calls = 0

    def changing_load(config_path: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        target = dict(real_load(config_path))
        if calls >= 2:
            target["key"] = "OTHER"
        return target

    monkeypatch.setattr(web, "load_project_target", changing_load)

    with pytest.raises(web.ProjectApiError) as captured:
        web._project_target_and_root(workspace, key)

    assert captured.value.code == "project_config_changed"


def test_invalid_artifact_step_segment_uses_stable_error() -> None:
    with pytest.raises(web.ProjectApiError) as captured:
        web._decode_readiness_step_id("..%2Fsecret")
    assert captured.value.code == "invalid_readiness_step_id"


@pytest.mark.parametrize(
    "code",
    [
        "project_root_identity_missing",
        "project_entry_changed",
        "readiness_input_conflict",
        "readiness_retry_required",
        "readiness_step_binding_mismatch",
    ],
)
def test_readiness_conflicts_use_stable_409_envelope(code: str) -> None:
    error = web.ReadinessExecutionError(code, "conflict", recovery=["reload_project"])

    status, payload = web._project_api_error_response(error)

    assert status == 409
    assert payload["code"] == code
    assert set(payload) == {"code", "message", "details", "recovery", "request_id"}


def test_partial_inventory_api_retains_evidence_and_blocks_workflow(
    tmp_path: Path,
    synchronous_ready_executor: None,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("print('ok')\n")
    (root / "large.py").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    key = import_project(workspace, str(root))["project"]["project_key"]
    status, response = web.handle_project_api_post(
        workspace, f"/api/projects/{key}/readiness/start", {"inputs": {}},
    )
    assert status == 202
    assert response["readiness"]["execution"]["status"] == "failed"
    assert response["capabilities"]["start_workflow"] is False
    artifact_status, artifact = web.handle_project_api_get(
        workspace, f"/api/projects/{key}/readiness/steps/repository_understanding/artifact",
    )
    assert artifact_status == 200
    assert artifact["content"]["scan"]["snapshot_omitted_files"] == 1
    with pytest.raises(web.ReadinessExecutionError) as failure:
        web.handle_project_api_post(workspace, f"/api/projects/{key}/workflow", {})
    assert failure.value.code == "readiness_not_ready"
