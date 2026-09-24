"""Existing dispatch/CLI path, with explicit model-free Runtime provenance.

The real task capture, Worker, child transport and report persistence are used.
Only the container/provider boundary is scripted; no native/live claim is made.
"""
import pytest

from goodteam_os.dispatch import dispatch_tracks
from goodteam_os.skills import codex_container, review_execution
from test_codex_review_runtime import AdmissionFixture, ScriptedEngine
from test_skill_review_results import report
from test_skill_review_sources import GitFixture, commits
from test_task_skill_entrypoints import bind, ctx as ctx, method


def direct(ctx):
    selected = method(ctx, "Check actual code, not a completion token.")
    bind(ctx, selected, subject="role:code_review")
    target = commits(ctx.repo)
    track = {"role": "code_review", "adapter": "codex", "model": "explicit-test-model",
             "task": "Review the exact change", "review_target": target}
    return {"project_root": ctx.repo, "tracks": [track], "allow_unsafe": True,
            "projects_dir": ctx.workspace / "projects", "logical_task_id": "direct-entry"}, target


def source_report():
    return report(evidence=[{"id": "E1", "path": "head/a.py", "line_start": 2, "line_end": 2,
                            "quote": "    return a + b", "observation": "Inspected the exact new expression."}])


def test_existing_dispatch_returns_the_saved_advisory_report_not_worker_failure(ctx, monkeypatch):
    request, target = direct(ctx)
    runtime = GitFixture(ctx.root)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    (ctx.repo / "a.py").write_text("Unrelated worktree edit; not this review's result.\n")
    result = dispatch_tracks(**request)
    job = result["jobs"][0]
    assert job["status"] == "review_received", result
    assert result["all_ok"], "A valid received advisory report is not a failed dispatch"
    assert job["outcome"] == "review_received" and result["reviews"] == [job]
    assert not result["ok"] and not result["needs_human"]
    assert job["review"]["target"]["head_sha"] == target["head_sha"]
    assert job["review"]["report_available"] and job["review"]["report"]["findings"] == []
    assert job["review"]["runtime_verification"] == "contract_fixture"
    assert job["verify"] == {"passed": None, "skipped": True, "reason": "advisory_review"}
    assert job["changed_files"] == [] and job["result_path"] == ""
    assert job["approval_changed"] is False
    assert len(runtime.records) == 1


def test_dispatch_keeps_the_composed_admission_through_executor_thread(ctx, monkeypatch):
    request, target = direct(ctx)
    admission = AdmissionFixture()
    engine = ScriptedEngine(admission.selected, output_report=source_report())
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    result = dispatch_tracks(**request, codex_review_admission=admission)
    assert result["all_ok"], result
    assert engine.attaches == 1 and engine.removed
    assert len(admission.acquires) == 1 and admission.finishes == [(admission.acquires[0], True)]
    detail = result["reviews"][0]["review"]
    assert detail["runtime"]["model"] == "explicit-test-model"
    assert detail["target"]["base_sha"] == target["base_sha"]
    assert detail["runtime_verification"] == "contract_fixture"
    # Default construction recovers this exact receipt without acquiring again.
    replay = dispatch_tracks(**request)
    assert replay["reviews"][0]["review"] == detail
    assert len(admission.acquires) == 1 and engine.attaches == 1


def test_cli_carries_explicit_model_and_returns_original_report_on_replay(ctx, monkeypatch, capsys):
    import json
    from goodteam_os.cli import main

    _request, target = direct(ctx)
    admission = AdmissionFixture()
    engine = ScriptedEngine(admission.selected, output_report=source_report())
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    argv = ["dispatch", "--project-root", str(ctx.repo), "--allow-unsafe",
            "--track", "code_review:codex:Review this exact change", "--task-id", "cli-entry",
            "--review-base", target["base_sha"], "--review-head", target["head_sha"],
            "--review-model", "explicit-test-model"]
    assert main(argv, codex_review_admission=admission) == 0
    result = json.loads(capsys.readouterr().out)
    received = result["reviews"][0]
    assert received["review"]["report_available"]
    assert received["review"]["runtime"]["model"] == "explicit-test-model"
    assert received["review"]["target"]["head_sha"] == target["head_sha"]
    assert received["review"]["runtime_verification"] == "contract_fixture"
    assert received["outcome"] == "review_received" and not result["ok"]
    bind(ctx, None, subject="role:code_review", key="after-cli-result")
    assert main(argv) == 0
    restored = json.loads(capsys.readouterr().out)
    assert restored["reviews"][0]["review"] == received["review"]
    assert engine.attaches == 1 and len(admission.acquires) == 1
    # An explicit new model is a different request, not a silent old-task retry.
    with pytest.raises(ValueError):
        main([*argv[:-1], "different-model"], codex_review_admission=admission)
    assert engine.attaches == 1 and len(admission.acquires) == 1


def test_model_cli_flag_does_not_authorize_provider_or_native_probe(ctx, monkeypatch, capsys):
    import json
    from goodteam_os.cli import main

    _request, target = direct(ctx)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: pytest.fail("No authority was composed"))
    assert main(["dispatch", "--project-root", str(ctx.repo), "--allow-unsafe",
                 "--track", "code_review:codex:Review", "--review-base", target["base_sha"],
                 "--review-head", target["head_sha"], "--review-model", "explicit-test-model"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["reviews"] == []
    assert result["jobs"][0]["skill_error_code"] == "skill_runtime_not_connected"
    assert result["jobs"][0]["result_path"] == ""


@pytest.mark.parametrize("extra", [{"codex_review_admission": {"verified": True}},
                                  {"verify_commands": {"code_review": "echo must-not-execute"}}])
def test_invalid_authority_or_post_review_shell_fails_before_task_capture(ctx, monkeypatch, extra):
    from goodteam_os.skills import task_start

    request, _target = direct(ctx)
    monkeypatch.setattr(task_start, "capture_task_start", lambda **_: pytest.fail("Invalid request recorded a task"))
    with pytest.raises(ValueError):
        dispatch_tracks(**request, **extra)
    assert ctx.workers.list_jobs() == []


def test_admitted_dispatch_requires_model_before_acquiring_or_creating_job(ctx, monkeypatch):
    request, _target = direct(ctx)
    del request["tracks"][0]["model"]
    admission = AdmissionFixture()
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: pytest.fail("No exact model"))
    with pytest.raises(ValueError, match="explicit model"):
        dispatch_tracks(**request, codex_review_admission=admission)
    assert not admission.acquires and ctx.workers.list_jobs() == []


@pytest.mark.parametrize("track,extra", [("builder:codex:Build", []), ("code_review:codex:Review", []),
    ("code_review:claude:Review", ["--review-base", "a"*40, "--review-head", "b"*40]),
    ("code_review:codex:Review", ["--review-base", "a"*40, "--review-head", "b"*40, "--review-model", "bad model"])])
def test_review_model_rejects_wrong_scope_before_dispatch(tmp_path, monkeypatch, track, extra):
    from goodteam_os import cli

    monkeypatch.setattr(cli, "dispatch_tracks", lambda **_: pytest.fail("Invalid model scope was dispatched"))
    with pytest.raises(ValueError):
        cli.main(["dispatch", "--project-root", str(tmp_path), "--track", track,
                  "--review-model", "explicit-test-model", *extra])


def test_forged_received_projection_cannot_produce_report_or_delivery_success(ctx, monkeypatch):
    from goodteam_os.workers import WorkerJobStore

    request, _target = direct(ctx)
    monkeypatch.setattr(WorkerJobStore, "run_job", lambda *_: {"status": "review_received"})
    result = dispatch_tracks(**request)
    assert not result["all_ok"] and not result["reviews"]
    assert result["jobs"][0]["outcome"] == "needs_human"
    assert result["jobs"][0]["result_path"] == "" and "review" not in result["jobs"][0]


def test_report_with_finding_is_delivered_without_approval_or_shell_execution(ctx, monkeypatch):
    request, _target = direct(ctx)
    payload = source_report()
    # Synthetic test-only finding proves that dispatch delivery is not approval.
    payload["findings"] = [{"id": "F1", "severity": "medium", "title": "Fixture finding",
        "detail": "Synthetic fixture, not a claim about this source.", "evidence_ids": ["E1"],
        "recommendation": "Human review of this synthetic test finding."}]
    admission = AdmissionFixture()
    engine = ScriptedEngine(admission.selected, output_report=payload)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    monkeypatch.setattr("goodteam_os.dispatch.run_track_verify", lambda *_args, **_kw: pytest.fail("Review ran shell"))
    result = dispatch_tracks(**request, codex_review_admission=admission)
    assert result["all_ok"] and not result["ok"]
    received = result["reviews"][0]
    assert received["review"]["finding_count"] == 1
    assert received["approval_changed"] is False and received["review"]["advisory"] is True
    assert received["verify"]["passed"] is None


@pytest.mark.parametrize("failure", ["report", "cleanup"])
def test_invalid_report_or_unknown_cleanup_never_becomes_successful_delivery(ctx, monkeypatch, failure):
    request, _target = direct(ctx)
    payload = source_report()
    if failure == "report":
        payload["evidence"][0]["path"] = "base/a.py"
    admission = AdmissionFixture(fail_finish=failure == "cleanup")
    engine = ScriptedEngine(admission.selected, output_report=payload)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    result = dispatch_tracks(**request, codex_review_admission=admission)
    assert not result["all_ok"] and not result["reviews"]
    assert result["jobs"][0]["outcome"] == "needs_human"
    assert "review" not in result["jobs"][0] and result["jobs"][0]["result_path"] == ""
