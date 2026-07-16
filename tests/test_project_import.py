from pathlib import Path

from goodteam_os.project_import import import_project
from goodteam_os.projects import build_registered_project_config


def snapshot_target(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def test_import_writes_only_goodteam_workspace_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "existing"
    root.mkdir()
    (root / "README.md").write_text("# Existing\n", encoding="utf-8")
    before = snapshot_target(root)

    result = import_project(workspace, str(root), display_name="Existing")

    after = snapshot_target(root)
    assert before == after
    assert result["created"] is True
    assert result["project"]["project_key"] == "EXISTING"
    assert result["home_url"] == "/projects/EXISTING/home"
    assert (workspace / "projects" / "existing.local.yaml").is_file()
    assert (workspace / "state" / "projects" / "EXISTING" / "readiness.json").is_file()
    assert not (root / "docs").exists()
    assert not (root / ".git").exists()


def test_reimport_returns_existing_project_without_duplicate_config(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "sample"
    root.mkdir()

    first = import_project(workspace, str(root))
    second = import_project(workspace, str(root))

    assert first["project"]["project_key"] == second["project"]["project_key"]
    assert second["created"] is False
    assert len(list((workspace / "projects").glob("*.local.yaml"))) == 1


def test_import_suffixes_derived_key_for_a_different_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    first = tmp_path / "one" / "sample"
    second = tmp_path / "two" / "sample"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    assert import_project(workspace, str(first))["project"]["project_key"] == "SAMPLE"
    assert import_project(workspace, str(second))["project"]["project_key"] == "SAMPLE-2"


def test_import_rejects_missing_root_with_stable_code(tmp_path: Path) -> None:
    from goodteam_os.project_import import ProjectImportError

    try:
        import_project(tmp_path / "workspace", str(tmp_path / "missing"))
    except ProjectImportError as exc:
        assert exc.code == "project_root_not_found"
    else:
        raise AssertionError("expected missing root to fail")


def test_import_rejects_unreadable_root_with_stable_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    root = tmp_path / "blocked"
    root.mkdir()
    original_iterdir = Path.iterdir

    def guarded_iterdir(path: Path):
        if path.resolve() == root.resolve():
            raise PermissionError("blocked")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    try:
        import_project(tmp_path / "workspace", str(root))
    except ProjectImportError as exc:
        assert exc.code == "project_root_not_readable"
    else:
        raise AssertionError("expected unreadable root to fail")


def test_import_rejects_file_root_with_stable_code(tmp_path: Path) -> None:
    from goodteam_os.project_import import ProjectImportError

    root = tmp_path / "not-a-directory"
    root.write_text("content\n", encoding="utf-8")

    try:
        import_project(tmp_path / "workspace", str(root))
    except ProjectImportError as exc:
        assert exc.code == "project_root_not_directory"
    else:
        raise AssertionError("expected file root to fail")


def test_import_initializes_missing_readiness_for_legacy_match(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "legacy"
    root.mkdir()
    config_path = workspace / "projects" / "legacy.local.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        build_registered_project_config("LEGACY", "Legacy", root),
        encoding="utf-8",
    )

    result = import_project(workspace, str(root), existing_configs=[config_path])

    assert result["created"] is False
    assert result["project"]["project_key"] == "LEGACY"
    assert (workspace / "state" / "projects" / "LEGACY" / "readiness.json").is_file()


def test_import_recovers_when_filename_appears_after_config_snapshot(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    first = tmp_path / "one" / "sample"
    second = tmp_path / "two" / "sample"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    config_path = workspace / "projects" / "sample.local.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        build_registered_project_config("SAMPLE", "Sample", first),
        encoding="utf-8",
    )

    result = import_project(workspace, str(second), existing_configs=[])

    assert result["project"]["project_key"] == "SAMPLE-2"
    assert len(list((workspace / "projects").glob("*.local.yaml"))) == 2
