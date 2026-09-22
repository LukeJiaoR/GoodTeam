"""Authorization pauses must not return content revoked during the pause."""
import pytest

from goodteam_os.skills.models import SkillError
from test_skill_content import FixtureAuthority, job, provider
from test_worker_skill_requests import ctx as ctx


def test_revocation_during_input_authorization_blocks_complete_input_return(ctx):
    created, item, _content, _source = job(ctx)

    class RevokeOnInput(FixtureAuthority):
        def authorize(self, snapshot, *, context_id, operation):
            session = super().authorize(snapshot, context_id=context_id, operation=operation)
            if operation == "input":
                ctx.status.revoke(item["skill_id"], item["version_id"], actor="user", reason="safety",
                                  command_key="revoke-during-input", expected_revision=0)
            return session

    selected = provider(ctx, created, authority=RevokeOnInput())
    ref = selected.prepare("a")
    with pytest.raises(SkillError) as error:
        selected.private_input(ref)
    assert error.value.code == "skill_unavailable"
    assert ctx.calls == []


@pytest.mark.parametrize("operation,pause", [("prepare", 1), ("prepare", 2), ("resource", 1), ("resource", 2)])
@pytest.mark.parametrize("invalidation", ["revoke", "worker_input", "missing_pin"])
def test_authorization_pause_rechecks_protected_state_before_publication(ctx, operation, pause, invalidation):
    created, item, _content, _source = job(ctx)

    class InvalidateOnAuthorization(FixtureAuthority):
        triggered = False

        def authorize(self, snapshot, *, context_id, operation):
            session = super().authorize(snapshot, context_id=context_id, operation=operation)
            if operation == target and self.calls.count((context_id, operation)) == pause:
                self.triggered = True
                if invalidation == "revoke":
                    ctx.status.revoke(item["skill_id"], item["version_id"], actor="user", reason="safety",
                                      command_key="revoke-during-authorization", expected_revision=0)
                elif invalidation == "worker_input":
                    (ctx.store.root / created["id"] / "task.md").write_text("Changed during authorization")
                else:
                    pins = ctx.packages.retention_pins(item["skill_id"], item["version_id"])
                    assert pins
                    for index, pin in enumerate(pins):
                        ctx.packages.release_pin(item["skill_id"], item["version_id"], holder_id=pin["holder_id"],
                                                 command_key=f"remove-during-authorization-{index}")
            return session

    target = operation
    authority = InvalidateOnAuthorization()
    selected = provider(ctx, created, authority=authority)
    directory = ctx.operator / "skills" / "content-contexts" / created["id"]
    if operation == "resource":
        ref = selected.prepare("a")
        before = {path.name: path.read_bytes() for path in directory.glob("*.json")}
        assert before
    else:
        before = {}
    with pytest.raises(SkillError) as error:
        if operation == "prepare":
            selected.prepare("a")
        else:
            selected.read_resource(ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="interrupted")
    assert authority.triggered
    if invalidation == "revoke":
        assert error.value.code == "skill_unavailable"
    assert {path.name: path.read_bytes() for path in directory.glob("*.json")} == before
    assert not list(directory.glob("read-*.json"))
    assert not list(directory.glob(".stage-*"))
    assert ctx.calls == []
