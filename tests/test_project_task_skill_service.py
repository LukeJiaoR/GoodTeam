"""Real Team/Library/current-status composition; no Runtime or provider invocation."""
from copy import deepcopy

import pytest

from goodteam_os import service
from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.skills.models import SkillConflict, SkillError
from goodteam_os.skills.task_locks import TaskSkillIdentity
from goodteam_os.skills.task_service import ProjectTaskSkillService
from goodteam_os.team_control_plane import TeamControlPlaneStore

LOADER = "test-metadata-loader.v1"
SUBJECT = "role:code_review"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    operator = root / "operator"
    workspace = root / "workspace"
    repo = root / "repo"
    repo.mkdir()
    monkeypatch.setattr(service, "service_state_dir", lambda: operator)
    team = TeamControlPlaneStore(workspace, "DEMO")
    team.load_or_migrate(legacy_target={"worker_backend": {"mode": "fake"}})
    composed = ProjectTaskSkillService(
        workspace, "DEMO", worker_roots=(repo,), supported_loaders=frozenset({LOADER}),
    )
    return root, operator, workspace, composed


def install(project, text="Version one", key="v1"):
    root, operator, _workspace, _composed = project
    source = root / "review-method"
    source.mkdir(exist_ok=True)
    (source / "SKILL.md").write_text(
        "---\nname: review-method\ndescription: Review supplied evidence.\n---\n" + text + "\n",
        encoding="utf-8",
    )
    library = SkillLibraryService(operator)
    preview = library.preview_local(str(source), command_key="preview-" + key)
    installed = library.install(preview["proposal_id"])["installed"]
    return {"skill_id": installed["skill_id"], "version_id": installed["version_id"],
            "package_digest": preview["package_digest"], "activation": "required", "enabled": True}


def bind(composed, items, *, subject=SUBJECT, mode="custom", key="bind-v1"):
    current = composed.team.read()
    return composed.team.execute(
        [{"kind": "set_role_skills", "subject": subject, "mode": mode, "bindings": items,
          "expected_binding_revision": current["skill_bindings"][subject]["binding_revision"]}],
        actor="user", surface="team_ui", command_key=key, expected_revision=current["revision"],
    )


def start(composed, logical="T1", *, subjects=(SUBJECT,), kind="workflow_run", revision=None, key=None, **changes):
    current = composed.team.read()
    contracts = {subject: {"ref": current["skill_bindings"][subject]["role_contract_ref"],
                           "input": "Bounded evidence", "output": "Findings"} for subject in subjects}
    request = {
        "command_key": key or "start-" + logical,
        "confirmed_input": {"goal": "persistent-goal", "request": "Review this change"},
        "subjects": subjects,
        "role_contracts": contracts,
        "expected_team_revision": current["revision"] if revision is None else revision,
        **changes,
    }
    return composed.begin(TaskSkillIdentity(kind, "DEMO", logical), **request)


def job_input():
    return {"task": "Review exact diff", "context": "Original evidence", "execution_policy": "read_only",
            "runtime": {"adapter": "codex", "model": "model-a"}, "expected_artifacts": ["findings.json"]}


def snapshot(composed, context, *, key="job-T1-review", request=None, subject=SUBJECT):
    return composed.snapshot(context, subject=subject, command_key=key,
                             job_input=request or job_input(), loader_version=LOADER)


def test_real_team_change_cannot_change_late_reviewer_or_old_task_retry(project):
    _root, _operator, _workspace, composed = project
    v1 = install(project)
    bound = bind(composed, [v1])
    revision = bound["team_revision"]
    t1 = start(composed, subjects=(SUBJECT, "role:qa"))
    v2 = install(project, "Version two", "v2")
    bind(composed, [v2], key="bind-v2")
    late = snapshot(composed, t1)
    verified = composed.preflight(t1, late, command_key="job-T1-review", job_input=job_input())
    assert verified["methods"]["items"] == [v1]
    assert composed.read(t1)["subjects"]["role:qa"]["diagnostics"] == [{"code": "default_unresolved"}]
    replay = start(composed, subjects=(SUBJECT, "role:qa"), revision=revision)
    assert replay == t1
    assert snapshot(composed, replay) == late
    retry = snapshot(composed, replay, key="job-T1-review-retry")
    assert composed.preflight(replay, retry, command_key="job-T1-review-retry", job_input=job_input())["methods"]["items"] == [v1]
    t2 = start(composed, "T2")
    second = snapshot(composed, t2, key="job-T2-review")
    assert composed.preflight(t2, second, command_key="job-T2-review", job_input=job_input())["methods"]["items"] == [v2]
    assert t1["task_lock_ref"] != t2["task_lock_ref"]


def test_restore_default_does_not_downgrade_an_existing_custom_task(project):
    _root, _operator, _workspace, composed = project
    v1 = install(project)
    revision = bind(composed, [v1])["team_revision"]
    t1 = start(composed)
    bind(composed, [], mode="default", key="restore-default")
    assert start(composed, revision=revision) == t1
    assert composed.read(t1)["subjects"][SUBJECT]["binding"]["mode"] == "custom"
    old_job = snapshot(composed, t1)
    assert composed.preflight(t1, old_job, command_key="job-T1-review", job_input=job_input())["methods"]["items"] == [v1]


def test_stale_new_confirmation_rejects_but_exact_old_start_replays_without_reading_team(project, monkeypatch):
    _root, _operator, _workspace, composed = project
    bind(composed, [install(project)])
    state = composed.team.read()
    revision = state["revision"]
    t1 = start(composed)
    bind(composed, [], mode="default", key="default-after-start")
    with pytest.raises(SkillConflict, match="Team changed"):
        start(composed, "T2", revision=revision)
    contracts = {SUBJECT: {"ref": state["skill_bindings"][SUBJECT]["role_contract_ref"],
                           "input": "Bounded evidence", "output": "Findings"}}

    def unexpected(*_args, **_kwargs):
        raise AssertionError("A task replay or job derivation must not read current Team truth")

    monkeypatch.setattr(composed.team, "read", unexpected)
    replay = composed.begin(TaskSkillIdentity("workflow_run", "DEMO", "T1"), command_key="start-T1",
                            confirmed_input={"goal": "persistent-goal", "request": "Review this change"},
                            subjects=(SUBJECT,), role_contracts=contracts, expected_team_revision=revision)
    assert replay == t1
    selected = snapshot(composed, replay)
    assert composed.preflight(replay, selected, command_key="job-T1-review", job_input=job_input())["task"]["logical_id"] == "T1"


@pytest.mark.parametrize("change", [
    {"task": "Different task"},
    {"context": "Different evidence"},
    {"execution_policy": "standard"},
    {"runtime": {"adapter": "codex", "model": "model-b"}},
    {"expected_artifacts": []},
])
def test_same_logical_job_key_never_turns_different_input_into_another_job(project, change):
    _root, _operator, _workspace, composed = project
    bind(composed, [install(project)])
    context = start(composed)
    original = snapshot(composed, context)
    with pytest.raises(SkillConflict):
        snapshot(composed, context, request={**job_input(), **change})
    assert snapshot(composed, context) == original


def test_current_revocation_is_checked_on_each_preflight_not_captured_forever(project):
    _root, _operator, _workspace, composed = project
    item = install(project)
    bind(composed, [item])
    context = start(composed)
    selected = snapshot(composed, context)
    assert composed.preflight(context, selected, command_key="job-T1-review", job_input=job_input())["methods"]["items"] == [item]
    composed.availability.revoke(item["skill_id"], item["version_id"], actor="user", reason="Security revocation",
                                 command_key="revoke-v1", expected_revision=composed.availability.revision())
    with pytest.raises(SkillError):
        composed.preflight(context, selected, command_key="job-T1-review", job_input=job_input())
    assert composed.read(context)["subjects"][SUBJECT]["items"] == [item]
    assert composed.packages.retention_pins(item["skill_id"], item["version_id"])


def test_status_claim_in_job_input_cannot_override_the_trusted_status_reader(project):
    _root, _operator, _workspace, composed = project
    item = install(project)
    bind(composed, [item])
    context = start(composed)
    request = {**job_input(), "current_availability": {"available": True, "revoked": False}}
    selected = snapshot(composed, context, request=request)
    composed.availability.revoke(item["skill_id"], item["version_id"], actor="user", reason="Security revocation",
                                 command_key="revoke-v1", expected_revision=0)
    with pytest.raises(SkillError):
        composed.preflight(context, selected, command_key="job-T1-review", job_input=request)


def test_replay_and_read_never_recreate_a_missing_revocation_index(project):
    _root, _operator, _workspace, composed = project
    item = install(project)
    revision = bind(composed, [item])["team_revision"]
    context = start(composed)
    selected = snapshot(composed, context)
    index = composed.availability.root / composed.packages.scope.namespace / "registry" / "index.json"
    assert index.is_file()
    index.unlink()
    assert start(composed, revision=revision) == context
    assert composed.read(context)["subjects"][SUBJECT]["items"] == [item]
    assert not index.exists()
    with pytest.raises(SkillError):
        composed.preflight(context, selected, command_key="job-T1-review", job_input=job_input())
    assert not index.exists()


def test_unresolved_default_and_empty_custom_are_not_implicit_legacy_fallback(project):
    _root, _operator, _workspace, composed = project
    default = start(composed)
    default_job = snapshot(composed, default)
    with pytest.raises(SkillError):
        composed.preflight(default, default_job, command_key="job-T1-review", job_input=job_input())
    bind(composed, [], key="empty-custom")
    empty = start(composed, "T2")
    empty_job = snapshot(composed, empty, key="job-T2-review")
    with pytest.raises(SkillError):
        composed.preflight(empty, empty_job, command_key="job-T2-review", job_input=job_input())


def test_manager_messages_share_no_project_wide_skill_epoch(project):
    _root, _operator, _workspace, composed = project
    subject = "manager:orchestrator"
    v1 = install(project)
    revision = bind(composed, [v1], subject=subject)["team_revision"]
    first = start(composed, "message-1", kind="manager_message", subjects=(subject,))
    v2 = install(project, "Manager version two", "v2")
    bind(composed, [v2], subject=subject, key="bind-manager-v2")
    assert start(composed, "message-1", kind="manager_message", subjects=(subject,), revision=revision) == first
    second = start(composed, "message-2", kind="manager_message", subjects=(subject,))
    assert composed.read(first)["subjects"][subject]["items"] == [v1]
    assert composed.read(second)["subjects"][subject]["items"] == [v2]
    with pytest.raises(SkillError):
        TaskSkillIdentity("manager_message", "DEMO", "task-assistant:DEMO")


def test_another_project_service_cannot_read_or_derive_from_this_context(project):
    _root, _operator, workspace, composed = project
    context = start(composed)
    other = ProjectTaskSkillService(workspace, "OTHER", supported_loaders=frozenset({LOADER}))
    with pytest.raises(SkillError, match="does not belong"):
        other.read(context)
    with pytest.raises(SkillError, match="does not belong"):
        snapshot(other, context)
    changed = deepcopy(context)
    changed["task_lock_ref"]["scope"]["owner_id"] = "another-owner"
    with pytest.raises(SkillError):
        composed.read(changed)


def test_server_loader_support_defaults_to_fail_closed(project):
    _root, _operator, workspace, composed = project
    bind(composed, [install(project)])
    context = start(composed)
    selected = snapshot(composed, context)
    unopened = ProjectTaskSkillService(workspace, "DEMO")
    with pytest.raises(SkillError):
        unopened.preflight(context, selected, command_key="job-T1-review", job_input=job_input())


def test_operator_state_inside_worker_root_is_rejected_before_creation(tmp_path, monkeypatch):
    workspace = tmp_path.resolve() / "workspace"
    monkeypatch.setattr(service, "service_state_dir", lambda: workspace / "operator")
    with pytest.raises(SkillError, match="Worker-writable"):
        ProjectTaskSkillService(workspace, "DEMO")
    assert not workspace.exists()


def test_unknown_subject_cannot_be_manufactured_by_a_caller(project):
    _root, _operator, _workspace, composed = project
    with pytest.raises(SkillError, match="unknown Team or workflow subject"):
        composed.begin(TaskSkillIdentity("direct_task", "DEMO", "direct-1"), command_key="start-direct-1",
                       confirmed_input={}, subjects=("role:invented",),
                       role_contracts={"role:invented": {"ref": "role:invented@v1"}},
                       expected_team_revision=composed.team.read()["revision"])
    with pytest.raises(SkillError):
        composed.availability.revision()
