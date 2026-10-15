"""Bounded new-file sealing using local Git/files only; no live runtime."""
import os
import subprocess

import pytest

from goodteam_os.execution import correction_seal as seal
from goodteam_os.execution.task_snapshot import TaskInputError
from goodteam_os.skills.models import SkillError
from test_candidate_validation import git
from test_correction_codex_seal import prepared as prepared_fixture, returned  # noqa: F401
from test_correction_worker import worker_context as worker_fixture  # noqa: F401
from test_task_input_snapshot import ctx  # noqa: F401


@pytest.fixture
def source(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "original.txt").write_bytes(b"original\n")
    (tmp_path / ".gitignore").write_text("ignored/\n*.cache\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Offline input")
    return tmp_path, git(tmp_path, "rev-parse", "HEAD")


def add(root, name, data=b"new\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def capture(source):
    root, head = source
    return seal._capture(seal._Objects(root), head, allow_new=True)


def test_nested_binary_executable_additions_and_addition_only_progress(request):
    c = returned(request.getfixturevalue("prepared_fixture"))
    original_index = (c.project / ".git" / "index").read_bytes()
    (c.worktree / "feature.py").write_bytes((c.project / "feature.py").read_bytes())
    data = b"\x00\xffraw\r\n"
    add(c.worktree, "new sources/raw.bin", data)
    add(c.worktree, "new sources/run.py", b"print('new')\n").chmod(0o755)
    sealed = seal.seal_stopped_candidate(c.authority)
    head = sealed["candidate"]["head_sha"]
    assert head != c.head and git(c.worktree, "rev-parse", "HEAD^") == c.head
    assert subprocess.check_output(["git", "show", head + ":new sources/raw.bin"], cwd=c.project) == data
    assert git(c.worktree, "ls-tree", head, "new sources/run.py").startswith("100755 blob ")
    assert seal.seal_stopped_candidate(c.authority) == sealed
    assert git(c.project, "rev-parse", "HEAD") == c.head
    assert (c.project / ".git" / "index").read_bytes() == original_index
    assert not (c.project / "new sources").exists()


@pytest.mark.parametrize("name", [".env", ".ENV.production", ".goodteam/cache", ".Git/data", "nested/.gitignore",
    "nested/.codex/auth.json", "AUTH.JSON", "Credentials.JSON", "secrets/data", "private.KEY", "id_ed25519"])
def test_sensitive_and_hidden_paths_reject_before_reading_addition(source, monkeypatch, name):
    root, _ = source
    added = add(root, name)
    original = seal.regular_file
    def read(fd, leaf, **kwargs):
        assert (os.fstat(fd).st_ino, leaf) != (added.parent.stat().st_ino, added.name), "Rejected addition bytes were read"
        return original(fd, leaf, **kwargs)
    monkeypatch.setattr(seal, "regular_file", read)
    with pytest.raises((TaskInputError, SkillError)):
        capture(source)


@pytest.mark.parametrize("name", ["ignored/file.txt", "artifact.cache"])
def test_ignored_additions_reject_before_reading_bytes(source, monkeypatch, name):
    root, _ = source
    added = add(root, name)
    original = seal.regular_file
    def read(fd, leaf, **kwargs):
        assert leaf != added.name, "Ignored bytes were read"
        return original(fd, leaf, **kwargs)
    monkeypatch.setattr(seal, "regular_file", read)
    with pytest.raises(TaskInputError, match="ignored"):
        capture(source)


@pytest.mark.parametrize("mutation", ["delete", "remove_rule", "negate_rule", "mode", "nested"])
def test_additions_cannot_change_ignore_rules(source, mutation):
    root, _ = source
    if mutation == "nested":
        add(root, "nested/.gitignore", b"private.txt\n")
        git(root, "add", "nested/.gitignore")
        git(root, "commit", "-qm", "Nested ignore input")
        source = root, git(root, "rev-parse", "HEAD")
        (root / "nested/.gitignore").write_bytes(b"")
        add(root, "nested/private.txt")
    else:
        add(root, "ignored/file.txt")
        path = root / ".gitignore"
        if mutation == "delete":
            path.unlink()
        elif mutation == "mode":
            path.chmod(0o755)
        else:
            path.write_text("" if mutation == "remove_rule" else "ignored/*\n!ignored/file.txt\n")
    with pytest.raises(TaskInputError, match="ignore_changed"):
        capture(source)


@pytest.mark.parametrize("mutation", ["symlink", "hardlink", "fifo", "special_mode", "linked_parent", "nested_repo"])
def test_new_files_require_safe_regular_nodes(source, tmp_path, mutation):
    root, _ = source
    target = root / "new.txt"
    if mutation == "symlink":
        target.symlink_to(root / "original.txt")
    elif mutation == "hardlink":
        os.link(root / "original.txt", target)
    elif mutation == "fifo":
        os.mkfifo(target)
    elif mutation == "special_mode":
        add(root, "new.txt").chmod(0o4644)
    elif mutation == "linked_parent":
        target.symlink_to(root, target_is_directory=True)
    else:
        add(root, "nested/new.txt")
        git(root / "nested", "init", "-q")
    with pytest.raises((TaskInputError, SkillError, OSError)):
        capture(source)


@pytest.mark.parametrize("name", ["ORIGINAL.txt", "original.txt/child", "folder/A.txt"])
def test_new_path_identity_collisions_fail_closed(source, name):
    root, _ = source
    if name == "original.txt/child":
        (root / "original.txt").unlink()
    if name == "folder/A.txt":
        add(root, "Folder/b.txt")
    add(root, name)
    with pytest.raises((TaskInputError, SkillError)):
        capture(source)


@pytest.mark.parametrize("limit", ["files", "bytes"])
def test_additions_share_existing_capture_limits(source, monkeypatch, limit):
    root, _ = source
    add(root, "new.txt", b"x" * 100)
    monkeypatch.setattr(seal, "MAX_TRACKED_FILES" if limit == "files" else "MAX_TRACKED_BYTES", 2 if limit == "files" else 50)
    with pytest.raises((TaskInputError, SkillError)):
        capture(source)


@pytest.mark.parametrize("limit", ["entries", "depth"])
def test_metadata_walk_is_bounded_even_for_empty_directories(source, monkeypatch, limit):
    root, _ = source
    if limit == "entries":
        monkeypatch.setattr(seal, "MAX_TRACKED_FILES", 2)
        for name in ("one", "two", "three"):
            (root / name).mkdir()
    else:
        (root / "/".join(["directory"] * 65)).mkdir(parents=True)
    with pytest.raises(TaskInputError, match="source_limit"):
        capture(source)


@pytest.mark.parametrize("raw", [b"../outside\0", b"/absolute\0", b"new\\file\0", b"new\nfile\0", b"no-terminator", b"x\0x\0"])
def test_malformed_addition_paths_reject(raw):
    with pytest.raises((TaskInputError, SkillError)):
        seal._new_paths(raw, {})


@pytest.mark.parametrize("mutation", ["content", "mode", "missing", "new_extra"])
def test_new_file_drift_before_ref_publication_fails_closed(request, monkeypatch, mutation):
    c = returned(request.getfixturevalue("prepared_fixture"))
    path = add(c.worktree, "new.txt")
    original = seal._Objects.write
    def changed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if args[0] == "commit-tree":
            if mutation == "content":
                path.write_bytes(b"changed")
            elif mutation == "mode":
                path.chmod(0o755)
            elif mutation == "missing":
                path.unlink()
            else:
                add(c.worktree, "extra.txt")
        return result
    monkeypatch.setattr(seal._Objects, "write", changed)
    with pytest.raises(TaskInputError, match="source_changed"):
        seal.seal_stopped_candidate(c.authority)
    assert git(c.worktree, "rev-parse", "HEAD") == c.head
    assert not (c.authority.root / "seal-result.json").exists()


@pytest.mark.parametrize("name", ["later.txt", ".goodteam/cache"])
def test_completed_new_file_seal_rejects_any_extra_on_replay(request, name):
    c = returned(request.getfixturevalue("prepared_fixture"))
    add(c.worktree, "new.txt")
    sealed = seal.seal_stopped_candidate(c.authority)
    add(c.worktree, name)
    with pytest.raises(TaskInputError):
        seal.seal_stopped_candidate(c.authority)
    assert git(c.worktree, "rev-parse", "HEAD") == sealed["candidate"]["head_sha"]


def test_extra_ignored_file_during_ref_publication_cannot_publish_success(request, monkeypatch):
    c = returned(request.getfixturevalue("prepared_fixture"))
    add(c.worktree, "new.txt")
    original = seal._Objects.write
    def changed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if args[0] == "read-tree":
            add(c.worktree, ".goodteam/cache")
        return result
    monkeypatch.setattr(seal._Objects, "write", changed)
    with pytest.raises(TaskInputError, match="untracked"):
        seal.seal_stopped_candidate(c.authority)
    assert not (c.authority.root / "seal-result.json").exists()
    with pytest.raises(TaskInputError, match="outcome_unknown"):
        seal.seal_stopped_candidate(c.authority)
