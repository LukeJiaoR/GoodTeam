"""Resolve an explicitly selected original Builder's retained Git worktree.

The caller supplies a job identity, never a path. Mutable sandbox metadata is
only corroboration: the path, branch, original protected job and Git registration
must agree. A frozen observation detects replacement between calls, not arbitrary
same-UID rollback or concurrent edit-and-revert.
"""
from __future__ import annotations

import os
from pathlib import Path

from goodteam_os.skills.filesystem import directory, regular_file
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.review_sources import _Git

from .task_snapshot import TaskInputError, canonical_bytes, digest


def builder_source(project_root, snapshot, worker):
    from goodteam_os.workers import WorkerJobStore
    from .ledger import ExecutionLedger
    from .models import ExecutionRunStatus
    from .orchestrator import _require_snapshot_run
    from .sandbox import SandboxService

    if worker != snapshot.job_id:
        raise TaskInputError("validation_builder_mismatch")
    try:
        admitted = WorkerJobStore(project_root).admit_task_input(worker)
        if admitted is None or admitted.data != snapshot.data:
            raise TaskInputError("validation_builder_mismatch")
        run = ExecutionLedger(project_root).read_run(snapshot.execution_id)
        if run is None or run.status is not ExecutionRunStatus.SUCCEEDED:
            raise TaskInputError("validation_builder_incomplete")
        _require_snapshot_run(snapshot, run, snapshot.execution_id)
        root = project_root / ".goodteam" / "sandboxes" / snapshot.execution_id / "worktree"
        branch = "refs/heads/sandbox/" + snapshot.execution_id
        metadata = SandboxService(project_root).get_sandbox(snapshot.execution_id)
        expected = {"execution_id": snapshot.execution_id, "mode": "worktree",
                    "status": "retained_for_pr", "worktree_path": str(root),
                    "workspace_path": str(root), "branch_name": branch.removeprefix("refs/heads/")}
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise TaskInputError("validation_builder_workspace_mismatch")
        # Anchored directory walks reject symlinks in every component.
        with directory(root) as fd:
            info = os.fstat(fd)
            root_identity = [info.st_dev, info.st_ino]
        project_git, worker_git = _Git(project_root), _Git(root)
        common = Path(project_git.read("rev-parse", "--path-format=absolute", "--git-common-dir", maximum=8192).decode().strip())
        worker_common = Path(worker_git.read("rev-parse", "--path-format=absolute", "--git-common-dir", maximum=8192).decode().strip())
        gitdir = Path(worker_git.read("rev-parse", "--absolute-git-dir", maximum=8192).decode().strip())
        if (worker_common != common or gitdir.parent != common / "worktrees"
                or worker_git.read("symbolic-ref", "HEAD", maximum=8192).decode().strip() != branch):
            raise TaskInputError("validation_builder_workspace_mismatch")
        with directory(gitdir) as fd:
            info = os.fstat(fd)
            git_identity = [info.st_dev, info.st_ino]
            backlink, _ = regular_file(fd, "gitdir", maximum=8192)
        if backlink.decode().strip() != str(root / ".git"):
            raise TaskInputError("validation_builder_workspace_mismatch")
        binding = {"schema_version": "candidate_builder_source.v1", "worker": worker,
                   "snapshot": snapshot.reference, "branch": branch,
                   "workspace_identity": root_identity, "git_identity": git_identity,
                   "registration_digest": digest(canonical_bytes({"common": str(common), "gitdir": str(gitdir),
                       "created_at": metadata.get("created_at"), "sandbox_id": metadata.get("sandbox_id")}))}
        return root, binding
    except TaskInputError:
        raise
    except (SkillError, OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise TaskInputError("validation_builder_unavailable") from exc
