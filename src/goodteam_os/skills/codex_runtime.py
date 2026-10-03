"""Concrete pinned Codex adapter behind an internal, rechecked admission lease.

No public JSON flag, package directive, environment variable or saved PASS file
creates a lease. The host/provider admission implementation is an independent
control-plane dependency. It is deliberately absent by default until #440's
actual platform, discovery, egress and spend checks have passed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import fcntl
import os
import re
from pathlib import Path
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .codex_local import LocalLease

from . import worker_requests as requests
from .codex_contract import ADAPTER_VERSION, CODEX_BINARY_DIGEST, CODEX_VERSION, CodexContract
from .codex_container import CodexContainer
from .codex_guard import source_bytes
from .content import ContentSession, _bytes, _decode, _publish_immutable
from .filesystem import directory, open_lock_file, regular_file, subdirectory, write_new
from .models import SkillError, content_digest


@dataclass(frozen=True)
class CodexLease:
    contract: CodexContract
    seccomp_bytes: bytes
    session: ContentSession

    def __post_init__(self):
        if (not isinstance(self.contract, CodexContract) or not isinstance(self.seccomp_bytes, bytes)
                or not 0 < len(self.seccomp_bytes) <= 1024 * 1024
                or content_digest(self.seccomp_bytes) != self.contract.seccomp_digest
                or not isinstance(self.session, ContentSession)
                or self.session.adapter != "codex" or self.session.adapter_version != ADAPTER_VERSION
                or self.session.binary_version != CODEX_VERSION or self.session.binary_digest != CODEX_BINARY_DIGEST
                or self.session.config_digest != self.contract.digest or self.session.model != self.contract.model):
            raise SkillError("codex_admission_invalid", "Codex admission must bind the exact binary, policy, model and session.")

        policy = _decode(self.seccomp_bytes)
        if policy.get("defaultAction") != "SCMP_ACT_ERRNO" or not isinstance(policy.get("syscalls"), list):
            raise SkillError("codex_admission_invalid", "The selected syscall profile must deny by default.")


class CodexReviewAdmission(ABC):
    """Internal host/provider authority; not user-installable Runtime config.

    A strict implementation must independently establish the actual pinned binary and
    image, native discovery inventory, enforced read/write and descendant
    isolation, provider-only egress and provider request/spend/model ceilings.
    It must bind those to the original snapshot and fresh context, recheck
    revocation and in-flight budget on every operation, and keep authentication
    outside the Worker. A method that just reads a PASS flag is not admission.

    Strict admission remains absent by default. The separately composed local
    CLI subclass returns a different lease and cannot activate bound methods or
    claim this pinned envelope's verification. Contract tests retain fixture
    provenance. Public requests cannot construct either authority.
    """

    @abstractmethod
    def acquire(self, snapshot: dict, *, context_id: str) -> CodexLease | LocalLease:
        """Reserve an independently admitted fresh context; never launch/reuse a worker."""

    @abstractmethod
    def check(self, lease: CodexLease | LocalLease, snapshot: dict, *, context_id: str, operation: str) -> None:
        """Revalidate actual host/provider permission; raise before an invalid operation."""

    @abstractmethod
    def finish(self, lease: CodexLease | LocalLease, *, context_id: str, termination_confirmed: bool) -> None:
        """Finish this lease; strict routes retire, local context consumption is never refunded."""

    def runtime(self, workers):
        """Compose the pinned envelope by default; local execution has its own lease."""
        return PinnedCodexReviewRuntime(workers, self)


def review_worker_kwargs(admission: CodexReviewAdmission | None) -> dict[str, CodexReviewAdmission]:
    """Carry a typed startup dependency without interpreting public configuration.

    Validation is side-effect free: it does not acquire a route, inspect a
    container, resolve credentials, or promote an authority's verification.
    """
    if admission is None:
        return {}
    if not isinstance(admission, CodexReviewAdmission):
        raise ValueError("Codex Reviewer needs a control-plane admission authority, not configuration.")
    return {"codex_review_admission": admission}


def seal_input_tree(root: Path) -> str:
    """Make only freshly materialized, nonsecret inputs readable to container UID.

    The parent directory stays 0700 in Operator storage and is never mounted.
    All files become nonexecutable, dirs traversable, and the mount is read-only.
    Mode bits alone are not the security boundary; the container is mandatory.
    """
    manifest = []

    def visit(fd, prefix=""):
        for name in sorted(os.listdir(fd)):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            relative = prefix + name
            if stat.S_ISDIR(info.st_mode):
                with subdirectory(fd, name) as child:
                    visit(child, relative + "/")
            elif stat.S_ISREG(info.st_mode):
                data, before = regular_file(fd, name, maximum=8 * 1024 * 1024)
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    current = os.fstat(file_fd)
                    if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                        raise SkillError("codex_input_changed", "Reviewer staging changed while being sealed.")
                    os.fchmod(file_fd, 0o444)
                    os.fsync(file_fd)
                finally:
                    os.close(file_fd)
                manifest.append({"path": relative, "digest": content_digest(data), "size": len(data)})
            else:
                raise SkillError("codex_input_invalid", "Reviewer staging cannot contain links or special files.")
        os.fchmod(fd, 0o555)
        os.fsync(fd)

    with directory(root) as fd:
        visit(fd)
    return content_digest(_bytes(manifest))


class PinnedCodexReviewRuntime:
    def __init__(self, workers, admission: CodexReviewAdmission):
        if not isinstance(admission, CodexReviewAdmission):
            raise SkillError("codex_admission_invalid", "A saved configuration is not a Runtime admission authority.")
        self.workers, self.admission = workers, admission
        self._leases: dict[str, tuple[str, CodexLease]] = {}
        self._entered: set[str] = set()
        self._acquiring: set[str] = set()

    def authorize(self, snapshot, *, context_id, operation):
        if not isinstance(context_id, str) or re.fullmatch(r"review-[0-9a-f]{32}", context_id) is None:
            raise SkillError("codex_context_invalid", "Codex requires a fresh parent-owned review context identity.")
        request = snapshot["job_input"]["request"]
        if (snapshot["subject"] != "role:code_review" or request["adapter"] != "codex"
                or request["execution_policy"] != "read_only" or request.get("session_policy", "new") != "new"
                or request.get("worker_session_ref")):
            raise SkillError("codex_scope_unsupported", "Pinned Codex is restricted to a fresh, read-only Reviewer.")
        identity = content_digest(_bytes(snapshot))
        previous = self._leases.get(context_id)
        if previous is None:
            if operation != "prepare":
                raise SkillError("codex_context_missing", "No fresh admitted context exists for this operation.")
            self._acquiring.add(context_id)
            lease = self.admission.acquire(deepcopy(snapshot), context_id=context_id)
            if not isinstance(lease, CodexLease):
                raise SkillError("codex_admission_invalid", "Admission returned no exact Codex lease.")
            self._leases[context_id] = (identity, lease)
        else:
            if previous[0] != identity:
                raise SkillError("codex_context_conflict", "A Codex context cannot be lent to another task or input.")
            lease = previous[1]
        if lease.session.model != request["model"] or lease.session.config_digest != lease.contract.digest:
            raise SkillError("runtime_identity_changed", "The admitted model differs from the original Worker request.")
        self.admission.check(lease, deepcopy(snapshot), context_id=context_id, operation=operation)
        self._pin_task_contract(snapshot, lease)
        return lease.session

    def _pin_task_contract(self, snapshot, lease):
        # The first admitted execution pins native configuration to the original
        # TaskSkillLock. A later retry cannot silently use today's provider,
        # image/policy/guard even when the requested model string is unchanged.
        operator = requests._services(self.workers)[1]
        record = {"schema_version": "goodteam.codex-task-binding.v1", "task": snapshot["task"],
                  "task_lock_ref": snapshot["task_lock_ref"], "subject": snapshot["subject"],
                  "contract_digest": lease.contract.digest, "adapter": ADAPTER_VERSION}
        name = content_digest(_bytes([snapshot["task_lock_ref"], snapshot["subject"]]))[7:]
        with directory(operator / "skills" / "codex-task-bindings", create=True) as fd:
            lock = open_lock_file(fd, name + ".lock")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                _publish_immutable(fd, name + ".json", _bytes(record), code="codex_task_runtime_changed")
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                os.close(lock)

    def check_current(self, provider, context_ref):
        # Provider calls authorize again and performs a fresh protected preflight.
        provider.private_input(context_ref)

    @contextmanager
    def execution(self, record, provider, context_ref):
        from .review_execution import materialize_review_inputs
        from .review_cancel import check_cancellation
        from .review_results import ReviewResultStore

        self.check_current(provider, context_ref)
        context_id = context_ref["context_id"]
        _identity, lease = self._leases[context_id]
        if record["runtime_session"] != vars(lease.session):
            raise SkillError("codex_context_conflict", "Execution cannot borrow another admitted session.")
        operator = requests._services(self.workers)[1]
        parent = operator / "skills" / "codex-review" / provider.job_id
        root = parent / context_id
        self._entered.add(context_id)
        cleanup_confirmed = True  # Nothing has been launched yet.
        try:
            # Atomic ownership: a crashed/previous context is never reattached.
            with directory(parent, create=True) as fd:
                os.mkdir(context_id, 0o700, dir_fd=fd)
                os.fsync(fd)
            with directory(root) as fd:
                os.mkdir("control", 0o700, dir_fd=fd)
                os.fsync(fd)
            stage, control = root / "inputs", root / "control"
            prompt = materialize_review_inputs(provider, context_ref, record, stage)
            with directory(stage / ".runtime", create=True) as fd:
                write_new(fd, "guard.py", source_bytes(), mode=0o400)
                os.fsync(fd)
            stage_digest = seal_input_tree(stage)
            with directory(control) as fd:
                write_new(fd, "input.json", _bytes({"context_ref": context_ref, "input_digest": content_digest(prompt),
                          "stage_digest": stage_digest, "contract_digest": lease.contract.digest}))
                os.fsync(fd)
            check_cancellation(ReviewResultStore(self.workers, provider.job_id))
            self.check_current(provider, context_ref)
            cleanup_confirmed = False
            with CodexContainer(lease.contract).execution(stage, control=control, context_id=context_id,
                                                         seccomp_bytes=lease.seccomp_bytes) as process:
                self.check_current(provider, context_ref)
                yield process, prompt
            cleanup_confirmed = True
        except SkillError as exc:
            # The container signals uncertain cleanup explicitly, including a
            # create timeout. All other exits from its finally confirm cleanup.
            cleanup_confirmed = exc.code != "review_termination_unknown"
            raise
        finally:
            try:
                self.admission.finish(lease, context_id=context_id, termination_confirmed=cleanup_confirmed)
            except (OSError, SkillError) as exc:
                # A route that could still spend cannot be reported as stopped.
                raise SkillError("review_termination_unknown", "Provider-route retirement is unconfirmed.") from exc

    def release_unstarted(self, context_id):
        # Preparation/begin can fail before execution() is entered. Do not leak a
        # reserved provider context in that branch. Entered envelopes retire it
        # before receive(), not after a successful result has been published.
        if context_id in self._acquiring and context_id not in self._leases:
            # A lost/invalid acquire reply does not prove that its external side
            # effect failed. Without a lease we cannot confirm its retirement.
            raise SkillError("review_termination_unknown", "Provider acquisition has no confirmed retirement.")
        if context_id in self._leases and context_id not in self._entered:
            _identity, lease = self._leases[context_id]
            self._entered.add(context_id)
            try:
                self.admission.finish(lease, context_id=context_id, termination_confirmed=True)
            except (OSError, SkillError) as exc:
                raise SkillError("review_termination_unknown", "Unstarted provider-route retirement is unconfirmed.") from exc


def selected_codex_runtime(workers):
    admission = getattr(workers, "_codex_review_admission", None)
    if admission is None:
        return None
    return admission.runtime(workers)
