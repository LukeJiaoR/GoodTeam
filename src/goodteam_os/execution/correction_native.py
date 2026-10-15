"""Explicit one-attempt native Codex authority, absent from default startup.

This admits trusted native self-use, not a verified sandbox or provider route.
The existing native CLI owns configuration/login. No credential bytes are read,
copied or inherited; an operator must separately authorize that exposure.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from goodteam_os.skills.codex_local import _binary_digest, _binary_stamp, _private_file
from goodteam_os.skills.filesystem import directory

from .correction_codex import CLI_VERSION, proposed_command
from .task_snapshot import TaskInputError, canonical_bytes, digest

POLICY_SCHEMA = "goodteam.trusted-codex-correction-policy.v1"
RUNTIME_SCHEMA = "goodteam.trusted-codex-correction-runtime.v1"


def correction_worker_kwargs(admission):
    if admission is None:
        return {}
    if not isinstance(admission, TrustedCodexCorrectionAdmission):
        raise TaskInputError("correction_native_authority_invalid")
    return {"codex_correction_admission": admission}


class TrustedCodexCorrectionAdmission:
    """Private, revocable permission for exactly one already captured attempt.

    Construction is explicit startup code only. An acknowledgement, job field,
    environment variable or a Reviewer authority cannot construct this grant.
    """
    verification_kind = "native_contract_unverified"

    def __init__(self, policy_path):
        self.path = Path(policy_path).absolute()
        self.raw = _private_file(self.path, maximum=32768)
        try:
            p = json.loads(self.raw)
            keys = {"schema_version", "project_root", "input", "workspace", "codex", "codex_home",
                    "version", "binary_digest", "model", "expires_at", "max_seconds", "max_invocations",
                    "trusted_task_inputs", "accept_native_managed_configuration", "accept_unknown_cost",
                    "provider_request_limit", "usd_limit"}
            if (not isinstance(p, dict) or set(p) != keys or canonical_bytes(p) != self.raw
                    or p["schema_version"] != POLICY_SCHEMA or p["version"] != CLI_VERSION
                    or type(p["expires_at"]) is not int or not time.time() < p["expires_at"] <= time.time() + 86400
                    or type(p["max_seconds"]) is not int or not 1 <= p["max_seconds"] <= 600
                    or type(p["max_invocations"]) is not int or p["max_invocations"] != 1
                    or any(p[key] is not True for key in ("trusted_task_inputs", "accept_native_managed_configuration", "accept_unknown_cost"))
                    or not isinstance(p["binary_digest"], str) or re.fullmatch(r"sha256:[a-f0-9]{64}", p["binary_digest"]) is None
                    or not isinstance(p["input"], dict)
                    or any(not isinstance(p[key], str) or not Path(p[key]).is_absolute() or ".." in Path(p[key]).parts
                           for key in ("project_root", "workspace", "codex", "codex_home"))):
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            raise TaskInputError("correction_native_policy_invalid") from exc
        if p["provider_request_limit"] is not None or p["usd_limit"] is not None:
            raise TaskInputError("correction_native_budget_unsupported")
        self.policy = p
        self.root, self.home, self.binary = (Path(p[key]) for key in ("project_root", "codex_home", "codex"))
        if any(path.resolve().is_relative_to(self.root.resolve()) for path in (self.path, self.home, self.binary)):
            raise TaskInputError("correction_native_policy_unsafe")
        self.command()  # Validate model/path shape without invocation.
        self._check_native()
        self.binary_digest = _binary_digest(self.binary)
        if self.binary_digest != p["binary_digest"]:
            raise TaskInputError("correction_native_binary_changed")
        self.stamp = _binary_stamp(self.binary)
        self._probe_version()
        self.pid = os.getpid()
        self.binding = {"schema_version": RUNTIME_SCHEMA, "input": p["input"], "policy_digest": digest(self.raw),
                        "binary_digest": self.binary_digest, "version": CLI_VERSION, "model_requested": p["model"],
                        "max_seconds": p["max_seconds"], "max_invocations": 1,
                        "runtime_verification": self.verification_kind,
                        "native_configuration": "native_managed", "sandbox_requested": "workspace-write",
                        "sandbox_confinement_verified": False, "provider_binding_verified": False,
                        "provider_request_limit": None, "usd_limit": None}

    def command(self):
        return proposed_command(binary=self.binary, home=self.home, workspace=self.policy["workspace"],
                                model=self.policy["model"], version=self.policy["version"])

    def _check_native(self):
        if os.name != "posix":
            raise TaskInputError("correction_transport_unsupported")
        # Observe metadata only; do not read login/config content or project it.
        with directory(self.home) as fd:
            root = os.fstat(fd)
            if root.st_uid != os.getuid() or root.st_mode & 0o022:
                raise TaskInputError("correction_native_home_unsafe")
            for name in ("auth.json", "config.toml"):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.getuid() or info.st_mode & 0o077):
                    raise TaskInputError("correction_native_home_unsafe")
        with directory(self.binary.parent) as fd:
            binary = os.open(self.binary.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(binary, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise TaskInputError("correction_native_binary_unsupported")
            if stream.read(2) == b"#!":
                raise TaskInputError("correction_native_binary_unsupported")

    def _probe_version(self):
        from .candidate_process import _terminate
        from .correction_process import communicate_correction
        process = subprocess.Popen((str(self.binary), "--version"), cwd=self.path.parent,
            env={"PATH": os.defpath}, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, close_fds=True)
        try:
            stdout, _stderr = communicate_correction(process, "", seconds=10, check_current=lambda: None)
            if process.returncode != 0 or stdout.strip() != "codex-cli " + CLI_VERSION:
                raise TaskInputError("correction_native_version_unavailable")
        finally:
            try:
                _terminate(process)
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()

    def check(self, authority, value):
        if (os.getpid() != self.pid or time.time() >= self.policy["expires_at"]
                or _private_file(self.path, maximum=32768) != self.raw):
            raise TaskInputError("correction_native_authorization_revoked")
        if _binary_stamp(self.binary) != self.stamp:
            raise TaskInputError("correction_native_binary_changed")
        self._check_native()
        if (authority.project_root != self.root or value.reference != self.policy["input"]
                or value.original.to_dict()["adapter"] != "codex"
                or str(authority.workspace(value)) != self.policy["workspace"]
                or self.policy["max_seconds"] > value.to_dict()["max_seconds"]):
            raise TaskInputError("correction_native_task_mismatch")
        from .candidate_validation import _read
        ready = _read(authority.execution_root / "job-ready.json")
        if ready is None or ready.get("snapshot") != value.original.reference:
            raise TaskInputError("task_input_job_not_ready")
        if ready["request"].get("execution_policy", "standard") != "standard":
            raise TaskInputError("correction_native_execution_policy_unsupported")
        model = ready["request"].get("model") or ""
        if model.startswith("codex:"):
            model = model[6:]
        if model and model != self.policy["model"]:
            raise TaskInputError("correction_native_model_mismatch")
        return self.binding

    def capability(self, authority, value):
        self.check(authority, value)
        return {"available": True, "status": "explicit_trusted_native", "reason": "exact_attempt_authorized",
                "verification": self.verification_kind, "sandbox_confinement_verified": False}
