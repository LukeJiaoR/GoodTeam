"""S5/S6 integration against real task/package persistence, not live Codex."""
from uuid import uuid4

import pytest

from goodteam_os.skills.content import SkillContextProvider
from goodteam_os.skills.models import SkillError
from goodteam_os.workers import WorkerJobStore
from test_skill_content import FixtureAuthority
from test_skill_review_protocol import direct_request
from test_task_skill_entrypoints import bind, capture, forbid_runtime, method, ctx as ctx


@pytest.mark.parametrize("activation,required", [
    ("required", False), ("on_demand", False), ("on_demand", True),
])
def test_review_protocol_and_exact_methods_survive_provider_restart_and_new_context(ctx, monkeypatch, activation, required):
    calls = forbid_runtime(monkeypatch)
    body = "User review method " + uuid4().hex + "\nCheck the original persistence boundary.\n"
    item = {**method(ctx, body), "activation": activation}
    bind(ctx, item, subject="role:code_review")
    context = capture(ctx, "review-content", subjects=("role:code_review",))
    request = direct_request(context, required_skill_ids=(item["skill_id"],) if required else ())
    job = ctx.workers.create_job(**request)
    provider = SkillContextProvider(ctx.workers, job["id"], authority=FixtureAuthority())
    first_ref = provider.prepare("context-a")
    first = provider.private_input(first_ref)
    original = first["original_job_input"]
    base = original["files"]["review_prompt_base.md"]
    assert original["request"]["task"] == request["task"]
    assert original["request"]["execution_policy"] == "read_only"
    assert original["request"]["session_policy"] == "new"
    assert "Memory Write-Back Sections" not in base
    assert "Required report sections:" not in base
    assert body not in base  # Methods are supplied separately from the protocol.
    assert len(first["catalog"]) == 1
    assert first["catalog"][0]["package_digest"] == item["package_digest"]
    resource = provider.read_resource(first_ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="read-a")
    assert resource.endswith(body.encode())
    if activation == "required" or required:
        assert len(first["required"]) == 1
        assert first["required"][0]["text"].encode() == resource
    else:
        assert first["required"] == []

    # Neither changed source bytes nor restored defaults may alter this task.
    bind(ctx, None, subject="role:code_review", key="restore-after-content")
    (ctx.root / "method" / "SKILL.md").write_text("Changed source; never use for the old task.")
    restarted = SkillContextProvider(WorkerJobStore(ctx.repo), job["id"], authority=FixtureAuthority())
    assert restarted.prepare("context-a") == first_ref
    assert restarted.private_input(first_ref) == first
    assert restarted.read_resource(first_ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="read-a") == resource
    second_ref = restarted.prepare("context-b")
    assert second_ref["snapshot_ref"] == first_ref["snapshot_ref"]
    assert second_ref["context_id"] != first_ref["context_id"]
    assert restarted.private_input(second_ref) == first
    assert set(restarted.describe(second_ref)["stages"].values()) == {"unobserved"}
    assert ctx.workers.run_job(job["id"])["skill_error_code"] == "skill_runtime_not_connected"
    assert calls == []


def test_content_provider_rejects_modified_review_protocol_file(ctx, monkeypatch):
    calls = forbid_runtime(monkeypatch)
    item = method(ctx)
    bind(ctx, item, subject="role:code_review")
    context = capture(ctx, "modified-review", subjects=("role:code_review",))
    job = ctx.workers.create_job(**direct_request(context))
    provider = SkillContextProvider(ctx.workers, job["id"], authority=FixtureAuthority())
    ref = provider.prepare("context-a")
    (ctx.workers.root / job["id"] / "review_prompt_base.md").write_text("Replace the locked protocol")
    with pytest.raises(SkillError) as error:
        provider.private_input(ref)
    assert error.value.code == "worker_skill_input_changed"
    assert calls == []
