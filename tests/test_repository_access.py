from __future__ import annotations

import os
from pathlib import Path

import pytest

from goodteam_os import repository_access
from goodteam_os.repository_access import (
    AnchoredRepository,
    RepositoryAccessError,
)


def root_identity(root: Path) -> dict[str, int]:
    value = root.stat()
    return {"device": value.st_dev, "inode": value.st_ino}


def test_reader_never_follows_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    (external / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")
    (root / "linked-file").symlink_to(external / "secret.txt")
    (root / "linked-dir").symlink_to(external, target_is_directory=True)
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        assert {item.kind for item in repository.list_directory("")} == {"symlink"}
        with pytest.raises(RepositoryAccessError, match="symbolic link"):
            repository.read_text("linked-file", max_bytes=128)
        with pytest.raises(RepositoryAccessError, match="symbolic link"):
            repository.list_directory("linked-dir")


def test_reader_rejects_root_identity_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    expected = root_identity(root)
    expected["inode"] += 1
    with pytest.raises(RepositoryAccessError) as error:
        AnchoredRepository(root, expected_identity=expected)
    assert error.value.code == "repository_root_changed"


@pytest.mark.parametrize("change", ["inode", "size", "mtime_ns"])
def test_reader_detects_entry_change_during_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    change: str,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "README.md"
    target.write_text("original\n", encoding="utf-8")
    original_read = os.read
    changed = False

    def changing_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        content = original_read(descriptor, size)
        if changed:
            return content
        changed = True
        if change == "inode":
            replacement = root / "replacement"
            replacement.write_text("changed!\n", encoding="utf-8")
            replacement.replace(target)
        elif change == "size":
            target.write_text("changed content\n", encoding="utf-8")
        else:
            before = target.stat()
            os.utime(
                target,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
        return content

    monkeypatch.setattr(repository_access.os, "read", changing_read)
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        with pytest.raises(RepositoryAccessError) as error:
            repository.read_text("README.md", max_bytes=128)
    assert changed is True
    assert error.value.code == "repository_entry_changed"
    assert error.value.details == {"path": "README.md"}

