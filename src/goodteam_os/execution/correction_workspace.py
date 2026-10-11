"""Materialize exact tracked Git bytes without checkout hooks or filters."""
from __future__ import annotations

from goodteam_os.skills.filesystem import directory, write_new
from goodteam_os.skills.models import resource_path
from goodteam_os.skills.review_sources import _Git

from .candidate_probe import MAX_TRACKED_BYTES, MAX_TRACKED_FILES, OID
from .task_snapshot import TaskInputError


class CorrectionWorktreeGit:
    """Narrow worktree materializer; no fetch, shell, checkout or cleanup action."""
    def create_worktree(self, *, repo_root, worktree_path, branch_name, base_ref):
        if OID.fullmatch(base_ref) is None:
            raise TaskInputError("validation_head_invalid")
        git = _Git(repo_root)
        entries = git.read("ls-tree", "-r", "-z", base_ref, maximum=2 * 1024 * 1024).split(b"\0")
        if entries[-1] or len(entries) > MAX_TRACKED_FILES + 1:
            raise TaskInputError("validation_source_limit")
        files, total = [], 0
        for entry in entries[:-1]:
            meta, raw_path = entry.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split()
            path = resource_path(raw_path.decode("utf-8"))
            if kind != "blob" or mode not in {"100644", "100755"} or any(p.casefold() == ".git" for p in path.split("/")):
                raise TaskInputError("validation_source_unsupported")
            data = git.read("cat-file", "blob", oid, maximum=MAX_TRACKED_BYTES - total)
            total += len(data)
            files.append((path, data, mode))
        with directory(worktree_path.parent, create=True):
            pass
        if worktree_path.exists() or worktree_path.is_symlink():
            raise TaskInputError("correction_workspace_preparation_unknown")
        git.read("worktree", "add", "--no-checkout", "-b", branch_name, str(worktree_path), base_ref, maximum=8192)
        for path, data, mode in files:
            target = worktree_path / path
            with directory(target.parent, create=True) as fd:
                write_new(fd, target.name, data, mode=0o755 if mode == "100755" else 0o644)
        _Git(worktree_path).read("read-tree", "--reset", base_ref, maximum=8192)
        return {"status": "created", "stdout": "", "stderr": ""}


def worktree_identity(repo_root, worktree_path, job_id):
    """Bind one linked-worktree admin directory and its exclusive branch ref."""
    import os
    from pathlib import Path
    from goodteam_os.memory_storage import read_bytes
    from .task_snapshot import digest

    def absolute(root, value):
        return Path(os.path.abspath(root / value))

    def required(path):
        value = read_bytes(path, maximum=8192)
        if value is None:
            raise TaskInputError("correction_workspace_mismatch")
        return value

    original = _Git(repo_root)
    common = absolute(repo_root, original.read("rev-parse", "--git-common-dir", maximum=8192).decode().strip())
    native = _Git(worktree_path)
    selected_common = absolute(worktree_path, native.read("rev-parse", "--git-common-dir", maximum=8192).decode().strip())
    admin = absolute(worktree_path, native.read("rev-parse", "--absolute-git-dir", maximum=8192).decode().strip())
    with directory(common), directory(admin):
        pass
    gitfile = required(worktree_path / ".git")
    backlink = required(admin / "gitdir")
    common_file = required(admin / "commondir")
    expected_ref = "refs/heads/sandbox/" + job_id
    # A matching common directory alone permits borrowing the original or a
    # sibling worktree's HEAD/index. Both backlinks and the branch must agree.
    if (selected_common != common or admin.parent != common / "worktrees"
            or gitfile != ("gitdir: " + str(admin) + "\n").encode()
            or backlink != (str(worktree_path / ".git") + "\n").encode()
            or absolute(admin, common_file.decode().strip()) != common
            or required(admin / "HEAD") != ("ref: " + expected_ref + "\n").encode()
            or native.read("symbolic-ref", "HEAD", maximum=8192).decode().strip() != expected_ref):
        raise TaskInputError("correction_workspace_mismatch")
    refs = native.read("for-each-ref", "--format=%(refname)%00%(symref)", expected_ref, maximum=8192).decode().splitlines()
    if [line for line in refs if line.split("\0")[0] == expected_ref] != [expected_ref + "\0"]:
        raise TaskInputError("correction_workspace_mismatch")
    return {"common_git_dir": str(common), "admin_git_dir": str(admin), "branch_ref": expected_ref,
            "gitfile_digest": digest(gitfile), "backlink_digest": digest(backlink), "common_file_digest": digest(common_file)}
