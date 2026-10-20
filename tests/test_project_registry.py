import json
from pathlib import Path

import pytest

from goodteam_os.project_registry import (
    ProjectRegistry,
    ProjectRegistryReadError,
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


def _registry_operation(registry, operation, tmp_path):
    if operation == "register":
        return registry.register_project(tmp_path / "new.local.yaml", key="NEW")
    if operation == "unregister":
        return registry.unregister_project("OLD")
    if operation == "list":
        return registry.list_registered_projects()
    if operation == "configs":
        return registry.list_registered_configs()
    return registry.find_project("OLD")


@pytest.mark.parametrize("operation", ["register", "unregister", "list", "configs", "find"])
@pytest.mark.parametrize(
    "original",
    [
        b"not json",
        b'{"version": 1, "projects": [',
        b"\xff",
        b"[]",
        b"null",
        b"{}",
        b'{"version": 1, "projects": null}',
        b'{"version": 1, "projects": {}}',
        b'{"version": 2, "projects": []}',
        b'{"version": true, "projects": []}',
        b'{"version": 1, "projects": ["OLD"]}',
        b'{"version": 1, "projects": [{"key": "OLD"}]}',
        b'{"version": 1, "projects": [{"config_path": " "}]}',
        b'{"version": 1, "projects": [{"config_path": 123}]}',
        b'{"version": 1, "projects": [{"config_path": "/old.yaml", "key": []}]}',
        b'{"version": 1, "projects": [{"config_path": "/old.yaml", "root": null}]}',
        b'{"version": 1, "projects": [{"config_path": "/old.yaml", "state_path": []}]}',
        b'{"version": 1, "projects": [{"config_path": "/old.yaml", "updated_at": 1}]}',
        b'{"version": 1, "projects": [{"config_path": "/old.yaml"}], "projects": []}',
        b'{"version": 1, "projects": [{"config_path": "/old.yaml", "config_path": "/other.yaml"}]}',
        b'{"version": 1, "projects": [], "extra": {"keep": 1, "keep": 2}}',
        b'{"version": 1, "projects": [], "extra": NaN}',
        b'{"version": 1, "projects": [], "extra": Infinity}',
        b'{"version": 1, "projects": [], "extra": -Infinity}',
        b'{"version": 1, "projects": [], "extra": 1e999}',
        b'{"version": 1, "projects": [], "extra": -1e999}',
        pytest.param(b"[" * 2000 + b"0" + b"]" * 2000, id="excessive-nesting"),
    ],
)
def test_corrupt_registry_blocks_operations_and_preserves_bytes(tmp_path, monkeypatch, operation, original):
    registry_file = tmp_path / "projects.json"
    registry_file.write_bytes(original)
    registry = ProjectRegistry(registry_file)

    def unexpected_write(data):
        pytest.fail("An unreadable registry must never reach the writer")

    monkeypatch.setattr(registry, "_write_data", unexpected_write)
    with pytest.raises(ProjectRegistryReadError, match="registry has been left unchanged") as error:
        _registry_operation(registry, operation, tmp_path)

    assert str(registry_file) in str(error.value)
    assert error.value.__cause__ is not None
    assert registry_file.read_bytes() == original
    assert list(tmp_path.iterdir()) == [registry_file]


@pytest.mark.parametrize("operation", ["register", "unregister", "list", "configs", "find"])
@pytest.mark.parametrize("read_error", [PermissionError("read denied"), OSError("read failed")])
def test_unreadable_registry_blocks_operations_and_preserves_bytes(tmp_path, monkeypatch, operation, read_error):
    registry_file = tmp_path / "projects.json"
    original = b'{"version": 1, "projects": [{"key": "OLD", "config_path": "/old.yaml"}]}'
    registry_file.write_bytes(original)
    registry = ProjectRegistry(registry_file)
    read_text = Path.read_text

    def unreadable(path, *args, **kwargs):
        if path == registry_file:
            raise read_error
        return read_text(path, *args, **kwargs)

    def unexpected_write(data):
        pytest.fail("An unreadable registry must never reach the writer")

    monkeypatch.setattr(Path, "read_text", unreadable)
    monkeypatch.setattr(registry, "_write_data", unexpected_write)
    with pytest.raises(ProjectRegistryReadError) as error:
        _registry_operation(registry, operation, tmp_path)

    assert error.value.__cause__ is read_error
    assert registry_file.read_bytes() == original
    assert list(tmp_path.iterdir()) == [registry_file]


@pytest.mark.parametrize("operation", ["register", "unregister", "list"])
def test_registry_directory_is_not_treated_as_a_missing_file(tmp_path, operation):
    registry_path = tmp_path / "projects.json"
    registry_path.mkdir()
    sentinel = registry_path / "keep"
    sentinel.write_bytes(b"original")
    registry = ProjectRegistry(registry_path)

    with pytest.raises(ProjectRegistryReadError):
        _registry_operation(registry, operation, tmp_path)

    assert sentinel.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [registry_path]


def test_missing_registry_is_empty_until_first_registration(tmp_path):
    registry_file = tmp_path / "new-home" / "projects.json"
    registry = ProjectRegistry(registry_file)

    assert registry.list_registered_projects() == []
    assert registry.list_registered_configs() == []
    assert registry.find_project("OLD") is None
    assert registry.unregister_project("OLD") is False
    assert not registry_file.parent.exists()

    registry.register_project(tmp_path / "new.local.yaml", key="NEW")
    assert registry_file.is_file()
    assert registry.find_project("NEW")["key"] == "NEW"


def test_legacy_version_and_unknown_fields_are_preserved(tmp_path):
    registry_file = tmp_path / "projects.json"
    original_entry = {"config_path": str(tmp_path / "old.yaml"), "key": "OLD", "custom": {"keep": True, "weight": 1.25}}
    registry_file.write_text(json.dumps({"projects": [original_entry], "custom_top": ["keep"]}), encoding="utf-8")
    registry = ProjectRegistry(registry_file)

    registry.register_project(tmp_path / "new.yaml", key="NEW")
    assert registry.find_project("OLD") == original_entry
    assert registry.unregister_project("NEW") is True
    assert json.loads(registry_file.read_text(encoding="utf-8")) == {
        "version": 1,
        "projects": [original_entry],
        "custom_top": ["keep"],
    }


def test_registry_can_be_used_after_explicit_repair(tmp_path):
    registry_file = tmp_path / "projects.json"
    registry_file.write_bytes(b"not json")
    registry = ProjectRegistry(registry_file)

    with pytest.raises(ProjectRegistryReadError):
        registry.register_project(tmp_path / "new.yaml", key="NEW")
    assert registry_file.read_bytes() == b"not json"

    registry_file.write_text('{"version": 1, "projects": []}', encoding="utf-8")
    registry.register_project(tmp_path / "new.yaml", key="NEW")
    assert registry.find_project("NEW")["key"] == "NEW"
    assert registry.unregister_project("NEW") is True
