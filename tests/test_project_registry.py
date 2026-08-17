from goodteam_os.project_registry import (
    ProjectRegistry,
    get_default_registry_path,
)


def test_default_registry_path_env_override(monkeypatch, tmp_path):
    override_path = tmp_path / "custom_registry.json"
    monkeypatch.setenv("GOODTEAM_PROJECTS_REGISTRY", str(override_path))
    assert get_default_registry_path() == override_path.resolve()

    monkeypatch.delenv("GOODTEAM_PROJECTS_REGISTRY", raising=False)
    home_override = tmp_path / "goodteam_home"
    monkeypatch.setenv("GOODTEAM_HOME", str(home_override))
    assert get_default_registry_path() == (home_override / "projects.json").resolve()


def test_register_and_list_projects(tmp_path):
    registry_file = tmp_path / "projects.json"
    registry = ProjectRegistry(registry_file)

    config_path = tmp_path / "app.local.yaml"
    config_path.write_text("name: Test App\nkey: TESTAPP\n", encoding="utf-8")
    root_path = tmp_path / "repo"
    root_path.mkdir()

    registry.register_project(config_path, root=root_path, key="TESTAPP")

    projects = registry.list_registered_projects()
    assert len(projects) == 1
    assert projects[0]["key"] == "TESTAPP"
    assert projects[0]["config_path"] == str(config_path.resolve())
    assert projects[0]["root"] == str(root_path.resolve())

    configs = registry.list_registered_configs()
    assert configs == [config_path.resolve()]


def test_register_project_updates_existing(tmp_path):
    registry_file = tmp_path / "projects.json"
    registry = ProjectRegistry(registry_file)

    config_path = tmp_path / "app.local.yaml"
    config_path.write_text("name: Test App\nkey: TESTAPP\n", encoding="utf-8")
    root_1 = tmp_path / "repo1"
    root_2 = tmp_path / "repo2"

    registry.register_project(config_path, root=root_1, key="TESTAPP")
    registry.register_project(config_path, root=root_2, key="TESTAPP")

    projects = registry.list_registered_projects()
    assert len(projects) == 1
    assert projects[0]["root"] == str(root_2.resolve())


def test_unregister_project(tmp_path):
    registry_file = tmp_path / "projects.json"
    registry = ProjectRegistry(registry_file)

    config1 = tmp_path / "app1.local.yaml"
    config1.write_text("key: APP1\n", encoding="utf-8")
    config2 = tmp_path / "app2.local.yaml"
    config2.write_text("key: APP2\n", encoding="utf-8")

    registry.register_project(config1, key="APP1")
    registry.register_project(config2, key="APP2")

    assert len(registry.list_registered_projects()) == 2

    assert registry.unregister_project("APP1") is True
    assert len(registry.list_registered_projects()) == 1
    assert registry.list_registered_projects()[0]["key"] == "APP2"

    assert registry.unregister_project(config2) is True
    assert len(registry.list_registered_projects()) == 0


def test_list_registered_configs_ignores_missing_files(tmp_path):
    registry_file = tmp_path / "projects.json"
    registry = ProjectRegistry(registry_file)

    missing_config = tmp_path / "missing.local.yaml"
    existing_config = tmp_path / "existing.local.yaml"
    existing_config.write_text("key: EXIST\n", encoding="utf-8")

    registry.register_project(missing_config, key="MISSING")
    registry.register_project(existing_config, key="EXIST")

    configs = registry.list_registered_configs()
    assert configs == [existing_config.resolve()]


def test_corrupt_registry_handling(tmp_path):
    registry_file = tmp_path / "projects.json"
    registry_file.write_text("not json", encoding="utf-8")
    registry = ProjectRegistry(registry_file)

    assert registry.list_registered_projects() == []
    assert registry.list_registered_configs() == []
