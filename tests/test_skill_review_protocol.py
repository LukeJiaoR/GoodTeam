"""Actual S6 task/Worker preparation; none of these fixtures verifies a Runtime."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from goodteam_os.skills.models import SkillError
from goodteam_os.skills.review_protocol import (
    REVIEW_PROTOCOL,
    is_review_contract,
    locked_review_contract,
    review_core_package,
    review_role_contract,
)
from goodteam_os.skills.worker_requests import WORKER_METADATA_LOADER
from goodteam_os.workers import WorkerJobStore
from test_task_skill_entrypoints import bind, capture, forbid_runtime, method, ctx as ctx


def custom_context(ctx, *, logical="review-run"):
    selected = method(ctx)
    bind(ctx, selected, subject="role:code_review")
    return capture(ctx, logical, subjects=("role:code_review",)), selected


def direct_request(context, **changes):
    return {
        "run_id": context["task"]["logical_id"], "project_key": "DEMO",
        "role": "code_review", "adapter": "codex", "model": "explicit-test-model",
        "task": "Check the exact requested boundary.\n", "context": "Original evidence.\n",
        "constraints": ["No file writes.", "Check the user's specified case."],
        "additional_prompt": "  User-supplied requirement; retain whitespace.\n",
        "expected_artifacts": ["report.md"], "execution_policy": "read_only",
        "idempotency_key": "code-review-1", "task_skill_context": context,
        "skill_subject": "role:code_review", "skill_loader_version": WORKER_METADATA_LOADER,
        **changes,
    }


def parallel_target(*profiles):
    return {
        "reviewer_profiles": {"implementation": list(profiles)},
        "reviewer_dispatch": {
            "mode": "parallel", "phases": {"implementation": "parallel"},
            "rollout": {"enabled": True, "acknowledge": "parallel-reviewer-dispatch-is-advisory"},
        },
        "worker_type_settings": {"code_review": {"additional_prompt": "User requires the rollover case."}},
    }


def dispatch_review(ctx, context, *, profiles=("correctness-security",)):
    from goodteam_os.reviewer_dispatch import dispatch_reviewer_jobs_for_gate

    artifact = ctx.root / "candidate.md"
    if not artifact.exists():
        artifact.write_text("Exact candidate evidence.\n")
    state = SimpleNamespace(run_id=context["task"]["logical_id"], project_key="DEMO", task_skill_context=context)
    return dispatch_reviewer_jobs_for_gate(
        store=ctx.workers, state=state, target=parallel_target(*profiles), approval="qa",
        artifact_key="qa_report", artifact_path=str(artifact), context="Original explicit requirements and evidence.\n",
    )


def test_new_task_captures_protocol_not_legacy_method_and_replays_it_without_current_defaults(ctx, monkeypatch):
    context, selected = custom_context(ctx)
    contract = locked_review_contract(ctx.workers, context)
    assert contract["protocol_schema"] == REVIEW_PROTOCOL
    assert is_review_contract(contract)
    stored = ctx.service.read(context)
    assert stored["subjects"]["role:code_review"]["items"] == [selected]
    bind(ctx, None, subject="role:code_review", key="restore-defaults")
    def cannot_resolve_again(*_args):
        raise AssertionError("Replay must use the captured contract, not the current protocol factory")
    monkeypatch.setattr("goodteam_os.skills.review_protocol.review_role_contract", cannot_resolve_again)
    assert capture(ctx, "review-run", subjects=("role:code_review",)) == context
    assert ctx.service.read(context) == stored


def test_pre_s6_task_replay_is_not_migrated_and_keeps_original_worker_bytes(ctx, monkeypatch):
    def historical_contract(ref):
        return {"ref": ref, "subject": "role:code_review", "input_contract": "Original pre-S6 contract."}
    with monkeypatch.context() as patch:
        patch.setattr("goodteam_os.skills.review_protocol.review_role_contract", historical_contract)
        context, _selected = custom_context(ctx)
    request = direct_request(context, execution_policy="standard")
    first = ctx.workers.create_job(**request)
    path = ctx.workers.root / first["id"]
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    assert locked_review_contract(ctx.workers, context) is None
    assert not (path / "review_prompt_base.md").exists()
    assert capture(ctx, "review-run", subjects=("role:code_review",)) == context
    assert ctx.workers.create_job(**request)["id"] == first["id"]
    assert {p.name: p.read_bytes() for p in path.iterdir()} == before
    new_context = capture(ctx, "new-review", subjects=("role:code_review",))
    assert locked_review_contract(ctx.workers, new_context)["protocol_schema"] == REVIEW_PROTOCOL


def test_actual_worker_prepares_exact_explicit_inputs_without_hidden_methods(ctx, monkeypatch):
    context, _selected = custom_context(ctx)
    calls = forbid_runtime(monkeypatch)
    task = "User explicitly requests Risk assessment and a Learned section.\n"
    request = direct_request(context, task=task)
    job = ctx.workers.create_job(**request)
    snapshot = ctx.service.locks.find_snapshot(command_key=job["id"])["snapshot"]
    captured = snapshot["job_input"]["files"]
    base = captured["review_prompt_base.md"]
    inputs = json.loads(base.split("## Original task inputs\n\n", 1)[1])
    assert inputs == {key: request[key] for key in ("task", "context", "constraints", "expected_artifacts", "additional_prompt")}
    assert captured["task.md"] == task
    assert "Memory Write-Back Sections" not in base
    assert "PRIMARY decision context" not in base
    assert "At the end, summarize changed files" not in base
    assert "Checks quality, security, performance" not in base
    assert captured["role_memory_paths.json"] == captured["role_pickup_packet.json"] == "{}"
    assert (ctx.workers.root / job["id"] / "review_prompt_base.md").read_text() == base
    result = ctx.workers.run_job(job["id"])
    assert result["skill_error_code"] == "skill_runtime_not_connected"
    assert result["invocation_evidence"]["external_invocation"] is False
    assert calls == []


@pytest.mark.parametrize("profile_id", ["correctness-security", "architecture-maintainability", "product-operator"])
def test_actual_parallel_reviewer_keeps_target_identity_but_not_profile_methods(ctx, monkeypatch, profile_id):
    from goodteam_os import reviewer_dispatch
    from goodteam_os.reviewer_profiles import get_reviewer_profile

    context, _selected = custom_context(ctx)
    calls = forbid_runtime(monkeypatch)
    original = dispatch_review(ctx, context, profiles=(profile_id,))[0]
    snapshot = ctx.service.locks.find_snapshot(command_key=original["id"])["snapshot"]
    request = snapshot["job_input"]["request"]
    task = request["task"]
    target = json.loads(task.split("\n\n", 1)[1])
    assert target == {key: request[key] for key in ("approval", "artifact_key", "artifact_path", "artifact_version")}
    assert target["artifact_version"].startswith("sha256:")
    assert request["profile_id"] == profile_id
    assert request["execution_policy"] == "read_only"
    assert request["additional_prompt"] == "User requires the rollover case."
    assert "Original explicit requirements" in request["context"]
    profile = get_reviewer_profile(profile_id)
    assert profile.label not in task
    assert all(area not in task for area in profile.focus_areas)
    assert "Required report sections:" not in task
    mutated = replace(profile, label="Changed method label", focus_areas=("New hidden checklist",),
                      required_report_sections=("Another hidden template",))
    monkeypatch.setattr(reviewer_dispatch, "get_reviewer_profile", lambda _profile_id: mutated)
    assert dispatch_review(ctx, context, profiles=(profile_id,))[0]["id"] == original["id"]
    assert calls == []


def test_missing_exact_artifact_cannot_create_new_protocol_reviewer(ctx):
    from goodteam_os.reviewer_dispatch import dispatch_reviewer_jobs_for_gate

    context, _selected = custom_context(ctx)
    state = SimpleNamespace(run_id="review-run", project_key="DEMO", task_skill_context=context)
    with pytest.raises(SkillError):
        dispatch_reviewer_jobs_for_gate(store=ctx.workers, state=state, target=parallel_target("correctness-security"),
                                       approval="qa", artifact_key="qa_report", artifact_path=str(ctx.root / "missing"), context="Exact task")
    assert ctx.workers.list_jobs() == []


@pytest.mark.parametrize("changes", [
    {"execution_policy": "standard"}, {"session_policy": "resume"},
    {"worker_session_ref": "builder-session"},
])
def test_new_protocol_rejects_writable_or_borrowed_session_before_publication(ctx, changes):
    context, _selected = custom_context(ctx)
    with pytest.raises(SkillError) as error:
        ctx.workers.create_job(**direct_request(context, **changes))
    assert error.value.code == "review_execution_policy_invalid"
    assert ctx.workers.list_jobs() == []


def test_real_direct_producer_selects_readonly_and_preserves_the_user_task(ctx, monkeypatch):
    from goodteam_os.dispatch import dispatch_tracks

    selected = method(ctx)
    bind(ctx, selected, subject="role:code_review")
    calls = forbid_runtime(monkeypatch)
    text = "Review ONLY the requested rollover, not a default checklist.\n"
    tracks = [{"role": "code_review", "adapter": "fake", "task": text, "expected_artifacts": ["report.md"]}]
    result = dispatch_tracks(project_root=ctx.repo, tracks=tracks, logical_task_id="direct-review",
                             projects_dir=ctx.workspace / "projects", allow_unsafe=True)
    assert not result["all_ok"]
    job = ctx.workers.list_jobs()[0]
    snapshot = ctx.service.locks.find_snapshot(command_key=job["id"])["snapshot"]
    assert snapshot["job_input"]["request"]["execution_policy"] == "read_only"
    assert snapshot["job_input"]["request"]["session_policy"] == "new"
    assert snapshot["job_input"]["files"]["task.md"] == text
    assert snapshot["methods"]["items"] == [selected]
    assert calls == []


def test_actual_reviewer_retry_copies_protocol_base_without_new_team_or_method_lookup(ctx, monkeypatch):
    from goodteam_os.reviewer_dispatch import retry_failed_reviewer_jobs_for_gate

    context, selected = custom_context(ctx)
    calls = forbid_runtime(monkeypatch)
    original = dispatch_review(ctx, context)[0]
    result = ctx.workers.run_job(original["id"])
    assert result["skill_preflight_status"] == "verified"
    snapshot = ctx.service.locks.find_snapshot(command_key=original["id"])["snapshot"]
    files_before = deepcopy(snapshot["job_input"]["files"])
    bind(ctx, None, subject="role:code_review", key="restore-before-retry")
    def no_new_prompt(*_args):
        raise AssertionError("Retry must copy the original base, not rebuild current instructions")
    monkeypatch.setattr("goodteam_os.skills.review_protocol.review_prompt_base", no_new_prompt)
    retried = retry_failed_reviewer_jobs_for_gate(store=ctx.workers, jobs=ctx.workers.list_jobs(),
                                                 run_id="review-run", approval="qa", artifact_key="qa_report")
    assert retried["errors"] == []
    assert retried["retried_count"] == 1
    retry = ctx.service.locks.find_snapshot(command_key=retried["results"][0]["job_id"])["snapshot"]
    assert retry["job_input"]["files"] == files_before
    assert retry["methods"]["items"] == [selected]
    assert retry["task_lock_ref"] == snapshot["task_lock_ref"]
    assert retry["job_input"]["request"]["execution_policy"] == "read_only"
    assert calls == []


def test_legacy_prompt_builder_cannot_wrap_a_protected_skill_job(ctx):
    context, _selected = custom_context(ctx)
    job = ctx.workers.create_job(**direct_request(context))
    with pytest.raises(SkillError) as error:
        ctx.workers._worker_prompt(ctx.workers.root / job["id"], job)
    assert error.value.code == "skill_runtime_not_connected"


def test_unbound_legacy_code_review_keeps_its_original_prompt_and_memory_contract(tmp_path):
    from goodteam_os.roles import stable_role_prompt

    store = WorkerJobStore(tmp_path)
    job = store.create_job(run_id="legacy", role="code_review", adapter="fake", task="Original task", context="Evidence")
    root = store.root / job["id"]
    assert (root / "role_prompt.md").read_text() == stable_role_prompt("code_review")
    assert not (root / "review_prompt_base.md").exists()
    assert "## Memory Write-Back Sections" in store._worker_prompt(root, job)


@pytest.mark.parametrize("mutation", ["version", "method", "subject"])
def test_unknown_or_modified_protocol_is_not_silently_accepted(mutation):
    contract = review_role_contract("role:code_review@v1")
    if mutation == "version":
        contract["protocol_schema"] = "unknown"
    elif mutation == "method":
        contract["hidden_method"] = "Mandatory secret checklist"
    else:
        contract["subject"] = "role:implementation"
    with pytest.raises(SkillError):
        is_review_contract(contract)


def test_optional_builtin_is_a_standard_first_party_package_not_an_implicit_binding(ctx):
    before = ctx.service.team.read()
    package, source = review_core_package()
    assert source.kind == "builtin" and source.locator == "builtin:review-core"
    assert b"defects, regressions, maintainability" in package.resource("SKILL.md")
    assert b"Security regressions" in package.resource("references/profile-methods.md")
    assert b"No external Skill" in package.resource("NOTICE.md")
    assert package.digest == review_core_package()[0].digest
    assert ctx.service.team.read() == before
    assert ctx.workers.list_jobs() == []
    # An explicit installation does not make the preset mandatory either.
    published = ctx.service.packages.publish(package, source, command_key="explicit-builtin-install")
    context, selected = custom_context(ctx)
    locked = ctx.service.read(context)["subjects"]["role:code_review"]["items"]
    assert locked == [selected]
    assert all(item["skill_id"] != published.installed.skill_id for item in locked)


def test_actual_artifact_producer_prepares_readonly_code_review_and_stays_blocked(ctx, monkeypatch):
    from goodteam_os.graph import generate_artifact_via_worker
    from goodteam_os.state import create_initial_state

    context, _selected = custom_context(ctx)
    calls = forbid_runtime(monkeypatch)
    state = create_initial_state("review-run", "DEMO", "Review exact evidence")
    state.task_skill_context = context
    with pytest.raises(SkillError) as error:
        generate_artifact_via_worker(state=state, workspace=ctx.workspace, project_config=ctx.config,
                                     role="code_review", artifact_key="code_review", task_text="User-directed review.\n")
    assert error.value.code == "skill_runtime_not_connected"
    job = ctx.workers.list_jobs()[0]
    snapshot = ctx.service.locks.find_snapshot(command_key=job["id"])["snapshot"]
    assert snapshot["job_input"]["request"]["execution_policy"] == "read_only"
    assert snapshot["job_input"]["files"]["task.md"] == "User-directed review.\n"
    assert "review_prompt_base.md" in snapshot["job_input"]["files"]
    assert calls == []


def test_protocol_selection_rejects_a_different_worker_root(ctx):
    context, _selected = custom_context(ctx)
    other = ctx.root / "other-worker"
    other.mkdir()
    with pytest.raises(SkillError) as error:
        locked_review_contract(WorkerJobStore(other), context)
    assert error.value.code == "snapshot_scope_mismatch"
    assert not (other / ".goodteam").exists()
