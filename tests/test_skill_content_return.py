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
