from __future__ import annotations

import os
from pathlib import Path

import pytest

from goodteam_os import repository_access
from goodteam_os.repository_access import (
    AnchoredRepository,
    RepositoryAccessError,
    canonical_relative_path,
)


def root_identity(root: Path) -> dict[str, int]:
    value = root.stat()
    return {"device": value.st_dev, "inode": value.st_ino}


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../secret",
        "src/../secret",
        "./src/app.py",
        "src//app.py",
        "src/app.py/",
    ],
)
def test_reader_rejects_noncanonical_paths(tmp_path: Path, path: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        with pytest.raises(
            RepositoryAccessError,
            match="canonical repository-relative",
        ) as error:
            repository.read_text(path, max_bytes=128)
    assert error.value.code == "context_path_rejected"
    assert error.value.details["path"] == path


def test_canonical_relative_path_accepts_root_only_when_requested() -> None:
    assert canonical_relative_path("", allow_root=True) == ""
    with pytest.raises(RepositoryAccessError, match="canonical repository-relative"):
        canonical_relative_path("")


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


def test_reader_rejects_symlink_in_intermediate_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    (external / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")
    (root / "linked-dir").symlink_to(external, target_is_directory=True)
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        with pytest.raises(RepositoryAccessError, match="symbolic link"):
            repository.read_text("linked-dir/secret.txt", max_bytes=128)


def test_reader_returns_bounded_text_and_identity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        result = repository.read_text("README.md", max_bytes=11)
    assert result.text == "alpha\nbeta\n"
    assert result.truncated is True
    assert result.content_hash == (
        "sha256:e49c81e2d2f84e259d40e2fb8192f3bcd198b355184845d76d8f58807d0d78ee"
    )
    assert result.size == 17
    assert (result.start_line, result.end_line) == (1, 2)


def test_reader_returns_longest_valid_utf8_prefix_without_newline(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "message.txt").write_text("abc\u00e9z", encoding="utf-8")
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        result = repository.read_text("message.txt", max_bytes=4)
    assert result.text == "abc"
    assert result.truncated is True
    assert (result.start_line, result.end_line) == (1, 1)


def test_walk_is_deterministic_and_skips_ignored_directories(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / ".git" / "config").write_text("secret\n", encoding="utf-8")
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        entries = list(repository.walk())
    assert [(entry.path, entry.kind) for entry in entries] == [
        ("src", "directory"),
        ("src/app.py", "file"),
    ]


def test_reader_rejects_nul_as_binary(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "data.bin").write_bytes(b"alpha\x00beta")
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        with pytest.raises(RepositoryAccessError) as error:
            repository.read_text("data.bin", max_bytes=128)
    assert error.value.code == "repository_binary_file"


def test_reader_rejects_invalid_utf8(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "data.txt").write_bytes(b"alpha\xffbeta")
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        with pytest.raises(RepositoryAccessError) as error:
            repository.read_text("data.txt", max_bytes=128)
    assert error.value.code == "repository_non_text_file"


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
