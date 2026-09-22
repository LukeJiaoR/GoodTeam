"""Bare Worker APIs cannot drop an existing task lock by changing the job key."""
from dataclasses import asdict
import json
import shutil

import pytest

from goodteam_os.skills.models import SkillError
from goodteam_os.skills.task_context import task_skill_context
from goodteam_os.skills.task_locks import TaskSkillIdentity
from test_worker_skill_requests import ctx as _shared_ctx

# Re-export the shared isolated fixture without shadowing an unused import.
ctx = _shared_ctx


def legacy_request(ctx, **changes):
    return {**{key: value for key, value in ctx.request.items()
               if not key.startswith(("skill_", "task_skill_"))}, **changes}


@pytest.mark.parametrize("key", ["", "changed-key"])
@pytest.mark.parametrize("entry", ["create_job", "create_or_reuse_job"])
def test_alternate_or_missing_key_cannot_detach_a_captured_workflow(ctx, key, entry):
    with pytest.raises(SkillError, match="registered method lock"):
        getattr(ctx.store, entry)(**legacy_request(ctx, idempotency_key=key))
    assert ctx.store.list_jobs() == []
    assert ctx.calls == []


def test_renamed_and_stripped_skill_job_is_blocked_before_runtime(ctx):
    job = ctx.store.create_job(**ctx.request)
    clone = ctx.store.root / "job-legacy-shaped"
    shutil.copytree(ctx.store.root / job["id"], clone)
    saved = json.loads((clone / "job.json").read_text())
    saved = {key: value for key, value in saved.items() if not key.startswith(("skill_", "task_skill_"))}
    saved.update(id=clone.name, idempotency_key="changed-key")
    (clone / "job.json").write_text(json.dumps(saved))
    before = (clone / "job.json").read_bytes()
    with pytest.raises(SkillError, match="registered method lock"):
        ctx.store.run_job(clone.name)
    assert (clone / "job.json").read_bytes() == before
    assert not (clone / "status.json").exists()
    assert ctx.calls == []


def test_explicit_issue_identity_is_checked_independently_of_run_id(ctx):
    task = TaskSkillIdentity("issue_execution", "DEMO", "issue-execution-1")
    ref = ctx.locks.begin(task, command_key="issue-start", task_input={}, resolve_plan=lambda: ctx.plan)
    context = task_skill_context(task, ref)
    assert context["task"] == asdict(task)
    with pytest.raises(SkillError, match="registered method lock"):
        ctx.store.create_job(**legacy_request(ctx, run_id="different-diagnostic-run",
                                             execution_id=task.logical_id, idempotency_key="new-issue-job"))
    assert ctx.store.list_jobs() == []
    assert ctx.calls == []


def test_unrelated_original_legacy_task_is_not_upgraded_or_blocked(ctx):
    job, created = ctx.store.create_or_reuse_job(**legacy_request(ctx, run_id="original-legacy-run",
                                                                idempotency_key="original-legacy-key"))
    assert created
    assert job["run_id"] == "original-legacy-run"
    assert not job["id"].startswith("job-skill-")
    assert "task_skill_context" not in job
    assert ctx.calls == []
