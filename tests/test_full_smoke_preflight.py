import json

import pytest

from goodteam_os import full_smoke


@pytest.mark.parametrize("worker_mode", sorted(full_smoke.REAL_WORKER_MODES))
@pytest.mark.parametrize("dry_run", [False, True])
def test_failed_preflight_stops_before_registry_or_suite(tmp_path, monkeypatch, worker_mode, dry_run):
    output = tmp_path / "output"
    message = "Worker binary failed local readiness checks."
    calls = []

    def unexpected_execution(*args, **kwargs):
        calls.append("execution")
        raise AssertionError("Failed preflight must not prepare projects or run a suite")

    monkeypatch.setattr(full_smoke, "worker_backend_local_preflight", lambda *args, **kwargs: {
        "status": "fail", "message": message, "local_only": True, "external_invocation": False,
    })
    monkeypatch.setattr(full_smoke, "build_smoke_project_registry", unexpected_execution)
    monkeypatch.setattr(full_smoke, "run_smoke_suite", unexpected_execution)
    monkeypatch.delenv("GOODTEAM_CONTENTOPS_ROOT", raising=False)
    monkeypatch.setenv("GOODTEAM_SELF_WORKTREE_ROOT", str(tmp_path / "original"))
    original_env = full_smoke._managed_env_snapshot()
    arguments = ["--repo-root", str(tmp_path), "--output-root", str(output),
                 "--suite", "contentops,self", "--worker-mode", worker_mode]
    arguments += ["--dry-run"] if dry_run else ["--allow-real-worker"]

    assert full_smoke.main(arguments) == 1

    assert calls == []
    assert full_smoke._managed_env_snapshot() == original_env
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["failures"] == [{"classification": "env", "message": message}]
    assert summary["worker_backend_preflight"]["status"] == "fail"
    assert summary["real_task_acceptance"] == "not_assessed"
    assert "smoke_report" not in summary
    assert "run_ids" not in summary
    assert "target_roots" not in summary
    assert not (output / "logs" / "smoke.json").exists()
    assert message in (output / "report.md").read_text()
    matrix = (output / "coverage-matrix.md").read_text()
    for suite in ("contentops", "self"):
        assert f"| {suite} cycle 1 | NOT RUN |" in matrix
    assert "| PASS |" not in matrix


@pytest.mark.parametrize("status", ["ready", "warn"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_nonfailed_preflight_preserves_suite_execution(tmp_path, monkeypatch, status, dry_run):
    output = tmp_path / "output"
    calls = []
    message = "Local preflight observation."
    monkeypatch.setattr(full_smoke, "worker_backend_local_preflight", lambda *args, **kwargs: {
        "status": status, "message": message, "local_only": True, "external_invocation": False,
    })
    monkeypatch.setattr(full_smoke, "build_smoke_project_registry", lambda *args: {})

    def controlled_suite(**kwargs):
        calls.append(kwargs)
        outcome = "planned" if dry_run else "passed"
        return {"status": outcome, "runs": [{"suite": "contentops", "cycle": 1, "status": outcome}]}

    monkeypatch.setattr(full_smoke, "run_smoke_suite", controlled_suite)
    arguments = ["--repo-root", str(tmp_path), "--output-root", str(output),
                 "--suite", "contentops", "--worker-mode", "codex"]
    arguments += ["--dry-run"] if dry_run else ["--allow-real-worker"]
    assert full_smoke.main(arguments) == 0
    assert len(calls) == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == ("planned" if dry_run else "passed")
    assert (message in summary["warnings"]) is (status == "warn")
