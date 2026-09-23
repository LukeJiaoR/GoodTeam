"""Capture bounded Git changes from the registered Worker root, before dispatch.

Only exact commits and raw objects are accepted. No checkout, working-tree read,
network fetch, hook, filter, textconv, or model-selected source path is used.
Renames are represented as deletion + addition, retaining both sides. Unsupported
files block the whole capture instead of disappearing from a successful review.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import time

from .filesystem import directory
from .models import SkillError, canonical_json, content_digest, resource_path
from .review_process import _terminate

SOURCE_FILE = "review-source-snapshot.json"
SOURCE_SCHEMA = "goodteam.review-source-snapshot.v1"
MAX_FILES = 128
MAX_FILE_BYTES = 256 * 1024
MAX_SOURCE_BYTES = 768 * 1024
_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def source_request(value):
    if (not isinstance(value, dict) or set(value) != {"kind", "base_sha", "head_sha"}
            or value.get("kind") != "git_change"
            or any(not isinstance(value[k], str) or _OID.fullmatch(value[k]) is None for k in ("base_sha", "head_sha"))
            or len(value["base_sha"]) != len(value["head_sha"])):
        raise SkillError("review_target_invalid", "Git review requires two full commit IDs, not refs, URLs or directories.")
    return deepcopy(value)


def _encoded(value):
    return canonical_json(value).encode("utf-8")


def _blob_id(data, width):
    digest = hashlib.sha1 if width == 40 else hashlib.sha256
    return digest(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


class _Git:
    def __init__(self, root):
        self.root = Path(root).absolute()
        with directory(self.root):
            pass
        self.binary = shutil.which("git", path=os.defpath)
        if not self.binary:
            raise SkillError("review_git_unavailable", "The controller requires a local Git binary for source capture.")
        self.deadline = time.monotonic() + 30

    def read(self, *args, maximum=MAX_SOURCE_BYTES):
        # No inherited credentials, startup variables, helpers or replacement objects.
        env = {"PATH": os.defpath, "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
               "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "", "GIT_TERMINAL_PROMPT": "0",
               "GIT_OPTIONAL_LOCKS": "0"}
        command = [self.binary, "--no-pager", "-c", "core.hooksPath=" + os.devnull,
                   "-c", "core.fsmonitor=false", "-c", "protocol.allow=never", *args]
        process = subprocess.Popen(command, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
        output = bytearray()
        error_size = 0
        try:
            with selectors.DefaultSelector() as selector:
                for stream in (process.stdout, process.stderr):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() >= self.deadline:
                        raise SkillError("review_source_timeout", "Git source capture exceeded its bounded deadline.")
                    for key, _events in selector.select(timeout=0.05):
                        data = os.read(key.fd, 65536)
                        if not data:
                            selector.unregister(key.fileobj)
                        elif key.fileobj is process.stdout:
                            output.extend(data)
                            if len(output) > maximum:
                                raise SkillError("review_source_limit", "The complete Git change exceeds its source budget.")
                        else:
                            error_size += len(data)
                            if error_size > 65536:
                                raise SkillError("review_source_limit", "Git diagnostics exceeded their bound.")
                try:
                    process.wait(timeout=max(0.001, self.deadline - time.monotonic()))
                except subprocess.TimeoutExpired as exc:
                    raise SkillError("review_source_timeout", "Git source capture did not exit.") from exc
            if process.returncode:
                raise SkillError("review_source_unavailable", "Exact local Git objects are unavailable; no fetch or fallback is allowed.")
            return bytes(output)
        finally:
            _terminate(process)
            process.stdout.close()
            process.stderr.close()


def capture_git_sources(root, request):
    selected = source_request(request)
    git = _Git(root)
    top = git.read("rev-parse", "--show-toplevel", maximum=8192).decode("utf-8").strip()
    if Path(top).resolve() != Path(root).resolve():
        raise SkillError("review_source_scope", "Git source must belong to the registered Worker root, not an ancestor repository.")
    for field in ("base_sha", "head_sha"):
        if git.read("cat-file", "-t", selected[field], maximum=64) != b"commit\n":
            raise SkillError("review_target_invalid", "Review targets must be commits, not tags, trees or blobs.")
    raw = git.read("diff-tree", "--raw", "--no-abbrev", "--no-renames", "--no-ext-diff", "--no-textconv",
                   "--no-commit-id", "--no-relative", "--ignore-submodules=none", "-r", "-z", selected["base_sha"], selected["head_sha"], "--")
    parts = raw.split(b"\0")
    if not raw or parts[-1] != b"" or (len(parts) - 1) % 2:
        raise SkillError("review_source_empty", "The requested commits have no supported complete change to review.")
    if (len(parts) - 1) // 2 > MAX_FILES:
        raise SkillError("review_source_limit", "The complete change has too many files; none was silently omitted.")
    entries, sources = [], {}
    for index in range(0, len(parts) - 1, 2):
        header = parts[index].decode("ascii").split()
        if len(header) != 5 or not header[0].startswith(":") or header[4] not in {"A", "D", "M", "T"}:
            raise SkillError("review_source_invalid", "Unexpected Git change record.")
        try:
            path = resource_path(parts[index + 1].decode("utf-8"))
        except (UnicodeError, SkillError) as exc:
            raise SkillError("review_source_unsupported", "This change contains an unsupported source filename.") from exc
        if any(part.casefold() == ".git" for part in path.split("/")):
            raise SkillError("review_source_unsupported", "Git administrative paths cannot enter a Runtime source projection.")
        entry = {"path": path, "change": header[4]}
        for side, mode, oid in (("base", header[0][1:], header[2]), ("head", header[1], header[3])):
            if mode == "000000" and oid == "0" * len(selected["head_sha"]):
                entry[side] = None
                continue
            if mode not in {"100644", "100755"} or _OID.fullmatch(oid) is None:
                raise SkillError("review_source_unsupported", "Symlink, submodule and special-mode changes require another source adapter.")
            size = int(git.read("cat-file", "-s", oid, maximum=64))
            if not 0 <= size <= MAX_FILE_BYTES:
                raise SkillError("review_source_limit", "A changed source exceeds the per-file bound; no partial review was prepared.")
            data = git.read("cat-file", "blob", oid, maximum=MAX_FILE_BYTES)
            if len(data) != size or _blob_id(data, len(oid)) != oid:
                raise SkillError("review_source_invalid", "Captured bytes do not match the exact Git blob.")
            try:
                text = data.decode("utf-8")
                if "\0" in text or text.startswith("version https://git-lfs.github.com/spec/v1\n"):
                    raise ValueError("Not supported source text")
            except (UnicodeError, ValueError) as exc:
                raise SkillError("review_source_unsupported", "Binary, non-UTF-8 and LFS sources are not silently excluded.") from exc
            name = side + "/" + path
            sources[name] = text
            entry[side] = {"source_name": name, "blob_id": oid, "mode": mode, "size": size, "digest": content_digest(data)}
        entries.append(entry)
        if len(_encoded({"files": entries, "sources": sources})) > MAX_SOURCE_BYTES:
            raise SkillError("review_source_limit", "The complete captured change exceeds its source budget.")
    payload = {"schema_version": SOURCE_SCHEMA, "request": selected, "files": entries, "sources": sources}
    return validate_git_sources(payload, selected)


def validate_git_sources(payload, request):
    """Verify captured structure/bytes, without reopening a mutable repository."""
    selected = source_request(request)
    if (not isinstance(payload, dict) or set(payload) != {"schema_version", "request", "files", "sources"}
            or payload.get("schema_version") != SOURCE_SCHEMA or payload.get("request") != selected
            or not isinstance(payload["files"], list) or not 0 < len(payload["files"]) <= MAX_FILES
            or not isinstance(payload["sources"], dict) or len(_encoded(payload)) > MAX_SOURCE_BYTES):
        raise SkillError("review_source_invalid", "The captured source identity or budget is invalid.")
    paths, names = set(), set()
    try:
        for entry in payload["files"]:
            if set(entry) != {"path", "change", "base", "head"} or entry["change"] not in {"A", "D", "M"}:
                raise ValueError("Invalid entry")
            path = resource_path(entry["path"])
            if path in paths or any(part.casefold() == ".git" for part in path.split("/")):
                raise ValueError("Duplicate source")
            paths.add(path)
            if ((entry["change"] == "A" and (entry["base"] is not None or entry["head"] is None))
                    or (entry["change"] == "D" and (entry["head"] is not None or entry["base"] is None))
                    or (entry["change"] == "M" and (entry["base"] is None or entry["head"] is None))):
                raise ValueError("Invalid sides")
            for side in ("base", "head"):
                value = entry[side]
                if value is None:
                    continue
                name = side + "/" + path
                if (set(value) != {"source_name", "blob_id", "mode", "size", "digest"}
                        or value["source_name"] != name or value["mode"] not in {"100644", "100755"}
                        or type(value["size"]) is not int or not 0 <= value["size"] <= MAX_FILE_BYTES):
                    raise ValueError("Invalid source record")
                data = payload["sources"][name].encode("utf-8")
                if (value["blob_id"] != _blob_id(data, len(selected["head_sha"]))
                        or value["size"] != len(data) or value["digest"] != content_digest(data)):
                    raise ValueError("Changed source bytes")
                names.add(name)
        if set(payload["sources"]) != names:
            raise ValueError("Missing or unrelated source")
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        raise SkillError("review_source_invalid", "Captured source bytes or manifest are incomplete or changed.") from exc
    return deepcopy(payload)


def captured_git_target(snapshot):
    from .worker_requests import _json

    request = snapshot["job_input"]["request"]["review_target"]
    payload = validate_git_sources(_json(snapshot["job_input"]["files"][SOURCE_FILE].encode("utf-8")), request)
    target = {"kind": "git_change", "artifact_key": "git-change", "artifact_version": content_digest(_encoded(payload)),
              "base_sha": request["base_sha"], "head_sha": request["head_sha"],
              "display_name": request["base_sha"][:12] + " → " + request["head_sha"][:12],
              "files": payload["files"], "source_scope": "all_changed_files_both_sides"}
    return target, payload["sources"]
