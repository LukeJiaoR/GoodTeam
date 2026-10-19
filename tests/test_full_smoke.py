import json
from pathlib import Path

import pytest

from goodteam_os import full_smoke


def test_full_smoke_preserves_approval_blocker_and_exits_nonzero(tmp_path, monkeypatch):
    output = tmp_path / "blocked-smoke"
    reason = "Record why you are continuing despite the current review limitations."
    monkeypatch.setattr(full_smoke, "run_smoke_suite", lambda **kwargs: {
        "status": "blocked", "runs": [{"project_key": "CONTENTOPS", "status": "blocked",
        "classification": "product", "blocked_reason": {"code": "approval_reason_required", "message": reason}}],
    })
    result = full_smoke.main(["--output-root", str(output), "--suite", "contentops", "--worker-mode", "fake"])
    assert result == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "blocked"
    assert summary["failures"] == [{"classification": "product", "message": reason}]
    assert reason in (output / "report.md").read_text()


def test_full_smoke_configures_isolated_contentops_and_external_self_worktree(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_root = repo_root / "output" / "full-smoke-test"
    workspace = output_root / "workspace"
    env = {}

    updates, warnings = full_smoke._configure_isolated_env(output_root, workspace, repo_root, env)

    assert updates["GOODTEAM_CONTENTOPS_ROOT"] == str(output_root / "targets" / "contentops")
    assert env["GOODTEAM_CONTENTOPS_ROOT"] == updates["GOODTEAM_CONTENTOPS_ROOT"]
    assert Path(updates["GOODTEAM_SELF_WORKTREE_ROOT"]).is_absolute()
    assert repo_root not in Path(updates["GOODTEAM_SELF_WORKTREE_ROOT"]).parents
    assert any("external temp root" in warning for warning in warnings)


def test_full_smoke_blocks_real_worker_without_explicit_opt_in(tmp_path):
    with pytest.raises(SystemExit) as exc:
        full_smoke.main(["--output-root", str(tmp_path / "out"), "--worker-mode", "codex"])

    assert "explicit opt-in only" in str(exc.value)


def test_full_smoke_real_dry_run_records_local_only_preflight(tmp_path, monkeypatch):
    output_root = tmp_path / "full-smoke"
    monkeypatch.setattr(
        full_smoke,
        "worker_backend_local_preflight",
        lambda *args, **kwargs: {
            "mode": "codex",
            "status": "ready",
            "local_only": True,
            "external_invocation": False,
            "version": "codex 1.2.3",
            "planned_command": ["codex", "exec", "<prompt-via-stdin>"],
            "cwd": "isolated-workspace",
            "sandbox_mode": "isolated-output-root",
            "message": "Codex CLI passed local readiness checks.",
        },
    )

    result = full_smoke.main(
        [
            "--output-root",
            str(output_root),
            "--suite",
            "contentops",
            "--worker-mode",
            "codex",
            "--dry-run",
            "--issue",
            "#322",
        ]
    )

    summary = json.loads((output_root / "summary.json").read_text(encoding="utf-8"))
    report = (output_root / "report.md").read_text(encoding="utf-8")
    assert result == 0
    assert summary["worker_backend_preflight"]["status"] == "ready"
    assert summary["worker_backend_preflight"]["external_invocation"] is False
    assert summary["runtime_policy_preflight"]["worker_backend"]["preflight"]["local_only"] is True
    assert "<prompt-via-stdin>" in report


def test_full_smoke_failed_real_preflight_is_reported_as_environment_failure(tmp_path, monkeypatch):
    output_root = tmp_path / "full-smoke"
    monkeypatch.setattr(
        full_smoke,
        "worker_backend_local_preflight",
        lambda *args, **kwargs: {
            "status": "fail",
            "local_only": True,
            "external_invocation": False,
            "message": "Codex CLI binary is not available.",
        },
    )

    result = full_smoke.main(
        [
            "--output-root",
            str(output_root),
            "--suite",
            "contentops",
            "--worker-mode",
            "codex",
            "--dry-run",
        ]
    )

    summary = json.loads((output_root / "summary.json").read_text(encoding="utf-8"))
    assert result == 1
    assert summary["status"] == "failed"
    assert summary["failures"] == [
        {"classification": "env", "message": "Codex CLI binary is not available."}
    ]


def test_full_smoke_dry_run_writes_standard_outputs(tmp_path, monkeypatch):
    output_root = tmp_path / "full-smoke"
    monkeypatch.delenv("GOODTEAM_CONTENTOPS_ROOT", raising=False)
    monkeypatch.delenv("GOODTEAM_SELF_WORKTREE_ROOT", raising=False)

    result = full_smoke.main(
        [
            "--output-root",
            str(output_root),
            "--suite",
            "contentops",
            "--cycles",
            "1",
            "--dry-run",
            "--issue",
            "#323",
        ]
    )

    summary_path = output_root / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert result == 0
    assert (output_root / "report.md").is_file()
    assert summary_path.is_file()
    assert (output_root / "coverage-matrix.md").is_file()
    assert (output_root / "screenshots" / "index.json").is_file()
    assert (output_root / "logs" / "environment.log").is_file()
    assert (output_root / "state-snapshots").is_dir()
    assert summary["issue"] == "#323"
    assert summary["target_roots"]["contentops"] == str(output_root / "targets" / "contentops")
    assert summary["runtime_policy_preflight"]["schema_version"] == "runtime_policy.v1"
    assert summary["runtime_policy_preflight"]["worker_backend"]["class"] == "dry-run"
    assert summary["runtime_policy_preflight"]["external_invocation"]["blocked"] is True
    assert summary["runtime_policy_preflight"]["verification"]["full_smoke_compatible"] is True
    assert summary["smoke_report"]["runs"][0]["status"] == "planned"
    assert summary["status"] == "planned"
    assert summary["evidence_mode"] == "planned"
    assert summary["real_task_acceptance"] == "not_assessed"
    assert "Result: **planned**" in (output_root / "report.md").read_text()
    assert "| contentops cycle 1 | PLANNED |" in (output_root / "coverage-matrix.md").read_text()
    assert "| self | NOT RUN |" in (output_root / "coverage-matrix.md").read_text()


@pytest.mark.parametrize("arguments", [
    ["--cycles", "0"], ["--cycles", "-1"], ["--suite", " , "], ["--suite", "contentops,contentops"],
])
def test_full_smoke_rejects_empty_execution_plan(tmp_path, monkeypatch, arguments):
    called = []
    monkeypatch.setattr(full_smoke, "run_smoke_suite", lambda **kwargs: called.append(kwargs))
    with pytest.raises(SystemExit) as exc:
        full_smoke.main(["--output-root", str(tmp_path / "out"), "--dry-run", *arguments])
    assert exc.value.code == 2
    assert not called
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("report", [
    {"status": "passed", "runs": []},
    {"status": "unknown", "runs": []},
    {"status": "passed", "runs": [{"suite": "self", "cycle": 1, "status": "passed"}]},
    {"status": "passed", "runs": [{"suite": "contentops", "cycle": 1, "status": "planned"}]},
])
def test_full_smoke_incomplete_or_inconsistent_report_cannot_pass(tmp_path, monkeypatch, report):
    output = tmp_path / "out"
    monkeypatch.setattr(full_smoke, "run_smoke_suite", lambda **kwargs: report)
    assert full_smoke.main(["--output-root", str(output), "--suite", "contentops"]) == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["failures"]


def test_full_smoke_simulated_success_is_labeled_and_not_real_acceptance(tmp_path, monkeypatch):
    output = tmp_path / "out"
    monkeypatch.setattr(full_smoke, "run_smoke_suite", lambda **kwargs: {
        "status": "passed", "runs": [{"suite": "contentops", "cycle": 1, "status": "passed"}],
    })
    assert full_smoke.main(["--output-root", str(output), "--suite", "contentops"]) == 0
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "passed"
    assert summary["evidence_mode"] == "simulated"
    assert summary["real_task_acceptance"] == "not_assessed"
    matrix = (output / "coverage-matrix.md").read_text()
    assert "| contentops cycle 1 | SIMULATED PASS |" in matrix
    assert "| self | NOT RUN |" in matrix
    assert "| M0 real-task acceptance | NOT ASSESSED |" in matrix


def test_full_smoke_registry_failure_does_not_claim_coverage(tmp_path, monkeypatch):
    output = tmp_path / "out"
    def fail_registry(*args):
        raise OSError("registry unavailable")
    monkeypatch.setattr(full_smoke, "build_smoke_project_registry", fail_registry)
    assert full_smoke.main(["--output-root", str(output), "--suite", "contentops"]) == 1
    matrix = (output / "coverage-matrix.md").read_text()
    assert "| contentops cycle 1 | NOT RUN |" in matrix
    assert "| contentops target location | NOT AVAILABLE |" in matrix
    assert "| self | NOT RUN |" in matrix
    assert "| PASS |" not in matrix


def test_full_smoke_explicit_external_target_is_not_labeled_isolated(tmp_path, monkeypatch):
    output = tmp_path / "out"
    external = tmp_path / "external"
    monkeypatch.setenv("GOODTEAM_CONTENTOPS_ROOT", str(external))
    assert full_smoke.main(["--output-root", str(output), "--suite", "contentops", "--dry-run"]) == 0
    matrix = (output / "coverage-matrix.md").read_text()
    assert "| contentops target location | OUTSIDE OUTPUT ROOT |" in matrix
    assert not external.exists()


@pytest.mark.parametrize("dry_run,cycle,status", [
    (True, 1, "passed"), (False, True, "passed"), (False, 1.0, "passed"), (False, 1, ["passed"]),
])
def test_full_smoke_invalid_run_does_not_pass_in_matrix(tmp_path, monkeypatch, dry_run, cycle, status):
    output = tmp_path / "out"
    monkeypatch.setattr(full_smoke, "run_smoke_suite", lambda **kwargs: {
        "status": "planned" if dry_run else "passed",
        "runs": [{"suite": "contentops", "cycle": cycle, "status": status}],
    })
    arguments = ["--output-root", str(output), "--suite", "contentops"]
    assert full_smoke.main([*arguments, *(["--dry-run"] if dry_run else [])]) == 1
    assert json.loads((output / "summary.json").read_text())["status"] == "failed"
    matrix = (output / "coverage-matrix.md").read_text()
    assert "| contentops cycle 1 | INVALID EVIDENCE |" in matrix
    assert "PASS" not in matrix
