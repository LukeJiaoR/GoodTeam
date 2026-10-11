"""Bounded observation of a clean tracked candidate, not a filesystem sandbox."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from goodteam_os.skills.filesystem import directory, regular_file
from goodteam_os.skills.models import SkillError, resource_path
from goodteam_os.skills.review_sources import _Git

from .task_snapshot import TaskInputError

MAX_TRACKED_BYTES = 32 * 1024 * 1024
MAX_TRACKED_FILES = 10000
OID = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})")


def probe_candidate(root: Path, head: str) -> dict[str, str]:
    """Verify raw tracked bytes too: index assume-unchanged is not evidence.

    Ignored dependencies/caches and the environment are not part of this Git
    artifact. No hook/filter/fetch runs. Unsupported source types fail closed.
    This observes boundaries; it cannot exclude edit-and-revert during a command.
    """
    if not isinstance(head, str) or OID.fullmatch(head) is None:
        raise TaskInputError("validation_head_invalid")
    try:
        git = _Git(root)
        top = git.read("rev-parse", "--show-toplevel", maximum=8192).decode().strip()
        if Path(top).resolve() != root.resolve():
            raise TaskInputError("validation_source_scope")
        if git.read("rev-parse", "HEAD", maximum=128).decode().strip() != head:
            raise TaskInputError("validation_head_changed")
        if git.read("cat-file", "-t", head, maximum=64) != b"commit\n":
            raise TaskInputError("validation_head_invalid")
        # `git status` may invoke repository-configured clean filters. Inspect
        # the raw index and untracked path list instead; never execute a filter.
        if git.read("ls-files", "--others", "--exclude-standard", "-z", maximum=65536):
            raise TaskInputError("validation_worktree_dirty")
        index_entries = git.read("ls-files", "--stage", "-z", maximum=2 * 1024 * 1024).split(b"\0")
        indexed = {}
        for entry in index_entries[:-1]:
            meta, path = entry.split(b"\t", 1)
            mode, oid, stage = meta.decode("ascii").split()
            if stage != "0" or path in indexed:
                raise TaskInputError("validation_worktree_dirty")
            indexed[path] = (mode, oid)
        tree = git.read("rev-parse", head + "^{tree}", maximum=128).decode().strip()
        entries = git.read("ls-tree", "-r", "-z", head, maximum=2 * 1024 * 1024).split(b"\0")
        if entries[-1] or len(entries) > MAX_TRACKED_FILES + 1:
            raise TaskInputError("validation_source_limit")
        total, tracked = 0, {}
        for entry in entries[:-1]:
            meta, raw_path = entry.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split()
            tracked[raw_path] = (mode, oid)
            path = resource_path(raw_path.decode("utf-8"))
            if kind != "blob" or mode not in {"100644", "100755"} or any(p.casefold() == ".git" for p in path.split("/")):
                raise TaskInputError("validation_source_unsupported")
            target = root / path
            with directory(target.parent) as fd:
                data, info = regular_file(fd, target.name, maximum=MAX_TRACKED_BYTES - total)
            total += len(data)
            hash_fn = hashlib.sha1 if len(head) == 40 else hashlib.sha256
            actual = hash_fn(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if actual != oid or bool(info.st_mode & 0o111) != (mode == "100755"):
                raise TaskInputError("validation_worktree_dirty")
        if indexed != tracked:
            raise TaskInputError("validation_worktree_dirty")
        if git.read("rev-parse", "HEAD", maximum=128).decode().strip() != head:
            raise TaskInputError("validation_head_changed")
        return {"head_sha": head, "tree_sha": tree, "source_boundary": "clean_tracked_bytes_observed"}
    except TaskInputError:
        raise
    except (SkillError, OSError, ValueError, UnicodeError) as exc:
        raise TaskInputError("validation_source_unavailable") from exc
