"""Portable state references, not automatic task-start or Runtime activation."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from goodteam_os.skills.bindings import default_binding
from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.skills.models import SkillError, SkillScope
from goodteam_os.skills.store import SkillStore
from goodteam_os.skills.task_context import (
    task_skill_context, validate_task_skill_context, workflow_skill_context,
)
from goodteam_os.skills.task_locks import TaskSkillIdentity, TaskSkillLockStore, TaskSkillPlan
from goodteam_os.state import AgileDevState, StateStore, create_initial_state


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(StateStore, "_candidate_project_state_dirs", lambda *_args: [])
    packages = SkillLibraryService(root / "operator").store
    locks = TaskSkillLockStore(root / "locks", packages)
    task = TaskSkillIdentity("workflow_run", "DEMO", "run-1")
    subject = "role:code_review"
    binding = {**default_binding(subject, project_key="DEMO"), "mode": "custom"}
    plan = TaskSkillPlan(3, {subject: binding}, {subject: {
        "ref": binding["role_contract_ref"], "input": "Bounded evidence", "output": "Findings",
    }})
    ref = locks.begin(task, command_key="start-run-1", task_input={"intent": "Review"}, resolve_plan=lambda: plan)
    return root, locks, task, task_skill_context(task, ref)


def state_with(context):
    return AgileDevState("run-1", "DEMO", "Review", task_skill_context=context)


def test_current_checkpoint_and_archive_preserve_exact_bound_reference(ctx):
    root, locks, task, context = ctx
    store = StateStore(root / "workspace")
    state = state_with(context)
    global_path = store.write_current(state)
    current = store.write_project_current(state)
    assert json.loads(global_path.read_text())["task_skill_context"] == context
    assert store.read_current().task_skill_context == context
    assert store.read_project_current("DEMO").task_skill_context == context
    for checkpoint in (store.write_checkpoint(state), store.write_project_checkpoint(state)):
        payload = json.loads(checkpoint.read_text())
        assert payload["task_skill_context"] == context
        payload.pop("checkpoint")
        restored = AgileDevState(**payload)
        assert restored.task_skill_context == context
    archived = store.archive_project_current("DEMO", reason="completed")
    assert not current.exists()
    history = store.list_project_archived("DEMO")
    assert len(history) == 1
    assert history[0][0] == archived
    assert history[0][1].task_skill_context == context
    assert locks.read(history[0][1].task_skill_context["task_lock_ref"], task=task)["team_revision"] == 3


def test_serialization_and_reads_never_open_the_operator_store_or_create_pins(ctx, monkeypatch):
    root, _locks, _task, context = ctx

    def unexpected(*_args, **_kwargs):
        raise AssertionError("State I/O must not resolve methods, repair locks or acquire pins")

    monkeypatch.setattr(SkillStore, "__init__", unexpected)
    monkeypatch.setattr(TaskSkillLockStore, "__init__", unexpected)
    monkeypatch.setattr(TaskSkillLockStore, "begin", unexpected)
    monkeypatch.setattr(TaskSkillLockStore, "read", unexpected)
    store = StateStore(root / "workspace")
    state = state_with(context)
    store.write_current(state)
    store.write_project_current(state)
    store.write_project_checkpoint(state)
    assert store.read_current().task_skill_context == context
    assert store.read_project_current("demo").task_skill_context == context
    store.archive_project_current("DEMO")
    assert store.list_project_archived("DEMO")[0][1].task_skill_context == context


def test_absent_legacy_field_remains_legacy_without_rewriting_the_input(tmp_path, monkeypatch):
    monkeypatch.setattr(StateStore, "_candidate_project_state_dirs", lambda *_args: [])
    store = StateStore(tmp_path.resolve() / "workspace")
    state = create_initial_state("old-run", "DEMO", "Old task")
    assert state.task_skill_context == {}
    path = store.write_project_current(state)
    payload = json.loads(path.read_text())
    payload.pop("task_skill_context")
    original = json.dumps(payload).encode()
    path.write_bytes(original)
    loaded = store.read_project_current("DEMO")
    assert loaded.task_skill_context == {}
    assert path.read_bytes() == original
    assert not (tmp_path / "operator").exists()


def test_context_is_copied_not_aliased_to_the_callers_mutable_reference(ctx):
    _root, _locks, task, context = ctx
    ref = deepcopy(context["task_lock_ref"])
    constructed = task_skill_context(task, ref)
    ref["digest"] = "sha256:" + "f" * 64
    assert constructed == context
    state = state_with(constructed)
    constructed["task"]["logical_id"] = "different-run"
    assert state.task_skill_context == context


@pytest.mark.parametrize("patch", [
    {"schema_version": "task_skill_context.v2"},
    {"task": None},
    {"task": {"kind": [], "project_key": "DEMO", "logical_id": "run-1"}},
    {"task_lock_ref": None},
    {"unexpected": True},
])
def test_malformed_context_is_not_treated_as_legacy(ctx, patch):
    _root, _locks, _task, context = ctx
    with pytest.raises(SkillError):
        state_with({**context, **patch})


@pytest.mark.parametrize("value", [None, [], "", False])
def test_only_an_empty_object_is_an_explicit_legacy_context(value):
    with pytest.raises(SkillError):
        AgileDevState("run-1", "DEMO", "Review", task_skill_context=value)
    assert workflow_skill_context({}, run_id="old", project_key="DEMO") == {}


@pytest.mark.parametrize("patch", [
    {"schema_version": "skill_resource_ref.v2"},
    {"resource_schema": "skill_execution_snapshot.v1"},
    {"digest": "latest"},
    {"id": "task-" + "0" * 64},
    {"scope": {"kind": "user", "owner_id": "another-owner", "project_key": ""}},
    {"scope": {"kind": "project", "owner_id": "owner", "project_key": "OTHER"}},
    {"scope": {"kind": [], "owner_id": "owner", "project_key": ""}},
    {"path": "/untrusted/lock.json"},
])
def test_invalid_or_detached_task_lock_reference_fails_closed(ctx, patch):
    _root, _locks, _task, context = ctx
    with pytest.raises(SkillError):
        validate_task_skill_context({**context, "task_lock_ref": {**context["task_lock_ref"], **patch}})


@pytest.mark.parametrize("kind,project,logical", [
    ("workflow_run", "DEMO", "run-2"),
    ("workflow_run", "OTHER", "run-1"),
    ("issue_execution", "DEMO", "run-1"),
    ("direct_task", "DEMO", "run-1"),
    ("manager_message", "DEMO", "message-1"),
])
def test_valid_reference_of_another_task_cannot_be_lent_to_workflow_state(ctx, kind, project, logical):
    _root, locks, _task, _context = ctx
    identity = TaskSkillIdentity(kind, project, logical)
    subject = "role:code_review"
    binding = default_binding(subject, project_key=project)
    plan = TaskSkillPlan(3, {subject: binding}, {subject: {"ref": binding["role_contract_ref"]}})
    ref = locks.begin(identity, command_key=f"start-{kind}-{project}-{logical}", task_input={}, resolve_plan=lambda: plan)
    other = task_skill_context(identity, ref)
    assert validate_task_skill_context(other) == other
    with pytest.raises(SkillError, match="another run, project or task kind"):
        state_with(other)


def test_project_override_cannot_rehome_a_task_lock_or_mutate_state(ctx):
    root, _locks, _task, context = ctx
    store = StateStore(root / "workspace")
    state = state_with(context)
    for write in (store.write_project_current, store.write_project_checkpoint):
        with pytest.raises(SkillError, match="another run, project or task kind"):
            write(state, project_key="OTHER")
        assert state.project_key == "DEMO"
    assert not (root / "workspace").exists()


def test_mutated_context_is_revalidated_before_current_or_checkpoint_write(ctx):
    root, _locks, _task, context = ctx
    store = StateStore(root / "workspace")
    state = state_with(context)
    current = store.write_project_current(state)
    before = current.read_bytes()
    state.task_skill_context["task"]["logical_id"] = "different-run"
    for write in (store.write_current, store.write_project_current, store.write_checkpoint, store.write_project_checkpoint):
        with pytest.raises(SkillError):
            write(state)
    assert current.read_bytes() == before
    assert not (root / "workspace" / "state" / "checkpoints").exists()
    assert not (current.parent / "checkpoints").exists()


def test_corrupt_persisted_context_is_rejected_without_repair(ctx):
    root, _locks, _task, context = ctx
    store = StateStore(root / "workspace")
    path = store.write_project_current(state_with(context))
    payload = json.loads(path.read_text())
    payload["task_skill_context"] = None
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    with pytest.raises(SkillError):
        store.read_project_current("DEMO")
    assert path.read_bytes() == before


def test_project_read_rejects_a_valid_context_stored_under_another_project(ctx):
    root, _locks, _task, context = ctx
    store = StateStore(root / "workspace")
    wrong = root / "workspace" / "state" / "projects" / "OTHER" / "current.json"
    wrong.parent.mkdir(parents=True)
    wrong.write_text(json.dumps(asdict(state_with(context))))
    with pytest.raises(SkillError, match="another run, project or task kind"):
        store.read_project_current("OTHER")


def test_context_owner_validation_is_not_a_claim_of_authorization(ctx):
    _root, _locks, _task, context = ctx
    assert context["task_lock_ref"]["scope"] == asdict(SkillScope("user", "local-operator"))
    # A syntactically valid reference still needs trusted-store preflight. The
    # structural reader cannot prove resource existence from a digest alone.
    value = deepcopy(context)
    value["task_lock_ref"]["digest"] = "sha256:" + "f" * 64
    assert validate_task_skill_context(value) == value
