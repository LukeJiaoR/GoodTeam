import json
import errno
import os
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event

import pytest

from goodteam_os import project_import
from goodteam_os import project_readiness
from goodteam_os.project_import import import_project
from goodteam_os.project_readiness import ProjectReadinessStore
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


def test_confirm_project_mode_serializes_concurrent_read_modify_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    imported = import_project(workspace, str(root))
    key = imported["project"]["project_key"]
    readiness_path = workspace / "state" / "projects" / key / "readiness.json"
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    readiness["steps"].append({"id": "external_fact", "status": "pending", "required": False})
    readiness_path.write_text(json.dumps(readiness), encoding="utf-8")
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
                project_import.confirm_project_mode,
                workspace,
                key,
                root,
                mode,
            )
            for mode in ("new_project", "existing_project")
        ]
        results = [future.result(timeout=10) for future in futures]

    final = json.loads(readiness_path.read_text(encoding="utf-8"))
    assert {result["project_mode"] for result in results} == {
        "new_project",
        "existing_project",
    }
    assert final["project_mode"] in {"new_project", "existing_project"}
    assert [step["id"] for step in final["steps"]].count("external_fact") == 1
    assert not list(readiness_path.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_key",
        "wrong_root",
        "missing_key",
        "missing_root",
        "nonstr_key",
        "nonstr_root",
    ],
)
def test_confirm_project_mode_rejects_mismatched_readiness_without_writing(
    tmp_path: Path,
    mutation: str,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    imported = import_project(workspace, str(root))
    key = imported["project"]["project_key"]
    readiness_path = workspace / "state" / "projects" / key / "readiness.json"
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    if mutation == "wrong_key":
        readiness["project_key"] = "OTHER"
    elif mutation == "wrong_root":
        readiness["canonical_root"] = str(tmp_path / "sensitive-other-root")
    elif mutation == "missing_key":
        readiness.pop("project_key")
    elif mutation == "missing_root":
        readiness.pop("canonical_root")
    elif mutation == "nonstr_key":
        readiness["project_key"] = ["PROJECT"]
    else:
        readiness["canonical_root"] = {"path": str(root)}
    readiness_path.write_text(json.dumps(readiness, indent=1) + "\n", encoding="utf-8")
    before = readiness_path.read_bytes()

    with pytest.raises(ProjectImportError) as error:
        project_import.confirm_project_mode(
            workspace,
            key,
            root,
            "existing_project",
        )

    assert error.value.code == "project_readiness_mismatch"
    assert error.value.details == {"project_key": key}
    assert "sensitive-other-root" not in str(error.value)
    assert readiness_path.read_bytes() == before
    assert [path.name for path in readiness_path.parent.iterdir()] == ["readiness.json"]


@pytest.mark.parametrize("failure_point", ["post_replace", "post_publish_verify"])
def test_confirm_project_mode_restores_exact_readiness_after_anchored_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    imported = import_project(workspace, str(root))
    key = imported["project"]["project_key"]
    readiness_path = workspace / "state" / "projects" / key / "readiness.json"
    before_readiness = readiness_path.read_bytes()
    before_target = snapshot_target(root)

    if failure_point == "post_replace":
        original_replace = project_readiness.os.replace
        failed = False

        def replace_then_fail(source, target, *args, **kwargs):
            nonlocal failed
            result = original_replace(source, target, *args, **kwargs)
            if not failed and str(source).endswith(".tmp"):
                failed = True
                raise OSError(errno.ENOTDIR, "injected post-replace verification failure")
            return result

        monkeypatch.setattr(project_readiness.os, "replace", replace_then_fail)
    else:
        def fail_published_verify(_store, _project_key):
            raise OSError(errno.ENOTDIR, "injected directory identity failure")

        monkeypatch.setattr(
            ProjectReadinessStore,
            "_verify_anchored_write",
            fail_published_verify,
        )

    with pytest.raises(ProjectImportError) as error:
        project_import.confirm_project_mode(
            workspace,
            key,
            root,
            "existing_project",
        )

    assert error.value.code == "project_workspace_not_isolated"
    assert readiness_path.read_bytes() == before_readiness
    assert [path.name for path in readiness_path.parent.iterdir()] == ["readiness.json"]
    assert snapshot_target(root) == before_target


def test_confirm_project_mode_rollback_preserves_external_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    imported = import_project(workspace, str(root))
    key = imported["project"]["project_key"]
    readiness_path = workspace / "state" / "projects" / key / "readiness.json"
    state_dir = readiness_path.parent
    before_readiness = readiness_path.read_bytes()
    replacement = b'{"owner":"external-replacement"}\n'
    collision = b'{"owner":"external-temp-collision"}\n'
    replacement_identity: tuple[int, int] | None = None
    collision_path: Path | None = None

    def replace_then_fail_verify(store, project_key):
        nonlocal replacement_identity, collision_path
        transaction = store._anchored_writes[project_key]
        collision_path = state_dir / transaction.temporary_name
        collision_path.write_bytes(collision)
        readiness_path.unlink()
        readiness_path.write_bytes(replacement)
        replacement_stat = readiness_path.stat()
        replacement_identity = (replacement_stat.st_dev, replacement_stat.st_ino)
        raise OSError(errno.ENOTDIR, "injected external replacement")

    monkeypatch.setattr(
        ProjectReadinessStore,
        "_verify_anchored_write",
        replace_then_fail_verify,
    )

    with pytest.raises(ProjectImportError):
        project_import.confirm_project_mode(
            workspace,
            key,
            root,
            "existing_project",
        )

    assert readiness_path.read_bytes() == before_readiness
    assert collision_path is not None
    assert collision_path.read_bytes() == collision
    quarantined = [
        path
        for path in state_dir.iterdir()
        if path.is_file() and (path.stat().st_dev, path.stat().st_ino) == replacement_identity
    ]
    assert len(quarantined) == 1
    assert quarantined[0] != readiness_path
    assert quarantined[0] != collision_path
    assert quarantined[0].read_bytes() == replacement
    assert not list(state_dir.glob("*.backup"))


def test_confirm_project_mode_quarantines_repeated_external_replacements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    imported = import_project(workspace, str(root))
    key = imported["project"]["project_key"]
    readiness_path = workspace / "state" / "projects" / key / "readiness.json"
    state_dir = readiness_path.parent
    before_readiness = readiness_path.read_bytes()
    replacements = [
        b'{"owner":"first-external-replacement"}\n',
        b'{"owner":"second-external-replacement"}\n',
    ]
    replacement_identities: list[tuple[int, int]] = []

    def replace_then_fail_verify(_store, _project_key):
        readiness_path.unlink()
        readiness_path.write_bytes(replacements[0])
        replacement_stat = readiness_path.stat()
        replacement_identities.append(
            (replacement_stat.st_dev, replacement_stat.st_ino)
        )
        raise OSError(errno.ENOTDIR, "injected external replacement")

    original_link = project_readiness.os.link
    replaced_after_quarantine = False

    def link_then_replace_again(source, target, *args, **kwargs):
        nonlocal replaced_after_quarantine
        result = original_link(source, target, *args, **kwargs)
        if (
            not replaced_after_quarantine
            and source == "readiness.json"
            and target != "readiness.json"
            and not str(target).endswith(".backup")
        ):
            replaced_after_quarantine = True
            readiness_path.unlink()
            readiness_path.write_bytes(replacements[1])
            replacement_stat = readiness_path.stat()
            replacement_identities.append(
                (replacement_stat.st_dev, replacement_stat.st_ino)
            )
        return result

    monkeypatch.setattr(
        ProjectReadinessStore,
        "_verify_anchored_write",
        replace_then_fail_verify,
    )
    monkeypatch.setattr(project_readiness.os, "link", link_then_replace_again)

    with pytest.raises(ProjectImportError) as error:
        project_import.confirm_project_mode(
            workspace,
            key,
            root,
            "existing_project",
        )

    quarantined = {
        (path.stat().st_dev, path.stat().st_ino): path
        for path in state_dir.iterdir()
        if path.is_file() and path != readiness_path
    }
    assert error.value.code == "project_workspace_not_isolated"
    assert replaced_after_quarantine
    assert len(replacement_identities) == 2
    assert readiness_path.read_bytes() == before_readiness
    assert set(replacement_identities) <= quarantined.keys()
    assert {quarantined[identity].read_bytes() for identity in replacement_identities} == set(
        replacements
    )
    assert not list(state_dir.glob("*.backup"))


def test_confirm_project_mode_rejects_external_temp_replacement_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    imported = import_project(workspace, str(root))
    key = imported["project"]["project_key"]
    readiness_path = workspace / "state" / "projects" / key / "readiness.json"
    state_dir = readiness_path.parent
    before_readiness = readiness_path.read_bytes()
    before_target = snapshot_target(root)
    external_temp = b'{"owner":"external-temp-replacement"}\n'
    original_verify = ProjectReadinessStore._verify_current_chain
    verify_calls = 0

    def verify_then_replace_temp(store, project_key, pinned):
        nonlocal verify_calls
        result = original_verify(store, project_key, pinned)
        verify_calls += 1
        if verify_calls == 4:
            temporary_paths = list(state_dir.glob("*.tmp"))
            assert len(temporary_paths) == 1
            temporary_paths[0].unlink()
            temporary_paths[0].write_bytes(external_temp)
        return result

    monkeypatch.setattr(
        ProjectReadinessStore,
        "_verify_current_chain",
        verify_then_replace_temp,
    )

    with pytest.raises(ProjectImportError) as error:
        project_import.confirm_project_mode(
            workspace,
            key,
            root,
            "existing_project",
        )

    remaining_temps = list(state_dir.glob("*.tmp"))
    assert error.value.code == "project_workspace_not_isolated"
    assert readiness_path.read_bytes() == before_readiness
    assert len(remaining_temps) == 1
    assert remaining_temps[0].read_bytes() == external_temp
    assert not list(state_dir.glob("*.backup"))
    assert snapshot_target(root) == before_target


def test_confirm_project_mode_recovers_after_final_check_temp_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    imported = import_project(workspace, str(root))
    key = imported["project"]["project_key"]
    readiness_path = workspace / "state" / "projects" / key / "readiness.json"
    state_dir = readiness_path.parent
    before_readiness = readiness_path.read_bytes()
    before_target = snapshot_target(root)
    external = b'{"owner":"after-final-check"}\n'
    external_identity: tuple[int, int] | None = None
    original_replace = project_readiness.os.replace
    swapped = False

    def swap_temp_then_replace(source, target, *args, **kwargs):
        nonlocal external_identity, swapped
        source_dir_fd = kwargs.get("src_dir_fd")
        if not swapped and str(source).endswith(".tmp") and target == "readiness.json":
            swapped = True
            os.unlink(source, dir_fd=source_dir_fd)
            descriptor = os.open(
                source,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=source_dir_fd,
            )
            with os.fdopen(descriptor, "wb") as external_file:
                external_file.write(external)
                external_file.flush()
                os.fsync(external_file.fileno())
                stat_result = os.fstat(external_file.fileno())
                external_identity = (stat_result.st_dev, stat_result.st_ino)
        return original_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(project_readiness.os, "replace", swap_temp_then_replace)

    with pytest.raises(ProjectImportError) as error:
        project_import.confirm_project_mode(
            workspace,
            key,
            root,
            "existing_project",
        )

    quarantined = [
        path
        for path in state_dir.iterdir()
        if path.is_file() and (path.stat().st_dev, path.stat().st_ino) == external_identity
    ]
    assert error.value.code == "project_workspace_not_isolated"
    assert readiness_path.read_bytes() == before_readiness
    assert len(quarantined) == 1
    assert quarantined[0] != readiness_path
    assert quarantined[0].read_bytes() == external
    assert not list(state_dir.glob("*.backup"))
    assert snapshot_target(root) == before_target


def test_import_quarantines_external_final_inode_after_temp_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    root.mkdir()
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    before_target = snapshot_target(root)
    state_dir = workspace / "state" / "projects" / "PROJECT"
    external = b'{"owner":"create-after-final-check"}\n'
    external_identity: tuple[int, int] | None = None
    original_replace = project_readiness.os.replace
    swapped = False

    def swap_temp_then_replace(source, target, *args, **kwargs):
        nonlocal external_identity, swapped
        source_dir_fd = kwargs.get("src_dir_fd")
        if not swapped and str(source).endswith(".tmp") and target == "readiness.json":
            swapped = True
            os.unlink(source, dir_fd=source_dir_fd)
            descriptor = os.open(
                source,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=source_dir_fd,
            )
            with os.fdopen(descriptor, "wb") as external_file:
                external_file.write(external)
                external_file.flush()
                os.fsync(external_file.fileno())
                stat_result = os.fstat(external_file.fileno())
                external_identity = (stat_result.st_dev, stat_result.st_ino)
        return original_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(project_readiness.os, "replace", swap_temp_then_replace)

    with pytest.raises(ProjectImportError) as error:
        import_project(workspace, str(root))

    quarantined = [
        path
        for path in state_dir.iterdir()
        if path.is_file() and (path.stat().st_dev, path.stat().st_ino) == external_identity
    ]
    assert error.value.code == "project_workspace_not_isolated"
    assert not (state_dir / "readiness.json").exists()
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == external
    assert not list(state_dir.glob("*.backup"))
    assert not list((workspace / "projects").glob("*.local.yaml"))
    assert snapshot_target(root) == before_target


def test_confirm_project_mode_fails_closed_after_state_validation_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    redirected = root / "redirected"
    redirected.mkdir(parents=True)
    (root / "blob.bin").write_bytes(b"asset")
    imported = import_project(workspace, str(root))
    key = imported["project"]["project_key"]
    source_readiness = workspace / "state" / "projects" / key / "readiness.json"
    external_payload = json.loads(source_readiness.read_text(encoding="utf-8"))
    external_payload["owner"] = "external"
    external_content = json.dumps(external_payload, sort_keys=True) + "\n"
    external_readiness = redirected / "readiness.json"
    external_readiness.write_text(external_content, encoding="utf-8")
    before = snapshot_target(root)
    original_validate = project_import._validate_project_state_destination
    swapped = False

    def validate_then_swap(path: Path, target_root: Path, project_key: str):
        nonlocal swapped
        original_validate(path, target_root, project_key)
        if not swapped:
            swapped = True
            state_dir = workspace / "state"
            state_dir.rename(workspace / "state-detached")
            state_dir.symlink_to(redirected, target_is_directory=True)

    monkeypatch.setattr(
        project_import,
        "_validate_project_state_destination",
        validate_then_swap,
    )

    with pytest.raises(ProjectImportError) as error:
        project_import.confirm_project_mode(
            workspace,
            key,
            root,
            "existing_project",
        )

    assert error.value.code == "project_workspace_not_isolated"
    assert external_readiness.read_text(encoding="utf-8") == external_content
    assert snapshot_target(root) == before


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
    assert list(workspace.rglob("*.local.yaml")) == []
    assert list(workspace.rglob("*.tmp")) == []
    assert snapshot_target(root) == before


def test_import_rolls_back_config_when_readiness_initialization_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    before = snapshot_target(root)

    def fail_readiness(*_args, **_kwargs):
        raise RuntimeError("readiness failed")

    monkeypatch.setattr(ProjectReadinessStore, "initialize", fail_readiness)
    with pytest.raises(RuntimeError, match="readiness failed"):
        import_project(workspace, str(root))

    assert list(workspace.rglob("*.local.yaml")) == []
    assert list(workspace.rglob("*.tmp")) == []
    assert snapshot_target(root) == before


def test_import_rollback_does_not_delete_replacement_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    workspace = tmp_path / "workspace"
    config_path = workspace / "projects" / "project.local.yaml"
    replacement = "name: External\nkey: EXTERNAL\n"

    def replace_config_then_fail(*_args, **_kwargs):
        config_path.unlink()
        config_path.write_text(replacement, encoding="utf-8")
        raise RuntimeError("readiness failed")

    monkeypatch.setattr(
        ProjectReadinessStore,
        "initialize",
        replace_config_then_fail,
    )
    with pytest.raises(RuntimeError, match="readiness failed"):
        import_project(workspace, str(root))

    assert config_path.read_text(encoding="utf-8") == replacement


def test_import_does_not_claim_replacement_between_link_and_identity_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    root = tmp_path / "project"
    root.mkdir()
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    config_path = workspace / "projects" / "project.local.yaml"
    replacement = "name: External\nkey: EXTERNAL\n"
    before = snapshot_target(root)
    original_link = os.link
    owned_identities: list[tuple[int, int]] = []

    def link_then_replace(source, target, *args, **kwargs):
        result = original_link(source, target, *args, **kwargs)
        source_stat = os.stat(
            source,
            dir_fd=kwargs["src_dir_fd"],
            follow_symlinks=False,
        )
        owned_identities.append((source_stat.st_dev, source_stat.st_ino))
        destination_fd = kwargs["dst_dir_fd"]
        os.unlink(target, dir_fd=destination_fd)
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=destination_fd,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as replacement_file:
            replacement_file.write(replacement)
            replacement_file.flush()
            os.fsync(replacement_file.fileno())
        return result

    def fail_readiness(*_args, **_kwargs):
        raise RuntimeError("readiness failed")

    monkeypatch.setattr(project_import.os, "link", link_then_replace)
    monkeypatch.setattr(ProjectReadinessStore, "initialize", fail_readiness)
    with pytest.raises(ProjectImportError) as error:
        import_project(workspace, str(root))

    assert error.value.code == "project_workspace_not_isolated"
    assert config_path.read_text(encoding="utf-8") == replacement
    assert list(workspace.rglob("*.tmp")) == []
    remaining_identities = {
        (path.stat().st_dev, path.stat().st_ino)
        for path in workspace.rglob("*")
        if path.is_file()
    }
    assert owned_identities[0] not in remaining_identities
    assert snapshot_target(root) == before


def test_import_fails_closed_when_open_projects_directory_is_replaced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    root = tmp_path / "project"
    redirected = root / "redirected"
    redirected.mkdir(parents=True)
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    projects_dir = workspace / "projects"
    projects_dir.mkdir(parents=True)
    detached_projects = workspace / "projects-detached"
    before = snapshot_target(root)
    original_validate = project_import._validate_write_destination
    swapped = False

    def validate_then_swap(path: Path, target_root: Path, destination: Path):
        nonlocal swapped
        original_validate(path, target_root, destination)
        if not swapped and destination.name.endswith(".local.yaml"):
            swapped = True
            projects_dir.rename(detached_projects)
            projects_dir.symlink_to(redirected, target_is_directory=True)

    monkeypatch.setattr(project_import, "_validate_write_destination", validate_then_swap)
    with pytest.raises(ProjectImportError) as error:
        import_project(workspace, str(root))

    assert error.value.code == "project_workspace_not_isolated"
    assert list(tmp_path.rglob("*.local.yaml")) == []
    assert list(tmp_path.rglob("*.tmp")) == []
    assert snapshot_target(root) == before


def test_import_rolls_back_detached_config_when_projects_moves_after_publish(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    root = tmp_path / "project"
    redirected = root / "redirected"
    redirected.mkdir(parents=True)
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    projects_dir = workspace / "projects"
    detached_projects = workspace / "projects-detached"
    before = snapshot_target(root)
    original_link = os.link

    def link_then_swap(source, target, *args, **kwargs):
        result = original_link(source, target, *args, **kwargs)
        projects_dir.rename(detached_projects)
        projects_dir.symlink_to(redirected, target_is_directory=True)
        return result

    monkeypatch.setattr(project_import.os, "link", link_then_swap)
    with pytest.raises(ProjectImportError) as error:
        import_project(workspace, str(root))

    assert error.value.code == "project_workspace_not_isolated"
    assert list(tmp_path.rglob("*.local.yaml")) == []
    assert list(tmp_path.rglob("*.tmp")) == []
    assert snapshot_target(root) == before


@pytest.mark.parametrize("swapped_level", ["state", "projects", "key"])
def test_import_fails_closed_when_open_readiness_directory_is_replaced(
    tmp_path: Path,
    monkeypatch,
    swapped_level: str,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    root = tmp_path / "project"
    redirected = root / "redirected"
    redirected.mkdir(parents=True)
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    before = snapshot_target(root)
    original_open = project_readiness._open_directory_at
    swapped = False

    def open_then_swap(parent_fd: int, name: str, *, create: bool):
        nonlocal swapped
        descriptor = original_open(parent_fd, name, create=create)
        matches = name == swapped_level or (swapped_level == "key" and name == "PROJECT")
        if descriptor is not None and matches and not swapped:
            swapped = True
            if swapped_level == "state":
                path = workspace / "state"
                detached = workspace / "state-detached"
            elif swapped_level == "projects":
                path = workspace / "state" / "projects"
                detached = workspace / "state" / "projects-detached"
            else:
                path = workspace / "state" / "projects" / "PROJECT"
                detached = workspace / "state" / "projects" / "PROJECT-detached"
            path.rename(detached)
            path.symlink_to(redirected, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(project_readiness, "_open_directory_at", open_then_swap)
    with pytest.raises(ProjectImportError) as error:
        import_project(workspace, str(root))

    assert error.value.code == "project_workspace_not_isolated"
    assert list(tmp_path.rglob("*.local.yaml")) == []
    assert list(tmp_path.rglob("*.tmp")) == []
    assert not (
        workspace / "state" / "projects" / "PROJECT" / "readiness.json"
    ).exists()
    assert snapshot_target(root) == before


def test_import_rolls_back_detached_readiness_when_key_moves_after_replace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from goodteam_os.project_import import ProjectImportError

    root = tmp_path / "project"
    redirected = root / "redirected"
    redirected.mkdir(parents=True)
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    key_dir = workspace / "state" / "projects" / "PROJECT"
    detached_key = workspace / "state" / "projects" / "PROJECT-detached"
    before = snapshot_target(root)
    original_replace = os.replace

    def replace_then_swap(source, target, *args, **kwargs):
        result = original_replace(source, target, *args, **kwargs)
        key_dir.rename(detached_key)
        key_dir.symlink_to(redirected, target_is_directory=True)
        return result

    monkeypatch.setattr(project_readiness.os, "replace", replace_then_swap)
    with pytest.raises(ProjectImportError) as error:
        import_project(workspace, str(root))

    assert error.value.code == "project_workspace_not_isolated"
    assert list(tmp_path.rglob("*.local.yaml")) == []
    assert list(tmp_path.rglob("*.tmp")) == []
    assert not (detached_key / "readiness.json").exists()
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
