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


def test_completed_archive_is_not_reported_as_cancelled(tmp_path, delivery):
    state, _ = delivery
    for reason in ("complete", "completed"):
        summary = build_delivery_summary(tmp_path, state=state, historical_reason=reason)
        assert summary["status"] == "needs_verification"
        assert summary["verification"] == "unverified"


def test_distinct_same_role_jobs_cannot_hide_a_failure(tmp_path, delivery):
    state, _ = delivery
    jobs = [
        {"id": "failed-task", "run_id": state.run_id, "role": "implementation", "worker_id": "worker-a", "status": "failed", "updated_at": "2026-09-19T10:00:00Z"},
        {"id": "other-task", "run_id": state.run_id, "role": "implementation", "worker_id": "worker-a", "status": "completed", "updated_at": "2026-09-19T11:00:00Z"},
    ]
    assert build_delivery_summary(tmp_path, state=state, jobs=jobs)["status"] == "blocked"


def retry_jobs(state):
    failed = {"id": "failed", "run_id": state.run_id, "role": "qa", "status": "failed",
              "worker_id": "reviewer-a", "artifact_key": "qa_report", "artifact_version": "sha256:v1",
              "idempotency_key": "first"}
    retried = {**failed, "id": "retried", "status": "completed", "idempotency_key": "second",
               "original_failed_job_id": "failed", "retry_of_idempotency_key": "first", "retry_attempt": 1}
    return failed, retried


def test_only_an_explicit_same_contract_retry_supersedes_failure(tmp_path, delivery):
    state, _ = delivery
    failed, retried = retry_jobs(state)
    summary = build_delivery_summary(tmp_path, state=state, jobs=[failed, retried])
    assert summary["status"] == "needs_verification"
    assert [item["id"] for item in summary["attempts"]] == ["failed", "retried"]
    assert summary["verification"] == "unverified"


@pytest.mark.parametrize("field,value", [
    ("artifact_version", "sha256:v2"), ("worker_id", "other-worker"),
    ("retry_of_idempotency_key", "unrelated"), ("retry_attempt", 0),
])
def test_unrelated_or_malformed_retry_cannot_hide_failure(tmp_path, delivery, field, value):
    state, _ = delivery
    failed, retried = retry_jobs(state)
    retried[field] = value
    assert build_delivery_summary(tmp_path, state=state, jobs=[failed, retried])["status"] == "blocked"


@pytest.mark.parametrize("status", ["interrupted", "timed_out", "unknown", ""])
def test_unknown_or_interrupted_job_is_not_a_clean_terminal_result(tmp_path, delivery, status):
    state, _ = delivery
    job = {"id": "one", "run_id": state.run_id, "role": "qa", "status": status}
    assert build_delivery_summary(tmp_path, state=state, jobs=[job])["status"] == "blocked"


def test_explicit_foreign_project_job_is_excluded_even_with_same_run_id(tmp_path, delivery):
    state, _ = delivery
    summary = build_delivery_summary(tmp_path, state=state, jobs=[
        {"id": "foreign", "project_key": "OTHER", "run_id": state.run_id, "status": "failed", "adapter": "fake"},
    ])
    assert summary["attempts"] == []
    assert summary["simulated"] is False


@pytest.mark.parametrize("content", [b"", b" \n\t", b"\xff\xfe"])
def test_empty_or_non_utf8_report_is_unavailable_to_summary_and_read(tmp_path, delivery, content):
    state, path = delivery
    path.write_bytes(content)
    assert build_delivery_summary(tmp_path, state=state)["artifacts"][0]["href"] == ""
    with pytest.raises(DeliveryArtifactError) as caught:
        read_delivery_artifact(tmp_path, project_key="DEMO", run_id=state.run_id,
                               artifact_key="implementation_handoff", version="sha256:any")
    assert caught.value.status == 404
    assert str(tmp_path) not in str(caught.value)


def test_report_from_another_run_directory_is_rejected(tmp_path, delivery):
    state, _ = delivery
    outside = tmp_path / "artifacts/runs/run-two/result.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("Do not return this other run's report.")
    state.artifacts["implementation_handoff"] = str(outside)
    assert build_delivery_summary(tmp_path, state=state)["artifacts"][0]["href"] == ""


def test_relative_registered_report_is_supported_without_path_escape(tmp_path, delivery):
    state, path = delivery
    state.artifacts["implementation_handoff"] = str(path.relative_to(tmp_path))
    StateStore(tmp_path).write_project_current(state)
    artifact = build_delivery_summary(tmp_path, state=state)["artifacts"][0]
    assert artifact["status"] == "available"
    result = read_delivery_artifact(tmp_path, project_key="DEMO", run_id=state.run_id,
                                    artifact_key=artifact["key"], version=artifact["version"])
    assert result["project_key"] == "DEMO"
    assert result["run_id"] == state.run_id
    assert result["version"] == artifact["version"]


def test_parent_directory_symlink_is_not_followed(tmp_path, delivery):
    state, path = delivery
    actual = path.parent.with_name("actual")
    path.parent.rename(actual)
    path.parent.symlink_to(actual, target_is_directory=True)
    assert build_delivery_summary(tmp_path, state=state)["artifacts"][0]["href"] == ""


def test_reads_preserve_durable_state_and_report_bytes(tmp_path, delivery):
    state, path = delivery
    state_file = StateStore(tmp_path).project_state_dir("DEMO") / "current.json"
    before = state_file.read_bytes(), path.read_bytes()
    artifact = build_delivery_summary(tmp_path, state=state)["artifacts"][0]
    read_delivery_artifact(tmp_path, project_key="DEMO", run_id=state.run_id,
                           artifact_key=artifact["key"], version=artifact["version"])
    assert (state_file.read_bytes(), path.read_bytes()) == before


@pytest.mark.parametrize("run_id", ["../run-one", "run/one", "", "/tmp", "run%2fone"])
def test_unregistered_or_malformed_run_ids_fail_closed(tmp_path, delivery, run_id):
    with pytest.raises(DeliveryArtifactError):
        read_delivery_artifact(tmp_path, project_key="DEMO", run_id=run_id,
                               artifact_key="implementation_handoff", version="sha256:unknown")
