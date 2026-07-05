from pathlib import Path

import pytest

from goodteam_os.multi_reviewer_projection import build_multi_reviewer_projection
from goodteam_os.graph import run_fix_qa
from goodteam_os.reviewer_dispatch import (
    dispatch_reviewer_jobs_for_gate,
    normalize_reviewer_dispatch_config,
    reviewer_dispatch_rollout_status,
)
from goodteam_os.state import create_initial_state
from goodteam_os.workers import WorkerJobStore


def _state():
    return create_initial_state("run-1", "DEMO", "Ship the product")


def _target(*profile_ids: str, dispatch: dict[str, object] | None = None) -> dict[str, object]:
    target: dict[str, object] = {}
    if profile_ids:
        target["reviewer_profiles"] = {"implementation": list(profile_ids)}
    if dispatch is not None:
        target["reviewer_dispatch"] = dispatch
    return target


def _artifact(tmp_path: Path, name: str = "staging-report.md", content: str = "# Staging\n\nPass.\n") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _parallel_dispatch() -> dict[str, object]:
    return {
        "mode": "parallel",
        "rollout": {
            "enabled": True,
            "acknowledge": "parallel-reviewer-dispatch-is-advisory",
        },
        "phases": {"implementation": "parallel"},
    }


def _parallel_dispatch_without_ack() -> dict[str, object]:
    return {"mode": "parallel", "phases": {"implementation": "parallel"}}


def _project_config(tmp_path: Path, *, dispatch: dict[str, object] | None = None) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = tmp_path / "projects" / "demo.local.yaml"
    config.parent.mkdir()
    lines = [
        "name: Demo",
        "key: DEMO",
        "repos:",
        f"  app: {project_root}",
        "reviewer_profiles:",
        "  implementation:",
        "    - correctness-security",
        "    - architecture-maintainability",
    ]
    if dispatch is not None:
        lines.extend(
            [
                "reviewer_dispatch:",
                f"  mode: {dispatch.get('mode', 'single')}",
            ]
        )
        rollout = dispatch.get("rollout")
        if isinstance(rollout, dict):
            lines.extend(
                [
                    "  rollout:",
                    f"    enabled: {str(rollout.get('enabled', False)).lower()}",
                    f"    acknowledge: {rollout.get('acknowledge', '')}",
                ]
            )
        lines.extend(["  phases:", "    implementation: parallel"])
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config, project_root


def test_missing_dispatch_config_defaults_to_single_mode():
    assert normalize_reviewer_dispatch_config({}) == {"mode": "single", "phases": {}}
    assert normalize_reviewer_dispatch_config(None) == {"mode": "single", "phases": {}}


def test_rollout_status_requires_second_acknowledgement_for_parallel_mode():
    status = reviewer_dispatch_rollout_status({"reviewer_dispatch": _parallel_dispatch_without_ack()})

    assert status["parallel_requested"] is True
    assert status["parallel_enabled"] is False
    assert status["errors"] == []
    assert status["warnings"] == [
        "reviewer_dispatch.mode: parallel requires rollout.enabled: true and "
        'rollout.acknowledge: "parallel-reviewer-dispatch-is-advisory".'
    ]


def test_rollout_status_accepts_full_explicit_parallel_acknowledgement():
    status = reviewer_dispatch_rollout_status({"reviewer_dispatch": _parallel_dispatch()})

    assert status == {
        "parallel_requested": True,
        "parallel_enabled": True,
        "warnings": [],
        "errors": [],
    }


def test_rollout_status_accepts_project_loader_true_string():
    dispatch = _parallel_dispatch()
    dispatch["rollout"] = {
        "enabled": "true",
        "acknowledge": "parallel-reviewer-dispatch-is-advisory",
    }

    status = reviewer_dispatch_rollout_status({"reviewer_dispatch": dispatch})

    assert status["parallel_enabled"] is True


def test_unknown_dispatch_mode_fails_clearly():
    with pytest.raises(ValueError, match="reviewer_dispatch.mode"):
        normalize_reviewer_dispatch_config({"reviewer_dispatch": {"mode": "fast"}})


def test_unknown_dispatch_phase_fails_clearly():
    with pytest.raises(ValueError, match="Unknown reviewer dispatch phase"):
        normalize_reviewer_dispatch_config(
            {"reviewer_dispatch": {"mode": "parallel", "phases": {"design": "parallel"}}}
        )


def test_single_mode_does_not_create_reviewer_jobs(tmp_path: Path):
    store = WorkerJobStore(tmp_path)
    artifact = _artifact(tmp_path)

    results = dispatch_reviewer_jobs_for_gate(
        store=store,
        state=_state(),
        target=_target(
            "correctness-security",
            "architecture-maintainability",
            dispatch={"mode": "single", "phases": {"implementation": "parallel"}},
        ),
        approval="staging",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        context="review context",
    )

    assert results == []
    assert store.list_jobs() == []


def test_parallel_phase_opt_in_creates_one_job_per_profile_with_identity(tmp_path: Path):
    store = WorkerJobStore(tmp_path)
    artifact = _artifact(tmp_path)

    results = dispatch_reviewer_jobs_for_gate(
        store=store,
        state=_state(),
        target=_target(
            "correctness-security",
            "architecture-maintainability",
            dispatch=_parallel_dispatch(),
        ),
        approval="staging",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        context="review context",
    )

    assert [result["profile_id"] for result in results] == [
        "correctness-security",
        "architecture-maintainability",
    ]
    jobs = store.list_jobs()
    assert {job["profile_id"] for job in jobs} == {
        "correctness-security",
        "architecture-maintainability",
    }
    assert {job["approval"] for job in jobs} == {"staging"}
    assert {job["gate"] for job in jobs} == {"staging"}
    assert {job["phase"] for job in jobs} == {"implementation"}
    assert {job["artifact_key"] for job in jobs} == {"staging_report"}
    assert {job["artifact_path"] for job in jobs} == {str(artifact)}
    assert all(str(job["artifact_version"]).startswith("sha256:") for job in jobs)
    assert len({job["dispatch_group_id"] for job in jobs}) == 1
    assert all(job["idempotency_key"] for job in jobs)


def test_parallel_mode_without_rollout_acknowledgement_does_not_create_jobs(tmp_path: Path):
    store = WorkerJobStore(tmp_path)
    artifact = _artifact(tmp_path)

    results = dispatch_reviewer_jobs_for_gate(
        store=store,
        state=_state(),
        target=_target(
            "correctness-security",
            "architecture-maintainability",
            dispatch=_parallel_dispatch_without_ack(),
        ),
        approval="staging",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        context="review context",
    )

    assert results == []
    assert store.list_jobs() == []


def test_graph_default_project_config_does_not_create_reviewer_jobs(tmp_path: Path):
    config, project_root = _project_config(tmp_path)
    state = _state()

    result = run_fix_qa(state, tmp_path, config)

    assert result.current_node == "qa_review_gate"
    assert result.next_node == "await_qa_approval"
    assert WorkerJobStore(project_root).list_jobs() == []


def test_graph_parallel_mode_without_rollout_acknowledgement_does_not_create_reviewer_jobs(tmp_path: Path):
    config, project_root = _project_config(tmp_path, dispatch=_parallel_dispatch_without_ack())
    state = _state()

    run_fix_qa(state, tmp_path, config)

    assert WorkerJobStore(project_root).list_jobs() == []


def test_graph_full_parallel_opt_in_creates_idempotent_reviewer_jobs_with_identity(tmp_path: Path):
    config, project_root = _project_config(tmp_path, dispatch=_parallel_dispatch())
    state = _state()

    run_fix_qa(state, tmp_path, config)
    run_fix_qa(state, tmp_path, config)

    jobs = sorted(WorkerJobStore(project_root).list_jobs(), key=lambda job: str(job.get("profile_id", "")))
    assert [job["profile_id"] for job in jobs] == [
        "architecture-maintainability",
        "correctness-security",
    ]
    assert {job["approval"] for job in jobs} == {"qa"}
    assert {job["gate"] for job in jobs} == {"qa"}
    assert {job["phase"] for job in jobs} == {"implementation"}
    assert {job["artifact_key"] for job in jobs} == {"qa_report"}
    assert {job["artifact_path"] for job in jobs} == {state.artifacts["qa_report"]}
    assert all(str(job["artifact_version"]).startswith("sha256:") for job in jobs)
    assert len({job["dispatch_group_id"] for job in jobs}) == 1
    assert len({job["idempotency_key"] for job in jobs}) == 2


def test_repeated_parallel_dispatch_reuses_existing_reviewer_jobs(tmp_path: Path):
    store = WorkerJobStore(tmp_path)
    artifact = _artifact(tmp_path)
    kwargs = {
        "store": store,
        "state": _state(),
        "target": _target(
            "correctness-security",
            "architecture-maintainability",
            dispatch=_parallel_dispatch(),
        ),
        "approval": "staging",
        "artifact_key": "staging_report",
        "artifact_path": str(artifact),
        "context": "review context",
    }

    first = dispatch_reviewer_jobs_for_gate(**kwargs)
    second = dispatch_reviewer_jobs_for_gate(**kwargs)

    assert [result["job_id"] for result in second] == [result["job_id"] for result in first]
    assert len(store.list_jobs()) == 2


def test_different_artifact_version_does_not_reuse_reviewer_job(tmp_path: Path):
    store = WorkerJobStore(tmp_path)
    artifact = _artifact(tmp_path, content="# Staging\n\nFirst.\n")
    target = _target("correctness-security", dispatch=_parallel_dispatch())

    first = dispatch_reviewer_jobs_for_gate(
        store=store,
        state=_state(),
        target=target,
        approval="staging",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        context="review context",
    )
    artifact.write_text("# Staging\n\nSecond.\n", encoding="utf-8")
    second = dispatch_reviewer_jobs_for_gate(
        store=store,
        state=_state(),
        target=target,
        approval="staging",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        context="review context",
    )

    assert second[0]["job_id"] != first[0]["job_id"]
    assert len(store.list_jobs()) == 2


def test_omitted_reviewer_profiles_do_not_create_fanout_jobs(tmp_path: Path):
    store = WorkerJobStore(tmp_path)
    artifact = _artifact(tmp_path)

    results = dispatch_reviewer_jobs_for_gate(
        store=store,
        state=_state(),
        target={"reviewer_dispatch": _parallel_dispatch()},
        approval="staging",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        context="review context",
    )

    assert results == []
    assert store.list_jobs() == []


def test_projection_matches_fanout_statuses_by_profile_id(tmp_path: Path):
    store = WorkerJobStore(tmp_path)
    artifact = _artifact(tmp_path)
    target = _target(
        "correctness-security",
        "architecture-maintainability",
        dispatch=_parallel_dispatch(),
    )
    dispatch_reviewer_jobs_for_gate(
        store=store,
        state=_state(),
        target=target,
        approval="staging",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        context="review context",
    )

    projection = build_multi_reviewer_projection(
        approval="staging",
        reviewer_target=target,
        reviewer_statuses=store.list_jobs(),
    )

    assert [reviewer["match_kind"] for reviewer in projection["reviewers"]] == ["profile_id", "profile_id"]
    assert [reviewer["status"]["profile_id"] for reviewer in projection["reviewers"]] == [
        "correctness-security",
        "architecture-maintainability",
    ]
    assert projection["aggregate_policy"]["advisory_only"] is True
    assert projection["aggregate_policy"]["can_human_override"] is True
