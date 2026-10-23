"""Retained, isolated Git workspaces for direct dispatch.

Callers hold ``dispatch_workspace_lock`` across preparation, execution and
integration. The source checkout is only inspected; raw file bytes, its original
index and HEAD are captured before any worker starts. Unsupported captures fail
closed instead of producing an incomplete baseline.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from goodteam_os.memory_storage import read_bytes, role_lock
from goodteam_os.repository_access import AnchoredRepository, RepositoryAccessError, canonical_relative_path
from goodteam_os.repository_snapshot import (
    SnapshotLimits,
    _absolute_root,
    _canonical_json,
    _expected_root_identity,
    _open_verified_executable,
    _parse_index_records,
    _trusted_git_executable,
)
from goodteam_os.skills.filesystem import directory


class DispatchWorkspaceError(ValueError):
    """A workspace could not be completely and safely captured or integrated."""


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", value):
        raise DispatchWorkspaceError("Invalid dispatch run or job identity.")
    return value


def dispatch_artifact_root(project_root: Path, run_id: str) -> Path:
    from goodteam_os.service import service_state_dir

    root = _absolute_root(project_root)
    key = hashlib.sha256(os.fsencode(root)).hexdigest()[:24]
    return service_state_dir().expanduser().resolve() / "dispatch-workspaces" / key / _identifier(run_id)


@contextmanager
def dispatch_workspace_lock(project_root: Path, run_id: str, *, artifact_root: Path | None = None):
    root = _artifact_root(project_root, run_id, artifact_root)
    with role_lock(root):
        yield root


def _artifact_root(project_root: Path, run_id: str, artifact_root: Path | None) -> Path:
    root = Path(artifact_root).absolute() if artifact_root is not None else dispatch_artifact_root(project_root, run_id)
    source = _absolute_root(project_root)
    if ".." in root.parts or root == source or source in root.parents:
        raise DispatchWorkspaceError("Dispatch artifacts must be outside the source checkout.")
    # Reject symlink traversal before using an existing artifact directory.
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    before = ancestor.lstat()
    with directory(ancestor) as descriptor:
        # Other jobs publish snapshots and temporary indexes in this directory.
        # Its contents may change while its identity and permissions stay fixed.
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid")
        for current in (os.fstat(descriptor), ancestor.lstat()):
            if (not stat.S_ISDIR(current.st_mode)
                    or any(getattr(before, field) != getattr(current, field) for field in fields)):
                raise DispatchWorkspaceError("Dispatch artifact directory identity or permissions changed.")
    return root


def _git(root: Path, arguments: list[str], *, data: bytes | None = None,
         index: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    executable, trusted_path, identity = _trusted_git_executable(root, arguments[0])
    environment = {
        "PATH": trusted_path, "HOME": os.environ.get("HOME", ""),
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "LC_ALL": "C",
        "GIT_AUTHOR_NAME": "GoodTeam", "GIT_AUTHOR_EMAIL": "dispatch@goodteam.invalid",
        "GIT_COMMITTER_NAME": "GoodTeam", "GIT_COMMITTER_EMAIL": "dispatch@goodteam.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
    }
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    command = [executable, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
               "-c", "commit.gpgSign=false", "-c", "core.autocrlf=false", "-c", "protocol.allow=never",
               "-c", "protocol.file.allow=always", "-c", "submodule.recurse=false", "--no-pager", *arguments]
    descriptor = _open_verified_executable(executable, identity, arguments[0])
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                result = subprocess.run(command, cwd=root, env=environment, input=data,
                                        stdout=stdout, stderr=stderr, timeout=60, check=False)
            except subprocess.TimeoutExpired as exc:
                raise DispatchWorkspaceError("Dispatch Git operation timed out.") from exc
            if stdout.tell() + stderr.tell() > 32 * 1024 * 1024:
                raise DispatchWorkspaceError("Dispatch Git operation exceeded its output limit.")
            stdout.seek(0)
            stderr.seek(0)
            result.stdout, result.stderr = stdout.read(), stderr.read()
    finally:
        os.close(descriptor)
    if check and result.returncode:
        raise DispatchWorkspaceError(f"Dispatch Git {arguments[0]} failed: {os.fsdecode(result.stderr)[:1000]}")
    return result


def _read_git(root: Path, arguments: list[str]) -> bytes:
    return _git(root, arguments).stdout


def _metadata_path(path: str) -> bool:
    return ".goodteam" in Path(path).parts or ".git" in Path(path).parts


def _read_file(repository: AnchoredRepository, path: str, limits: SnapshotLimits):
    try:
        parent = repository._open_directory(str(Path(path).parent) if "/" in path else "")
    except RepositoryAccessError as exc:
        if exc.code == "context_path_rejected" and str(exc).startswith("Repository path does not exist:"):
            raise FileNotFoundError(path) from exc
        raise
    try:
        before = os.stat(Path(path).name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise DispatchWorkspaceError(f"Unsupported nonregular dispatch file: {path}")
        result = repository.read_bytes(path, max_bytes=limits.max_file_bytes)
        after = os.stat(Path(path).name, dir_fd=parent, follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in fields) or result.truncated:
            raise DispatchWorkspaceError(f"Dispatch file changed or exceeded its capture limit: {path}")
        return result.data, "100755" if before.st_mode & 0o111 else "100644"
    finally:
        os.close(parent)


def _capture(root: Path, limits: SnapshotLimits, *, expected_identity=None, extra_paths=(), original=False):
    limits.validate()
    with AnchoredRepository(root, expected_identity=expected_identity) as repository:
        if os.fsdecode(_read_git(root, ["rev-parse", "--show-toplevel"]).strip()) != str(root):
            raise DispatchWorkspaceError("Dispatch source must be the Git worktree root.")
        stage = _read_git(root, ["ls-files", "--stage", "-z"])
        records = _parse_index_records(stage)
        for record in records:
            if record["stage"] != 0 or record["mode"] not in {"100644", "100755"}:
                raise DispatchWorkspaceError("Dispatch requires a merged index containing only regular files.")
        paths = {str(record["path"]) for record in records} | set(extra_paths)
        paths.update(canonical_relative_path(os.fsdecode(path)) for path in
                     _read_git(root, ["ls-files", "--others", "--exclude-standard", "-z"]).split(b"\0") if path)
        source = {}
        if original:
            head = os.fsdecode(_read_git(root, ["rev-parse", "--verify", "HEAD^{commit}"]).strip())
            if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
                raise DispatchWorkspaceError("Dispatch requires an exact Git commit HEAD.")
            if _read_git(root, ["rev-parse", "--shared-index-path"]).strip():
                raise DispatchWorkspaceError("Split indexes are unsupported for dispatch capture.")
            sparse = _git(root, ["config", "--bool", "core.sparseCheckout"], check=False)
            if sparse.stdout.strip() == b"true":
                raise DispatchWorkspaceError("Sparse worktrees are unsupported for dispatch capture.")
            head_files = _read_git(root, ["ls-tree", "-r", "-z", head])
            for item in head_files.split(b"\0"):
                if not item:
                    continue
                metadata, raw_path = item.split(b"\t", 1)
                if metadata.split(b" ", 1)[0] not in {b"100644", b"100755"}:
                    raise DispatchWorkspaceError("Dispatch requires regular tracked files; submodules and symlinks are unsupported.")
                paths.add(canonical_relative_path(os.fsdecode(raw_path)))
            index_path = Path(os.fsdecode(_read_git(root, ["rev-parse", "--git-path", "index"]).strip()))
            if not index_path.is_absolute():
                index_path = root / index_path
            with AnchoredRepository(index_path.parent) as index_repository:
                captured_index = index_repository.read_bytes(index_path.name, max_bytes=32 * 1024 * 1024)
            if captured_index.truncated:
                raise DispatchWorkspaceError("Dispatch index exceeded its capture limit.")
            source = {"original_head": head, "original_index": captured_index.data}
        files, total = {}, 0
        for path in sorted(paths):
            if _metadata_path(path):
                continue
            try:
                content, mode = _read_file(repository, path, limits)
            except FileNotFoundError:
                continue
            total += len(content)
            if len(files) >= limits.max_files or total > limits.max_total_bytes:
                raise DispatchWorkspaceError("Dispatch capture exceeded its file or total byte limit.")
            files[path] = {"data": content, "mode": mode}
        repository.verify_root()
        return {"files": files, "index_entries": stage, **source}


def _file_manifest(capture):
    return {path: {"mode": value["mode"], "sha256": hashlib.sha256(value["data"]).hexdigest(),
                   "size": len(value["data"])} for path, value in capture["files"].items()}


def _capture_identity(capture):
    return {"files": _file_manifest(capture), "index_entries": hashlib.sha256(capture["index_entries"]).hexdigest(),
            "head": capture.get("original_head"),
            "index": hashlib.sha256(capture.get("original_index", b"")).hexdigest()}


def _overlay(root: Path, capture):
    for path, value in capture["files"].items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value["data"])
        destination.chmod(0o755 if value["mode"] == "100755" else 0o644)


def _raw_commit(root: Path, capture, parent: str, artifact_root: Path) -> str:
    descriptor, filename = tempfile.mkstemp(prefix="raw-index-", dir=artifact_root)
    os.close(descriptor)
    index = Path(filename)
    index.unlink()  # Git requires a nonexistent or valid index, not an empty file.
    try:
        _git(root, ["read-tree", "--empty"], index=index)
        with tempfile.TemporaryDirectory(prefix="raw-blobs-", dir=artifact_root) as blobs:
            inputs = []
            for number, value in enumerate(capture["files"].values()):
                path = Path(blobs) / str(number)
                path.write_bytes(value["data"])
                # Git's --stdin-paths accepts its C/octal path quoting. Quote
                # every byte so even newlines and non-UTF8 artifact paths work.
                inputs.append(b'"' + b"".join(f"\\{byte:03o}".encode() for byte in os.fsencode(path)) + b'"\n')
            oids = _git(root, ["hash-object", "-w", "--no-filters", "--stdin-paths"], data=b"".join(inputs)).stdout.splitlines()
        if len(oids) != len(capture["files"]):
            raise DispatchWorkspaceError("Git did not retain every raw snapshot file.")
        rows = [value["mode"].encode() + b" " + oid + b"\t" + os.fsencode(path) + b"\0"
                for (path, value), oid in zip(capture["files"].items(), oids, strict=True)]
        _git(root, ["update-index", "-z", "--index-info"], data=b"".join(rows), index=index)
        tree = _git(root, ["write-tree"], index=index).stdout.strip().decode("ascii")
        return _git(root, ["commit-tree", tree, "-p", parent, "-m", "GoodTeam raw dispatch snapshot"]).stdout.strip().decode("ascii")
    finally:
        index.unlink(missing_ok=True)


def _clone(source: Path, destination: Path):
    _git(destination.parent, ["clone", "--quiet", "--local", "--no-hardlinks", "--no-checkout", "--", str(source), str(destination)])
    _git(destination, ["remote", "remove", "origin"])


def _git_identity(root: Path):
    value = (root / ".git").lstat()
    if not stat.S_ISDIR(value.st_mode):
        raise DispatchWorkspaceError("Dispatch workspace Git directory was replaced.")
    if os.fsdecode(_read_git(root, ["rev-parse", "--absolute-git-dir"]).strip()) != str(root / ".git"):
        raise DispatchWorkspaceError("Dispatch workspace no longer owns its Git directory.")
    if os.fsdecode(_read_git(root, ["rev-parse", "--show-toplevel"]).strip()) != str(root):
        raise DispatchWorkspaceError("Dispatch workspace no longer owns its worktree.")
    common = Path(os.fsdecode(_read_git(root, ["rev-parse", "--git-common-dir"]).strip()))
    if not common.is_absolute():
        common = root / common
    if common != root / ".git" or (root / ".git/objects/info/alternates").exists():
        raise DispatchWorkspaceError("Dispatch workspace must have independent Git metadata and objects.")
    return {"device": value.st_dev, "inode": value.st_ino}


def _write_json(path: Path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(_canonical_json(value) + b"\n")
    temporary.replace(path)


class DispatchWorkspace:
    def __init__(self, artifact_root: Path, metadata: dict, limits: SnapshotLimits):
        self.artifact_root, self.metadata, self.limits = artifact_root, metadata, limits

    def workspace(self, job_id: str) -> Path:
        if job_id not in self.metadata["jobs"]:
            raise DispatchWorkspaceError("Job does not belong to this dispatch baseline.")
        return self.artifact_root / "jobs" / hashlib.sha256(job_id.encode()).hexdigest()[:24]

    def _verify_job(self, job_id: str) -> Path:
        root = self.workspace(job_id)
        with AnchoredRepository(root, expected_identity=self.metadata["jobs"][job_id]["root_identity"]):
            if _git_identity(root) != self.metadata["jobs"][job_id]["git_identity"]:
                raise DispatchWorkspaceError("Dispatch workspace Git directory identity changed.")
        return root

    def _baseline_capture(self):
        root = self.artifact_root / "baseline"
        with AnchoredRepository(root, expected_identity=self.metadata["baseline_root_identity"]):
            if _git_identity(root) != self.metadata["baseline_git_identity"]:
                raise DispatchWorkspaceError("Retained dispatch baseline Git identity changed.")
            if _read_git(root, ["rev-parse", "HEAD^{commit}"]).strip().decode("ascii") != self.metadata["baseline_commit"]:
                raise DispatchWorkspaceError("Retained dispatch baseline commit changed.")
            captured = _capture(root, self.limits, extra_paths=self.metadata["files"])
        if _file_manifest(captured) != self.metadata["files"]:
            raise DispatchWorkspaceError("Retained dispatch baseline bytes changed.")
        index = read_bytes(self.artifact_root / "original.index", maximum=32 * 1024 * 1024)
        if index is None or hashlib.sha256(index).hexdigest() != self.metadata["original_index_sha256"]:
            raise DispatchWorkspaceError("Retained dispatch original index changed.")
        return captured

    def collect(self, job_id: str, *, include_source_preview: bool = False, source_preview_scope=None) -> dict:
        root = self._verify_job(job_id)
        first = _capture(root, self.limits, extra_paths=self.metadata["files"])
        second = _capture(root, self.limits, extra_paths=self.metadata["files"])
        if _capture_identity(first) != _capture_identity(second):
            raise DispatchWorkspaceError("Worker files changed during result collection.")
        commit = _raw_commit(root, second, self.metadata["baseline_commit"], self.artifact_root)
        _git(root, ["update-ref", "refs/goodteam/dispatch-result", commit])
        changed = _git(root, ["diff", "--name-only", "--no-renames", "-z", self.metadata["baseline_commit"], commit]).stdout
        result = {"job_id": job_id, "baseline_commit": self.metadata["baseline_commit"],
                  "result_commit": commit, "changed_files": [os.fsdecode(path) for path in changed.split(b"\0") if path],
                  "workspace_path": str(root)}
        _write_json(self.artifact_root / f"result-{hashlib.sha256(job_id.encode()).hexdigest()[:24]}.json",
                    {**result, "files": _file_manifest(second)})
        if include_source_preview:
            from goodteam_os.source_preview import build_source_preview

            result["source_preview"] = build_source_preview(capture=second, result=result,
                                                            project_key=source_preview_scope[0], run_id=source_preview_scope[1])
        return result

    def integrate(self, job_ids: list[str], *, expected_results: dict[str, str] | None = None) -> dict:
        if len(set(job_ids)) != len(job_ids):
            raise DispatchWorkspaceError("Integration job identities must be unique.")
        baseline_capture = self._baseline_capture()
        results = [self.collect(job_id) for job_id in job_ids]
        if expected_results is not None and any(
            expected_results.get(result["job_id"]) != result["result_commit"] for result in results
        ):
            raise DispatchWorkspaceError("Dispatch result changed after verification; integration is blocked.")
        candidate = Path(tempfile.mkdtemp(prefix="integration-", dir=self.artifact_root)) / "candidate"
        _clone(self.artifact_root / "baseline", candidate)
        baseline = self.metadata["baseline_commit"]
        # Checkout attributes must not normalize the preserved raw baseline.
        (candidate / ".git/info/attributes").write_text("* -text -filter -ident -eol -working-tree-encoding\n", encoding="utf-8")
        _git(candidate, ["checkout", "--quiet", "--detach", baseline])
        _overlay(candidate, baseline_capture)
        _git(candidate, ["update-index", "--refresh"])
        conflicts = []
        for result in results:
            _git(candidate, ["fetch", "--quiet", "--no-tags", "--", result["workspace_path"], result["result_commit"]])
            merged = _git(candidate, ["merge", "--no-edit", "--no-ff", result["result_commit"]], check=False)
            if merged.returncode:
                conflicts = [os.fsdecode(path) for path in
                             _git(candidate, ["diff", "--name-only", "--diff-filter=U", "-z"]).stdout.split(b"\0") if path]
                if not conflicts:
                    raise DispatchWorkspaceError(f"Dispatch integration failed; retained at {candidate}: {os.fsdecode(merged.stderr)[:500]}")
                break
        changed = _git(candidate, ["diff", "--name-only", "--no-renames", "-z", baseline]).stdout
        result = {"status": "conflicted" if conflicts else "succeeded", "candidate_path": str(candidate),
                  "candidate_commit": None if conflicts else _git(candidate, ["rev-parse", "HEAD"]).stdout.strip().decode("ascii"),
                  "baseline_commit": baseline, "original_head": self.metadata["original_head"],
                  "changed_files": [os.fsdecode(path) for path in changed.split(b"\0") if path], "conflicts": conflicts}
        _write_json(candidate.parent / "integration.json", result)
        return result


def prepare_dispatch_workspace(project_root: Path, run_id: str, job_ids: list[str] | None, *,
                               artifact_root: Path | None = None, limits: SnapshotLimits = SnapshotLimits()) -> DispatchWorkspace:
    """Prepare or reopen one fixed task baseline; caller holds the task lock."""
    project_root = _absolute_root(project_root)
    _identifier(run_id)
    for job_id in job_ids or []:
        _identifier(job_id)
    if job_ids is not None and (len(set(job_ids)) != len(job_ids) or not job_ids):
        raise DispatchWorkspaceError("Dispatch needs unique nonempty job identities.")
    root = _artifact_root(project_root, run_id, artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "baseline.json"
    if manifest.exists():
        metadata = json.loads(read_bytes(manifest, maximum=32 * 1024 * 1024))
        if (metadata.get("schema") != "dispatch_workspace.v1" or metadata.get("run_id") != run_id
                or metadata.get("project_root") != str(project_root)
                or (job_ids is not None and set(metadata.get("jobs", {})) != set(job_ids))):
            raise DispatchWorkspaceError("Dispatch replay does not match the retained task identity.")
        with AnchoredRepository(project_root, expected_identity=metadata["source_root_identity"]):
            pass
        workspace = DispatchWorkspace(root, metadata, limits)
        workspace._baseline_capture()
        for job_id in metadata["jobs"]:
            workspace._verify_job(job_id)
        return workspace
    if job_ids is None:
        raise DispatchWorkspaceError("No retained dispatch baseline exists for this run.")
    if (root / "baseline").exists() or (root / "jobs").exists():
        raise DispatchWorkspaceError(f"Incomplete dispatch preparation retained at {root}; use a new run identity.")
    identity = _expected_root_identity(project_root, None)
    first = _capture(project_root, limits, expected_identity=identity, original=True)
    baseline = root / "baseline"
    _clone(project_root, baseline)
    second = _capture(project_root, limits, expected_identity=identity, original=True)
    if _capture_identity(first) != _capture_identity(second):
        raise DispatchWorkspaceError(f"Source changed during dispatch preparation; incomplete artifacts retained at {root}.")
    _overlay(baseline, second)
    baseline_commit = _raw_commit(baseline, second, second["original_head"], root)
    _git(baseline, ["update-ref", "HEAD", baseline_commit])
    _git(baseline, ["read-tree", baseline_commit])
    (root / "original.index").write_bytes(second["original_index"])
    metadata = {"schema": "dispatch_workspace.v1", "run_id": run_id, "project_root": str(project_root),
                "source_root_identity": identity, "original_head": second["original_head"],
                "original_index_sha256": hashlib.sha256(second["original_index"]).hexdigest(),
                "baseline_commit": baseline_commit, "files": _file_manifest(second), "jobs": {},
                "baseline_root_identity": _expected_root_identity(baseline, None),
                "baseline_git_identity": _git_identity(baseline),
                "exclusion_policy": ["Git ignored untracked files", ".goodteam metadata", "empty directories"]}
    (root / "jobs").mkdir()
    workspace = DispatchWorkspace(root, metadata, limits)
    for job_id in job_ids:
        metadata["jobs"][job_id] = {}
        job_root = workspace.workspace(job_id)
        _clone(baseline, job_root)
        _git(job_root, ["update-ref", "refs/goodteam/dispatch-baseline", baseline_commit])
        _git(job_root, ["update-ref", "HEAD", second["original_head"]])
        _overlay(job_root, second)
        shutil.copyfile(root / "original.index", job_root / ".git/index")
        metadata["jobs"][job_id] = {"root_identity": _expected_root_identity(job_root, None),
                                     "git_identity": _git_identity(job_root)}
    _write_json(manifest, metadata)
    return workspace
