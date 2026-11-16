"""Native Manager input reuses pinned Role/Skill records without a Worker job."""
import json

import pytest

from goodteam_os.manager_skill_context import ManagerSkillContext
from goodteam_os.skills.models import SkillConflict, SkillError
from goodteam_os.team_control_plane import TeamControlPlaneStore


def test_native_manager_methods_replay_original_input_and_recheck_revocation(tmp_path, monkeypatch):
    workspace, root = tmp_path / "workspace", tmp_path / "project"
    root.mkdir()
    team = TeamControlPlaneStore(workspace, "DEMO")
    team.load_or_migrate(legacy_target={})
    first = ManagerSkillContext(workspace, "DEMO", root, "message:first", {"text": "Coordinate this project"})
    instructions = first.instructions
    assert "bounded-work" in instructions and "manager-coordination" in instructions
    assert not (root / ".goodteam" / "workers" / "jobs").exists()
    snapshot = first.preflight()
    item = snapshot["methods"]["items"][0]
    original = first.resource(item["skill_id"], "SKILL.md")
    assert original["version_id"] == item["version_id"] and json.dumps(original["text"], ensure_ascii=False) in instructions
    with pytest.raises(SkillError):
        first.resource(item["skill_id"], "../SKILL.md")
    with pytest.raises(SkillConflict):
        ManagerSkillContext(workspace, "DEMO", root, "message:first", {"text": "Different input"})

    state = team.read()
    team.execute([{"kind": "set_role_skills", "subject": "manager:orchestrator", "mode": "custom",
                   "bindings": [], "expected_binding_revision":
                       state["skill_bindings"]["manager:orchestrator"]["binding_revision"]}],
                 actor="user", surface="team_ui", expected_revision=state["revision"], command_key="custom-empty")
    with monkeypatch.context() as pinned:
        pinned.setattr("goodteam_os.manager_skill_context.stable_role_prompt",
                       lambda role: pytest.fail("Retry must retain original Role bytes"))
        replay = ManagerSkillContext(workspace, "DEMO", root, "message:first", {"text": "Coordinate this project"})
        assert replay.instructions == instructions and replay.evidence == first.evidence
    with pytest.raises(SkillError) as error:
        ManagerSkillContext(workspace, "DEMO", root, "message:next", {"text": "New work"})
    assert error.value.code == "methods_blocked"

    status = first.service.availability.current_status(first.service.packages.scope,
                                                       item["skill_id"], item["version_id"])
    first.service.availability.revoke(item["skill_id"], item["version_id"], actor="test-owner",
        reason="Fixture revocation", command_key="revoke-one", expected_revision=status["revision"])
    with pytest.raises(SkillError) as error:
        replay.resource(item["skill_id"], "SKILL.md")
    assert error.value.code == "skill_unavailable"
