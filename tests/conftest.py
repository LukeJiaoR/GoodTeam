import pytest


@pytest.fixture(autouse=True)
def isolate_project_registry(monkeypatch, tmp_path_factory):
    registry_dir = tmp_path_factory.mktemp("registry")
    registry_file = registry_dir / "projects.json"
    monkeypatch.setenv("GOODTEAM_PROJECTS_REGISTRY", str(registry_file))
    monkeypatch.setenv("GOODTEAM_HOME", str(registry_dir))
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(registry_dir / "operator-state"))
