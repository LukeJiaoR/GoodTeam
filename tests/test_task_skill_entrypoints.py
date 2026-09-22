"""Actual task confirmation, graph and Manager paths; Runtime remains closed."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from goodteam_os import manager_execution, service, web
from goodteam_os.assistant_drafts import confirm_and_prepare
from goodteam_os.projects import write_project_config
from goodteam_os.skills.models import SkillConflict, SkillError
from goodteam_os.skills.task_locks import TaskSkillIdentity
from goodteam_os.skills.task_service import ProjectTaskSkillService
from goodteam_os.skills.task_start import capture_task_start
from goodteam_os.state import StateStore
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.team_control_plane import TeamControlPlaneStore, ensure_team_state
from goodteam_os.workers import WorkerJobStore


@pytest.fixture
def ctx(tmp_path):
    root = tmp_path.resolve()
    workspace = root / "workspace"
    repo = root / "repo"
    repo.mkdir()
    config = write_project_config(workspace / "projects", "DEMO", "Demo", repo_path=str(repo), worker_mode="fake")
    ensure_team_state(workspace, "DEMO", config)
    composed = ProjectTaskSkillService(workspace, "DEMO", worker_roots=(repo,))
    return SimpleNamespace(root=root, workspace=workspace, repo=repo, config=config, service=composed,
                           workers=WorkerJobStore(repo))


def method(ctx, text="Method v1", key="v1"):
    from goodteam_os.skills.library import SkillLibraryService

    source = ctx.root / "method"
    source.mkdir(exist_ok=True)
    (source / "SKILL.md").write_text("---\nname: user-method\ndescription: Inspect exact task evidence.\n---\n" + text)
    library = SkillLibraryService(service.service_state_dir())
    preview = library.preview_local(str(source), command_key=key)
    installed = library.install(preview["proposal_id"])["installed"]
    return {"skill_id": installed["skill_id"], "version_id": installed["version_id"],
            "package_digest": preview["package_digest"], "activation": "required", "enabled": True}


def bind(ctx, item, subject="role:product_requirement", key="bind"):
    current = ctx.service.team.read()
    return ctx.service.team.execute(
        [{"kind": "set_role_skills", "subject": subject, "mode": "custom" if item is not None else "default",
          "bindings": [item] if item is not None else [],
          "expected_binding_revision": current["skill_bindings"][subject]["binding_revision"]}],
        actor="user", surface="team_ui", command_key=key, expected_revision=current["revision"],
    )


def capture(ctx, logical="run-1", **changes):
    request = {"workspace": ctx.workspace, "project_key": "DEMO", "worker_root": ctx.repo,
               "task": TaskSkillIdentity("workflow_run", "DEMO", logical), "command_key": "start:" + logical,
               "confirmed_input": {"exact_request": "Original task"}}
    return capture_task_start(**{**request, **changes})


def forbid_runtime(monkeypatch):
    calls = []
    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("No provider, CLI probe or fake Skill execution is allowed")
    for name in ("_run_cli_backend", "_run_cli_worker", "_run_session_backend", "_complete_fake", "_complete_noop"):
        monkeypatch.setattr(WorkerJobStore, name, forbidden)
    return calls


def test_producer_replays_original_confirmation_without_reading_new_team(ctx, monkeypatch):
    first = method(ctx)
    bind(ctx, first)
    context = capture(ctx)
    second = method(ctx, "Method v2", "v2")
    bind(ctx, second, key="bind-v2")
    later = capture(ctx, "run-2")
    assert ctx.service.read(context)["subjects"]["role:product_requirement"]["items"] == [first]
    assert ctx.service.read(later)["subjects"]["role:product_requirement"]["items"] == [second]
    def no_team(*_args):
        raise AssertionError("A confirmed task replay must not consult current Team")
    monkeypatch.setattr(TeamControlPlaneStore, "read", no_team)
    assert capture(ctx) == context
    with pytest.raises(SkillConflict):
        capture(ctx, confirmed_input={"exact_request": "Different task"})
    with pytest.raises(SkillConflict):
        capture(ctx, worker_root=ctx.root / "different-repo")


def test_interrupted_producer_pin_recovers_original_input_after_team_change(ctx, monkeypatch):
    first = method(ctx)
    bind(ctx, first)
    from goodteam_os.skills.store import SkillStore
    original = SkillStore.pin
    def interrupted(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise OSError("after durable pin")
    with monkeypatch.context() as patch:
        patch.setattr(SkillStore, "pin", interrupted)
        with pytest.raises(SkillError):
            capture(ctx)
    second = method(ctx, "Method v2", "v2")
    bind(ctx, second, key="new-binding")
    context = capture(ctx)
    assert ctx.service.read(context)["subjects"]["role:product_requirement"]["items"] == [first]


def test_stale_confirmation_revision_rejects_before_capture(ctx):
    before = ctx.service.team.read()["revision"]
    bind(ctx, method(ctx))
    with pytest.raises(SkillConflict, match="Team changed"):
        capture(ctx, expected_team_revision=before)
    assert ctx.workers.list_jobs() == []


class Ready:
    def status(self, *_args):
        return {"lifecycle": "ready", "project_mode": "new_project", "steps": []}

    def start(self, *_args, **_kwargs):
        raise AssertionError("Ready confirmation must not invoke preparation")


def proposal(ctx):
    store = TaskAssistantSessionStore(ctx.workspace, "DEMO")
    store.append_message("m1", "user", "Original task")
    state = store.propose_task(["m1"], "Original task")
    return store, state["proposed_task"]


def confirm(ctx, draft, **extra):
    assistant = TaskAssistantService(ctx.workspace, project_config=ctx.config, readiness_executor=Ready())
    return confirm_and_prepare(assistant, project_key="DEMO", root=ctx.repo,
                               request={"proposal_id": draft["id"], "expected_revision": draft["revision"], **extra})


def test_actual_confirmation_persists_nonempty_context_before_start_and_uses_old_version(ctx):
    first = method(ctx)
    bind(ctx, first)
    store, draft = proposal(ctx)
    session, _ = confirm(ctx, draft)
    context = session["proposed_task"]["task_skill_context"]
    assert context["task"]["logical_id"] == "task-" + draft["id"]
    assert ctx.workers.list_jobs() == []
    bind(ctx, method(ctx, "v2", "v2"), key="v2")
    replay, _ = confirm(ctx, draft)
    assert replay["proposed_task"]["task_skill_context"] == context
    assert store.read()["proposed_task"]["task_skill_context"] == context
    assert ctx.service.read(context)["subjects"]["role:product_requirement"]["items"] == [first]


def test_stale_or_unresolved_draft_cannot_capture_methods(ctx):
    bind(ctx, method(ctx))
    _store, draft = proposal(ctx)
    with pytest.raises(ValueError):
        confirm(ctx, draft, expected_revision=draft["revision"] + 1)
    found = ctx.service.locks.find_task(task=TaskSkillIdentity("workflow_run", "DEMO", "task-" + draft["id"]),
                                       command_key="task-confirm:DEMO:" + draft["id"])
    assert found is None


def test_legacy_confirmation_is_not_migrated_after_new_custom_configuration(ctx):
    _store, draft = proposal(ctx)
    session, _ = confirm(ctx, draft)
    assert session["proposed_task"]["task_skill_context"] == {}
    bind(ctx, method(ctx))
    session, _ = confirm(ctx, draft)
    assert session["proposed_task"]["task_skill_context"] == {}


def test_malformed_persisted_proposal_reference_cannot_become_legacy(ctx):
    bind(ctx, method(ctx))
    store, draft = proposal(ctx)
    confirm(ctx, draft)
    data = json.loads(store.path.read_text())
    data["proposed_task"]["task_skill_context"] = None
    store.path.write_text(json.dumps(data))
    before = store.path.read_bytes()
    with pytest.raises(SkillError):
        store.read()
    assert store.path.read_bytes() == before


def test_actual_workflow_start_persists_lock_and_stops_before_fake_or_provider(ctx, monkeypatch):
    bind(ctx, method(ctx))
    calls = forbid_runtime(monkeypatch)
    result = web.start_workflow(workspace=ctx.workspace, project_key="DEMO", idea="Original task",
                                model_config=Path("models.yaml"), model_profile="balanced",
                                project_config=ctx.config, source_type="new_project", run_id="new-run")
    saved = StateStore(ctx.workspace).read_project_current("DEMO")
    assert saved.task_skill_context == result["task_skill_context"]
    assert saved.task_skill_context["task"]["logical_id"] == "new-run"
    assert saved.blockers
    assert saved.current_node != "complete"
    jobs = ctx.workers.list_jobs()
    assert jobs and all(job["id"].startswith("job-skill-") for job in jobs)
    assert all(job["status"] == "failed" for job in jobs)
    assert calls == []
    projection = web.build_project_run_history(ctx.workspace, "DEMO")
    assert projection["runs"][0]["task_skill_context"] == saved.task_skill_context


def manager_call(ctx, *, text="Original message", message="m1", logical="", context="Original context"):
    return manager_execution.run_manager_chat_turn(project_config=ctx.config, project_key="DEMO", message_id=message,
                                                    logical_message_id=logical, text=text, context=context)


def test_actual_manager_messages_lock_independently_and_same_message_reuses_one_job(ctx, monkeypatch):
    first = method(ctx)
    bind(ctx, first, subject="manager:orchestrator")
    calls = forbid_runtime(monkeypatch)
    with pytest.raises(manager_execution.ManagerExecutionError) as error:
        manager_call(ctx)
    assert error.value.code == "skill_runtime_not_connected"
    original = ctx.workers.list_jobs()[0]
    second = method(ctx, "v2", "v2")
    bind(ctx, second, subject="manager:orchestrator", key="v2")
    with pytest.raises(manager_execution.ManagerExecutionError):
        manager_call(ctx)
    assert len(ctx.workers.list_jobs()) == 1
    with pytest.raises(manager_execution.ManagerExecutionError):
        manager_call(ctx, message="m2")
    jobs = ctx.workers.list_jobs()
    assert len(jobs) == 2
    for job in jobs:
        snapshot = ctx.service.locks.find_snapshot(command_key=job["id"])["snapshot"]
        assert snapshot["methods"]["items"] == ([first] if job["id"] == original["id"] else [second])
    with pytest.raises(SkillConflict):
        manager_call(ctx, text="A different request")
    assert calls == []


def test_manager_logical_retry_keeps_first_context_across_attempts_and_team_updates(ctx, monkeypatch):
    first = method(ctx)
    bind(ctx, first, subject="manager:orchestrator")
    calls = forbid_runtime(monkeypatch)
    with pytest.raises(manager_execution.ManagerExecutionError):
        manager_call(ctx, message="attempt-1", logical="message-1")
    original = ctx.workers.list_jobs()[0]
    bind(ctx, method(ctx, "v2", "v2"), subject="manager:orchestrator", key="v2")
    with pytest.raises(manager_execution.ManagerExecutionError):
        manager_call(ctx, message="attempt-2", logical="message-1", context="New server timeline and Team summary")
    assert len(ctx.workers.list_jobs()) == 1
    snapshot = ctx.service.locks.find_snapshot(command_key=original["id"])["snapshot"]
    assert snapshot["job_input"]["request"]["context"] == "Original context"
    assert snapshot["methods"]["items"] == [first]
    assert calls == []


def test_actual_manager_intent_replays_one_job_and_new_discussion_event_is_separate(ctx, monkeypatch):
    bind(ctx, method(ctx), subject="manager:orchestrator")
    calls = forbid_runtime(monkeypatch)
    request = dict(project_config=ctx.config, project_key="DEMO", run_id="business-run", intent_id="intent-1",
                   change_request="Keep the original boundary", context="Bound artifact")
    for _ in range(2):
        with pytest.raises(manager_execution.ManagerExecutionError):
            manager_execution.run_manager_interpretation(**request)
    assert len(ctx.workers.list_jobs()) == 1
    with pytest.raises(manager_execution.ManagerExecutionError):
        manager_execution.run_manager_interpretation(**{**request, "change_request": "New explicitly continued request",
                                                         "logical_intent_id": "intent-1:event-continued"})
    assert len(ctx.workers.list_jobs()) == 2
    assert calls == []


def test_actual_assistant_retry_preserves_message_lock_even_when_builtin_is_selected(ctx, monkeypatch):
    first = method(ctx)
    bind(ctx, first, subject="manager:orchestrator")
    calls = forbid_runtime(monkeypatch)
    assistant = TaskAssistantService(ctx.workspace, project_config=ctx.config, readiness_executor=Ready())
    with pytest.raises(manager_execution.ManagerExecutionError):
        assistant.send_message(project_key="DEMO", root=ctx.repo, text="Original request", message_id="request-1")
    original = ctx.workers.list_jobs()[0]
    bind(ctx, method(ctx, "v2", "v2"), subject="manager:orchestrator", key="v2")
    with pytest.raises(manager_execution.ManagerExecutionError):
        assistant.retry_message(project_key="DEMO", root=ctx.repo, message_id="request-1", expected_attempt=1)
    assert len(ctx.workers.list_jobs()) == 1
    snapshot = ctx.service.locks.find_snapshot(command_key=original["id"])["snapshot"]
    assert snapshot["task"]["logical_id"] == "message:request-1"
    assert snapshot["methods"]["items"] == [first]
    assert calls == []


@pytest.mark.parametrize("mutation", ["empty", "missing"])
def test_lost_confirmed_reference_cannot_become_legacy_after_default_restoration(ctx, mutation):
    bind(ctx, method(ctx))
    store, draft = proposal(ctx)
    confirm(ctx, draft)
    bind(ctx, None, key="restore-default")
    data = json.loads(store.path.read_text())
    if mutation == "empty":
        data["proposed_task"]["task_skill_context"] = {}
    else:
        del data["proposed_task"]["task_skill_context"]
    store.path.write_text(json.dumps(data))
    with pytest.raises(SkillError, match="cannot become legacy"):
        confirm(ctx, draft)
    assert ctx.workers.list_jobs() == []


def test_lost_workflow_reference_cannot_generate_a_legacy_template(ctx):
    from goodteam_os.graph import run_until_next_gate
    from goodteam_os.state import create_initial_state

    bind(ctx, method(ctx))
    capture(ctx)
    state = create_initial_state("run-1", "DEMO", "Original task")
    with pytest.raises(SkillError, match="cannot become legacy"):
        run_until_next_gate(state=state, workspace=ctx.workspace, project_config=ctx.config)
    assert not (ctx.workspace / "artifacts" / "runs" / "run-1").exists()


def test_missing_runtime_configuration_does_not_generate_a_successful_skill_template(ctx):
    from goodteam_os.graph import run_until_next_gate
    from goodteam_os.state import create_initial_state

    bind(ctx, method(ctx))
    state = create_initial_state("run-1", "DEMO", "Original task")
    state.task_skill_context = capture(ctx)
    with pytest.raises(SkillError, match="fallback is forbidden"):
        run_until_next_gate(state=state, workspace=ctx.workspace, project_config=None)
    assert not (ctx.workspace / "artifacts" / "runs" / "run-1").exists()


def test_actual_late_reviewer_and_retry_keep_original_method_and_evidence(ctx, monkeypatch):
    from goodteam_os.reviewer_dispatch import dispatch_reviewer_jobs_for_gate, retry_failed_reviewer_jobs_for_gate
    from goodteam_os.review_provenance import build_reviewer_provenance
    from goodteam_os.state import create_initial_state

    first = method(ctx)
    bind(ctx, first, subject="role:code_review")
    state = create_initial_state("run-review", "DEMO", "Review original evidence")
    state.task_skill_context = capture(ctx, "run-review")
    bind(ctx, method(ctx, "new review", "v2"), subject="role:code_review", key="v2")
    artifact = ctx.root / "review.md"
    artifact.write_text("Review these original bytes")
    target = {"reviewer_profiles": {"implementation": ["correctness-security"]},
              "reviewer_dispatch": {"mode": "parallel", "phases": {"implementation": "parallel"},
                                     "rollout": {"enabled": True, "acknowledge": "parallel-reviewer-dispatch-is-advisory"}}}
    calls = forbid_runtime(monkeypatch)
    queued = dispatch_reviewer_jobs_for_gate(store=ctx.workers, state=state, target=target, approval="qa",
                                             artifact_key="qa_report", artifact_path=str(artifact), context="Original review context")
    assert len(queued) == 1
    original = queued[0]
    result = ctx.workers.run_job(original["id"])
    assert result["skill_preflight_status"] == "verified"
    provenance = build_reviewer_provenance(result)
    assert provenance["badge"]["kind"] == "blocked"
    assert provenance["invocation"]["external_invocation"] is False
    assert provenance["skill_execution"]["task_skill_context"] == state.task_skill_context
    before = {p.name: p.read_bytes() for p in (ctx.workers.root / original["id"]).iterdir() if p.is_file()}
    retried = retry_failed_reviewer_jobs_for_gate(store=ctx.workers, jobs=ctx.workers.list_jobs(),
                                                 run_id=state.run_id, approval="qa", artifact_key="qa_report")
    assert retried["errors"] == []
    assert retried["retried_count"] == 1
    retry_id = retried["results"][0]["job_id"]
    retry = ctx.service.locks.find_snapshot(command_key=retry_id)["snapshot"]
    assert retry["methods"]["items"] == [first]
    assert retry["task_lock_ref"] == state.task_skill_context["task_lock_ref"]
    assert retry["job_input"]["request"]["execution_policy"] == "read_only"
    for name in ("task.md", "context.md", "constraints.md", "role_prompt.md", "expected_artifacts.json"):
        assert (ctx.workers.root / retry_id / name).read_bytes() == before[name]
    assert ctx.workers.run_job(retry_id)["skill_preflight_status"] == "verified"
    assert calls == []


def test_actual_issue_execution_captures_identity_and_preserves_provenance_without_sandbox(ctx, monkeypatch):
    from goodteam_os.execution import ExecutionAPI, GitHubIssueSnapshot
    from goodteam_os.execution.orchestrator import ExecutionOrchestrator
    from goodteam_os.skills.task_start import execution_job_fields

    first = method(ctx)
    bind(ctx, first, subject="role:implementation")
    class FakeGitHub:
        def fetch_issue(self, repo, issue_number):
            return GitHubIssueSnapshot(repo, issue_number, "Exact issue", "Original issue body", "https://example.invalid/issue", [])
    calls = forbid_runtime(monkeypatch)
    def no_sandbox(*_args):
        raise AssertionError("Blocked methods must not create a sandbox")
    monkeypatch.setattr(ExecutionOrchestrator, "_create_sandbox_context", no_sandbox)
    api = ExecutionAPI(ctx.repo, github_client=FakeGitHub())
    initial = api.intake_github_issue(repo="owner/project", issue_number=1, adapter="fake")
    execution = initial["execution_id"]
    job = ctx.workers.list_jobs()[0]
    assert job["task_skill_context"]["task"] == {"kind": "issue_execution", "project_key": "DEMO", "logical_id": execution}
    bind(ctx, method(ctx, "v2", "v2"), subject="role:implementation", key="v2")
    assert api.intake_github_issue(repo="owner/project", issue_number=1, adapter="fake")["execution_id"] == execution
    with pytest.raises(SkillConflict):
        api.intake_github_issue(repo="owner/project", issue_number=1, adapter="codex")
    result = api.run_execution(execution)
    assert result["worker_job"]["skill_preflight_status"] == "verified"
    assert result["worker_job"]["skill_runtime_status"] == "not_connected"
    fields = execution_job_fields(ctx.workers, execution_id=execution, project_key="DEMO", role="qa")
    assert fields["task_skill_context"] == job["task_skill_context"]
    evidence = api.get_evidence(execution)
    # Evidence API keeps the original references in its ledger-derived manifest.
    rendered = json.dumps(evidence)
    assert job["skill_execution_snapshot_ref"]["id"] in rendered
    assert ctx.service.locks.find_snapshot(command_key=job["id"])["snapshot"]["methods"]["items"] == [first]
    assert calls == []


def test_actual_direct_dispatch_uses_explicit_task_identity_and_one_stable_job(ctx, monkeypatch):
    from goodteam_os.dispatch import dispatch_tracks

    first = method(ctx)
    bind(ctx, first, subject="role:implementation")
    calls = forbid_runtime(monkeypatch)
    tracks = [{"role": "implementation", "adapter": "fake", "task": "Original direct task"}]
    result = dispatch_tracks(project_root=ctx.repo, tracks=tracks, logical_task_id="bounded-direct-1", projects_dir=ctx.workspace / "projects", allow_unsafe=True)
    assert result["all_ok"] is False
    assert result["needs_human"]
    job = ctx.workers.list_jobs()[0]
    assert job["task_skill_context"]["task"] == {"kind": "direct_task", "project_key": "DEMO", "logical_id": "bounded-direct-1"}
    assert job["task_skill_context"]["task"]["logical_id"] != result["run_id"]
    bind(ctx, method(ctx, "v2", "v2"), subject="role:implementation", key="v2")
    again = dispatch_tracks(project_root=ctx.repo, tracks=tracks, logical_task_id="bounded-direct-1", projects_dir=ctx.workspace / "projects", allow_unsafe=True)
    assert again["run_id"] == result["run_id"]
    assert len(ctx.workers.list_jobs()) == 1
    with pytest.raises(SkillConflict):
        dispatch_tracks(project_root=ctx.repo, tracks=[{**tracks[0], "task": "Different task"}], logical_task_id="bounded-direct-1",
                        projects_dir=ctx.workspace / "projects", allow_unsafe=True)
    assert ctx.service.locks.find_snapshot(command_key=job["id"])["snapshot"]["methods"]["items"] == [first]
    assert calls == []


def test_unwired_skill_debate_fails_explicitly_before_any_worker(ctx, monkeypatch):
    from goodteam_os.debate import default_debate_dispatch
    from goodteam_os.state import create_initial_state

    bind(ctx, method(ctx))
    state = create_initial_state("run-1", "DEMO", "Original task")
    state.task_skill_context = capture(ctx)
    StateStore(ctx.workspace).write_project_current(state)
    calls = forbid_runtime(monkeypatch)
    with pytest.raises(SkillError, match="debate is not connected"):
        default_debate_dispatch(ctx.workspace, "DEMO", "run-1", adapter="fake")
    assert ctx.workers.list_jobs() == []
    assert calls == []


def test_worktree_helper_rejects_bound_methods_before_creating_a_branch(ctx, monkeypatch):
    from goodteam_os.state import create_initial_state

    bind(ctx, method(ctx))
    state = create_initial_state("run-1", "DEMO", "Original task")
    state.task_skill_context = capture(ctx)
    StateStore(ctx.workspace).write_project_current(state)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Unsupported Skill dispatch must fail before creating any worktree")
    monkeypatch.setattr(web, "_ensure_dispatch_worktree", forbidden)
    with pytest.raises(SkillError, match="cannot enter the legacy"):
        web.start_dispatch_track(ctx.workspace, {"project_key": "DEMO", "name": "bound-task", "role": "implementation",
                                                 "adapter": "fake", "task": "Original task"})
    assert ctx.workers.list_jobs() == []


def test_lost_project_registry_is_not_an_authorization_for_legacy_direct_execution(ctx):
    from goodteam_os.project_registry import get_default_registry_path
    from goodteam_os.skills.task_start import registered_project_scope

    bind(ctx, method(ctx))
    capture(ctx)
    get_default_registry_path().unlink()
    with pytest.raises(SkillError, match="missing scope cannot become legacy"):
        registered_project_scope(ctx.repo)
    assert ctx.workers.list_jobs() == []


def test_public_worker_summary_keeps_actual_task_binding_and_blocked_evidence(ctx, monkeypatch):
    bind(ctx, method(ctx))
    calls = forbid_runtime(monkeypatch)
    result = web.start_workflow(workspace=ctx.workspace, project_key="DEMO", idea="Original task",
                                model_config=Path("models.yaml"), model_profile="balanced",
                                project_config=ctx.config, source_type="new_project", run_id="projection-run")
    jobs = ctx.workers.list_jobs()
    projected = web._serialize_worker_job_items(jobs)
    assert projected and calls == []
    for source, job in zip(jobs, projected, strict=True):
        assert job["run_id"] == "projection-run"
        assert job["project_key"] == "DEMO"
        assert job["task_skill_context"] == result["task_skill_context"]
        assert job["skill_execution_snapshot_ref"] == source["skill_execution_snapshot_ref"]
        assert job["skill_runtime_status"] == "not_connected"
        assert job["skill_error_code"] == source["skill_error_code"]
        assert job["invocation_evidence"]["external_invocation"] is False
        assert "command" not in job["invocation_evidence"]
        assert "task_source" not in job and "research_report_source" not in job
        assert "role_prompt" not in job and "task" not in job and "context" not in job
        assert job["task_skill_context"] is not source["task_skill_context"]
    altered = dict(jobs[0])
    altered["private_input"] = "PRIVATE-BODY"
    altered["skill_execution_snapshot_ref"] = {**altered["skill_execution_snapshot_ref"], "content": "PRIVATE-BODY"}
    altered["invocation_evidence"] = {**altered["invocation_evidence"], "command": "PRIVATE-BODY"}
    assert "PRIVATE-BODY" not in json.dumps(web._serialize_worker_job_items([altered]))


@pytest.mark.parametrize("existing_legacy", [False, True])
def test_new_worktree_dispatch_cannot_hide_custom_methods_behind_legacy_state(ctx, monkeypatch, existing_legacy):
    from goodteam_os.state import create_initial_state

    bind(ctx, method(ctx), subject="role:implementation")
    if existing_legacy:
        state = create_initial_state("old-legacy", "DEMO", "Earlier legacy task")
        state.current_node = "complete"
        state.next_node = "complete"
        StateStore(ctx.workspace).write_project_current(state)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Reject unsupported custom methods before worktree creation")

    monkeypatch.setattr(web, "_ensure_dispatch_worktree", forbidden)
    with pytest.raises(SkillError, match="Custom-method worktree"):
        web.start_dispatch_track(ctx.workspace, {"project_key": "DEMO", "name": "new-task",
                                  "role": "implementation", "adapter": "fake", "task": "New task"})
    assert ctx.workers.list_jobs() == []


def test_unscoped_worktree_dispatch_is_blocked_before_branch_creation(ctx, monkeypatch):
    # An authority already exists, so dropping project_key cannot opt out of it.
    bind(ctx, method(ctx))
    capture(ctx)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Missing project scope must fail before worktree creation")

    monkeypatch.setattr(web, "_ensure_dispatch_worktree", forbidden)
    with pytest.raises(SkillError, match="explicit project scope"):
        web.start_dispatch_track(ctx.workspace, {"name": "unscoped-task", "role": "implementation",
                                  "adapter": "fake", "task": "New task"})
    assert ctx.workers.list_jobs() == []


@pytest.mark.parametrize("replacement_state", [False, True])
def test_debate_cannot_downgrade_a_registered_task_when_current_state_is_absent_or_different(ctx, monkeypatch, replacement_state):
    from goodteam_os.debate import default_debate_dispatch
    from goodteam_os.state import create_initial_state

    bind(ctx, method(ctx))
    capture(ctx, "registered-run")
    bind(ctx, None, key="restore-before-debate")
    if replacement_state:
        StateStore(ctx.workspace).write_project_current(create_initial_state("new-legacy-run", "DEMO", "Another task"))
    calls = forbid_runtime(monkeypatch)
    with pytest.raises(SkillError, match="cannot become legacy"):
        default_debate_dispatch(ctx.workspace, "DEMO", "registered-run", adapter="fake")
    assert ctx.workers.list_jobs() == []
    assert calls == []
