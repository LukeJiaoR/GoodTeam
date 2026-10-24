"""One retained raw Git baseline and execution directory per workflow task.

Sequential workflow Workers share the task directory, while Worker metadata
remains in the source store. Only an explicit new-task start may prepare it;
every later lookup reopens the original baseline without sampling source files.
"""
from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from goodteam_os.dispatch_workspace import (
    DispatchGitOutputLimitError,
    _read_git,
    dispatch_artifact_root,
    dispatch_workspace_lock,
    prepare_dispatch_workspace,
)
from goodteam_os.repository_snapshot import _absolute_root, _canonical_json
from goodteam_os.repository_access import canonical_relative_path
from goodteam_os.state import normalize_project_key


SCHEMA = "goodteam.workflow-workspace.v1"
SLOT = "workflow-task"


class WorkflowWorkspaceError(ValueError):
    """The original workflow execution directory is missing or changed."""


def _workspace_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except WorkflowWorkspaceError:
            raise
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise WorkflowWorkspaceError(f"Workflow baseline is unavailable or changed: {exc}") from exc
    return wrapped


def adapter_requires_workflow_workspace(adapter: str) -> bool:
    """Builtin simulations/manual delivery do not enter a Git coding Runtime."""
    return str(adapter) not in {"fake", "noop", "human", "manual"}


def _scope(source_root: Path, project_key: str, run_id: str) -> tuple[Path, str]:
    source = _absolute_root(source_root)
    if (not isinstance(project_key, str) or normalize_project_key(project_key) != project_key
            or not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 512):
        raise WorkflowWorkspaceError("Workflow baseline requires its original project and run identity.")
    digest = hashlib.sha256(_canonical_json({"project_key": project_key, "run_id": run_id})).hexdigest()
    return source, "workflow-" + digest


def _binding(workspace, *, project_key: str, run_id: str) -> dict:
    metadata = workspace.metadata
    return {
        "schema": SCHEMA, "project_key": project_key, "run_id": run_id,
        "source_root": metadata["project_root"], "execution_id": metadata["run_id"], "slot": SLOT,
        "baseline_commit": metadata["baseline_commit"], "original_head": metadata["original_head"],
        "original_index_sha256": metadata["original_index_sha256"],
        "source_root_identity": metadata["source_root_identity"],
        "workspace_root_identity": metadata["jobs"][SLOT]["root_identity"],
        "workspace_git_identity": metadata["jobs"][SLOT]["git_identity"],
        "workspace_path": str(workspace.workspace(SLOT)),
    }


@_workspace_errors
def prepare_workflow_workspace(source_root: Path, *, project_key: str, run_id: str) -> dict:
    """Capture at an authorized new-task start, before its first live Worker."""
    source, execution_id = _scope(source_root, project_key, run_id)
    with dispatch_workspace_lock(source, execution_id):
        workspace = prepare_dispatch_workspace(source, execution_id, [SLOT])
        return _binding(workspace, project_key=project_key, run_id=run_id)


@_workspace_errors
def _reopen(source_root: Path, binding: object, *, project_key: str, run_id: str):
    source, execution_id = _scope(source_root, project_key, run_id)
    if not isinstance(binding, dict) or not binding or binding.get("schema") != SCHEMA:
        raise WorkflowWorkspaceError("Workflow baseline is missing. Start a new task; an existing run cannot recapture source files.")
    if (binding.get("source_root") != str(source) or binding.get("execution_id") != execution_id
            or binding.get("project_key") != project_key or binding.get("run_id") != run_id):
        raise WorkflowWorkspaceError("Workflow baseline belongs to a different source, project or run.")
    # Do not create directories or a replacement baseline during recovery.
    if not dispatch_artifact_root(source, execution_id).is_dir():
        raise WorkflowWorkspaceError("Retained workflow baseline is unavailable; source recapture is forbidden.")
    workspace = prepare_dispatch_workspace(source, execution_id, None)
    if binding != _binding(workspace, project_key=project_key, run_id=run_id):
        raise WorkflowWorkspaceError("Workflow baseline binding or workspace identity changed.")
    return workspace


def workspace_for_workflow(source_root: Path, binding: object, *, project_key: str, run_id: str) -> Path:
    """Read-only lookup: never create a baseline for an already-started run."""
    return _reopen(source_root, binding, project_key=project_key, run_id=run_id).workspace(SLOT)


def context_for_job(source_root: Path, binding: object, *, project_key: str, run_id: str, job_id: str) -> dict:
    workspace = _reopen(source_root, binding, project_key=project_key, run_id=run_id)
    if not isinstance(job_id, str) or not job_id or Path(job_id).name != job_id or job_id in {".", ".."}:
        raise WorkflowWorkspaceError("Workflow execution requires a bounded Worker job identity.")
    return {
        "available": True, "schema_version": SCHEMA, "mode": "workflow_isolated",
        "execution_id": workspace.metadata["run_id"], "sandbox_id": SLOT,
        "workspace_path": str(workspace.workspace(SLOT)), "worktree_path": str(workspace.workspace(SLOT)),
        "branch_name": "", "claimed_paths": [],
        "workflow_run_id": run_id, "project_key": project_key, "job_id": job_id,
        "source_root": workspace.metadata["project_root"],
        "baseline_commit": workspace.metadata["baseline_commit"],
    }


def validate_workflow_job_context(source_root: Path, job: dict) -> Path:
    expected = context_for_job(source_root, job.get("workflow_workspace"),
                               project_key=job.get("project_key", ""), run_id=job.get("run_id", ""),
                               job_id=job.get("id", ""))
    if job.get("sandbox_context") != expected:
        raise WorkflowWorkspaceError("Workflow Worker context no longer matches its original task and job.")
    return Path(expected["workspace_path"])


@contextmanager
def workflow_job_lock(source_root: Path, job: dict):
    """Serialize every actual Worker using this shared sequential task slot."""
    validate_workflow_job_context(source_root, job)
    source, execution_id = _scope(source_root, job["project_key"], job["run_id"])
    with dispatch_workspace_lock(source, execution_id):
        validate_workflow_job_context(source, job)
        yield


@_workspace_errors
def collect_workflow_workspace(source_root: Path, binding: object, *, project_key: str,
                               run_id: str, candidate: bool = False, include_source_preview: bool = False) -> dict:
    """Retain cumulative task changes; a candidate never writes to the source."""
    source, execution_id = _scope(source_root, project_key, run_id)
    # Verify first so a missing recovery lookup cannot create an artifact root.
    _reopen(source, binding, project_key=project_key, run_id=run_id)
    with dispatch_workspace_lock(source, execution_id):
        workspace = _reopen(source, binding, project_key=project_key, run_id=run_id)
        result = workspace.collect(SLOT, include_source_preview=True, source_preview_scope=(project_key, run_id)) if include_source_preview else workspace.collect(SLOT)
        result.update(schema=SCHEMA, project_key=project_key, run_id=run_id,
                      original_head=workspace.metadata["original_head"],
                      attribution_scope="workflow_task", exclusion_policy=workspace.metadata["exclusion_policy"])
        if candidate:
            result["integration"] = workspace.integrate([SLOT], expected_results={SLOT: result["result_commit"]})
        return result


@_workspace_errors
def read_workflow_delivery(source_root: Path, binding: object, result: dict, *,
                           project_key: str, run_id: str, max_bytes: int, max_files: int) -> list[dict]:
    """Read only a host-collected result, never paths proposed in Worker prose."""
    source, execution_id = _scope(source_root, project_key, run_id)
    _reopen(source, binding, project_key=project_key, run_id=run_id)
    with dispatch_workspace_lock(source, execution_id):
        workspace = _reopen(source, binding, project_key=project_key, run_id=run_id)
        root = workspace._verify_job(SLOT)
        baseline, commit = workspace.metadata["baseline_commit"], result.get("result_commit", "")
        if (result.get("schema") != SCHEMA or result.get("project_key") != project_key
                or result.get("run_id") != run_id or result.get("job_id") != SLOT
                or result.get("workspace_path") != str(root) or result.get("baseline_commit") != baseline
                or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit)):
            raise WorkflowWorkspaceError("Delivery requires the original host-collected task result.")
        parents = _read_git(root, ["rev-list", "--parents", "-n", "1", commit]).decode("ascii").split()
        if parents != [commit, baseline]:
            raise WorkflowWorkspaceError("Delivery result is not a direct child of its task baseline.")
        paths = [canonical_relative_path(os.fsdecode(path)) for path in
                 _read_git(root, ["diff", "--name-only", "--no-renames", "-z", baseline, commit]).split(b"\0") if path]
        if paths != result.get("changed_files"):
            raise WorkflowWorkspaceError("Delivery paths no longer match the collected result.")
        patch_item = {"kind": "patch", "relative_path": "changes.patch", "aliases": ["changes.patch", "./changes.patch"]}
        try:
            patch_item["data"] = _read_git(root, ["diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv",
                                          "--no-renames", baseline, commit, "--"], max_output_bytes=max_bytes)
        except DispatchGitOutputLimitError:
            # Optional preview only: binding, identity and Git failures still fail closed.
            # Never retain a truncated diff as the exact patch.
            patch_item["unavailable_reason"] = "preview_limit"
        items = [patch_item]
        entries = {}
        for row in _read_git(root, ["ls-tree", "-r", "-z", commit]).split(b"\0"):
            if row:
                metadata, path = row.split(b"\t", 1)
                entries[os.fsdecode(path)] = metadata.split()
        for path in paths[:max_files]:
            item = {"kind": "source", "relative_path": path,
                    "aliases": [path, "./" + path, str(root / path), str(source / path)]}
            entry = entries.get(path)
            if entry is None:
                item["unavailable_reason"] = "deleted"
            elif entry[0] not in {b"100644", b"100755"} or entry[1] != b"blob":
                item["unavailable_reason"] = "not_regular_file"
            else:
                blob = entry[2].decode("ascii")
                item["blob_id"] = blob
                if int(_read_git(root, ["cat-file", "-s", blob])) > max_bytes:
                    item["unavailable_reason"] = "preview_limit"
                else:
                    item["data"] = _read_git(root, ["cat-file", "blob", blob])
            items.append(item)
        if len(paths) > max_files:
            items.append({"kind": "source", "relative_path": "", "unavailable_reason": "file_count_limit"})
        return items
