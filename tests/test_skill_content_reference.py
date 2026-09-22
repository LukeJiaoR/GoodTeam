"""Caller-owned references cannot retarget protected reads or their receipts."""
from copy import deepcopy
import json

import pytest

from test_skill_content import FixtureAuthority, job, provider
from test_worker_skill_requests import ctx as ctx


@pytest.mark.parametrize("pause", [1, 2])
@pytest.mark.parametrize("replacement", ["same_job_context", "other_job", "empty"])
def test_resource_receipt_uses_validated_identity_after_caller_mutation(ctx, pause, replacement):
    created, item, content, _source = job(ctx)
    caller_ref = {}
    replacement_ref = {}

    class MutateCallerReference(FixtureAuthority):
        reached = 0

        def authorize(self, snapshot, *, context_id, operation):
            session = super().authorize(snapshot, context_id=context_id, operation=operation)
            if operation == "resource":
                self.reached += 1
                if self.reached == pause:
                    caller_ref.clear()
                    caller_ref.update(deepcopy(replacement_ref))
            return session

    authority = MutateCallerReference()
    selected = provider(ctx, created, authority=authority)
    original = selected.prepare("original")
    caller_ref.update(deepcopy(original))
    if replacement == "same_job_context":
        replacement_ref.update(selected.prepare("another-context"))
    elif replacement == "other_job":
        other, _item, _content, _source = job(ctx)
        replacement_ref.update(provider(ctx, other).prepare("other-job-context"))
    directory = ctx.operator / "skills" / "content-contexts" / created["id"]
    before = {path.name: path.read_bytes() for path in directory.glob("*.json")}

    assert selected.read_resource(caller_ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="once") == content
    assert authority.reached == 2
    assert caller_ref == replacement_ref
    assert caller_ref != original
    receipts = list(directory.glob("read-*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["context_ref"] == original
    assert receipt["skill_id"] == item["skill_id"]
    assert receipt["version_id"] == item["version_id"]
    assert receipt["runtime_read_observed"] is False
    assert all((directory / name).read_bytes() == data for name, data in before.items())

    # A fresh original reference replays the same immutable receipt, rather than
    # conflicting with a receipt accidentally bound to a different context/job.
    receipt_bytes = receipts[0].read_bytes()
    assert selected.read_resource(deepcopy(original), skill_id=item["skill_id"], relative="SKILL.md", call_id="once") == content
    assert receipts[0].read_bytes() == receipt_bytes
    assert len(list(directory.glob("read-*.json"))) == 1
    assert ctx.calls == []


@pytest.mark.parametrize("operation", ["input", "resource", "describe"])
def test_read_freezes_nested_reference_before_protected_preflight(ctx, monkeypatch, operation):
    created, item, content, _source = job(ctx)
    selected = provider(ctx, created)
    original = selected.prepare("original")
    caller_ref = deepcopy(original)
    original_preflight = selected._validated
    pauses = []

    def preflight_with_caller_mutation():
        result = original_preflight()
        if not pauses:
            pauses.append(True)
            # Deep copying is required: a shallow copy still aliases snapshot_ref.
            caller_ref["snapshot_ref"].clear()
            caller_ref["context_id"] = "changed-during-preflight"
        return result

    with monkeypatch.context() as patch:
        patch.setattr(selected, "_validated", preflight_with_caller_mutation)
        if operation == "input":
            assert selected.private_input(caller_ref)["required"][0]["text"].encode() == content
        elif operation == "resource":
            assert selected.read_resource(caller_ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="once") == content
        else:
            assert selected.describe(caller_ref)["context_ref"] == original
    assert pauses == [True]
    assert caller_ref["snapshot_ref"] == {}
    assert selected.prepare("original") == original
    assert ctx.calls == []
