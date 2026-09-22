"""Current content authority must be checked after the last adapter callback.

These tests invalidate real fixture persistence inside a synthetic authorization
pause. They do not execute a provider or establish actual Runtime conformance.
"""
from copy import deepcopy
from uuid import uuid4

import pytest

from goodteam_os.skills.content import ContentLimits
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.task_context import task_skill_context
from goodteam_os.skills.task_locks import TaskSkillIdentity, TaskSkillPlan
from test_skill_content import FixtureAuthority, job, provider
from test_worker_skill_requests import ctx as ctx


@pytest.mark.parametrize("operation", ["prepare", "resource"])
@pytest.mark.parametrize("invalidation", ["revoke", "worker_input", "publication", "pin"])
def test_final_authorization_cannot_publish_or_return_invalidated_content(ctx, operation, invalidation):
    created, item, _content, _source = job(ctx)

    class InvalidateOnFinalAuthorization(FixtureAuthority):
        def __init__(self):
            super().__init__()
            self.reached = 0

        def authorize(self, snapshot, *, context_id, operation):
            session = super().authorize(snapshot, context_id=context_id, operation=operation)
            if operation != target_operation:
                return session
            self.reached += 1
            if self.reached != 2:
                return session
            if invalidation == "revoke":
                ctx.status.revoke(item["skill_id"], item["version_id"], actor="user", reason="safety",
                                  command_key="revoke-in-final-authorization", expected_revision=0)
            elif invalidation == "worker_input":
                (ctx.store.root / created["id"] / "task.md").write_text("changed during authorization")
            elif invalidation == "publication":
                (ctx.operator / "skills" / "worker-requests" / (created["id"] + ".published.json")).unlink()
            else:
                for pin in ctx.packages.retention_pins(item["skill_id"], item["version_id"]):
                    ctx.packages.release_pin(item["skill_id"], item["version_id"],
                                             holder_id=pin["holder_id"], command_key=uuid4().hex)
            return session

    target_operation = operation
    authority = InvalidateOnFinalAuthorization()
    selected = provider(ctx, created, authority=authority)
    directory = ctx.operator / "skills" / "content-contexts" / created["id"]
    before = {}
    if operation == "resource":
        ref = selected.prepare("a")
        before = {path.name: path.read_bytes() for path in directory.glob("*.json")}
        assert len(before) == 1
    with pytest.raises(SkillError) as error:
        if operation == "prepare":
            selected.prepare("a")
        else:
            selected.read_resource(ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="read-once")
    assert authority.reached == 2
    if invalidation == "revoke":
        assert error.value.code == "skill_unavailable"
    elif invalidation == "worker_input":
        assert error.value.code == "worker_skill_input_changed"
    elif invalidation == "publication":
        assert error.value.code == "worker_skill_publication_missing"
    else:
        assert "pin is missing" in str(error.value)
        assert ctx.packages.retention_pins(item["skill_id"], item["version_id"]) == ()
    assert {path.name: path.read_bytes() for path in directory.glob("*.json")} == before
    assert not list(directory.glob("read-*.json"))
    assert ctx.calls == []


def test_current_catalog_budget_applies_when_returning_prepared_input(ctx):
    created, _item, _content, _source = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    before = selected.private_input(ref)
    limited = provider(ctx, created, limits=ContentLimits(max_catalog_bytes=1))
    with pytest.raises(SkillError, match="catalog exceeds") as error:
        limited.private_input(ref)
    assert error.value.code == "content_budget_exceeded"
    assert selected.private_input(ref) == before
    assert selected.prepare("a") == ref
    assert ctx.calls == []


def test_current_package_count_applies_to_original_multi_package_context(ctx):
    _first, first, _content, _source = job(ctx)
    _second, second, _other_content, _other_source = job(ctx)
    token = uuid4().hex
    binding = deepcopy(ctx.plan.bindings["role:code_review"])
    binding["items"] = [first, second]
    plan = TaskSkillPlan(3, {"role:code_review": binding}, ctx.plan.role_contracts)
    task = TaskSkillIdentity("workflow_run", "DEMO", "run-" + token)
    lock = ctx.locks.begin(task, command_key=token, task_input={"request": "Review both methods"},
                           resolve_plan=lambda: plan)
    created = ctx.store.create_job(**{
        **ctx.request, "run_id": task.logical_id, "idempotency_key": "job-" + token,
        "task_skill_context": task_skill_context(task, lock), "required_skill_ids": (),
    })
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    before = selected.private_input(ref)
    assert len(before["catalog"]) == len(before["required"]) == 2
    with pytest.raises(SkillError, match="package count") as error:
        provider(ctx, created, limits=ContentLimits(max_packages=1)).private_input(ref)
    assert error.value.code == "content_budget_exceeded"
    assert selected.private_input(ref) == before
    assert selected.prepare("a") == ref
    assert ctx.calls == []


def test_larger_current_resource_limit_does_not_enlarge_captured_budget(ctx):
    created, item, _content, _source = job(ctx)
    selected = provider(ctx, created, limits=ContentLimits(max_resource_bytes=3))
    ref = selected.prepare("a")
    with pytest.raises(SkillError, match="no partial read") as error:
        provider(ctx, created).read_resource(ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="oversized")
    assert error.value.code == "content_budget_exceeded"
    directory = ctx.operator / "skills" / "content-contexts" / created["id"]
    assert not list(directory.glob("read-*.json"))
    assert selected.prepare("a") == ref
    assert ctx.calls == []
