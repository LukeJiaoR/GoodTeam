"""Trusted self-use native sessions, not strict third-party Skill admission.

The existing Codex process owns its complete managed configuration and login.
We do not guess/override its provider or pretend a user-file hash seals it. Only
an operator-authorized method-free task may reuse that native authority. Normal
construction remains closed; no HTTP/task/Skill flag can supply this policy.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import time

from .codex_local import (EXEC_OPTIONS, MAX_INPUT, VERSION, LocalCodexReviewRuntime, LocalLease,
                          _binary_digest, _binary_stamp, _private_file)
from .codex_runtime import CodexReviewAdmission
from .content import ContentSession, _bytes, _decode, _publish_immutable
from .filesystem import directory, open_lock_file, write_new
from .models import SkillError, content_digest
from .review_process import ReviewProcess
from .review_progress import MAX_REVIEW_SECONDS
from .task_locks import TaskSkillIdentity

ADAPTER = "goodteam.codex-trusted-review.v1"
POLICY_SCHEMA = "goodteam.codex-trusted-review-policy.v1"
START_SCHEMA = "goodteam.trusted-native-review-start.v1"


def saved_workflow_review_context(workers, *, workspace, state):
    """Read a native review's private receipt; never infer authority from a job.

    Ordinary workflow roles keep their legacy context. The separately authorized
    direct review owns only the code_review subject, with the same displayed run.
    Read/retry/cancel routes can recover its registered receipt after restart.
    """
    from goodteam_os.service import service_state_dir
    from .task_context import task_skill_context
    from .task_service import ProjectTaskSkillService

    if not (Path(service_state_dir()) / "skills" / "task-locks").exists():
        return {}
    task = TaskSkillIdentity("direct_task", state.project_key, state.run_id)
    tasks = ProjectTaskSkillService(workspace, state.project_key, worker_roots=(workers.project_root,))
    previous = tasks.locks.find_task(task=task, command_key="native-review:" + state.run_id)
    if previous is None:
        return {}
    confirmed = previous["lock"].get("start_input", {}).get("request", {}).get("confirmed_input", {})
    if (previous["status"] != "committed" or set(previous["lock"]["subjects"]) != {"role:code_review"}
            or confirmed != {"schema_version": START_SCHEMA, "run_id": state.run_id,
                             "producer_binding": {"workspace": str(Path(workspace).resolve()),
                                                  "worker_root": str(Path(workers.project_root).resolve())}}):
        raise SkillError("trusted_codex_task_mismatch", "The saved native review belongs to a different producer or Worker root.")
    context = task_skill_context(task, previous["ref"])
    tasks.read(context)
    return context


def runtime_boundaries(reference):
    captured = _decode(reference.encode())
    if (set(captured) != {"mode", "contexts", "seconds"} or captured["mode"] != "trusted_self_use"
            or type(captured["contexts"]) is not int or not 1 <= captured["contexts"] <= 4
            or type(captured["seconds"]) is not int or not 1 <= captured["seconds"] <= MAX_REVIEW_SECONDS):
        raise SkillError("trusted_codex_boundary_invalid", "Saved native exposure bounds are invalid.")
    return {"schema_version": "goodteam.codex-trusted-boundaries.v1", "mode": captured["mode"],
            "context_limit": captured["contexts"], "deadline_seconds": captured["seconds"],
            "native_config_authority": "native_managed", "provider_binding_verified": False,
            "model_verification": "requested_only", "provider_request_limit": None, "usd_limit": None,
            "unknown_cost_accepted": True, "tool_inventory": "native_managed", "tool_absence_verified": False,
            "strict_skill_binding": False, "native_sandbox_requested": "read-only", "sandbox_confinement_verified": False,
            "extra_tool_authorization": False, "automatic_parent_retries": 0, "provider_retirement_supported": False}


class TrustedCodexReviewAdmission(CodexReviewAdmission):
    """Permission for a bounded native session, never a conformance witness.

    Native managed config/permissions determine the route and available tools.
    Read-only sandbox is requested; no permission bypass, credential projection,
    extra tool authorization or automatic parent retry is supplied. Native tool
    discovery, external tools' permissions and provider retries remain native
    concerns, not verified isolation/request/spend promises made by this class.
    """
    verification_kind = "local_cli"

    def __init__(self, policy_path):
        self.path = Path(policy_path).absolute()
        self.policy_bytes = _private_file(self.path, maximum=32768)
        p = self.policy = _decode(self.policy_bytes)
        keys = {"schema_version", "authorization_id", "worker_root", "task", "codex", "codex_home", "model",
                "max_contexts", "deadline_seconds", "expires_at", "trusted_task_inputs",
                "accept_native_managed_configuration", "accept_unknown_cost", "provider_request_limit", "usd_limit"}
        if (set(p) != keys or p["schema_version"] != POLICY_SCHEMA
                or not isinstance(p["authorization_id"], str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", p["authorization_id"]) is None
                or type(p["max_contexts"]) is not int or not 1 <= p["max_contexts"] <= 4
                or type(p["deadline_seconds"]) is not int or not 1 <= p["deadline_seconds"] <= MAX_REVIEW_SECONDS
                or type(p["expires_at"]) is not int or not time.time() < p["expires_at"] <= time.time() + 86400
                or any(p[key] is not True for key in ("trusted_task_inputs", "accept_native_managed_configuration", "accept_unknown_cost"))
                or not isinstance(p["model"], str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", p["model"]) is None):
            raise SkillError("trusted_codex_policy_invalid", "A private explicit trusted-task/native-configuration exposure authorization is required.")
        if p["provider_request_limit"] is not None or p["usd_limit"] is not None:
            raise SkillError("trusted_codex_budget_unsupported", "Native context/deadline limits are not upstream request or USD ceilings.")
        TaskSkillIdentity(**p["task"])
        if any(not isinstance(p[key], str) or not Path(p[key]).is_absolute()
               for key in ("worker_root", "codex", "codex_home")):
            raise SkillError("trusted_codex_policy_invalid", "Explicit absolute task, native executable and original login roots are required.")
        self.root = Path(p["worker_root"]).absolute()
        self.codex, self.home = Path(p["codex"]).absolute(), Path(p["codex_home"]).absolute()
        if any(path.resolve().is_relative_to(self.root.resolve()) for path in (self.path, self.codex, self.home)):
            raise SkillError("trusted_codex_policy_unsafe", "Worker storage cannot supply native authority, login or executable.")
        # Stat the original login roots, never read or project auth/config bytes.
        self._check_login_root()
        if self.verification_kind != "contract_fixture":
            with self.codex.open("rb") as executable:
                if executable.read(2) == b"#!":
                    raise SkillError("trusted_codex_binary_unavailable", "Select the existing native executable, not a script wrapper.")
        self.binary_digest = _binary_digest(self.codex)
        self.binary_stamp = _binary_stamp(self.codex)
        try:
            result = subprocess.run((str(self.codex), "--version"), env={"PATH": "/usr/bin:/bin"},
                                    capture_output=True, timeout=10, check=False)
            if result.returncode or result.stdout.strip() != ("codex-cli " + VERSION).encode():
                raise ValueError("Unsupported version")
        except Exception:
            raise SkillError("trusted_codex_binary_unavailable", "The selected existing native version is unavailable.") from None
        self.digest = content_digest(_bytes({"policy": p, "binary_digest": self.binary_digest,
                        "command": self.command(), "authority": "native_managed"}))
        self.leases, self.retired = {}, set()

    def command(self):
        # Do not override model_provider/definitions or ignore user/managed
        # config/rules. No feature list is claimed as tool-absence proof.
        return (str(self.codex), *EXEC_OPTIONS, "--sandbox", "read-only", "--model", self.policy["model"], "-")

    def _check_login_root(self):
        with directory(self.home) as fd:
            root = os.fstat(fd)
            if root.st_uid != os.getuid() or root.st_mode & 0o022:
                raise SkillError("trusted_codex_policy_unsafe", "The original native home must be owned by this user and not writable by others.")
            for name in ("auth.json", "config.toml"):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise SkillError("trusted_codex_policy_unsafe", "Existing native login/config files must be private regular files.")

    def runtime(self, workers):
        if Path(workers.project_root).absolute() != self.root:
            raise SkillError("trusted_codex_task_mismatch", "Native self-use permission belongs to another Worker root.")
        return TrustedCodexReviewRuntime(workers, self)

    def capture_workflow_review(self, workers, *, workspace, state):
        """Called by the guarded page Run action, never by Worker metadata."""
        from dataclasses import asdict
        from .task_start import capture_task_start, reject_missing_task_context

        task = TaskSkillIdentity("direct_task", state.project_key, state.run_id)
        if Path(workers.project_root).absolute() != self.root or asdict(task) != self.policy["task"]:
            raise SkillError("trusted_codex_task_mismatch", "Native permission must name this exact current-task review and Worker root.")
        if time.time() >= self.policy["expires_at"] or _private_file(self.path, maximum=32768) != self.policy_bytes:
            raise SkillError("trusted_codex_authorization_revoked", "The private task authorization expired or changed.")
        reject_missing_task_context(TaskSkillIdentity("workflow_run", state.project_key, state.run_id))
        return capture_task_start(workspace=workspace, project_key=state.project_key, worker_root=workers.project_root,
            task=task, command_key="native-review:" + state.run_id,
            confirmed_input={"schema_version": START_SCHEMA, "run_id": state.run_id},
            subjects=("role:code_review",), protocol_only_review=True)

    def page_start_available(self, workers, *, state):
        """Display an already authorized default-task start; no grant or probe."""
        from dataclasses import asdict
        task = TaskSkillIdentity("direct_task", state.project_key, state.run_id)
        try:
            return (not state.task_skill_context and Path(workers.project_root).absolute() == self.root
                    and asdict(task) == self.policy["task"] and time.time() < self.policy["expires_at"]
                    and _private_file(self.path, maximum=32768) == self.policy_bytes)
        except (SkillError, OSError):
            # Revocation closes new starts; an availability hint is not authority
            # for reading an already registered immutable report.
            return False

    def _current(self, snapshot):
        if time.time() >= self.policy["expires_at"] or _private_file(self.path, maximum=32768) != self.policy_bytes:
            raise SkillError("trusted_codex_authorization_revoked", "The private task authorization expired or changed.")
        if _binary_stamp(self.codex) != self.binary_stamp:
            raise SkillError("trusted_codex_binary_changed", "The authorized executable changed.")
        self._check_login_root()
        request = snapshot["job_input"]["request"]
        if (snapshot["task"] != self.policy["task"] or snapshot["subject"] != "role:code_review"
                or request["adapter"] != "codex" or request["model"] != self.policy["model"]
                or request["execution_policy"] != "read_only" or request.get("session_policy", "new") != "new"
                or request.get("worker_session_ref")):
            raise SkillError("trusted_codex_task_mismatch", "Self-use permission requires the exact fresh read-only Reviewer task/model.")
        if snapshot["methods"]["items"] or snapshot["required_skill_ids"]:
            raise SkillError("trusted_codex_skills_unsupported", "Trusted self-use cannot admit third-party GoodTeam methods or scripts.")

    def acquire(self, snapshot, *, context_id):
        self._current(snapshot)
        if not isinstance(context_id, str) or re.fullmatch(r"review-[0-9a-f]{32}", context_id) is None:
            raise SkillError("codex_context_invalid", "A fresh parent-owned native context is required.")
        from goodteam_os.service import service_state_dir
        operator = Path(service_state_dir()).absolute()
        if operator.resolve().is_relative_to(self.root.resolve()):
            raise SkillError("trusted_codex_policy_unsafe", "The durable native context ledger must be outside Worker storage.")
        binding = {"policy_digest": self.digest, "snapshot_digest": content_digest(_bytes(snapshot)), "context_id": context_id}
        task_pin = {"task": snapshot["task"], "task_lock_ref": snapshot["task_lock_ref"], "subject": snapshot["subject"],
                    "codex": str(self.codex), "codex_home": str(self.home), "binary_digest": self.binary_digest,
                    "model_requested": self.policy["model"], "authority": "native_managed"}
        name = content_digest(_bytes([str(self.root), snapshot["task_lock_ref"], snapshot["subject"]]))[7:]
        with directory(operator / "codex-trusted-tasks", create=True) as fd:
            lock = open_lock_file(fd, name + ".lock")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                _publish_immutable(fd, name + ".json", _bytes(task_pin), code="trusted_codex_task_changed")
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                os.close(lock)
        with directory(operator / "codex-trusted-contexts" / self.policy["authorization_id"], create=True) as fd:
            lock = open_lock_file(fd, "budget.lock")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                _publish_immutable(fd, "authorization.json", _bytes({"policy": self.policy, "digest": self.digest}),
                                   code="trusted_codex_authorization_conflict")
                contexts = [n for n in os.listdir(fd) if re.fullmatch(r"review-[0-9a-f]{32}\.json", n)]
                if len(contexts) >= self.policy["max_contexts"]:
                    raise SkillError("trusted_codex_budget_exhausted", "The authorized native context budget is exhausted.")
                write_new(fd, context_id + ".json", _bytes(binding))
                os.fsync(fd)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                os.close(lock)
        boundaries = {"mode": "trusted_self_use", "contexts": self.policy["max_contexts"],
                      "seconds": self.policy["deadline_seconds"]}
        session = ContentSession("codex", ADAPTER, VERSION, self.binary_digest, self.policy["model"], self.digest,
                    "trusted:" + context_id, json.dumps(boundaries, sort_keys=True), self.verification_kind)
        lease = LocalLease(session, binding["snapshot_digest"])
        self.leases[context_id] = lease
        return lease

    def check(self, lease, snapshot, *, context_id, operation):
        self._current(snapshot)
        if (context_id in self.retired or self.leases.get(context_id) != lease
                or lease.snapshot_digest != content_digest(_bytes(snapshot))):
            raise SkillError("codex_context_conflict", "Self-use native permission cannot move to another task or input.")

    def finish(self, lease, *, context_id, termination_confirmed):
        self.retired.add(context_id)
        if self.leases.get(context_id) != lease or not termination_confirmed:
            raise SkillError("review_termination_unknown", "Owned native process stopping remains unconfirmed.")


class TrustedCodexReviewRuntime(LocalCodexReviewRuntime):
    def process_spec(self, root, lease):
        return ReviewProcess(self.admission.command(), root / "cwd",
            {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "en_US.UTF-8",
             "HOME": pwd.getpwuid(os.getuid()).pw_dir, "CODEX_HOME": str(self.admission.home)},
            self.admission.policy["deadline_seconds"], MAX_INPUT, forbid_tools=False,
            deadline_ceiling_seconds=MAX_REVIEW_SECONDS)
