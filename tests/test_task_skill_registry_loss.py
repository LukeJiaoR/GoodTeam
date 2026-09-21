"""A lost ownership index must not turn retained task resources into a new task."""
from dataclasses import asdict

import pytest

from goodteam_os.skills.bindings import default_binding
from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.task_locks import TaskSkillIdentity, TaskSkillLockStore, TaskSkillPlan


def _fixture(tmp_path):
    library = SkillLibraryService(tmp_path / "operator")
    source = tmp_path / "method"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: method\ndescription: A review method.\n---\nReview the task.\n",
        encoding="utf-8",
    )
    proposal = library.preview_local(str(source), command_key="preview")
    installed = library.install(proposal["proposal_id"])["installed"]
    item = {"skill_id": installed["skill_id"], "version_id": installed["version_id"],
            "package_digest": proposal["package_digest"], "activation": "required", "enabled": True}
    binding = {**default_binding("role:code_review", project_key="DEMO"), "mode": "custom", "items": [item]}
    plan = TaskSkillPlan(1, {"role:code_review": binding},
                         {"role:code_review": {"ref": binding["role_contract_ref"]}})
    locks = TaskSkillLockStore(tmp_path / "locks", library.store)
    return locks, library.store, item, plan


def _never_resolve():
    raise AssertionError("Missing metadata must not recapture current project methods")


@pytest.mark.parametrize("action", ["read", "snapshot", "preflight", "replay", "new_task"])
def test_missing_registry_blocks_all_bound_operations_without_repair(tmp_path, action):
    locks, packages, item, plan = _fixture(tmp_path)
    task = TaskSkillIdentity("workflow_run", "DEMO", "T1")
    ref = locks.begin(task, command_key="start", task_input={}, resolve_plan=lambda: plan)
    snapshot = locks.snapshot(ref, task=task, subject="role:code_review", command_key="job",
                              job_input={}, loader_version="loader.v1")
    namespace = locks.root / locks.scope.namespace
    objects_before = {p.name: p.read_bytes() for p in (namespace / "objects").iterdir()}
    pins_before = packages.retention_pins(item["skill_id"], item["version_id"])
    (namespace / "index.json").unlink()
    # Reconstructing the service after a restart must not make the store virgin.
    locks = TaskSkillLockStore(locks.root, packages)

    def available(scope, skill_id, version_id):
        return {"scope": asdict(scope), "skill_id": skill_id, "version_id": version_id,
                "available": True, "revoked": False, "revision": 0}

    with pytest.raises(SkillError) as error:
        if action == "read":
            locks.read(ref, task=task)
        elif action == "snapshot":
            locks.snapshot(ref, task=task, subject="role:code_review", command_key="late",
                           job_input={}, loader_version="loader.v1")
        elif action == "preflight":
            locks.preflight(snapshot, task=task, command_key="job", job_input={},
                            supported_loaders=("loader.v1",), current_availability=available)
        else:
            identity = task if action == "replay" else TaskSkillIdentity("workflow_run", "DEMO", "T2")
            locks.begin(identity, command_key="start", task_input={}, resolve_plan=_never_resolve)
    assert error.value.code == "snapshot_missing"
    assert not (namespace / "index.json").exists()
    assert objects_before == {p.name: p.read_bytes() for p in (namespace / "objects").iterdir()}
    assert packages.retention_pins(item["skill_id"], item["version_id"]) == pins_before


def test_unregistered_first_prepare_orphan_is_not_adopted_on_retry(tmp_path, monkeypatch):
    locks, packages, item, plan = _fixture(tmp_path)
    task = TaskSkillIdentity("workflow_run", "DEMO", "T1")

    def interrupted(*_args):
        raise OSError("Before first ownership index publication")

    with monkeypatch.context() as patch:
        patch.setattr(locks, "_write_registry", interrupted)
        with pytest.raises(SkillError):
            locks.begin(task, command_key="start", task_input={}, resolve_plan=lambda: plan)
    namespace = locks.root / locks.scope.namespace
    assert list((namespace / "objects").iterdir())
    assert not (namespace / "index.json").exists()
    with pytest.raises(SkillError) as error:
        locks.begin(task, command_key="start", task_input={}, resolve_plan=_never_resolve)
    assert error.value.code == "snapshot_missing"
    assert packages.retention_pins(item["skill_id"], item["version_id"]) == ()
