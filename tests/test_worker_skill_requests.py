"""Actual Worker create/reuse/run integration, without a provider or fake load.

Real Runtime loading remains S5. Even fake/noop cannot make a Skill-backed job
claim content delivery or execution from metadata-only evidence.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import json
import shutil
from types import SimpleNamespace

import pytest

from goodteam_os import service
from goodteam_os.skills import worker_requests as module
from goodteam_os.skills.availability import SkillAvailabilityStore
from goodteam_os.skills.bindings import default_binding
from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.skills.models import SkillConflict, SkillError
from goodteam_os.skills.task_context import task_skill_context
from goodteam_os.skills.task_locks import TaskSkillIdentity, TaskSkillLockStore, TaskSkillPlan
from goodteam_os.skills.worker_requests import WORKER_METADATA_LOADER
from goodteam_os.workers import WorkerJobStore


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    operator = root / "operator"
    repo = root / "repo"
    repo.mkdir()
    monkeypatch.setattr(service, "service_state_dir", lambda: operator)
    library = SkillLibraryService(operator)
    source = root / "method"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: own-review\ndescription: Review commit boundaries.\n---\nUse this method.\n")
    proposal = library.preview_local(str(source), command_key="install")
    installed = library.install(proposal["proposal_id"])["installed"]
    item = {"skill_id": installed["skill_id"], "version_id": installed["version_id"],
            "package_digest": proposal["package_digest"], "activation": "required", "enabled": True}
    locks = TaskSkillLockStore(operator / "skills" / "task-locks", library.store, worker_roots=(repo,))
    status = SkillAvailabilityStore(operator / "skills" / "availability", library.store, worker_roots=(repo,))
    status.initialize()
    task = TaskSkillIdentity("workflow_run", "DEMO", "run-1")
    subject = "role:code_review"
    binding = {**default_binding(subject, project_key="DEMO"), "mode": "custom", "items": [item]}
    plan = TaskSkillPlan(1, {subject: binding}, {subject: {"ref": binding["role_contract_ref"], "output": "Bounded findings"}})
    ref = locks.begin(task, command_key="start", task_input={"request": "Review"}, resolve_plan=lambda: plan)
    context = task_skill_context(task, ref)
    research = root / "research.md"
    research.write_text("Original research.\n")
    request = {"run_id": "run-1", "project_key": "DEMO", "role": "code_review", "adapter": "codex", "model": "original-model",
               "task": "Review the exact original change.\n", "context": "Original context.\n",
               "constraints": ["Read only.", "Do not approve."], "expected_artifacts": ["findings.json"],
               "execution_policy": "read_only", "idempotency_key": "review-step-1", "task_skill_context": context,
               "skill_subject": subject, "skill_loader_version": WORKER_METADATA_LOADER,
               "research_report_source": str(research)}
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("S4b must never invoke a provider, a Runtime probe, or claim a fake Skill execution")

    for name in ("_run_cli_backend", "_run_cli_worker", "_run_session_backend", "_complete_fake", "_complete_noop", "_wait_for_human"):
        monkeypatch.setattr(WorkerJobStore, name, forbidden)
    return SimpleNamespace(root=root, operator=operator, repo=repo, locks=locks, status=status, packages=library.store,
                           task=task, plan=plan, context=context, item=item, request=request, research=research,
                           store=WorkerJobStore(repo), calls=calls)


def path(ctx, job):
    return ctx.store.root / job["id"]


def test_actual_create_reuse_publishes_complete_registered_request_and_old_research(ctx):
    first, created = ctx.store.create_or_reuse_job(**ctx.request)
    assert created
    assert first["id"].startswith("job-skill-")
    registered = ctx.locks.find_snapshot(command_key=first["id"])
    assert first["skill_execution_snapshot_ref"] == registered["ref"]
    assert first["task_skill_context"] == ctx.context
    assert registered["snapshot"]["methods"]["items"] == [ctx.item]
    expected = {p.name: p.read_bytes() for p in path(ctx, first).iterdir()}
    ctx.research.write_text("Changed mutable source.\n")
    replay, created = ctx.store.create_or_reuse_job(**ctx.request)
    direct = ctx.store.create_job(**ctx.request)
    assert not created
    assert replay["id"] == direct["id"] == first["id"]
    assert {p.name: p.read_bytes() for p in path(ctx, first).iterdir()} == expected
    assert len(ctx.store.list_jobs()) == 1
    assert ctx.calls == []


@pytest.mark.parametrize("change", [
    {"task": "other"}, {"context": "other"}, {"constraints": ["other"]},
    {"model": "other"}, {"adapter": "fake"}, {"execution_policy": "standard"},
    {"expected_artifacts": ["other"]}, {"reasoning_depth": "low"},
    {"additional_prompt": "other method"}, {"worker_id": "other"},
    {"research_report_source": "different.md"}, {"skill_loader_version": "other-loader"},
])
def test_same_logical_key_with_changed_input_is_conflict_not_another_job(ctx, change):
    original = ctx.store.create_job(**ctx.request)
    with pytest.raises(SkillConflict):
        ctx.store.create_or_reuse_job(**{**ctx.request, **change})
    assert len(ctx.store.list_jobs()) == 1
    assert path(ctx, original).is_dir()
    assert ctx.calls == []


def test_required_method_change_does_not_manufacture_new_identity(ctx):
    ctx.store.create_job(**ctx.request)
    with pytest.raises(SkillConflict):
        ctx.store.create_job(**{**ctx.request, "required_skill_ids": (ctx.item["skill_id"],)})
    assert len(ctx.store.list_jobs()) == 1


def test_concurrent_identical_creation_has_one_snapshot_and_one_published_job(ctx):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ctx.store.create_or_reuse_job(**ctx.request), range(2)))
    assert len({job["id"] for job, _ in results}) == 1
    assert sorted(created for _, created in results) == [False, True]
    assert len(ctx.store.list_jobs()) == 1
    assert ctx.calls == []


@pytest.mark.parametrize("adapter", ["codex", "anthropic", "gemini", "opencode", "pi", "fake", "noop", "human"])
def test_actual_run_checks_metadata_but_never_opens_an_unverified_runtime(ctx, adapter):
    job = ctx.store.create_job(**{**ctx.request, "adapter": adapter})
    before = (path(ctx, job) / "job.json").read_bytes()
    result = WorkerJobStore(ctx.repo).run_job(job["id"])
    assert result["status"] == "failed"
    assert result["skill_error_code"] == "skill_runtime_not_connected"
    assert result["skill_preflight_status"] == "verified"
    assert result["skill_runtime_status"] == "not_connected"
    assert result["invocation_evidence"]["external_invocation"] is False
    assert result["task_skill_context"] == ctx.context
    assert (path(ctx, job) / "job.json").read_bytes() == before
    projected = ctx.store.list_jobs()[0]
    assert projected["skill_error_code"] == result["skill_error_code"]
    assert projected["skill_execution_snapshot_ref"] == job["skill_execution_snapshot_ref"]
    assert ctx.calls == []


def test_each_attempt_reads_current_revocation_without_rewriting_task_or_pins(ctx):
    job = ctx.store.create_job(**ctx.request)
    first = ctx.store.run_job(job["id"])
    assert first["skill_preflight_status"] == "verified"
    pins = ctx.packages.retention_pins(ctx.item["skill_id"], ctx.item["version_id"])
    ctx.status.revoke(ctx.item["skill_id"], ctx.item["version_id"], actor="operator", reason="Security revocation",
                      command_key="revoke", expected_revision=0)
    blocked = ctx.store.run_job(job["id"])
    assert blocked["skill_preflight_status"] == "blocked"
    assert blocked["skill_error_code"] == "skill_unavailable"
    assert first["skill_preflight_attempt_id"] != blocked["skill_preflight_attempt_id"]
    records = list((ctx.operator / "skills" / "worker-requests").glob(job["id"] + ".preflight-*.json"))
    assert len(records) == 2
    assert ctx.locks.read(ctx.context["task_lock_ref"], task=ctx.task)["subjects"]["role:code_review"]["items"] == [ctx.item]
    assert ctx.packages.retention_pins(ctx.item["skill_id"], ctx.item["version_id"]) == pins
    assert ctx.calls == []


@pytest.mark.parametrize("change", ["context", "delete-field", "policy", "identity", "missing-input"])
def test_persisted_input_or_deleted_skill_fields_cannot_enter_legacy(ctx, change):
    job = ctx.store.create_job(**ctx.request)
    job_file = path(ctx, job) / "job.json"
    data = json.loads(job_file.read_text())
    if change == "context":
        (path(ctx, job) / "context.md").write_text("Changed context")
    elif change == "delete-field":
        for key in list(data):
            if key.startswith(("skill_", "task_skill_")):
                del data[key]
    elif change == "policy":
        data["execution_policy"] = "standard"
    elif change == "identity":
        data["id"] = "job-legacy-shaped"
    else:
        (path(ctx, job) / "task.md").unlink()
    job_file.write_text(json.dumps(data))
    before = job_file.read_bytes()
    result = ctx.store.run_job(job["id"])
    assert result["skill_preflight_status"] == "blocked"
    assert result["invocation_evidence"]["external_invocation"] is False
    assert job_file.read_bytes() == before
    assert ctx.calls == []


def test_omitted_context_on_same_key_cannot_reuse_a_skill_job_as_legacy(ctx):
    ctx.store.create_job(**ctx.request)
    legacy = {k: v for k, v in ctx.request.items() if not k.startswith(("skill_", "task_skill_"))}
    for create in (ctx.store.create_or_reuse_job, ctx.store.create_job):
        with pytest.raises(ValueError, match="legacy"):
            create(**legacy)
    assert len(ctx.store.list_jobs()) == 1
    assert ctx.calls == []


@pytest.mark.parametrize("field,value", [("task_skill_context", {}), ("project_key", "OTHER"), ("run_id", "other-run"),
                                         ("skill_subject", "role:implementation")])
def test_malformed_or_detached_creation_does_not_publish_job(ctx, field, value):
    with pytest.raises(SkillError):
        ctx.store.create_job(**{**ctx.request, field: value})
    assert not ctx.store.root.exists()
    assert ctx.calls == []


def test_missing_current_status_never_reinitializes_to_unrevoked(ctx):
    job = ctx.store.create_job(**ctx.request)
    index = ctx.status.root / ctx.packages.scope.namespace / "registry" / "index.json"
    index.unlink()
    result = ctx.store.run_job(job["id"])
    assert result["skill_preflight_status"] == "blocked"
    assert not index.exists()
    assert ctx.calls == []


def test_missing_pin_blocks_actual_run_without_reacquisition(ctx):
    job = ctx.store.create_job(**ctx.request)
    holder = ctx.locks._holder(ctx.context["task_lock_ref"]["id"], "role:code_review", ctx.item["skill_id"])
    ctx.packages.release_pin(ctx.item["skill_id"], ctx.item["version_id"], holder_id=holder, command_key="release")
    result = ctx.store.run_job(job["id"])
    assert result["skill_error_code"] == "snapshot_pin_missing"
    assert ctx.packages.retention_pins(ctx.item["skill_id"], ctx.item["version_id"]) == ()
    assert ctx.calls == []


def test_partial_job_write_replays_registered_bytes_not_current_source(ctx, monkeypatch):
    actual = module.write_new

    def interrupted(fd, name, data, **kwargs):
        if name == "context.md":
            actual(fd, name, data[:3], **kwargs)
            raise OSError("during staged input publication")
        return actual(fd, name, data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module, "write_new", interrupted)
        with pytest.raises(SkillError):
            ctx.store.create_job(**ctx.request)
    assert ctx.store.list_jobs() == []
    assert not list(ctx.store.root.glob(".skill-prepare-*"))
    ctx.research.write_text("Changed later research")

    def no_recapture(*_args, **_kwargs):
        raise AssertionError("Recovery must use the registered immutable input bundle")

    monkeypatch.setattr(module, "_input_files", no_recapture)
    job = ctx.store.create_job(**ctx.request)
    assert (path(ctx, job) / "research-report.md").read_text() == "Original research.\n"
    assert ctx.calls == []


def test_published_job_loss_is_not_a_new_queued_job_or_erased_history(ctx):
    job = ctx.store.create_job(**ctx.request)
    ctx.store.run_job(job["id"])
    shutil.rmtree(path(ctx, job))
    with pytest.raises(SkillError, match="history cannot be reset"):
        ctx.store.create_job(**ctx.request)
    assert not path(ctx, job).exists()
    assert ctx.calls == []


def test_registration_lookup_does_not_resolve_bindings_or_repair_lost_registry(ctx):
    job = ctx.store.create_job(**ctx.request)
    registered = ctx.locks.find_snapshot(command_key=job["id"])
    assert registered["snapshot"]["task"] == asdict(ctx.task)
    index = ctx.locks.root / ctx.packages.scope.namespace / "index.json"
    index.unlink()
    with pytest.raises(SkillError):
        ctx.store.run_job(job["id"])
    assert not index.exists()
    assert ctx.calls == []


def test_another_worker_root_cannot_borrow_a_registered_job(ctx):
    job = ctx.store.create_job(**ctx.request)
    other = ctx.root / "other"
    other.mkdir()
    with pytest.raises(SkillError, match="another Worker root"):
        WorkerJobStore(other).run_job(job["id"])
    assert ctx.calls == []


def test_unknown_loader_is_not_enabled_by_the_request(ctx):
    job = ctx.store.create_job(**{**ctx.request, "skill_loader_version": "claimed-supported"})
    result = ctx.store.run_job(job["id"])
    assert result["skill_error_code"] == "loader_incompatible"
    assert ctx.calls == []


def test_later_subject_and_explicit_retry_keep_original_task_version(ctx):
    original = ctx.store.create_job(**ctx.request)
    # A different logical attempt uses the original carried task reference; it
    # does not re-resolve the project's currently configured methods.
    retry = ctx.store.create_job(**{**ctx.request, "idempotency_key": "review-step-1-retry", "retry_attempt": 2})
    for job in (original, retry):
        registered = ctx.locks.find_snapshot(command_key=job["id"])
        assert registered["snapshot"]["methods"]["items"] == [ctx.item]
        assert registered["snapshot"]["task_lock_ref"] == ctx.context["task_lock_ref"]
    assert ctx.calls == []


def test_original_unconfigured_context_cannot_fall_back_to_builtin(ctx):
    other = TaskSkillIdentity("workflow_run", "DEMO", "run-empty")
    plan = deepcopy(ctx.plan)
    plan.bindings["role:code_review"]["items"] = []
    ref = ctx.locks.begin(other, command_key="empty", task_input={}, resolve_plan=lambda: plan)
    job = ctx.store.create_job(**{**ctx.request, "run_id": "run-empty", "idempotency_key": "empty-job",
                                  "adapter": "fake", "task_skill_context": task_skill_context(other, ref)})
    result = ctx.store.run_job(job["id"])
    assert result["skill_error_code"] == "methods_blocked"
    assert ctx.calls == []


def test_explicit_null_context_is_not_an_implicit_legacy_request(ctx):
    with pytest.raises(SkillError):
        ctx.store.create_job(**{**ctx.request, "task_skill_context": None})
    assert ctx.store.list_jobs() == []
    assert ctx.calls == []


def test_cross_mode_concurrent_same_key_cannot_publish_two_jobs(ctx):
    legacy = {k: v for k, v in ctx.request.items() if not k.startswith(("skill_", "task_skill_"))}
    def create(request):
        try:
            return ctx.store.create_or_reuse_job(**request)[0]["id"]
        except ValueError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, (legacy, ctx.request)))
    assert results.count("conflict") == 1
    assert len(ctx.store.list_jobs()) == 1
    assert ctx.calls == []



def test_retry_cannot_name_itself_before_acquiring_request_lock(ctx, monkeypatch):
    job_id = module._job_id(ctx.repo, ctx.locks.scope, ctx.request["idempotency_key"])

    def forbidden(*_args, **_kwargs):
        raise AssertionError("A self-retry must be rejected before request locking")

    monkeypatch.setattr(module, "_request_lock", forbidden)
    with pytest.raises(SkillError, match="own original job"):
        ctx.store.create_job(**{**ctx.request, "original_failed_job_id": job_id})
    assert ctx.store.list_jobs() == []
    assert ctx.calls == []


def test_retry_unknown_parent_does_not_acquire_parent_request_lock(ctx, monkeypatch):
    from contextlib import contextmanager

    actual = module._request_lock
    acquired = []

    @contextmanager
    def one_request_lock(operator, job_id):
        acquired.append(job_id)
        assert len(acquired) == 1, "An unregistered parent must not acquire a second lock"
        with actual(operator, job_id) as fd:
            yield fd

    monkeypatch.setattr(module, "_request_lock", one_request_lock)
    with pytest.raises(SkillError, match="original Reviewer snapshot"):
        ctx.store.create_job(**{**ctx.request, "original_failed_job_id": "job-skill-" + "a" * 64})
    assert len(acquired) == 1
    assert ctx.store.list_jobs() == []
    assert ctx.calls == []
