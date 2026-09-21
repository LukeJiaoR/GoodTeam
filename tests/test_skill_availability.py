"""Trusted current-status integration with the S4 core, not Worker conformance."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import os
from types import SimpleNamespace

import pytest

from goodteam_os.skills import availability as module
from goodteam_os.skills import task_locks as lock_module
from goodteam_os.skills.availability import SkillAvailabilityStore
from goodteam_os.skills.bindings import default_binding
from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.skills.models import SkillConflict, SkillError, SkillScope
from goodteam_os.skills.task_locks import TaskSkillIdentity, TaskSkillLockStore, TaskSkillPlan


@pytest.fixture
def context(tmp_path):
    root = tmp_path.resolve()
    library = SkillLibraryService(root / "operator")
    source = root / "method"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: review-method\ndescription: Check version and audit contracts.\n---\nReview exact inputs.\n",
        encoding="utf-8",
    )
    proposal = library.preview_local(str(source), command_key="install-preview")
    installed = library.install(proposal["proposal_id"])["installed"]
    item = {"skill_id": installed["skill_id"], "version_id": installed["version_id"],
            "package_digest": proposal["package_digest"], "activation": "required", "enabled": True}
    packages = library.store
    status = SkillAvailabilityStore(root / "availability", packages, worker_roots=(root / "worker",))
    locks = TaskSkillLockStore(root / "locks", packages, worker_roots=(root / "worker",))
    task = TaskSkillIdentity("workflow_run", "DEMO", "run-1")
    subject = "role:code_review"
    binding = {**default_binding(subject, project_key="DEMO"), "mode": "custom", "items": [item]}
    plan = TaskSkillPlan(1, {subject: binding}, {subject: {"ref": binding["role_contract_ref"], "output": "findings"}})
    ref = locks.begin(task, command_key="start-run-1", task_input={"intent": "review"}, resolve_plan=lambda: plan)
    inputs = {"task": "review exact change", "context_digest": "sha256:" + "1" * 64,
              "execution_policy": "read_only", "output_contract": "findings", "runtime": "not-connected"}
    return SimpleNamespace(root=root, packages=packages, status=status, locks=locks, task=task, ref=ref,
                           item=item, inputs=inputs, scope=packages.scope, subject=subject)


def snapshot(ctx):
    return ctx.locks.snapshot(ctx.ref, task=ctx.task, subject=ctx.subject, command_key="review-run-1",
                              job_input=ctx.inputs, loader_version="skill_loader.v1")


def preflight(ctx, ref):
    return ctx.locks.preflight(ref, task=ctx.task, command_key="review-run-1", job_input=ctx.inputs,
                               supported_loaders=("skill_loader.v1",), current_availability=ctx.status.current_status)


def revoke(ctx, **kwargs):
    return ctx.status.revoke(ctx.item["skill_id"], ctx.item["version_id"],
                             **{"actor": "local-operator", "reason": "Explicit security withdrawal",
                                "command_key": "revoke-one", "expected_revision": 0, **kwargs})


def current(ctx):
    return ctx.status.current_status(ctx.scope, ctx.item["skill_id"], ctx.item["version_id"])


def registry(ctx):
    return ctx.status.root / ctx.scope.namespace / "registry" / "index.json"


def test_reader_requires_explicit_initialization_and_never_repairs_missing_metadata(context):
    ctx = context
    with pytest.raises(SkillError) as error:
        current(ctx)
    assert error.value.code == "availability_unavailable"
    assert not registry(ctx).exists()
    assert ctx.status.initialize() == {"scope": asdict(ctx.scope), "revision": 0}
    assert current(ctx)["available"] is True
    registry(ctx).unlink()
    for operation in (lambda: current(ctx), ctx.status.initialize):
        with pytest.raises(SkillError) as error:
            operation()
        assert error.value.code == "availability_unavailable"
    assert not registry(ctx).exists()


def test_current_revocation_blocks_existing_snapshot_without_rewriting_history_or_pins(context):
    ctx = context
    ctx.status.initialize()
    ref = snapshot(ctx)
    original = preflight(ctx, ref)
    pins = ctx.packages.retention_pins(ctx.item["skill_id"], ctx.item["version_id"])
    receipt = revoke(ctx)
    assert receipt["changed"] is True
    assert receipt["revision"] == 1
    assert receipt["revocation"]["actor"] == "local-operator"
    assert current(ctx)["revoked"] is True
    assert current(ctx)["available"] is False
    with pytest.raises(SkillError, match="revoked, unavailable"):
        preflight(ctx, ref)
    assert ctx.locks.read(ctx.ref, task=ctx.task)["subjects"][ctx.subject] == original["methods"]
    assert ctx.packages.retention_pins(ctx.item["skill_id"], ctx.item["version_id"]) == pins
    assert ctx.packages.get_version(ctx.item["skill_id"], ctx.item["version_id"]).package_digest == ctx.item["package_digest"]
    assert ctx.status.initialize()["revision"] == 1
    assert current(ctx)["revoked"] is True


def test_identical_retry_returns_original_receipt_and_different_input_conflicts(context):
    ctx = context
    ctx.status.initialize()
    first = revoke(ctx)
    other = revoke(ctx, command_key="already-revoked", expected_revision=1, reason="Second confirmation")
    assert other["changed"] is False
    assert other["revision"] == 2
    assert other["revocation"] == first["revocation"]
    assert revoke(ctx) == {**first, "replayed": True}
    before = registry(ctx).read_bytes()
    for changes in ({"actor": "other"}, {"reason": "changed"}):
        with pytest.raises(SkillConflict, match="different inputs"):
            revoke(ctx, **changes)
    with pytest.raises(SkillConflict, match="refresh"):
        revoke(ctx, command_key="stale")
    assert registry(ctx).read_bytes() == before


@pytest.mark.parametrize("revision", [None, True, -1, "0"])
def test_invalid_cas_does_not_commit(context, revision):
    ctx = context
    ctx.status.initialize()
    with pytest.raises(SkillError, match="non-negative"):
        revoke(ctx, expected_revision=revision)
    assert ctx.status.revision() == 0


def test_scope_and_version_ownership_are_not_client_claims(context):
    ctx = context
    ctx.status.initialize()
    with pytest.raises(SkillError, match="another authorized scope"):
        ctx.status.current_status(SkillScope("user", "other-owner"), ctx.item["skill_id"], ctx.item["version_id"])
    with pytest.raises(SkillError):
        ctx.status.revoke(ctx.item["skill_id"], "version-" + "0" * 64,
                          actor="local-operator", reason="Unknown version", command_key="unknown", expected_revision=0)
    status = ctx.status.current_status(ctx.scope, ctx.item["skill_id"], "version-" + "0" * 64)
    assert status["available"] is False
    assert status["revoked"] is False
    assert ctx.status.revision() == 0


def test_corrupt_package_can_be_revoked_and_revoked_status_does_not_read_package_bytes(context, monkeypatch):
    ctx = context
    ctx.status.initialize()

    def corrupt(*_args):
        raise SkillError("package_corrupt", "Stored bytes changed")

    monkeypatch.setattr(ctx.packages, "get_version", corrupt)
    assert current(ctx)["diagnostic"] == "package_corrupt"
    assert current(ctx)["available"] is False
    assert revoke(ctx)["changed"] is True

    def never_read(*_args):
        raise AssertionError("Revoked content must not be opened by the current-status reader")

    monkeypatch.setattr(ctx.packages, "get_version", never_read)
    assert current(ctx)["diagnostic"] == "security_revoked"


def test_concurrent_same_command_has_one_audit_record(context):
    ctx = context
    ctx.status.initialize()
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(lambda _: revoke(ctx), range(2)))
    assert sorted(receipt["replayed"] for receipt in receipts) == [False, True]
    assert len({receipt["revocation_id"] for receipt in receipts}) == 1
    assert ctx.status.revision() == 1


def test_concurrent_different_commands_with_same_cas_do_not_silently_rebase(context):
    ctx = context
    ctx.status.initialize()

    def decide(key):
        try:
            return revoke(ctx, command_key=key)
        except SkillConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(decide, ("first", "second")))
    assert receipts.count("conflict") == 1
    assert ctx.status.revision() == 1


@pytest.mark.parametrize("phase", ["before", "after"])
def test_interrupted_revocation_publication_recovers_original_semantics(context, monkeypatch, phase):
    ctx = context
    ctx.status.initialize()
    original = registry(ctx).read_bytes()
    actual_replace = module.os.replace

    def interrupted(source, destination, **kwargs):
        if destination == "index.json":
            if phase == "after":
                actual_replace(source, destination, **kwargs)
            raise OSError("interrupted status publication")
        return actual_replace(source, destination, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module.os, "replace", interrupted)
        with pytest.raises(SkillError, match="unavailable or unsafe"):
            revoke(ctx)
    if phase == "before":
        assert registry(ctx).read_bytes() == original
        assert current(ctx)["revoked"] is False
    else:
        assert current(ctx)["revoked"] is True
    result = revoke(ctx)
    assert result["revision"] == 1
    assert result["replayed"] is (phase == "after")
    assert ctx.status.revision() == 1
    assert not list(registry(ctx).parent.glob(".index-*"))


def test_partial_status_write_keeps_previous_complete_state(context, monkeypatch):
    ctx = context
    ctx.status.initialize()
    before = registry(ctx).read_bytes()
    actual = module.write_new

    def partial(fd, name, data, **kwargs):
        if name.startswith(".index-"):
            actual(fd, name, data[:len(data) // 2], **kwargs)
            raise OSError("partial temporary write")
        return actual(fd, name, data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module, "write_new", partial)
        with pytest.raises(SkillError, match="unavailable or unsafe"):
            revoke(ctx)
    assert registry(ctx).read_bytes() == before
    assert current(ctx)["revoked"] is False
    assert not list(registry(ctx).parent.glob(".index-*"))
    assert revoke(ctx)["revision"] == 1


def test_revocation_retry_cannot_report_success_before_directory_sync(context, monkeypatch):
    ctx = context
    ctx.status.initialize()
    inode = registry(ctx).parent.stat().st_ino
    actual = module.os.fsync

    def fail_registry_sync(fd):
        if os.fstat(fd).st_ino == inode:
            raise OSError("registry directory not durable")
        return actual(fd)

    with monkeypatch.context() as patch:
        patch.setattr(module.os, "fsync", fail_registry_sync)
        for _ in range(2):
            with pytest.raises(SkillError, match="unavailable or unsafe"):
                revoke(ctx)
            assert current(ctx)["revoked"] is True
    assert revoke(ctx)["replayed"] is True
    assert ctx.status.revision() == 1


def test_snapshot_retry_reestablishes_registry_durability_without_new_snapshot(context, monkeypatch):
    ctx = context
    namespace = ctx.locks.root / ctx.scope.namespace
    inode = namespace.stat().st_ino
    actual = lock_module.os.fsync

    def fail_registry_sync(fd):
        if os.fstat(fd).st_ino == inode:
            raise OSError("snapshot registry not durable")
        return actual(fd)

    with monkeypatch.context() as patch:
        patch.setattr(lock_module.os, "fsync", fail_registry_sync)
        for _ in range(2):
            with pytest.raises(SkillError, match="unavailable or unsafe"):
                snapshot(ctx)
    before = (namespace / "index.json").read_bytes()
    objects = sorted((namespace / "objects").iterdir())
    ref = snapshot(ctx)
    assert snapshot(ctx) == ref
    assert (namespace / "index.json").read_bytes() == before
    assert sorted((namespace / "objects").iterdir()) == objects


@pytest.mark.parametrize("payload", ['{"schema_version":"future"}', '{"revision":0,"revision":0}', '{}'])
def test_unknown_corrupt_or_duplicate_status_metadata_fails_closed(context, payload):
    ctx = context
    ctx.status.initialize()
    registry(ctx).write_text(payload, encoding="utf-8")
    for operation in (lambda: current(ctx), ctx.status.initialize, lambda: revoke(ctx)):
        with pytest.raises(SkillError):
            operation()
    assert registry(ctx).read_text() == payload


def test_replay_receipt_cannot_be_lost_or_detached_from_its_revocation(context):
    ctx = context
    ctx.status.initialize()
    revoke(ctx)
    path = registry(ctx)
    value = json.loads(path.read_text())
    value["commands"].clear()
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SkillError, match="audit receipts"):
        current(ctx)


def test_symlink_status_metadata_is_rejected(context):
    ctx = context
    ctx.status.initialize()
    path = registry(ctx)
    backup = ctx.root / "external.json"
    path.rename(backup)
    path.symlink_to(backup)
    with pytest.raises(SkillError):
        current(ctx)


def test_availability_store_rejects_worker_writable_root(context):
    ctx = context
    worker = ctx.root / "worker"
    with pytest.raises(SkillError, match="outside Worker"):
        SkillAvailabilityStore(worker / "state", ctx.packages, worker_roots=(worker,))
    assert not worker.exists()


def test_initial_partial_write_does_not_publish_or_leave_bootstrap_directory(context, monkeypatch):
    ctx = context
    actual = module.write_new

    def partial(fd, name, data, **kwargs):
        actual(fd, name, data[:len(data) // 2], **kwargs)
        raise OSError("partial bootstrap write")

    with monkeypatch.context() as patch:
        patch.setattr(module, "write_new", partial)
        with pytest.raises(SkillError, match="unavailable or unsafe"):
            ctx.status.initialize()
    assert not registry(ctx).exists()
    assert not list(registry(ctx).parent.parent.glob(".bootstrap-*"))
    assert ctx.status.initialize()["revision"] == 0
