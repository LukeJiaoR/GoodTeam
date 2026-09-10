import pytest
from goodteam_os.resources import ResourceDescriptor, ResourceKind, ResourceScope

from goodteam_os.delivery_outcomes import (
    DeliveryArtifactError,
    build_delivery_summary,
    read_delivery_artifact,
)
from goodteam_os.state import StateStore, create_initial_state


@pytest.fixture
def delivery(tmp_path):
    state = create_initial_state("run-one", "DEMO", "Improve diagnostics")
    path = tmp_path / "artifacts/runs/run-one/implementation/handoff.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Result\nLocal tests: not executed.\n")
    state.artifacts["implementation_handoff"] = str(path)
    state.current_node = "complete"
    state.next_node = ""
    StateStore(tmp_path).write_project_current(state)
    return state, path


def test_completed_process_is_not_verified_delivery(tmp_path, delivery):
    state, _ = delivery
    summary = build_delivery_summary(
        tmp_path,
        state=state,
        jobs=[
            {"id": "one", "run_id": state.run_id, "role": "implementation", "adapter": "codex", "status": "completed"},
            {"id": "other", "run_id": "other-run", "role": "qa", "adapter": "fake", "status": "failed"},
        ],
    )
    assert "needs_verification" == summary["status"]
    assert "unverified" == summary["verification"]
    assert ["one"] == [job["id"] for job in summary["attempts"]]
    assert "available" == summary["artifacts"][0]["status"]


def test_exact_artifact_version_and_restart(tmp_path, delivery):
    state, path = delivery
    artifact = build_delivery_summary(tmp_path, state=state)["artifacts"][0]
    result = read_delivery_artifact(
        tmp_path, project_key="DEMO", run_id=state.run_id, artifact_key=artifact["key"], version=artifact["version"]
    )
    assert path.read_text() == result["content"]
    path.write_text("New revision")
    with pytest.raises(DeliveryArtifactError) as error:
        read_delivery_artifact(
            tmp_path, project_key="DEMO", run_id=state.run_id, artifact_key=artifact["key"], version=artifact["version"]
        )
    assert 409 == error.value.status


@pytest.mark.parametrize("invalid", ["missing", "outside", "symlink", "oversized"])
def test_invalid_artifact_never_has_an_open_link(tmp_path, delivery, invalid):
    state, path = delivery
    if invalid == "missing":
        state.artifacts["implementation_handoff"] = str(path.parent / "missing.md")
    elif invalid == "outside":
        state.artifacts["implementation_handoff"] = str(tmp_path / "secret.md")
    elif invalid == "symlink":
        link = path.parent / "link.md"
        link.symlink_to(path)
        state.artifacts["implementation_handoff"] = str(link)
    else:
        path.write_bytes(b"x" * (1_048_576 + 1))
    artifact = build_delivery_summary(tmp_path, state=state)["artifacts"][0]
    assert "unavailable" == artifact["status"]
    assert "" == artifact["href"]


def test_cross_project_and_unregistered_files_are_rejected(tmp_path, delivery):
    state, _ = delivery
    for project, key in [("OTHER", "implementation_handoff"), ("DEMO", "private")]:
        with pytest.raises(DeliveryArtifactError) as error:
            read_delivery_artifact(
                tmp_path, project_key=project, run_id=state.run_id, artifact_key=key, version="sha256:unknown"
            )
        assert 404 == error.value.status


@pytest.mark.parametrize("status", ["failed", "cancelled", "running"])
def test_terminal_and_active_attempts_do_not_claim_completion(tmp_path, delivery, status):
    state, _ = delivery
    summary = build_delivery_summary(
        tmp_path,
        state=state,
        jobs=[
            {"id": "one", "run_id": state.run_id, "role": "qa", "adapter": "codex", "status": status},
        ],
    )
    assert {"failed": "blocked", "cancelled": "stopped", "running": "working"}[status] == summary["status"]


def test_simulated_jobs_remain_explicit(tmp_path, delivery):
    state, _ = delivery
    summary = build_delivery_summary(
        tmp_path,
        state=state,
        jobs=[
            {"id": "one", "run_id": state.run_id, "role": "qa", "adapter": "fake", "status": "completed"},
        ],
    )
    assert True is summary["simulated"]
    assert "needs_verification" == summary["status"]


def test_summary_without_state_is_absent(tmp_path):
    assert None is build_delivery_summary(tmp_path, state=None)


def test_resource_read_capability_cannot_be_bypassed(tmp_path, delivery):
    state, path = delivery
    state.resources["implementation_handoff"] = ResourceDescriptor(
        key="implementation_handoff",
        kind=ResourceKind.MARKDOWN_ARTIFACT,
        scope=ResourceScope.RUN,
        path=str(path),
        capabilities=[],
    )
    assert "" == build_delivery_summary(tmp_path, state=state)["artifacts"][0]["href"]


def test_archived_cancellation_keeps_its_own_report(tmp_path, delivery):
    state, path = delivery
    artifact = build_delivery_summary(tmp_path, state=state)["artifacts"][0]
    store = StateStore(tmp_path)
    store.archive_project_current("DEMO", reason="cancelled")
    newer = create_initial_state("run-new", "DEMO", "New task")
    store.write_project_current(newer)
    result = read_delivery_artifact(
        tmp_path, project_key="DEMO", run_id=state.run_id, artifact_key=artifact["key"], version=artifact["version"]
    )
    assert path.read_text() == result["content"]
    assert "stopped" == build_delivery_summary(tmp_path, state=state, historical_reason="cancelled")["status"]
