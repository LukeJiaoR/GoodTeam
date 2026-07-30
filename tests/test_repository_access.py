from __future__ import annotations

import os
from pathlib import Path

import pytest

from goodteam_os import repository_access
from goodteam_os.repository_access import (
    AnchoredRepository,
    RepositoryAccessError,
    RepositoryBytes,
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


def test_raw_reader_returns_exact_bounded_bytes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    payload = "abc\u00e9z".encode("utf-8")
    (root / "data.txt").write_bytes(payload)
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        result = repository.read_bytes("data.txt", max_bytes=4)
    assert result == RepositoryBytes(
        path="data.txt",
        data=payload[:4],
        size=len(payload),
        truncated=True,
    )


def test_raw_reader_allows_nul_and_invalid_utf8_without_decoding(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    payload = b"alpha\x00beta\xff"
    (root / "data.bin").write_bytes(payload)
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        result = repository.read_bytes("data.bin", max_bytes=128)
    assert result.data == payload
    assert result.size == len(payload)
    assert result.truncated is False


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


@pytest.mark.parametrize(
    "ignored_name",
    sorted(repository_access.DEFAULT_IGNORED_DIRECTORIES),
)
def test_walk_excludes_each_default_ignored_directory(
    tmp_path: Path,
    ignored_name: str,
) -> None:
    root = tmp_path / "repo"
    ignored_dir = root / ignored_name
    visible_dir = root / "src"
    ignored_dir.mkdir(parents=True)
    visible_dir.mkdir(parents=True)
    (ignored_dir / "hidden.txt").write_text("secret\n", encoding="utf-8")
    (visible_dir / "app.py").write_text("print('ok')\n", encoding="utf-8")

    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        entries = repository.walk()

    assert [entry.path for entry in entries] == ["src", "src/app.py"]


def test_list_directory_selects_deterministic_lexicographic_subset_before_statting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    metadata_by_name: dict[str, os.stat_result] = {}
    for name in ("a.txt", "b.txt", "c.txt"):
        path = root / name
        path.write_text(name, encoding="utf-8")
        metadata_by_name[name] = path.stat()

    stat_calls: list[str] = []

    class FakeDirEntry:
        def __init__(self, name: str) -> None:
            self.name = name

        def stat(self, *, follow_symlinks: bool = False) -> os.stat_result:
            assert follow_symlinks is False
            stat_calls.append(self.name)
            return metadata_by_name[self.name]

    class FakeScandir:
        def __enter__(self) -> FakeScandir:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def __iter__(self):
            for name in ("c.txt", "a.txt", "b.txt"):
                yield FakeDirEntry(name)

    monkeypatch.setattr(repository_access.os, "scandir", lambda fd: FakeScandir())

    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        entries = repository.list_directory("", max_entries=2)

    assert [entry.path for entry in entries] == ["a.txt", "b.txt"]
    assert stat_calls == ["a.txt", "b.txt"]


def test_walk_ignored_directories_do_not_consume_max_entries_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "src").mkdir()
    (root / ".git" / "config").write_text("secret\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

    original_repository_entry = repository_access._repository_entry
    observed_paths: list[str] = []

    def recording_repository_entry(*args, **kwargs):
        entry = original_repository_entry(*args, **kwargs)
        observed_paths.append(entry.path)
        return entry

    monkeypatch.setattr(repository_access, "_repository_entry", recording_repository_entry)

    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        entries = repository.walk(max_entries=2)

    assert [entry.path for entry in entries] == ["src", "src/app.py"]
    assert observed_paths == ["src", "src/app.py"]


def test_walk_stops_directory_observation_at_entry_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(20):
        (root / f"file-{index:02d}.txt").write_text("value\n", encoding="utf-8")
    original_repository_entry = repository_access._repository_entry
    observed_entries = 0

    def counting_repository_entry(*args, **kwargs):
        nonlocal observed_entries
        observed_entries += 1
        return original_repository_entry(*args, **kwargs)

    monkeypatch.setattr(
        repository_access,
        "_repository_entry",
        counting_repository_entry,
    )
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        entries = repository.walk(max_entries=3)

    assert len(entries) == 3
    assert observed_entries == 3


def test_walk_stops_directory_observation_at_depth_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    deepest = root / "level-1" / "level-2"
    deepest.mkdir(parents=True)
    (deepest / "secret.txt").write_text("secret\n", encoding="utf-8")
    original_repository_entry = repository_access._repository_entry
    observed_paths: list[str] = []

    def recording_repository_entry(*args, **kwargs):
        entry = original_repository_entry(*args, **kwargs)
        observed_paths.append(entry.path)
        return entry

    monkeypatch.setattr(
        repository_access,
        "_repository_entry",
        recording_repository_entry,
    )
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        entries = repository.walk(max_depth=2)

    assert [entry.path for entry in entries] == ["level-1", "level-1/level-2"]
    assert observed_paths == ["level-1", "level-1/level-2"]


def test_walk_rejects_negative_depth_limit(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        with pytest.raises(ValueError, match="max_depth must be non-negative"):
            repository.walk(max_depth=-1)


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


@pytest.mark.parametrize("path", ["README.md", "linked-file"])
def test_raw_reader_detects_symlink_or_replacement_during_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: str,
) -> None:
    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    target = root / "README.md"
    target.write_bytes(b"original\n")
    (external / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")
    if path == "linked-file":
        target.unlink()
        target.symlink_to(external / "secret.txt")
        with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
            with pytest.raises(RepositoryAccessError) as error:
                repository.read_bytes("README.md", max_bytes=128)
        assert error.value.code == "context_path_rejected"
        return

    original_read = os.read
    changed = False

    def changing_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        content = original_read(descriptor, size)
        if changed:
            return content
        changed = True
        replacement = root / "replacement"
        replacement.write_bytes(b"changed\n")
        replacement.replace(target)
        return content

    monkeypatch.setattr(repository_access.os, "read", changing_read)
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        with pytest.raises(RepositoryAccessError) as error:
            repository.read_bytes("README.md", max_bytes=128)
    assert changed is True
    assert error.value.code == "repository_entry_changed"


def test_reader_rechecks_root_after_text_processing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    original_root = tmp_path / "original-repo"
    root.mkdir()
    (root / "README.md").write_text("original\n", encoding="utf-8")
    original_bounded_text = repository_access._bounded_text_bytes

    def replacing_root(
        path: str,
        observed: bytes,
        *,
        max_bytes: int,
    ) -> tuple[bytes, bool]:
        result = original_bounded_text(path, observed, max_bytes=max_bytes)
        root.rename(original_root)
        root.mkdir()
        return result

    monkeypatch.setattr(repository_access, "_bounded_text_bytes", replacing_root)
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        with pytest.raises(RepositoryAccessError) as error:
            repository.read_text("README.md", max_bytes=128)

    assert error.value.code == "repository_root_changed"
