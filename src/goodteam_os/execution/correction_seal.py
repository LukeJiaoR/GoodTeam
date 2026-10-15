"""Dormant host-only sealing of a stopped correction's tracked-file edits.

No production entrypoint calls this module. No model/runtime is invoked. The
existing Codex correction gate remains closed. Objects and the dedicated attempt
ref/index are the only Git writes; the original checkout/index remain untouched.
"""
import os
import selectors
import subprocess
import tempfile
import time

from goodteam_os.memory_storage import read_bytes
from goodteam_os.skills.filesystem import directory, regular_file
from goodteam_os.skills.models import resource_path
from goodteam_os.skills.review_sources import _Git
from goodteam_os.skills.review_process import _terminate

from .candidate_probe import MAX_TRACKED_BYTES, MAX_TRACKED_FILES, OID, probe_candidate
from .candidate_validation import _bound, _read, _write
from .correction_codex import admit_output
from .correction_loop import CorrectionLoopService
from .task_snapshot import TaskInputError, canonical_bytes, digest


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


def _capture(git, head):
    if git.read("rev-parse", "HEAD", maximum=128).decode().strip() != head:
        raise TaskInputError("correction_seal_head_changed")
    # Deliberately include ignored files: nothing outside the tracked input is
    # silently committed or ignored by this initial, edit-only envelope.
    if git.read("ls-files", "--others", "-z", maximum=65536):
        raise TaskInputError("correction_seal_untracked")
    entries = git.read("ls-tree", "-r", "-z", head, maximum=2 * 1024 * 1024).split(b"\0")
    if entries[-1] or len(entries) > MAX_TRACKED_FILES + 1:
        raise TaskInputError("correction_seal_source_limit")
    expected_index, files, total = [], {}, 0
    for entry in entries[:-1]:
        meta, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split()
        path = resource_path(raw_path.decode("utf-8"))
        if kind != "blob" or mode not in {"100644", "100755"} or any(p.casefold() == ".git" for p in path.split("/")):
            raise TaskInputError("correction_seal_source_unsupported")
        expected_index.append(f"{mode} {oid} 0\t".encode() + raw_path + b"\0")
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
    confinement against unrestricted same-UID writers. No public caller exists.
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
        files = _capture(git, head)
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
        if _manifest(_capture(git, head)) != manifest:
            raise TaskInputError("correction_seal_source_changed")
        if CorrectionLoopService(authority.project_root).admit_worker_dispatch(value) <= 0:
            raise TaskInputError("correction_time_budget_exhausted")
        # No symbolic-ref following; compare-and-swap only this dedicated ref.
        git.write("update-ref", "--no-deref", "refs/heads/sandbox/" + value.job_id, commit, head)
        git.write("read-tree", "--reset", commit)
        candidate = probe_candidate(root, commit)
        result = _bound({"binding": binding, "intent": intent, "candidate": candidate,
                         "output": output, "acceptance_status": "unknown"})
        _write(authority.root / "seal-result.json", result)
        return result
