import os
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event

import pytest

from goodteam_os import project_import
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


def test_import_merges_stale_explicit_configs_with_workspace_discovery(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "sample"
    root.mkdir()

    first = import_project(workspace, str(root), requested_key="FIRST")
    second = import_project(
        workspace,
        str(root),
        requested_key="SECOND",
        existing_configs=[],
    )

    assert first["project"]["project_key"] == second["project"]["project_key"]
    assert second["created"] is False
    assert len(list((workspace / "projects").glob("*.local.yaml"))) == 1


def test_import_rejects_target_root_as_workspace_without_writing(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "README.md").write_text("# Project\n", encoding="utf-8")

    _assert_workspace_not_isolated(root, root)


def test_import_rejects_workspace_inside_target_without_writing(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "README.md").write_text("# Project\n", encoding="utf-8")

    _assert_workspace_not_isolated(root, root / ".goodteam")


def test_import_resolves_existing_workspace_ancestor_symlink(tmp_path: Path) -> None:
    root = tmp_path / "project"
    redirected = root / "redirected"
    redirected.mkdir(parents=True)
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    workspace_alias = tmp_path / "workspace-alias"
    workspace_alias.symlink_to(redirected, target_is_directory=True)

    _assert_workspace_not_isolated(root, workspace_alias / "nested-workspace")


@pytest.mark.parametrize("redirected_name", ["projects", "state"])
def test_import_rejects_workspace_symlink_into_target_without_writing(
    tmp_path: Path,
    redirected_name: str,
) -> None:
    root = tmp_path / "project"
    redirected = root / "redirected"
    redirected.mkdir(parents=True)
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / redirected_name).symlink_to(redirected, target_is_directory=True)

    _assert_workspace_not_isolated(root, workspace)


def test_concurrent_import_same_root_and_key_creates_one_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    results = _run_barrier_imports(
        tmp_path,
        monkeypatch,
        requested_keys=("SAMPLE", "SAMPLE"),
    )

    assert {result["project"]["project_key"] for result in results} == {"SAMPLE"}
    assert sorted(result["created"] for result in results) == [False, True]
    assert len(list((tmp_path / "workspace" / "projects").glob("*.local.yaml"))) == 1


def test_concurrent_import_same_root_with_different_keys_creates_one_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    results = _run_barrier_imports(
        tmp_path,
        monkeypatch,
        requested_keys=("FIRST", "SECOND"),
    )

    keys = {result["project"]["project_key"] for result in results}
    assert len(keys) == 1
    assert keys <= {"FIRST", "SECOND"}
    assert sorted(result["created"] for result in results) == [False, True]
    assert len(list((tmp_path / "workspace" / "projects").glob("*.local.yaml"))) == 1


def test_concurrent_import_merges_both_stale_explicit_config_snapshots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    results = _run_barrier_imports(
        tmp_path,
        monkeypatch,
        requested_keys=("FIRST", "SECOND"),
        existing_configs=[],
    )

    assert len({result["project"]["project_key"] for result in results}) == 1
    assert sorted(result["created"] for result in results) == [False, True]
    assert len(list((tmp_path / "workspace" / "projects").glob("*.local.yaml"))) == 1


@pytest.mark.parametrize("redirected_name", ["projects", "state"])
def test_import_fails_closed_when_workspace_directory_is_swapped_after_validation(
    tmp_path: Path,
    monkeypatch,
    redirected_name: str,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    root = tmp_path / "project"
    redirected = root / "redirected"
    redirected.mkdir(parents=True)
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    before = snapshot_target(root)
    if redirected_name == "projects":
        original_validate = project_import._validate_write_destination
        swapped = False

        def validate_then_swap(path: Path, target_root: Path, destination: Path):
            nonlocal swapped
            original_validate(path, target_root, destination)
            if not swapped and destination.name.endswith(".local.yaml"):
                swapped = True
                (path / "projects").symlink_to(redirected, target_is_directory=True)

        monkeypatch.setattr(project_import, "_validate_write_destination", validate_then_swap)
    else:
        original_validate_state = project_import._validate_project_state_destination

        def validate_state_then_swap(path: Path, target_root: Path, key: str):
            original_validate_state(path, target_root, key)
            (path / "state").symlink_to(redirected, target_is_directory=True)

        monkeypatch.setattr(
            project_import,
            "_validate_project_state_destination",
            validate_state_then_swap,
        )
    with pytest.raises(ProjectImportError) as error:
        import_project(workspace, str(root))

    assert error.value.code == "project_workspace_not_isolated"
    assert snapshot_target(root) == before


def test_config_publish_does_not_expose_partial_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    destination = projects_dir / "sample.local.yaml"
    content = "name: Sample\nkey: SAMPLE\n"
    ready_to_publish = Event()
    allow_publish = Event()
    original_link = os.link

    def guarded_link(source, target, *args, **kwargs):
        source_dir_fd = kwargs.get("src_dir_fd")
        if source_dir_fd is None:
            observed = Path(source).read_text(encoding="utf-8")
        else:
            descriptor = os.open(source, os.O_RDONLY, dir_fd=source_dir_fd)
            with os.fdopen(descriptor, encoding="utf-8") as source_file:
                observed = source_file.read()
        assert observed == content
        ready_to_publish.set()
        assert allow_publish.wait(timeout=5)
        return original_link(source, target, *args, **kwargs)

    monkeypatch.setattr(project_import.os, "link", guarded_link)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            project_import._publish_config_exclusively,
            destination,
            content,
        )
        try:
            assert ready_to_publish.wait(timeout=5)
            assert not destination.exists()
            assert list(projects_dir.glob("*.local.yaml")) == []
        finally:
            allow_publish.set()
        future.result(timeout=5)

    assert destination.read_text(encoding="utf-8") == content
    assert list(projects_dir.glob("*.tmp")) == []


def _assert_workspace_not_isolated(root: Path, workspace: Path) -> None:
    from goodteam_os.project_import import ProjectImportError

    before = snapshot_target(root)
    with pytest.raises(ProjectImportError) as error:
        import_project(workspace, str(root))

    assert error.value.code == "project_workspace_not_isolated"
    assert error.value.details["root"] == str(root.resolve())
    assert error.value.details["workspace"] == str(workspace.resolve())
    assert snapshot_target(root) == before


def _run_barrier_imports(
    tmp_path: Path,
    monkeypatch,
    *,
    requested_keys: tuple[str, str],
    existing_configs: list[Path] | None = None,
) -> list[dict[str, object]]:
    workspace = tmp_path / "workspace"
    root = tmp_path / "sample"
    root.mkdir()
    contenders_ready = Barrier(2)
    original_lock = project_import._workspace_import_lock

    @contextmanager
    def synchronized_lock(path: Path):
        contenders_ready.wait(timeout=5)
        with original_lock(path) as workspace_fd:
            yield workspace_fd

    monkeypatch.setattr(project_import, "_workspace_import_lock", synchronized_lock)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                import_project,
                workspace,
                str(root),
                requested_key=requested_key,
                existing_configs=existing_configs,
            )
            for requested_key in requested_keys
        ]
        return [future.result(timeout=10) for future in futures]


def _process_import_worker(
    workspace_value: str,
    root_value: str,
    requested_key: str,
    barrier,
    results,
) -> None:
    barrier.wait(timeout=10)
    result = import_project(
        Path(workspace_value),
        root_value,
        requested_key=requested_key,
    )
    results.put((result["created"], result["project"]["project_key"]))


def _process_exit_while_locked(workspace_value: str, ready) -> None:
    with project_import._workspace_import_lock(Path(workspace_value)):
        ready.set()
        os._exit(17)


@pytest.mark.skipif(os.name != "posix", reason="project import locking requires POSIX")
def test_concurrent_process_imports_create_one_project(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    workspace = tmp_path / "workspace"
    root = tmp_path / "sample"
    root.mkdir()
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_process_import_worker,
            args=(str(workspace), str(root), requested_key, barrier, results),
        )
        for requested_key in ("FIRST", "SECOND")
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    observed = [results.get(timeout=5) for _ in processes]
    assert sorted(created for created, _key in observed) == [False, True]
    assert len({key for _created, key in observed}) == 1
    assert len(list((workspace / "projects").glob("*.local.yaml"))) == 1


@pytest.mark.skipif(os.name != "posix", reason="project import locking requires POSIX")
def test_process_exit_releases_workspace_lock(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    workspace = tmp_path / "workspace"
    root = tmp_path / "sample"
    root.mkdir()
    ready = context.Event()
    process = context.Process(
        target=_process_exit_while_locked,
        args=(str(workspace), ready),
    )

    process.start()
    assert ready.wait(timeout=10)
    process.join(timeout=10)
    assert process.exitcode == 17

    result = import_project(workspace, str(root))
    assert result["created"] is True
    assert result["project"]["project_key"] == "SAMPLE"
