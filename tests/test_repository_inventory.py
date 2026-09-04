from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from goodteam_os import repository_inventory
from goodteam_os.repository_access import RepositoryEntry
from goodteam_os.repository_inventory import build_repository_inventory
from goodteam_os.repository_snapshot import (
    RepositorySnapshot,
    RepositorySnapshotError,
    capture_repository_snapshot,
    compute_repository_snapshot_id,
)


def test_inventory_is_snapshot_bound_and_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest", "build": "vite build"}}),
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "main.ts").write_text(
        "export const main = true;\n",
        encoding="utf-8",
    )
    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    first = build_repository_inventory(root, snapshot=snapshot)
    second = build_repository_inventory(root, snapshot=snapshot)

    assert first["schema"] == "repository_inventory.v1"
    assert first["snapshot_id"] == snapshot.snapshot_id
    assert first["inventory_digest"] == second["inventory_digest"]
    assert {item["command"] for item in first["commands"]} == {
        "npm run build",
        "npm run test",
    }
    assert all(item["classification"] == "heuristic" for item in first["commands"])
    assert "src/main.ts" in first["entry_points"]
    assert first["entrypoint_candidates"] == [
        {
            "path": "src/main.ts",
            "basis": "conventional_entrypoint_filename",
            "classification": "heuristic",
        }
    ]


def test_inventory_builds_from_dirty_git_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    tracked = root / "tracked.py"
    tracked.write_text("STAGED = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=root, check=True)
    tracked.write_text("STAGED = False\n", encoding="utf-8")
    (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    recaptured = capture_repository_snapshot(root, project_key="DEMO")
    inventory = build_repository_inventory(root, snapshot=snapshot)

    assert snapshot.source_control["dirty"] is True
    assert recaptured.snapshot_id == snapshot.snapshot_id
    assert inventory["snapshot_id"] == snapshot.snapshot_id
    assert inventory["scan"]["files_seen"] == 2


def test_inventory_ignores_goodteam_runtime_directory(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    runtime_home = root / ".goodteam" / "workers" / "job-1" / ".codex-home"
    runtime_home.mkdir(parents=True)
    (runtime_home / "auth.json").symlink_to(tmp_path / "outside-auth.json")

    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    inventory = build_repository_inventory(root, snapshot=snapshot)

    assert inventory["entry_points"] == ["src/app.py"]
    assert all(not path.startswith(".goodteam") for path in inventory["directory_structure"])


def test_inventory_treats_tracked_gitlink_as_opaque_directory(tmp_path: Path) -> None:
    submodule = tmp_path / "submodule"
    submodule.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=submodule, check=True)
    (submodule / "package.json").write_text(
        json.dumps({"scripts": {"test": "must-not-be-discovered"}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "package.json"], cwd=submodule, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=GoodTeam Test",
            "-c",
            "user.email=goodteam@example.invalid",
            "commit",
            "-qm",
            "submodule fixture",
        ],
        cwd=submodule,
        check=True,
    )

    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(submodule),
            "vendor/dependency",
        ],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", ".gitmodules", "vendor/dependency"], cwd=root, check=True)

    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    inventory = build_repository_inventory(root, snapshot=snapshot)

    gitlink = next(
        item
        for item in snapshot.path_manifest["entries"]
        if item["path"] == "vendor/dependency"
    )
    assert gitlink["git"]["mode"] == "160000"
    assert inventory["scan"]["entries_seen"] == 4
    assert inventory["scan"]["files_seen"] == 1
    assert inventory["scan"]["directories_seen"] == 1
    assert inventory["commands"] == []
    assert "vendor/dependency" not in inventory["directory_structure"]


def test_dirty_submodule_is_opaque_and_changes_snapshot_identity(tmp_path: Path) -> None:
    submodule = tmp_path / "submodule"
    submodule.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=submodule, check=True)
    (submodule / "inside.txt").write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "inside.txt"], cwd=submodule, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=GoodTeam Test",
            "-c",
            "user.email=goodteam@example.invalid",
            "commit",
            "-qm",
            "submodule fixture",
        ],
        cwd=submodule,
        check=True,
    )
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(submodule),
            "vendor/dependency",
        ],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", ".gitmodules", "vendor/dependency"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=GoodTeam Test",
            "-c",
            "user.email=goodteam@example.invalid",
            "commit",
            "-qm",
            "add submodule",
        ],
        cwd=root,
        check=True,
    )
    clean = capture_repository_snapshot(root, project_key="DEMO")
    (root / "vendor/dependency/inside.txt").write_text("dirty\n", encoding="utf-8")

    dirty = capture_repository_snapshot(root, project_key="DEMO")
    inventory = build_repository_inventory(root, snapshot=dirty)

    assert dirty.snapshot_id != clean.snapshot_id
    assert dirty.source_control["dirty"] is True
    assert dirty.content_scope["eligible_files"] == 1
    assert inventory["scan"]["files_seen"] == 1
    assert inventory["scan"]["directories_seen"] == 1


def test_inventory_accepts_round_tripped_legacy_gitlink_artifact(tmp_path: Path) -> None:
    submodule = tmp_path / "legacy-submodule"
    submodule.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=submodule, check=True)
    (submodule / "inside.txt").write_text("opaque\n", encoding="utf-8")
    subprocess.run(["git", "add", "inside.txt"], cwd=submodule, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=GoodTeam Test",
            "-c",
            "user.email=goodteam@example.invalid",
            "commit",
            "-qm",
            "legacy fixture",
        ],
        cwd=submodule,
        check=True,
    )
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(submodule),
            "dependency",
        ],
        cwd=root,
        check=True,
    )
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    manifest = dict(snapshot.path_manifest)
    entries = [dict(item) for item in manifest["entries"]]
    gitlink = next(item for item in entries if item["path"] == "dependency")
    gitlink["kind"] = "file"
    manifest["entries"] = entries
    legacy_scope = {
        **snapshot.content_scope,
        "eligible_files": snapshot.content_scope["eligible_files"] + 1,
    }
    legacy = replace(
        snapshot,
        path_manifest=manifest,
        content_scope=legacy_scope,
        snapshot_id="",
    )
    legacy = replace(legacy, snapshot_id=compute_repository_snapshot_id(legacy))
    artifact = tmp_path / "legacy-snapshot.json"
    artifact.write_text(json.dumps(legacy.as_dict()), encoding="utf-8")
    loaded = RepositorySnapshot(**json.loads(artifact.read_text(encoding="utf-8")))

    inventory = build_repository_inventory(root, snapshot=loaded)

    assert inventory["snapshot_id"] == legacy.snapshot_id
    assert inventory["scan"]["files_seen"] == 1
    assert inventory["scan"]["directories_seen"] == 0


def test_inventory_builds_from_linked_git_worktree_snapshot(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    primary.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=primary, check=True)
    (primary / "tracked.py").write_text("TRACKED = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=primary, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=GoodTeam Test",
            "-c",
            "user.email=goodteam@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=primary,
        check=True,
    )
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "linked-fixture", linked],
        cwd=primary,
        check=True,
    )

    snapshot = capture_repository_snapshot(linked, project_key="DEMO")

    inventory = build_repository_inventory(linked, snapshot=snapshot)
    bounded = build_repository_inventory(linked, snapshot=snapshot, max_entries=1)

    assert inventory["snapshot_id"] == snapshot.snapshot_id
    assert inventory["scan"]["files_seen"] == 1
    assert bounded["scan"]["entries_seen"] == 1
    assert bounded["scan"]["files_seen"] == 0
    assert bounded["scan"]["truncation_reasons"] == ["max_entries"]


@pytest.mark.parametrize(
    ("entry", "source_control", "expected"),
    [
        (
            RepositoryEntry(
                path=".git",
                kind="file",
                size=42,
                mtime_ns=1,
            ),
            {"kind": "git"},
            True,
        ),
        (
            RepositoryEntry(
                path="nested/.git",
                kind="file",
                size=42,
                mtime_ns=1,
            ),
            {"kind": "git"},
            False,
        ),
        (
            RepositoryEntry(
                path=".git",
                kind="symlink",
                size=42,
                mtime_ns=1,
            ),
            {"kind": "git"},
            False,
        ),
        (
            RepositoryEntry(
                path=".git",
                kind="file",
                size=42,
                mtime_ns=1,
            ),
            {"kind": "none"},
            False,
        ),
        (
            RepositoryEntry(
                path="node_modules",
                kind="file",
                size=42,
                mtime_ns=1,
            ),
            {"kind": "git"},
            False,
        ),
        (
            RepositoryEntry(
                path="nested/node_modules",
                kind="directory",
                size=0,
                mtime_ns=1,
            ),
            {"kind": "none"},
            True,
        ),
    ],
)
def test_inventory_policy_skips_only_allowed_metadata_entries(
    entry: RepositoryEntry,
    source_control: dict[str, object],
    expected: bool,
) -> None:
    assert (
        repository_inventory._is_policy_skipped_entry(
            entry,
            source_control=source_control,
        )
        is expected
    )


def test_inventory_digest_changes_with_a_new_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "main.py"
    source.write_text("print('first')\n", encoding="utf-8")
    first_snapshot = capture_repository_snapshot(root, project_key="DEMO")
    first = build_repository_inventory(root, snapshot=first_snapshot)
    source.write_text("print('second')\n", encoding="utf-8")
    second_snapshot = capture_repository_snapshot(root, project_key="DEMO")
    second = build_repository_inventory(root, snapshot=second_snapshot)

    assert first["snapshot_id"] != second["snapshot_id"]
    assert first["inventory_digest"] != second["inventory_digest"]


def test_inventory_rejects_snapshot_drift(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "main.py"
    source.write_text("print('first')\n", encoding="utf-8")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    source.write_text("print('changed')\n", encoding="utf-8")

    with pytest.raises(RepositorySnapshotError) as captured:
        build_repository_inventory(root, snapshot=snapshot)

    assert captured.value.code == "repository_snapshot_changed"


def test_constraint_candidates_are_not_confirmed_claims(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("# Rules\nNever execute deploy.\n", encoding="utf-8")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    inventory = build_repository_inventory(root, snapshot=snapshot)

    assert inventory["constraints"] == ["AGENTS.md: Rules", "AGENTS.md: Never execute deploy."]
    assert inventory["constraint_candidates"] == [
        {
            "path": "AGENTS.md",
            "text": "Rules",
            "basis": "instruction_document",
            "classification": "heuristic",
        },
        {
            "path": "AGENTS.md",
            "text": "Never execute deploy.",
            "basis": "instruction_document",
            "classification": "heuristic",
        },
    ]


def test_inventory_limits_entries_and_depth(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    current = root
    for depth in range(5):
        current = current / f"level-{depth}"
        current.mkdir()
        (current / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    inventory = build_repository_inventory(
        root,
        snapshot=snapshot,
        max_entries=3,
        max_depth=1,
    )

    assert inventory["scan"]["entries_seen"] <= 3
    assert inventory["scan"]["max_depth"] == 1
    assert inventory["scan"]["truncation_reasons"]


def test_inventory_rejects_transient_manifest_content_from_another_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    package_json = root / "package.json"
    original = json.dumps({"scripts": {"test": "vitest"}}) + "     "
    transient = json.dumps({"scripts": {"build": "vite build"}})
    assert len(original) == len(transient)
    package_json.write_text(original, encoding="utf-8")
    original_stat = package_json.stat()
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    real_read_inventory_text = repository_inventory._read_inventory_text
    injected = False

    def injecting_read_inventory_text(
        repository,
        snapshot,
        path_manifest,
        entry,
        *,
        max_read_bytes,
    ):
        nonlocal injected
        if not injected and entry.path == "package.json":
            injected = True
            package_json.write_text(transient, encoding="utf-8")
            os.utime(
                package_json,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            try:
                return real_read_inventory_text(
                    repository,
                    snapshot,
                    path_manifest,
                    entry,
                    max_read_bytes=max_read_bytes,
                )
            finally:
                package_json.write_text(original, encoding="utf-8")
                os.utime(
                    package_json,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                )
        return real_read_inventory_text(
            repository,
            snapshot,
            path_manifest,
            entry,
            max_read_bytes=max_read_bytes,
        )

    monkeypatch.setattr(
        repository_inventory,
        "_read_inventory_text",
        injecting_read_inventory_text,
    )

    with pytest.raises(RepositorySnapshotError) as captured:
        build_repository_inventory(root, snapshot=snapshot)

    assert captured.value.code in {
        "repository_snapshot_changed",
        "repository_entry_changed",
    }


def test_inventory_rejects_transient_constraint_content_from_another_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    agents = root / "AGENTS.md"
    original = "# Rules\nKeep tests focused.\n"
    transient = "# Rules\nShip directly now.\n "
    assert len(original) == len(transient)
    agents.write_text(original, encoding="utf-8")
    original_stat = agents.stat()
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    real_read_inventory_text = repository_inventory._read_inventory_text
    injected = False

    def injecting_read_inventory_text(
        repository,
        snapshot,
        path_manifest,
        entry,
        *,
        max_read_bytes,
    ):
        nonlocal injected
        if not injected and entry.path == "AGENTS.md":
            injected = True
            agents.write_text(transient, encoding="utf-8")
            os.utime(agents, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            try:
                return real_read_inventory_text(
                    repository,
                    snapshot,
                    path_manifest,
                    entry,
                    max_read_bytes=max_read_bytes,
                )
            finally:
                agents.write_text(original, encoding="utf-8")
                os.utime(
                    agents,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                )
        return real_read_inventory_text(
            repository,
            snapshot,
            path_manifest,
            entry,
            max_read_bytes=max_read_bytes,
        )

    monkeypatch.setattr(
        repository_inventory,
        "_read_inventory_text",
        injecting_read_inventory_text,
    )

    with pytest.raises(RepositorySnapshotError) as captured:
        build_repository_inventory(root, snapshot=snapshot)

    assert captured.value.code == "repository_snapshot_changed"


def test_inventory_excludes_transient_added_entrypoint_from_another_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "src"
    source.mkdir()
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    real_verify_repository_snapshot = repository_inventory.verify_repository_snapshot
    real_list_directory = repository_inventory.AnchoredRepository.list_directory
    verification_calls = 0
    scan_active = False
    injected = False

    def injecting_list_directory(self, path: str, *, max_entries=None):
        nonlocal injected
        if not injected and scan_active and path == "src":
            injected = True
            transient = source / "main.ts"
            transient.write_text("export const transient = true;\n", encoding="utf-8")
            try:
                return real_list_directory(self, path, max_entries=max_entries)
            finally:
                transient.unlink()
        return real_list_directory(self, path, max_entries=max_entries)

    def counting_verify_repository_snapshot(root_path, snapshot_value, limits=None):
        nonlocal verification_calls, scan_active
        verification_calls += 1
        if limits is None:
            result = real_verify_repository_snapshot(root_path, snapshot_value)
        else:
            result = real_verify_repository_snapshot(root_path, snapshot_value, limits)
        if verification_calls == 1:
            scan_active = True
        return result

    monkeypatch.setattr(
        repository_inventory.AnchoredRepository,
        "list_directory",
        injecting_list_directory,
    )
    monkeypatch.setattr(
        repository_inventory,
        "verify_repository_snapshot",
        counting_verify_repository_snapshot,
    )

    with pytest.raises(RepositorySnapshotError) as captured:
        build_repository_inventory(root, snapshot=snapshot)

    assert captured.value.code == "repository_snapshot_changed"


def test_inventory_reports_depth_truncation_without_claiming_entry_limit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    current = root
    for depth in range(3):
        current = current / f"level-{depth}"
        current.mkdir()
    (current / "main.py").write_text("print('deep')\n", encoding="utf-8")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    inventory = build_repository_inventory(
        root,
        snapshot=snapshot,
        max_entries=50,
        max_depth=1,
    )

    assert inventory["scan"]["truncated"] is True
    assert inventory["scan"]["truncation_reasons"] == ["max_depth"]
    assert not any("entry limit" in risk for risk in inventory["risks"])
    assert any("depth limit" in risk for risk in inventory["risks"])


def test_inventory_reports_entry_truncation_at_the_entry_ceiling(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(5):
        (root / f"file-{index}.py").write_text("VALUE = 1\n", encoding="utf-8")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")

    inventory = build_repository_inventory(
        root,
        snapshot=snapshot,
        max_entries=3,
        max_depth=5,
    )

    assert inventory["scan"]["truncated"] is True
    assert inventory["scan"]["truncation_reasons"] == ["max_entries"]
    assert any("entry limit" in risk for risk in inventory["risks"])
    assert not any("depth limit" in risk for risk in inventory["risks"])
