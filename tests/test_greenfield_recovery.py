"""Representative regressions from the real StockFlow greenfield trial."""


import pytest

from goodteam_os import web
from goodteam_os.project_import import confirm_project_mode, import_project


def test_empty_project_can_prepare_git_without_scaffolding_existing_files(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    workspace, root = tmp_path / "workspace", tmp_path / "stockflow"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]
    confirm_project_mode(workspace, key, root, "new_project")
    monkeypatch.setattr(web.ProjectReadinessExecutor, "_dispatch",
                        lambda _self, key, root, eid, attempt, runner, _background: runner(key, root, eid, attempt))
    monkeypatch.setattr(web.ProjectReadinessExecutor, "_default_preflight",
                        lambda *_: {"status": "ready", "mode": "codex", "local_only": True})
    web.ProjectReadinessExecutor(workspace).start(key, root, {"goal": "StockFlow", "brief": "Inventory"},
                                                idempotency_key="prepare")
    assert list(root.iterdir()) == []  # Preparation remains read-only.
    (root / ".DS_Store").write_bytes(b"ignored Finder metadata")
    recovered = web.initialize_project_git_for_request(workspace, {"project_key": key, "scaffold_new_project": True})
    assert recovered["state"] == "valid_head" and recovered["remote_created"] is False
    assert (root / "README.md").is_file() and (root / ".gitignore").is_file()
    (root / "business.txt").write_text("Do not scaffold an existing project")
    with pytest.raises(ValueError, match="empty imported new project"):
        web.initialize_project_git_for_request(workspace, {"project_key": key, "scaffold_new_project": True})
