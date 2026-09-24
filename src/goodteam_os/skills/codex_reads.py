"""Conservative native command witnesses; no inference from model prose.

Only an exact successful `cat -- /review/methods/<skill>/<path>` (possibly inside
one non-login bash/sh -c wrapper) is recognized. Shell expressions, aliases,
substitutions, multiple paths, relative paths or truncated output stay unobserved.
The complete output must equal bytes in the originally locked package. This is
read evidence, not evidence of comprehension or permission to execute a script.
"""
from __future__ import annotations

import re
import shlex

from .codex_contract import ADAPTER_VERSION, CODEX_BINARY_DIGEST, CODEX_VERSION
from .content import _bytes
from .models import SkillError, content_digest, identifier, resource_path
from .review_capture import CapturedCommand


def _path(command):
    try:
        parts = shlex.split(command, posix=True)
        if len(parts) == 3 and parts[0] in {"/bin/bash", "/bin/sh", "/usr/bin/bash", "/usr/bin/sh"} and parts[1] == "-c":
            command = parts[2]
        # No shell metacharacters, quoting, expansion, comments or redirection.
        # Packages may contain such names, but this witness doesn't certify them.
        match = re.fullmatch(r"(?:/usr/bin/cat|/bin/cat|cat) -- (/review/methods/(skill-[0-9a-f]{64})/([A-Za-z0-9._/-]+))", command)
        if match is None:
            return None
        skill_id, relative = match.group(2), match.group(3)
        identifier(skill_id, "skill-")
        resource_path(relative)
        return skill_id, relative
    except (ValueError, SkillError):
        return None


def resource_witnesses(capture, record, packages):
    session = record["runtime_session"]
    if (session["adapter"] != "codex" or session["adapter_version"] != ADAPTER_VERSION
            or session["binary_version"] != CODEX_VERSION or session["binary_digest"] != CODEX_BINARY_DIGEST):
        # Other versions/adapters do not inherit a parser's capabilities.
        return []
    methods = {item["skill_id"]: item for item in record["methods"]}
    witnesses = []
    for command in capture.commands:
        if not isinstance(command, CapturedCommand):
            raise SkillError("review_capture_command", "A model-written command claim is not a transport witness.")
        parsed = _path(command.command)
        if parsed is None:
            continue
        skill_id, relative = parsed
        method = methods.get(skill_id)
        if method is None:
            raise SkillError("codex_unbound_read", "Native output claims a read outside the originally bound methods.")
        package = packages.read_package(skill_id, method["version_id"])
        if package.digest != method["package_digest"]:
            raise SkillError("snapshot_corrupt", "The native resource witness differs from the locked package.")
        data = package.resource(relative)
        if command.output.encode("utf-8") != data:
            # Truncated/normalized output is not proof of the complete byte read.
            continue
        witnesses.append({"schema_version": "goodteam.codex-resource-read.v1", "origin": "native_command_event",
            "item_id": command.item_id, "skill_id": skill_id, "version_id": method["version_id"],
            "package_digest": method["package_digest"], "path": relative,
            "resource_digest": content_digest(data), "size": len(data),
            "started_digest": command.started_digest, "completed_digest": command.completed_digest,
            "transcript_digest": capture.transcript_digest, "context_digest": record["context_ref"]["digest"],
            "runtime_session_id": session["session_id"], "runtime_verification": session["verification_kind"]})
    return witnesses


def validate_witnesses(witnesses, *, capture, record, packages):
    if not isinstance(witnesses, list) or len(witnesses) > 1024:
        raise SkillError("review_result_corrupt", "Invalid native resource observation list.")
    session = record["runtime_session"]
    if witnesses and (session["adapter"] != "codex" or session["adapter_version"] != ADAPTER_VERSION
                      or session["binary_version"] != CODEX_VERSION or session["binary_digest"] != CODEX_BINARY_DIGEST):
        raise SkillError("review_result_corrupt", "Another Runtime cannot borrow pinned Codex resource witnesses.")
    methods = {item["skill_id"]: item for item in record["methods"]}
    ids = set()
    expected_fields = {"schema_version", "origin", "item_id", "skill_id", "version_id", "package_digest", "path",
        "resource_digest", "size", "started_digest", "completed_digest", "transcript_digest", "context_digest",
        "runtime_session_id", "runtime_verification"}
    for item in witnesses:
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise SkillError("review_result_corrupt", "Incomplete native resource witness.")
        if not isinstance(item["skill_id"], str):
            raise SkillError("review_result_corrupt", "A read witness has an invalid method identity.")
        method = methods.get(item["skill_id"])
        if (method is None or item["schema_version"] != "goodteam.codex-resource-read.v1"
                or item["origin"] != "native_command_event" or not isinstance(item["item_id"], str)
                or not item["item_id"] or len(item["item_id"]) > 128 or item["item_id"] in ids
                or item["version_id"] != method["version_id"] or item["package_digest"] != method["package_digest"]
                or item["context_digest"] != record["context_ref"]["digest"]
                or item["transcript_digest"] != capture["transcript_digest"]
                or item["runtime_session_id"] != record["runtime_session"]["session_id"]
                or item["runtime_verification"] != record["runtime_session"]["verification_kind"]
                or type(item["size"]) is not int or not 0 <= item["size"] <= 8 * 1024 * 1024):
            raise SkillError("review_result_corrupt", "Native read witness belongs to another package or context.")
        package = packages.read_package(item["skill_id"], item["version_id"])
        data = package.resource(item["path"])
        if (package.digest != item["package_digest"] or len(data) != item["size"]
                or content_digest(data) != item["resource_digest"]):
            raise SkillError("review_result_corrupt", "A saved read no longer matches its locked resource bytes.")
        ids.add(item["item_id"])
        resource_path(item["path"])
        for key in ("resource_digest", "started_digest", "completed_digest", "transcript_digest", "context_digest"):
            identifier(item[key], "sha256:")
    return content_digest(_bytes(witnesses))
