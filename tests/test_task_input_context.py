"""Task-facing input reads use real registered captures, never current Team."""
from pathlib import Path
import json
import shutil
import urllib.request

import pytest

from goodteam_os import service, web
from goodteam_os.skills.task_start import capture_task_start
from goodteam_os.skills.task_locks import TaskSkillIdentity, TaskSkillLockStore
from goodteam_os.state import StateStore, create_initial_state
from test_task_skill_entrypoints import bind, method, ctx as ctx
from test_skill_review_http import server_url


PATH = "/api/projects/DEMO/runs/run-1/input-context"


def captured(ctx):
    selected = method(ctx, "PRIVATE_METHOD_BODY")
    bind(ctx, selected)
    context = capture_task_start(
        workspace=ctx.workspace, project_key="DEMO", worker_root=ctx.repo,
        task=TaskSkillIdentity("workflow_run", "DEMO", "run-1"), command_key="input-test-start",
        confirmed_input={"exact_request": "Original task", "source_type": "new_project",
                         "delivery_mode": "verified", "delivery_standards": ["Keep the API stable"],
                         "private_context": "PRIVATE_CONTEXT_MUST_NOT_BE_EXPOSED"},
    )
    state = create_initial_state("run-1", "DEMO", "Mutable workflow title")
    state.task_skill_context = context
    StateStore(ctx.workspace).write_project_current(state)
    return selected, context


def files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_read_original_input_and_methods_after_current_team_changes(ctx, monkeypatch):
    selected, context = captured(ctx)
    bind(ctx, method(ctx, "NEW_TEAM_METHOD", key="new"), key="new-binding")
    before = files(Path(service.service_state_dir()))
    monkeypatch.setattr(TaskSkillLockStore, "begin", lambda *_, **__: pytest.fail("GET must not recapture inputs"))
    response = web.handle_project_api_get(ctx.workspace, PATH)
    assert response is not None, "Work has no task input-context read path"
    status, result = response
    assert status == 200 and result["capture_status"] == "locked"
    assert result["input"]["exact_request"] == "Original task"
    assert result["input"]["delivery_standards"] == ["Keep the API stable"]
    assert result["task_lock_ref"]["digest"] == context["task_lock_ref"]["digest"]
    subject = next(item for item in result["subjects"] if item["subject"] == "role:product_requirement")
    assert subject["methods"] == [{key: selected[key] for key in ("skill_id", "version_id", "package_digest", "activation")}]
    serialized = json.dumps(result)
    assert "PRIVATE_CONTEXT" not in serialized and "PRIVATE_METHOD_BODY" not in serialized
    assert str(ctx.workspace) not in serialized and str(ctx.repo) not in serialized
    assert files(Path(service.service_state_dir())) == before


def test_archived_input_remains_bound_to_its_run(ctx):
    _, context = captured(ctx)
    states = StateStore(ctx.workspace)
    states.archive_project_current("DEMO", reason="completed")
    states.write_project_current(create_initial_state("run-2", "DEMO", "New current task"))
    status, result = web.handle_project_api_get(ctx.workspace, PATH)
    assert status == 200 and result["run_id"] == "run-1"
    assert result["task_lock_ref"]["digest"] == context["task_lock_ref"]["digest"]
    assert result["input"]["exact_request"] == "Original task"


@pytest.mark.parametrize("missing", ["objects", "index.json"])
def test_unavailable_capture_never_substitutes_mutable_title_or_repairs(ctx, missing):
    _, context = captured(ctx)
    root = ctx.service.locks.root / ctx.service.packages.scope.namespace
    target = root / missing
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    before = files(Path(service.service_state_dir()))
    status, result = web.handle_project_api_get(ctx.workspace, PATH)
    assert status == 200 and result["capture_status"] == "unavailable"
    assert result["input"] is None and result["subjects"] == []
    assert "Mutable workflow title" not in json.dumps(result)
    assert not target.exists()
    assert files(Path(service.service_state_dir())) == before


def test_missing_task_is_not_mapped_to_current_task(ctx):
    captured(ctx)
    status, result = web.handle_project_api_get(ctx.workspace, PATH.replace("run-1", "other-run"))
    assert status == 404 and result["error"] == "run_not_found"


def test_changed_project_root_cannot_relabel_old_input(ctx, monkeypatch):
    captured(ctx)
    real = web._project_target_and_root
    monkeypatch.setattr(web, "_project_target_and_root", lambda *args: (*real(*args)[:2], ctx.root / "different-repo"))
    status, result = web.handle_project_api_get(ctx.workspace, PATH)
    assert status == 200 and result["capture_status"] == "unavailable"
    assert result["error_code"] == "snapshot_scope_mismatch"


def test_legacy_input_is_explicitly_unverified(ctx):
    state = create_initial_state("run-1", "DEMO", "Legacy record")
    StateStore(ctx.workspace).write_project_current(state)
    before = files(Path(service.service_state_dir()))
    status, result = web.handle_project_api_get(ctx.workspace, PATH)
    assert status == 200 and result["capture_status"] == "legacy_unverified"
    assert result["input_origin"] == "workflow_state"
    assert result["input"]["exact_request"] == "Legacy record"
    assert result["task_lock_ref"] is None and result["subjects"] == []
    assert files(Path(service.service_state_dir())) == before


@pytest.mark.parametrize("handler", ["contract", "product"])
def test_actual_http_entry_reads_exact_original_input(ctx, monkeypatch, handler):
    captured(ctx)
    with server_url(ctx, monkeypatch, handler) as base:
        with urllib.request.urlopen(base + PATH, timeout=5) as response:
            assert response.status == 200
            result = json.load(response)
    assert result["schema_version"] == "goodteam.task-input-context.v1"
    assert result["project_key"] == "DEMO" and result["run_id"] == "run-1"
    assert result["task_kind"] == "workflow_run" and result["read_only"] is True
    assert result["input"]["exact_request"] == "Original task"


def test_stripped_reference_does_not_promote_mutable_state_to_legacy_input(ctx):
    captured(ctx)
    states = StateStore(ctx.workspace)
    state = states.read_project_current("DEMO")
    state.task_skill_context = {}
    states.write_project_current(state)
    status, result = web.handle_project_api_get(ctx.workspace, PATH)
    assert status == 200 and result["capture_status"] == "unavailable"
    assert result["error_code"] == "task_context_missing" and result["input"] is None


def test_legacy_read_does_not_initialize_operator_authority(tmp_path):
    from goodteam_os.projects import write_project_config
    from goodteam_os.project_registry import ProjectRegistry

    workspace, repo = tmp_path / "workspace", tmp_path / "repo"
    repo.mkdir()
    config = write_project_config(workspace / "projects", "DEMO", "Demo", repo_path=str(repo), worker_mode="fake")
    ProjectRegistry().register_project(config, root=repo, key="DEMO")
    StateStore(workspace).write_project_current(create_initial_state("run-1", "DEMO", "Legacy record"))
    operator = Path(service.service_state_dir())
    assert not operator.exists()
    status, result = web.handle_project_api_get(workspace, PATH)
    assert status == 200 and result["capture_status"] == "legacy_unverified"
    assert not operator.exists()


def test_surviving_capture_objects_prevent_legacy_after_reference_and_index_loss(ctx):
    captured(ctx)
    states = StateStore(ctx.workspace)
    state = states.read_project_current('DEMO')
    state.task_skill_context = {}
    states.write_project_current(state)
    index = ctx.service.locks.root / ctx.service.packages.scope.namespace / 'index.json'
    index.unlink()
    status, result = web.handle_project_api_get(ctx.workspace, PATH)
    assert status == 200 and result['capture_status'] == 'unavailable'
    assert result['input'] is None and not index.exists()


def test_read_does_not_recreate_lock_removed_after_store_open(ctx, monkeypatch):
    from goodteam_os.skills import task_locks
    import os

    captured(ctx)
    original = task_locks.open_lock_file
    lock = ctx.service.locks.root / ctx.service.packages.scope.namespace / '.lock'

    def disappear(fd, name, *, create=True):
        os.unlink(name, dir_fd=fd)
        return original(fd, name, create=create)

    monkeypatch.setattr(task_locks, 'open_lock_file', disappear)
    status, result = web.handle_project_api_get(ctx.workspace, PATH)
    assert status == 200 and result['capture_status'] == 'unavailable'
    assert result['input'] is None and not lock.exists()


def test_read_does_not_recreate_directory_removed_before_constructor_open(ctx, monkeypatch):
    captured(ctx)
    original = TaskSkillLockStore.__init__
    objects = ctx.service.locks.root / ctx.service.packages.scope.namespace / 'objects'

    def disappear(self, *args, **kwargs):
        shutil.rmtree(objects)
        original(self, *args, **kwargs)

    monkeypatch.setattr(TaskSkillLockStore, '__init__', disappear)
    status, result = web.handle_project_api_get(ctx.workspace, PATH)
    assert status == 200 and result['capture_status'] == 'unavailable'
    assert result['input'] is None and not objects.exists()


@pytest.mark.parametrize('archived', [False, True])
def test_foreign_legacy_state_is_not_relabelled_as_requested_project(ctx, archived):
    states = StateStore(ctx.workspace)
    path = states.write_project_current(create_initial_state('run-1', 'DEMO', 'Original'))
    if archived:
        path = states.archive_project_current('DEMO', reason='completed')
    data = json.loads(path.read_text())
    data.update(project_key='OTHER', idea='OTHER project input')
    path.write_text(json.dumps(data))
    status, result = web.handle_project_api_get(ctx.workspace, PATH)
    assert status == 200 and result['capture_status'] == 'unavailable'
    assert result['input'] is None and 'OTHER project input' not in json.dumps(result)


def test_noncreating_package_read_never_recreates_missing_lock(ctx):
    from goodteam_os.skills.store import SkillStore

    captured(ctx)
    store = SkillStore(ctx.service.packages.root, ctx.service.packages.scope, create=False)
    lock = store.root / store.scope.namespace / '.lock'
    lock.unlink()
    from goodteam_os.skills.models import SkillError

    with pytest.raises(SkillError) as error:
        store.list_installed()
    assert error.value.code == "filesystem_unavailable"
    assert not lock.exists()
