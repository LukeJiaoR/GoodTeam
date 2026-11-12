"""Semantic authority checks use isolated storage and no provider execution."""
from copy import deepcopy
import json

import pytest

from goodteam_os.goal_continuation import authority_snapshot, tick_goal
from goodteam_os.goals import GoalStore
from goodteam_os.team_control_plane import TeamControlPlaneStore, ensure_team_state


def test_display_edits_preserve_grant_but_execution_changes_do_not(tmp_path, monkeypatch):
    from goodteam_os import web
    from goodteam_os.providers import ProviderStore

    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path / "operator"))
    root = tmp_path / "source"
    root.mkdir()
    config = tmp_path / "projects/demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nname: Before\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n")
    ensure_team_state(tmp_path, "DEMO", config)
    models = tmp_path / "models.yaml"
    model_text = "profiles:\n  balanced:\n    orchestrator:\n      primary: fake:first\n  unused:\n    orchestrator:\n      primary: fake:unused\n"
    models.write_text(model_text)
    monkeypatch.setenv("GOODTEAM_MODEL_CONFIG", str(models))
    monkeypatch.setattr(web, "resolve_runtime_model_config", lambda _: models)
    connection = dict(id="fixture", agent="codex", protocol="responses", name="Display", revision=1,
                      base_url="https://example.invalid/v1", model="first", api_key="test-only-secret")
    monkeypatch.setattr(ProviderStore, "active", lambda _: deepcopy(connection))
    team_store = TeamControlPlaneStore(tmp_path, "DEMO")
    team = team_store.read()
    def snapshot():
        return authority_snapshot(tmp_path, "DEMO", config, root)
    approved = snapshot()
    original_config = config.read_text()
    config.write_text(original_config.replace("Before", "After") + "# unrelated formatting\n")
    changed = deepcopy(team)
    changed["revision"] += 1
    changed["executed_commands"]["rename"] = {"audit": "only"}
    changed["manager"]["updated_at"] = "later"
    for role in changed["roles"].values():
        role["updated_at"] = "later"
    for worker in changed["workers"]:
        worker.update(name="Display only", updated_at="later")
    team_store._write_state(changed)
    models.write_text(model_text.replace("fake:unused", "fake:other-unused") + "# catalog comment\n")
    connection.update(name="Renamed", revision=2)
    assert snapshot() == approved
    assert "test-only-secret" not in json.dumps(approved)

    # The actual persisted Goal accepts the same authority after presentation
    # edits, without dispatching a process or making a paid model call.
    store = GoalStore(tmp_path, "DEMO")
    goal = store.create_goal(objective="Fixture", acceptance_criteria=["Fixture works"],
                            allowed_scope=["Isolated fixture only"], max_work_units=1,
                            confirmed=True, idempotency_key="goal", authority_snapshot=approved)
    starts = []
    def starter(**kwargs):
        starts.append(kwargs)
        raise RuntimeError("No live execution in this test")
    tick_goal(tmp_path, "DEMO", goal["id"], starter=starter)
    assert len(starts) == 1

    for key, replacement in [("base_url", "https://other.invalid/v1"), ("model", "second"), ("api_key", "rotated")]:
        original = connection[key]
        connection[key] = replacement
        assert snapshot() != approved, key
        connection[key] = original
    for suffix in ["delivery_mode: custom\n", "unknown_execution_setting: changed\n"]:
        config.write_text(original_config + suffix)
        assert snapshot() != approved
    config.write_text(original_config)
    for mutate in [lambda t: t["roles"]["implementation"].update(state="disabled"),
                   lambda t: t["manager"]["runtime"].update(model="other"),
                   lambda t: t["workers"][0]["runtime"].update(additional_prompt="Different method"),
                   lambda t: t["workers"][0].update(lifecycle="released")]:
        altered = deepcopy(team)
        mutate(altered)
        team_store._write_state(altered)
        assert snapshot() != approved
    team_store._write_state(team)
    models.write_text(model_text.replace("fake:first", "fake:second"))
    assert snapshot() != approved
    models.write_text("profiles:\n  unused:\n    orchestrator:\n      primary: fake:unused\n")
    with pytest.raises(ValueError, match="Unknown model profile"):
        snapshot()
