"""Host-only sealing of a stopped correction's bounded regular-file edits.

No model/runtime is invoked here. Explicit native continuation requires the
complete protected return chain. Objects and the dedicated attempt
ref/index are the only Git writes; the original checkout/index remain untouched.
"""
import hashlib
import os
import selectors
import stat
import subprocess
import tempfile
import time

from goodteam_os.memory_storage import read_bytes
from goodteam_os._repository_content_policy import reject_sensitive_path
from goodteam_os.repository_access import RepositoryAccessError
from goodteam_os.skills.filesystem import directory, regular_file
from goodteam_os.skills.models import resource_path
from goodteam_os.skills.review_sources import _Git
from goodteam_os.skills.review_process import _terminate

from .candidate_probe import MAX_TRACKED_BYTES, MAX_TRACKED_FILES, OID, probe_candidate
from .candidate_validation import _bound, _read, _write
from .correction_codex import admit_output
from .correction_loop import CorrectionLoopService
from .task_snapshot import TaskInputError, canonical_bytes, digest


def _worktree_leaves(root):
    """Git omits FIFOs and can collapse nested repositories. Verify nodes too.

    Inspect metadata only, anchored to no-follow directory descriptors. Ignore
    just the root worktree Git pointer, whose identity the attempt store checks.
    Empty directories are not candidate artifacts, but still consume the bound.
    """
    leaves, count = set(), 0
    def walk(fd, prefix, depth):
        nonlocal count
        if depth > 64:
            raise TaskInputError("correction_seal_source_limit")
        with os.scandir(fd) as entries:
            for entry in entries:
                if not prefix and entry.name == ".git":
                    continue
                count += 1
                if count > MAX_TRACKED_FILES * 2:
                    raise TaskInputError("correction_seal_source_limit")
                path = resource_path(prefix + entry.name)
                if entry.name.casefold() == ".git":
                    raise TaskInputError("correction_seal_source_unsupported")
                info = entry.stat(follow_symlinks=False)
                if info.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                    raise TaskInputError("correction_seal_source_unsupported")
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        current = os.fstat(child)
                        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                            raise TaskInputError("correction_seal_source_changed")
                        walk(child, path + "/", depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    leaves.add(path)
                else:
                    raise TaskInputError("correction_seal_source_unsupported")
    with directory(root) as fd:
        walk(fd, "", 0)
    return leaves


class _Objects(_Git):
    def write(self, *args, data=b""):
        """Fixed plumbing commands only, raw stdin, no inherited identity/secrets."""
        env = {"PATH": os.defpath, "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
               "GIT_ALLOW_PROTOCOL": "", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
               "GIT_AUTHOR_NAME": "GoodTeam correction host", "GIT_AUTHOR_EMAIL": "host@goodteam.invalid",
               "GIT_COMMITTER_NAME": "GoodTeam correction host", "GIT_COMMITTER_EMAIL": "host@goodteam.invalid"}
        command = [self.binary, "--no-pager", "-c", "core.hooksPath=" + os.devnull,
                   "-c", "core.fsmonitor=false", "-c", "protocol.allow=never", "-c", "commit.gpgsign=false", *args]
        output, errors = bytearray(), 0
        # A host-owned temporary descriptor carries raw bytes, never a model
        # path, shell argument, filter, or inherited descriptor.
        with tempfile.TemporaryFile() as source:
            source.write(data)
            source.seek(0)
            process = subprocess.Popen(command, cwd=self.root, env=env, stdin=source,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
            try:
                with selectors.DefaultSelector() as selector:
                    for stream in (process.stdout, process.stderr):
                        os.set_blocking(stream.fileno(), False)
                        selector.register(stream, selectors.EVENT_READ)
                    while selector.get_map() or process.poll() is None:
                        if time.monotonic() >= self.deadline:
                            raise TaskInputError("correction_seal_git_timeout")
                        for key, _ in selector.select(timeout=0.05):
                            chunk = os.read(key.fd, 8192)
                            if not chunk:
                                selector.unregister(key.fileobj)
                            elif key.fileobj is process.stdout:
                                output.extend(chunk)
                            else:
                                errors += len(chunk)
                            if len(output) > 8192 or errors > 65536:
                                raise TaskInputError("correction_seal_git_failed")
                if process.returncode:
                    raise TaskInputError("correction_seal_git_failed")
                return bytes(output)
            finally:
                try:
                    _terminate(process)
                finally:
                    process.stdout.close()
                    process.stderr.close()


def _new_paths(raw, tracked):
    entries = raw.split(b"\0")
    if entries[-1] or len(entries) - 1 + len(tracked) > MAX_TRACKED_FILES:
        raise TaskInputError("correction_seal_source_limit")
    paths = [resource_path(p.decode("utf-8")) for p in entries[:-1]]
    if len(set(paths)) != len(paths):
        raise TaskInputError("correction_seal_source_unsupported")
    for path in paths:
        parts = path.casefold().split("/")
        if any(p.startswith(".") or p in {"auth.json", "credentials", "secrets"} for p in parts):
            raise TaskInputError("correction_seal_new_path_rejected")
        try:
            reject_sensitive_path(path.casefold())
        except RepositoryAccessError as exc:
            raise TaskInputError("correction_seal_new_path_rejected") from exc
    # Reject portable-name collisions and file/directory replacements, including
    # deleted tracked leaves. An addition cannot change another path's identity.
    prefixes, leaves = {}, {p.casefold() for p in [*tracked, *paths]}
    for path in [*tracked, *paths]:
        parts = path.split("/")
        for length in range(1, len(parts) + 1):
            prefix = "/".join(parts[:length])
            folded = prefix.casefold()
            if (prefixes.setdefault(folded, prefix) != prefix
                    or (length < len(parts) and folded in leaves)):
                raise TaskInputError("correction_seal_new_path_rejected")
    return paths


def _capture(git, head, *, allow_new=False):
    if git.read("rev-parse", "HEAD", maximum=128).decode().strip() != head:
        raise TaskInputError("correction_seal_head_changed")
    leaves = _worktree_leaves(git.root)
    # Replay is always strict, even for ignored extras. Only the initial seal
    # and its pre-publication recapture may admit additions.
    untracked = git.read("ls-files", "--others", "-z", maximum=65536)
    if untracked and not allow_new:
        raise TaskInputError("correction_seal_untracked")
    entries = git.read("ls-tree", "-r", "-z", head, maximum=2 * 1024 * 1024).split(b"\0")
    if entries[-1] or len(entries) > MAX_TRACKED_FILES + 1:
        raise TaskInputError("correction_seal_source_limit")
    expected_index, files, tracked, total = [], {}, {}, 0
    for entry in entries[:-1]:
        meta, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split()
        path = resource_path(raw_path.decode("utf-8"))
        if kind != "blob" or mode not in {"100644", "100755"} or any(p.casefold() == ".git" for p in path.split("/")):
            raise TaskInputError("correction_seal_source_unsupported")
        expected_index.append(f"{mode} {oid} 0\t".encode() + raw_path + b"\0")
        tracked[path] = (mode, oid)
        target = git.root / path
        # Missing tracked leaves are deletions; linked/missing parents fail closed.
        with directory(target.parent) as fd:
            try:
                data, info = regular_file(fd, target.name, maximum=MAX_TRACKED_BYTES - total)
            except FileNotFoundError:
                continue
        total += len(data)
        files[path] = ("100755" if info.st_mode & 0o111 else "100644", data)
    if git.read("ls-files", "--stage", "-z", maximum=2 * 1024 * 1024) != b"".join(expected_index):
        raise TaskInputError("correction_seal_index_changed")
    if leaves != set(files) | {p.decode("utf-8") for p in untracked.split(b"\0") if p}:
        raise TaskInputError("correction_seal_source_changed")
    if untracked:
        additions = _new_paths(untracked, tracked)
        # A worker may not declassify ignored files by changing ignore rules.
        # Validate raw, anchored bytes before asking Git to consult these files.
        for path, (mode, oid) in tracked.items():
            if path.split("/")[-1].casefold() == ".gitignore":
                current_mode, data = files.get(path, (None, b""))
                hash_fn = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
                actual = hash_fn(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                if current_mode != mode or actual != oid:
                    raise TaskInputError("correction_seal_ignore_changed")
        if git.read("ls-files", "--others", "--exclude-standard", "-z", maximum=65536) != untracked:
            raise TaskInputError("correction_seal_ignored")
        for path in additions:
            target = git.root / path
            with directory(target.parent) as fd:
                data, info = regular_file(fd, target.name, maximum=MAX_TRACKED_BYTES - total)
            total += len(data)
            files[path] = ("100755" if info.st_mode & 0o111 else "100644", data)
    return files


def _manifest(files):
    return digest(canonical_bytes({p: {"mode": mode, "digest": digest(data)} for p, (mode, data) in files.items()}))


def _tree(git, files):
    root = {}
    for path, (mode, data) in files.items():
        parent = root
        parts = path.split("/")
        for part in parts[:-1]:
            parent = parent.setdefault(part, {})
        oid = git.write("hash-object", "-w", "--stdin", "--no-filters", data=data).decode().strip()
        if OID.fullmatch(oid) is None:
            raise TaskInputError("correction_seal_git_failed")
        parent[parts[-1]] = (mode, oid)
    def write_tree(entries):
        rows = []
        for name, item in sorted(entries.items()):
            mode, oid = ("040000", write_tree(item)) if isinstance(item, dict) else item
            rows.append(f"{mode} {'tree' if mode == '040000' else 'blob'} {oid}\t{name}".encode() + b"\0")
        oid = git.write("mktree", "-z", data=b"".join(rows)).decode().strip()
        if OID.fullmatch(oid) is None:
            raise TaskInputError("correction_seal_git_failed")
        return oid
    return write_tree(root)


def seal_stopped_candidate(authority):
    """Require the complete protected return chain, not a success flag/output claim.

    For the existing one-shot Codex correction path, run-result is published
    only after _run_cli_worker confirms process-group cleanup and run_job records
    its host return. This is a host-path guarantee, not provider attestation or
    confinement against unrestricted same-UID writers. Native continuation and
    retained candidate validation enforce this artifact before recheck/review.
    """
    with authority.lock():
        value = authority.load()
        if value.original.to_dict()["adapter"] != "codex":
            raise TaskInputError("correction_seal_adapter_unsupported")
        started, outcome = authority.outcome(value)
        dispatch = _read(authority.root / "dispatch.json")
        if (started != {"input": value.reference} or outcome is None or outcome["status"] != "completed"
                or dispatch is None or dispatch.get("stage") != "transport_attempt"
                or outcome["host_return"]["dispatch"] != dispatch):
            raise TaskInputError("correction_seal_return_unverified")
        root = authority.workspace(value)
        job_root = authority.check_job_projection(value)
        raw = read_bytes(job_root / "result.md", maximum=1024 * 1024)
        if raw is None or digest(raw) != outcome["host_return"]["output_digest"]:
            raise TaskInputError("correction_seal_output_changed")
        output = admit_output(raw, value)
        binding = {"input": value.reference, "host_return_receipt": outcome["host_return"]["receipt_id"],
                   "parent_sha": value.to_dict()["candidate"]["head_sha"], "output_digest": digest(raw)}
        previous = _read(authority.root / "seal-result.json")
        intent = _read(authority.root / "seal-started.json")
        if previous is not None:
            if (intent is None or previous != _bound(previous) or previous.get("binding") != binding
                    or previous.get("intent") != intent):
                raise TaskInputError("correction_seal_outcome_unknown")
            if probe_candidate(root, previous["candidate"]["head_sha"]) != previous["candidate"]:
                raise TaskInputError("correction_seal_outcome_unknown")
            if _manifest(_capture(_Objects(root), previous["candidate"]["head_sha"])) != intent.get("files_digest"):
                raise TaskInputError("correction_seal_outcome_unknown")
            return previous
        if intent is not None:
            raise TaskInputError("correction_seal_outcome_unknown")
        if CorrectionLoopService(authority.project_root).admit_worker_dispatch(value) <= 0:
            raise TaskInputError("correction_time_budget_exhausted")
        git, head = _Objects(root), binding["parent_sha"]
        files = _capture(git, head, allow_new=True)
        manifest = _manifest(files)
        intent = _bound({"binding": binding, "files_digest": manifest})
        # Fail closed on interruption from the first shared-object write onward.
        _write(authority.root / "seal-started.json", intent)
        tree = _tree(git, files)
        if tree == value.to_dict()["candidate"]["tree_sha"]:
            raise TaskInputError("correction_no_progress")
        commit = git.write("commit-tree", tree, "-p", head,
                           data=("GoodTeam correction " + value.job_id + "\n").encode()).decode().strip()
        if OID.fullmatch(commit) is None:
            raise TaskInputError("correction_seal_git_failed")
        authority.workspace(value)
        if _manifest(_capture(git, head, allow_new=True)) != manifest:
            raise TaskInputError("correction_seal_source_changed")
        if CorrectionLoopService(authority.project_root).admit_worker_dispatch(value) <= 0:
            raise TaskInputError("correction_time_budget_exhausted")
        # No symbolic-ref following; compare-and-swap only this dedicated ref.
        git.write("update-ref", "--no-deref", "refs/heads/sandbox/" + value.job_id, commit, head)
        git.write("read-tree", "--reset", commit)
        candidate = probe_candidate(root, commit)
        if _manifest(_capture(git, commit)) != manifest:
            raise TaskInputError("correction_seal_source_changed")
        result = _bound({"binding": binding, "intent": intent, "candidate": candidate,
                         "output": output, "acceptance_status": "unknown"})
        _write(authority.root / "seal-result.json", result)
        return result
