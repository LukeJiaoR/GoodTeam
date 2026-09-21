"""S4 core contract tests. These do not claim production dispatch integration."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json

import pytest

from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.skills.models import SkillConflict, SkillError, SkillScope
from goodteam_os.skills.bindings import default_binding
from goodteam_os.skills.store import SkillStore
from goodteam_os.skills.task_locks import TaskSkillIdentity, TaskSkillLockStore, TaskSkillPlan


@pytest.fixture
def setup(tmp_path):
    root = tmp_path.resolve()
    library = SkillLibraryService(root / "operator")
    locks = TaskSkillLockStore(root / "task-locks", library.store, worker_roots=(root / "worker",))
    source = root / "my-method"
    source.mkdir()

    def install(body, key):
        (source / "SKILL.md").write_text(
            "---\nname: my-method\ndescription: Task-specific review method.\n---\n" + body,
            encoding="utf-8",
        )
        proposal = library.preview_local(str(source), command_key="preview-" + key)
        installed = library.install(proposal["proposal_id"])["installed"]
        return {"skill_id": installed["skill_id"], "version_id": installed["version_id"],
                "package_digest": proposal["package_digest"], "activation": "required", "enabled": True}

    return locks, library.store, install


def task(identity="T1", *, project="DEMO", kind="workflow_run"):
    return TaskSkillIdentity(kind, project, identity)


def plan(item, *, project="DEMO", revision=1, subjects=("role:implementation", "role:code_review")):
    bindings = {}
    for subject in subjects:
        bindings[subject] = {**default_binding(subject, project_key=project), "mode": "custom",
                             "binding_revision": revision, "items": [item] if item else []}
    return TaskSkillPlan(revision, bindings, {subject: {"ref": binding["role_contract_ref"],
                         "input": "Bounded task evidence", "output": "Review findings"}
                         for subject, binding in bindings.items()})


def begin(store, identity, current):
    return store.begin(identity, command_key="start-" + identity.logical_id,
                       task_input={"intent": "review the confirmed change"}, resolve_plan=lambda: current)


def job(store, ref, identity, key="review-job", *, subject="role:code_review", job_input=None, **kwargs):
    return store.snapshot(ref, task=identity, subject=subject, command_key=key,
                          job_input=job_input or {"task": "review", "execution_policy": "read_only"},
                          loader_version="skill_loader.v1", **kwargs)


def available(scope, skill_id, version_id):
    return {"scope": asdict(scope), "skill_id": skill_id, "version_id": version_id,
            "available": True, "revoked": False, "revision": 1}


def check(store, ref, identity, key="review-job", *, job_input=None, availability=available, loaders=("skill_loader.v1",)):
    return store.preflight(ref, task=identity, command_key=key,
                           job_input=job_input or {"task": "review", "execution_policy": "read_only"},
                           supported_loaders=loaders, current_availability=availability)


def never_resolve():
    raise AssertionError("Recovery or late dispatch must not query current project bindings")


def test_late_reviewer_and_retry_keep_v1_new_task_gets_v2(setup):
    locks, packages, install = setup
    v1 = install("Review method v1.", "one")
    t1 = task()
    original = begin(locks, t1, plan(v1))
    v2 = install("Review method v2.", "two")
    assert v1["version_id"] != v2["version_id"]
    replay = locks.begin(t1, command_key="start-T1", task_input={"intent": "review the confirmed change"},
                         resolve_plan=never_resolve)
    assert replay == original
    reviewer = job(locks, original, t1)
    retry = job(locks, original, t1, "retry-job")
    assert check(locks, reviewer, t1)["methods"]["items"] == [v1]
    assert check(locks, retry, t1, "retry-job")["methods"]["items"] == [v1]
    t2 = task("T2")
    updated = begin(locks, t2, plan(v2, revision=2))
    later = job(locks, updated, t2, "review-T2")
    assert check(locks, later, t2, "review-T2")["methods"]["items"] == [v2]
    assert len(packages.retention_pins(v1["skill_id"], v1["version_id"])) == 2
    assert len(packages.retention_pins(v2["skill_id"], v2["version_id"])) == 2


def test_task_kinds_and_manager_messages_are_independent(setup):
    locks, _, install = setup
    item = install("Review.", "one")
    refs = []
    for kind in ("workflow_run", "issue_execution", "direct_task", "manager_message"):
        identity = task("same-id", kind=kind)
        refs.append(locks.begin(identity, command_key=kind, task_input={}, resolve_plan=lambda: plan(item)))
    assert len({ref["id"] for ref in refs}) == 4
    with pytest.raises(SkillError, match="logical message"):
        task("task-assistant:DEMO", kind="manager_message")
    turn1 = task("message-1", kind="manager_message")
    first = begin(locks, turn1, plan(item, subjects=("manager:orchestrator",)))
    updated = install("New method.", "two")
    turn2 = task("message-2", kind="manager_message")
    second = begin(locks, turn2, plan(updated, subjects=("manager:orchestrator",)))
    assert locks.read(first, task=turn1)["subjects"]["manager:orchestrator"]["items"] == [item]
    assert locks.read(second, task=turn2)["subjects"]["manager:orchestrator"]["items"] == [updated]


def test_changed_task_input_or_reused_task_command_key_conflicts(setup):
    locks, _, install = setup
    original = begin(locks, task(), plan(install("Review.", "one")))
    with pytest.raises(SkillConflict):
        locks.begin(task(), command_key="start-T1", task_input={"intent": "different"}, resolve_plan=never_resolve)
    with pytest.raises(SkillConflict):
        locks.begin(task("T2"), command_key="start-T1", task_input={}, resolve_plan=never_resolve)
    with pytest.raises(SkillConflict):
        locks.begin(task(), command_key="a-new-key", task_input={}, resolve_plan=never_resolve)
    assert locks.read(original, task=task())["task"]["logical_id"] == "T1"


@pytest.mark.parametrize("field,value", [("task", "different"), ("context_ref", "other"),
                                         ("runtime", {"model": "other"}), ("execution_policy", "standard"),
                                         ("output_contract", "other")])
def test_same_job_key_with_different_inputs_is_not_a_second_job(setup, field, value):
    locks, _, install = setup
    ref = begin(locks, task(), plan(install("Review.", "one")))
    inputs = {"task": "review", "context_ref": "fixed", "runtime": {"model": "original"},
              "execution_policy": "read_only", "output_contract": "findings"}
    original = job(locks, ref, task(), job_input=inputs)
    assert job(locks, ref, task(), job_input=inputs) == original
    with pytest.raises(SkillConflict):
        job(locks, ref, task(), job_input={**inputs, field: value})
    with pytest.raises(SkillConflict):
        check(locks, original, task(), job_input={**inputs, field: value})


def test_job_key_cannot_switch_task_subject_loader_or_required_methods(setup):
    locks, _, install = setup
    item = install("Review.", "one")
    ref = begin(locks, task(), plan(item))
    job(locks, ref, task())
    other = begin(locks, task("T2"), plan(item))
    with pytest.raises(SkillConflict):
        job(locks, other, task("T2"))
    with pytest.raises(SkillConflict):
        job(locks, ref, task(), subject="role:implementation")
    with pytest.raises(SkillConflict):
        job(locks, ref, task(), required_skill_ids=(item["skill_id"],))
    with pytest.raises(SkillConflict):
        locks.snapshot(ref, task=task(), subject="role:code_review", command_key="review-job",
                       job_input={"task": "review", "execution_policy": "read_only"}, loader_version="other")


def test_unknown_subject_or_new_required_skill_never_uses_latest_binding(setup):
    locks, _, install = setup
    item = install("Review.", "one")
    ref = begin(locks, task(), plan(item))
    with pytest.raises(SkillError, match="not captured"):
        job(locks, ref, task(), subject="role:release")
    with pytest.raises(SkillError, match="not in the original"):
        job(locks, ref, task(), required_skill_ids=("skill-" + "f" * 64,))


def test_unavailable_unused_subject_does_not_block_valid_reviewer(setup):
    locks, _, install = setup
    item = install("Review.", "one")
    current = plan(item)
    current.bindings["role:implementation"]["items"][0] = {**item, "version_id": "version-" + "0" * 64}
    ref = begin(locks, task(), current)
    assert check(locks, job(locks, ref, task()), task())["methods"]["items"] == [item]
    missing = job(locks, ref, task(), "builder", subject="role:implementation")
    with pytest.raises(SkillError, match="unresolved or unavailable"):
        check(locks, missing, task(), "builder")


def test_empty_custom_and_unresolved_default_are_blocked_not_silently_filled(setup):
    locks, _, _ = setup
    current = plan(None)
    current.bindings["role:implementation"]["mode"] = "default"
    ref = begin(locks, task(), current)
    lock = locks.read(ref, task=task())
    assert lock["subjects"]["role:code_review"]["diagnostics"] == [{"code": "methods_unconfigured"}]
    assert lock["subjects"]["role:implementation"]["diagnostics"] == [{"code": "default_unresolved"}]
    with pytest.raises(SkillError, match="no fallback"):
        check(locks, job(locks, ref, task()), task())


def test_disabled_methods_are_not_pinned_or_promoted_to_required(setup):
    locks, packages, install = setup
    item = {**install("Review.", "one"), "enabled": False}
    ref = begin(locks, task(), plan(item))
    assert packages.retention_pins(item["skill_id"], item["version_id"]) == ()
    assert locks.read(ref, task=task())["subjects"]["role:code_review"]["items"] == []
    with pytest.raises(SkillError):
        job(locks, ref, task(), required_skill_ids=(item["skill_id"],))


def test_scope_and_task_identity_are_checked_even_with_a_valid_resource(setup, tmp_path):
    locks, _, install = setup
    ref = begin(locks, task(), plan(install("Review.", "one")))
    with pytest.raises(SkillError):
        locks.read(ref, task=task(project="OTHER"))
    with pytest.raises(SkillError):
        locks.read(ref, task=task("T2"))
    other = SkillStore(tmp_path.resolve() / "other-packages", SkillScope("user", "other-owner"))
    other_locks = TaskSkillLockStore(tmp_path.resolve() / "other-locks", other)
    with pytest.raises(SkillError):
        other_locks.read(ref, task=task())
    project_packages = SkillStore(tmp_path.resolve() / "project-packages", SkillScope("project", "owner", "ALPHA"))
    project_locks = TaskSkillLockStore(tmp_path.resolve() / "project-locks", project_packages)
    with pytest.raises(SkillError):
        project_locks.begin(task(project="BETA"), command_key="cross-project", task_input={}, resolve_plan=never_resolve)


def test_interrupted_pin_recovers_original_capture_once(setup, monkeypatch):
    locks, packages, install = setup
    item = install("Review v1.", "one")
    actual_pin = packages.pin
    called = []

    def interrupted(*args, **kwargs):
        result = actual_pin(*args, **kwargs)
        called.append(result["holder_id"])
        raise OSError("after pin")

    with monkeypatch.context() as patch:
        patch.setattr(packages, "pin", interrupted)
        with pytest.raises(SkillError, match="unavailable or unsafe"):
            begin(locks, task(), plan(item))
    install("Review v2.", "two")
    with locks._locked() as fd:
        pending = locks._registry(fd)["tasks"]
        incomplete_ref = next(iter(pending.values()))["ref"]
    with pytest.raises(SkillError, match="incomplete"):
        locks.read(incomplete_ref, task=task())
    ref = locks.begin(task(), command_key="start-T1", task_input={"intent": "review the confirmed change"},
                      resolve_plan=never_resolve)
    assert ref == incomplete_ref
    assert len(called) == 1
    assert len(packages.retention_pins(item["skill_id"], item["version_id"])) == 2
    assert check(locks, job(locks, ref, task()), task())["methods"]["items"] == [item]


def test_interrupted_final_registry_commit_keeps_pins_and_recovers_without_resolving(setup, monkeypatch):
    locks, packages, install = setup
    item = install("Review.", "one")
    write = locks._write_registry

    def fail_final(fd, registry):
        if any(entry["status"] == "committed" for entry in registry["tasks"].values()):
            raise OSError("before final registry")
        return write(fd, registry)

    with monkeypatch.context() as patch:
        patch.setattr(locks, "_write_registry", fail_final)
        with pytest.raises(SkillError, match="unavailable or unsafe"):
            begin(locks, task(), plan(item))
    assert len(packages.retention_pins(item["skill_id"], item["version_id"])) == 2
    ref = locks.begin(task(), command_key="start-T1", task_input={"intent": "review the confirmed change"},
                      resolve_plan=never_resolve)
    assert locks.read(ref, task=task())["team_revision"] == 1
    assert len(packages.retention_pins(item["skill_id"], item["version_id"])) == 2


def test_prepare_registry_failure_does_not_acquire_pins(setup, monkeypatch):
    locks, packages, install = setup
    item = install("Review.", "one")

    def fail(*_args):
        raise OSError("before prepare registry")

    monkeypatch.setattr(locks, "_write_registry", fail)
    with pytest.raises(SkillError, match="unavailable or unsafe"):
        begin(locks, task(), plan(item))
    assert packages.retention_pins(item["skill_id"], item["version_id"]) == ()


def test_concurrent_same_task_start_captures_once(setup):
    locks, packages, install = setup
    item = install("Review.", "one")
    calls = []

    def resolve():
        calls.append(True)
        return plan(item)

    def start(_index):
        return locks.begin(task(), command_key="same-start", task_input={}, resolve_plan=resolve)

    with ThreadPoolExecutor(max_workers=2) as executor:
        refs = list(executor.map(start, range(2)))
    assert refs[0] == refs[1]
    assert calls == [True]
    assert len(packages.retention_pins(item["skill_id"], item["version_id"])) == 2


@pytest.mark.parametrize("patch", [{"revoked": True}, {"available": False}, {"revision": None},
                                   {"scope": {}}, {"version_id": "other"}, {"revoked": None}])
def test_preflight_checks_current_revocation_and_unknown_status(setup, patch):
    locks, _, install = setup
    ref = begin(locks, task(), plan(install("Review.", "one")))
    snap = job(locks, ref, task())
    assert check(locks, snap, task())
    with pytest.raises(SkillError, match="revoked, unavailable"):
        check(locks, snap, task(), availability=lambda *args: {**available(*args), **patch})


def test_preflight_fails_for_missing_pin_without_recreating_it(setup):
    locks, packages, install = setup
    item = install("Review.", "one")
    ref = begin(locks, task(), plan(item))
    snap = job(locks, ref, task())
    holder = locks._holder(ref["id"], "role:code_review", item["skill_id"])
    packages.release_pin(item["skill_id"], item["version_id"], holder_id=holder, command_key="explicit-release")
    with pytest.raises(SkillError, match="pin is missing"):
        check(locks, snap, task())
    assert len(packages.retention_pins(item["skill_id"], item["version_id"])) == 1


def test_preflight_rejects_loader_and_altered_immutable_resource(setup):
    locks, _, install = setup
    ref = begin(locks, task(), plan(install("Review.", "one")))
    snap = job(locks, ref, task())
    with pytest.raises(SkillError, match="compatible loader"):
        check(locks, snap, task(), loaders=("other",))
    path = locks.root / locks.scope.namespace / "objects" / (snap["digest"][7:] + ".json")
    path.chmod(0o600)
    payload = json.loads(path.read_text())
    payload["job_input"]["execution_policy"] = "standard"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SkillError, match="bytes or identity"):
        check(locks, snap, task())


def test_missing_legacy_lock_is_not_created_by_read(setup):
    locks, _, _ = setup
    ref = {"schema_version": "skill_resource_ref.v1", "resource_schema": "task_skill_lock.v1",
           "scope": asdict(locks.scope), "id": "task-" + "0" * 64, "digest": "sha256:" + "1" * 64}
    with pytest.raises(SkillError):
        locks.read(ref, task=task("legacy-run"))
    assert not (locks.root / locks.scope.namespace / "index.json").exists()


def test_operator_lock_store_cannot_be_nested_in_worker_root(setup, tmp_path):
    _, packages, _ = setup
    root = tmp_path.resolve() / "worker"
    with pytest.raises(SkillError, match="outside Worker"):
        TaskSkillLockStore(root / "locks", packages, worker_roots=(root,))
    assert not root.exists()


def test_explicit_default_preset_and_on_demand_activation_are_frozen(setup):
    locks, _, install = setup
    item = {**install("Review.", "one"), "activation": "on_demand"}
    current = plan(None, subjects=("role:code_review",))
    current.bindings["role:code_review"]["mode"] = "default"
    current.default_items["role:code_review"] = [item]
    ref = begin(locks, task(), current)
    snap = job(locks, ref, task(), required_skill_ids=(item["skill_id"],))
    result = check(locks, snap, task())
    assert result["methods"]["binding"]["mode"] == "default"
    assert result["methods"]["items"] == [item]
    assert result["required_skill_ids"] == [item["skill_id"]]
    assert result["methods"]["items"][0]["activation"] == "on_demand"


def test_partial_registry_write_preserves_visible_index_and_earlier_task(setup, monkeypatch):
    from goodteam_os.skills import task_locks as module

    locks, packages, install = setup
    item = install("Review.", "one")
    original = begin(locks, task(), plan(item))
    index = locks.root / locks.scope.namespace / "index.json"
    before = index.read_bytes()
    actual_write = module.write_new

    def partial(fd, name, data, **kwargs):
        if name.startswith(".index-"):
            actual_write(fd, name, data[:len(data) // 2], **kwargs)
            raise OSError("torn temporary registry write")
        return actual_write(fd, name, data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module, "write_new", partial)
        with pytest.raises(SkillError, match="unavailable or unsafe"):
            begin(locks, task("T2"), plan(item))
    assert index.read_bytes() == before
    assert locks.read(original, task=task())["subjects"]["role:code_review"]["items"] == [item]
    assert len(packages.retention_pins(item["skill_id"], item["version_id"])) == 2
    assert not list(index.parent.glob(".index-*"))


def test_error_after_final_publish_replays_the_committed_record(setup, monkeypatch):
    locks, packages, install = setup
    item = install("Review.", "one")
    actual_write = locks._write_registry

    def after_final(fd, registry):
        actual_write(fd, registry)
        if any(entry["status"] == "committed" for entry in registry["tasks"].values()):
            raise OSError("after final publication")

    with monkeypatch.context() as patch:
        patch.setattr(locks, "_write_registry", after_final)
        with pytest.raises(SkillError, match="unavailable or unsafe"):
            begin(locks, task(), plan(item))
    ref = locks.begin(task(), command_key="start-T1", task_input={"intent": "review the confirmed change"},
                      resolve_plan=never_resolve)
    assert locks.read(ref, task=task())["team_revision"] == 1
    assert len(packages.retention_pins(item["skill_id"], item["version_id"])) == 2


def test_registry_unknown_schema_and_duplicate_keys_fail_closed(setup):
    locks, _, _ = setup
    index = locks.root / locks.scope.namespace / "index.json"
    index.write_text('{"schema_version":"future"}', encoding="utf-8")
    with pytest.raises(SkillError, match="schema or owner"):
        locks.begin(task(), command_key="key", task_input={}, resolve_plan=never_resolve)
    index.write_text('{"tasks":{},"tasks":{}}', encoding="utf-8")
    with pytest.raises(SkillError, match="metadata is invalid"):
        locks.begin(task(), command_key="key", task_input={}, resolve_plan=never_resolve)
