"""Production Reviewer retry preserves its original persisted request.

These tests use fake Workers and do not prove Skill Runtime conformance. The
unwired Skill-backed retry path is required to reject instead of going legacy.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from goodteam_os.reviewer_dispatch import (
    artifact_version_for_path, dispatch_reviewer_jobs_for_gate,
    retry_failed_reviewer_jobs_for_gate,
)
from goodteam_os.state import create_initial_state
from goodteam_os.workers import WorkerJobStore


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    artifact = root / "original-artifact.md"
    artifact.write_text("# Original artifact\n\nReview this exact evidence.\n", encoding="utf-8")
    research = root / "research-source.md"
    research.write_text("Research captured before configuration changed.\n", encoding="utf-8")
    store = WorkerJobStore(root)
    job = store.create_job(
        run_id="run-review", project_key="DEMO", role="code_review", adapter="fake", model="original-model",
        task="Full original review request.\n\nFocus on the transaction commit boundary.\n保留原始方法。\n",
        context="Exact original context.\n\nEvidence: task snapshot and confirmation.\n",
        task_source="original-task.md", constraints_source="original-constraints.md",
        research_report_source=str(research),
        constraints=["Read only.\nPreserve this continuation line.", "Report evidence and uncertainty."],
        expected_artifacts=["my-exact-report.md"], execution_policy="read_only",
        profile_id="correctness-security", approval="qa", gate="qa", phase="implementation",
        artifact_key="qa_report", artifact_path=str(artifact), artifact_version=artifact_version_for_path(str(artifact)),
        dispatch_group_id="reviewer-dispatch-original", idempotency_key="original-key",
        worker_id="worker-original", worker_name="Original reviewer", reasoning_depth="high",
        additional_prompt="Original instructions", desired_instances=1,
        pi_provider="original-provider", pi_model="original-pi-model", pi_thinking_level="high",
    )
    job_root = store.root / job["id"]
    (job_root / "status.json").write_text(json.dumps({"status": "failed", "summary": "Original failure"}), encoding="utf-8")

    def never_run(*_args, **_kwargs):
        raise AssertionError("Explicit retry queues a request; it must not invoke a provider")

    monkeypatch.setattr(WorkerJobStore, "run_job", never_run)
    return SimpleNamespace(root=root, store=store, job=job, job_root=job_root, artifact=artifact,
                           research=research, selected=store.list_jobs())


def retry(ctx, jobs=None):
    return retry_failed_reviewer_jobs_for_gate(
        store=ctx.store, jobs=ctx.selected if jobs is None else jobs,
        run_id="run-review", approval="qa", artifact_key="qa_report",
    )


def read_job(path: Path):
    return json.loads((path / "job.json").read_text(encoding="utf-8"))


def change_job(path: Path, **changes):
    data = read_job(path)
    data.update(changes)
    (path / "job.json").write_text(json.dumps(data), encoding="utf-8")


def assert_rejected(ctx):
    outcome = retry(ctx)
    assert outcome["retried_count"] == 0
    assert len(outcome["errors"]) == 1
    assert len(ctx.store.list_jobs()) == 1
    assert outcome["approval_unchanged"] is True
    return outcome


def test_real_retry_entry_reuses_full_task_context_output_runtime_and_readonly_policy(ctx):
    ctx.research.write_text("Mutable source changed after original job capture.\n", encoding="utf-8")
    # A status projection cannot choose a new runtime or broader execution policy.
    ctx.selected[0].update(model="different-model", worker_id="other-worker", execution_policy="standard")
    before = {path.name: path.read_bytes() for path in ctx.job_root.iterdir() if path.is_file()}
    outcome = retry(ctx)
    assert outcome["errors"] == []
    assert outcome["retried_count"] == 1
    new_root = ctx.store.root / outcome["results"][0]["job_id"]
    new = read_job(new_root)
    for key in ("run_id", "project_key", "role", "adapter", "model", "worker_id", "worker_name",
                "task_source", "constraints_source", "expected_artifacts", "reasoning_depth", "additional_prompt",
                "execution_policy", "pi_provider", "pi_model", "pi_thinking_level"):
        assert new[key] == ctx.job[key]
    assert new["execution_policy"] == "read_only"
    assert new["original_failed_job_id"] == ctx.job["id"]
    assert new["retry_of_idempotency_key"] == "original-key"
    assert new["idempotency_key"] != "original-key"
    assert new["research_report_source"] == str(ctx.job_root / "research-report.md")
    for name in ("task.md", "context.md", "constraints.md", "role_prompt.md", "research-report.md"):
        assert (new_root / name).read_bytes() == before[name]
    assert json.loads((new_root / "expected_artifacts.json").read_text()) == ["my-exact-report.md"]
    for name, data in before.items():
        assert (ctx.job_root / name).read_bytes() == data


def test_concurrent_stale_retry_requests_queue_one_job(ctx):
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: retry(ctx), range(2)))
    assert all(outcome["errors"] == [] for outcome in outcomes)
    assert len({outcome["results"][0]["job_id"] for outcome in outcomes}) == 1
    assert len(ctx.store.list_jobs()) == 2
    assert retry(ctx, ctx.store.list_jobs())["retried_count"] == 0


def test_retry_of_retry_still_uses_original_full_input_bytes(ctx):
    first = retry(ctx)
    first_root = ctx.store.root / first["results"][0]["job_id"]
    (first_root / "status.json").write_text('{"status":"failed"}', encoding="utf-8")
    second = retry(ctx, ctx.store.list_jobs())
    assert second["errors"] == []
    second_root = ctx.store.root / second["results"][0]["job_id"]
    assert read_job(second_root)["retry_attempt"] == "3"
    for name in ("task.md", "context.md", "constraints.md", "research-report.md"):
        assert (second_root / name).read_bytes() == (ctx.job_root / name).read_bytes()
    assert read_job(second_root)["execution_policy"] == "read_only"


@pytest.mark.parametrize("name", ["task.md", "context.md", "constraints.md", "role_prompt.md",
                                  "research-report.md", "expected_artifacts.json"])
def test_missing_original_inputs_fail_without_a_shortened_fallback(ctx, name):
    (ctx.job_root / name).unlink()
    assert_rejected(ctx)


@pytest.mark.parametrize("policy", [None, "future-policy", ""])
def test_unknown_policy_does_not_infer_broader_permissions(ctx, policy):
    change_job(ctx.job_root, execution_policy=policy)
    assert_rejected(ctx)


def test_old_explicit_standard_policy_is_not_silently_migrated(ctx):
    change_job(ctx.job_root, execution_policy="standard")
    outcome = retry(ctx)
    assert outcome["errors"] == []
    new = read_job(ctx.store.root / outcome["results"][0]["job_id"])
    assert new["execution_policy"] == "standard"
    assert "skill_execution_snapshot_ref" not in new


@pytest.mark.parametrize("field", ["skill_execution_snapshot_ref", "task_skill_lock_ref", "task_skill_context"])
def test_unwired_skill_backed_retry_cannot_fall_back_to_legacy(ctx, field):
    change_job(ctx.job_root, **{field: {"schema_version": "skill_resource_ref.v1"}})
    outcome = assert_rejected(ctx)
    assert "legacy fallback is forbidden" in outcome["errors"][0]["error"]


def test_changed_current_artifact_does_not_change_retry_evidence(ctx):
    ctx.artifact.write_text("New artifact version requiring a new review.\n", encoding="utf-8")
    outcome = assert_rejected(ctx)
    assert "latest bytes" in outcome["errors"][0]["error"]


def test_missing_exact_artifact_does_not_queue_an_unbound_review(ctx):
    ctx.artifact.unlink()
    assert_rejected(ctx)


def test_original_role_prompt_change_requires_new_confirmation(ctx, monkeypatch):
    from goodteam_os import workers

    actual = workers._role_prompt_for_job

    def changed(role):
        config, prompt = actual(role)
        return config, prompt + "\nNew method after the task started.\n"

    monkeypatch.setattr(workers, "_role_prompt_for_job", changed)
    assert_rejected(ctx)


def test_changed_input_under_existing_retry_key_conflicts_without_rewriting_evidence(ctx):
    first = retry(ctx)
    new_root = ctx.store.root / first["results"][0]["job_id"]
    original = (new_root / "context.md").read_bytes()
    (ctx.job_root / "context.md").write_text("Changed original context", encoding="utf-8")
    second = retry(ctx)
    assert second["retried_count"] == 0
    assert "different saved input bytes" in second["errors"][0]["error"]
    assert len(ctx.store.list_jobs()) == 2
    assert (new_root / "context.md").read_bytes() == original


def test_completed_original_cannot_be_retried_from_a_stale_failed_projection(ctx):
    (ctx.job_root / "status.json").write_text('{"status":"completed"}', encoding="utf-8")
    outcome = assert_rejected(ctx)
    assert "no longer failed" in outcome["errors"][0]["error"]


def test_matching_retry_replays_existing_receipt_without_rechecking_mutable_artifact(ctx):
    first = retry(ctx)
    ctx.artifact.write_text("Later unrelated artifact content", encoding="utf-8")
    second = retry(ctx)
    assert second["errors"] == []
    assert second["results"][0]["job_id"] == first["results"][0]["job_id"]
    assert len(ctx.store.list_jobs()) == 2


def test_output_contract_mismatch_blocks_without_inventing_a_default_report(ctx):
    (ctx.job_root / "expected_artifacts.json").write_text('["other-report.md"]', encoding="utf-8")
    assert_rejected(ctx)


def test_input_symlink_is_rejected(ctx):
    path = ctx.job_root / "task.md"
    backup = ctx.root / "external-task.md"
    path.rename(backup)
    path.symlink_to(backup)
    assert_rejected(ctx)


def test_new_parallel_reviewer_has_explicit_readonly_policy(tmp_path):
    root = tmp_path.resolve()
    artifact = root / "report.md"
    artifact.write_text("Original evidence", encoding="utf-8")
    store = WorkerJobStore(root)
    state = create_initial_state("run-1", "DEMO", "Review")
    from test_graph_sample import prepare_repository_fixture
    prepare_repository_fixture(state, root)
    result = dispatch_reviewer_jobs_for_gate(
        store=store, state=state,
        target={"reviewer_profiles": {"implementation": ["correctness-security"]},
                "reviewer_dispatch": {"mode": "parallel", "phases": {"implementation": "parallel"},
                                      "rollout": {"enabled": True, "acknowledge": "parallel-reviewer-dispatch-is-advisory"}}},
        approval="qa", artifact_key="qa_report", artifact_path=str(artifact), context="Original context",
    )
    assert len(result) == 1
    assert result[0]["execution_policy"] == "read_only"
    assert store.list_jobs()[0]["execution_policy"] == "read_only"
