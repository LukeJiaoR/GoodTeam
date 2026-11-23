"""[INPUT]: 任务锁、不可变作业、Skill 内容与隔离工作区凭据。
[OUTPUT]: 普通 Role 方法输入与执行锁。
[POS]: 核验 Codex 和 Pi 的原派工身份。Pi 会话继续使用资源代理。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from copy import deepcopy
from contextlib import contextmanager
import fcntl
import os

from .models import SkillError, content_digest, canonical_json


READONLY_CODEX_METHODS = "readonly_codex_methods.v1"
ORDINARY_PI_METHODS = frozenset({"ordinary_pi_methods.v1", "readonly_pi_methods.v1"})
ORDINARY_METHODS = ORDINARY_PI_METHODS | {READONLY_CODEX_METHODS}
METHOD_MODES = ORDINARY_METHODS | {"manager_pi_methods.v1"}


class RoleMethodInput:
    def __init__(self, workers, job_id, *, prepare=True):
        from .content import SkillContextProvider

        self.workers, self.job_id = workers, job_id
        self.provider = SkillContextProvider(workers, job_id, authority=self)
        _, snapshot = self._identity()
        self.request = deepcopy(snapshot["job_input"]["request"])
        self._check(self.request)
        self.manager_tools = self.request.get("method_loading_mode") == "manager_pi_methods.v1"
        self.readonly_tools = self.request.get("method_loading_mode") == "readonly_pi_methods.v1"
        self.context_id = "ordinary-" + job_id.removeprefix("job-skill-")
        self.ref = self.provider.prepare(self.context_id) if prepare else None

    @staticmethod
    def _check(request):
        mode = request.get("method_loading_mode")
        manager = mode == "manager_pi_methods.v1"
        codex = mode == READONLY_CODEX_METHODS
        readonly = mode == "readonly_pi_methods.v1" or codex
        readonly_roles = {"qa"} if codex else {"research", "planner", "qa"}
        if (mode not in METHOD_MODES
                or request.get("adapter") not in ({"codex"} if codex else {"pi", "pi-rpc"})
                or request.get("execution_policy") != ("read_only" if manager or readonly else "standard")
                or request.get("role") == "code_review"
                or request.get("session_policy") != "new" or request.get("worker_session_ref")
                or readonly and (request.get("role") not in readonly_roles
                    or request.get("review_target") is not None
                    or request.get("method_workspace_mode") != "dispatch_isolated"
                    or not request.get("project_task_id") or not request.get("track_id"))
                or manager and (request.get("role") != "orchestrator"
                    or request.get("web_search") is not None or request.get("method_workspace_mode")
                    or request.get("review_target") is not None
                    or request.get("project_task_id") or request.get("track_id"))):
            raise SkillError("ordinary_method_mode_unsupported", "This original request is not a supported Role method invocation.")

    def authorize(self, snapshot, *, context_id, operation):
        from .content import ContentSession

        request = snapshot["job_input"]["request"]
        self._check(request)
        if request != self.request or context_id != self.context_id:
            raise SkillError("ordinary_method_identity_invalid", "Methods belong to another invocation.")
        # These are explicit unknown binary/confinement facts, not conformance
        # evidence. Actual Pi preflight and invocation evidence remain Runtime-owned.
        return ContentSession(request["adapter"], request["method_loading_mode"], "unverified",
            content_digest(b"unverified"), request["model"],
            content_digest(canonical_json(request).encode()), context_id,
            "ordinary-method-input; no-isolation-claim",
            "ordinary_codex_methods" if request["adapter"] == "codex" else "ordinary_pi_methods")

    def validate(self, job_root, job):
        from .worker_requests import _metadata
        ref, snapshot = self._identity()
        if (job_root.resolve() != (self.workers.root / self.job_id).resolve()
                or job != _metadata(self.job_id, ref, snapshot)):
            raise SkillError("ordinary_method_identity_invalid", "Runtime differs from the original method invocation.")
        self.authorize(snapshot, context_id=self.context_id, operation="runtime")

    def _identity(self):
        from . import worker_requests as requests
        from .filesystem import directory
        root, operator, locks, _ = requests._services(self.workers)
        with requests._request_lock(operator, self.job_id) as authority:
            registered = locks.find_snapshot(command_key=self.job_id)
            if registered is None:
                raise SkillError("snapshot_missing", "Original method invocation is unavailable.")
            snapshot, ref = registered["snapshot"], registered["ref"]
            if (snapshot["job_input"]["request"].get("method_loading_mode") == "manager_pi_methods.v1"
                    and (snapshot.get("subject") != "manager:orchestrator"
                         or snapshot.get("task", {}).get("kind") != "manager_message")):
                raise SkillError("manager_method_identity_invalid", "Manager methods require the original Manager task binding.")
            request = snapshot["job_input"]["request"]
            if (request.get("method_loading_mode") in {"readonly_pi_methods.v1", READONLY_CODEX_METHODS}
                    and (snapshot.get("task", {}).get("kind") != "direct_task"
                         or snapshot.get("subject") != "role:" + request["role"])):
                raise SkillError("readonly_method_identity_invalid", "Read-only methods require the original direct task Role binding.")
            if snapshot["job_input"].get("worker_root") != str(root):
                raise SkillError("snapshot_scope_mismatch", "Invocation belongs to another root.")
            expected = requests._metadata(self.job_id, ref, snapshot)
            if not requests._read_publication(authority, self.job_id, expected):
                raise SkillError("worker_skill_publication_missing", "Original request was not published.")
            with directory(root / ".goodteam/workers/jobs" / self.job_id) as fd:
                requests._verify_files(fd, expected, snapshot)
        return ref, snapshot

    def bind_workspace(self, context):
        """Bind the existing dispatcher's exact retained workspace, never a path hint."""
        from . import worker_requests as requests
        from .filesystem import regular_file, write_new
        if not self.request.get("method_workspace_mode"):
            if context is not None:
                raise SkillError("ordinary_method_workspace_unsupported", "Direct methods cannot inherit an unbound workspace.")
            self.workspace_context = None
            return
        if not isinstance(context, dict):
            raise SkillError("ordinary_method_workspace_missing", "This invocation requires its original dispatch workspace.")
        expected = {"available": True, "mode": "dispatch_isolated",
                    "workspace_path": context.get("workspace_path"),
                    "execution_id": self.request["run_id"], "sandbox_id": self.job_id}
        if context.get("worker_setup"):
            expected["worker_setup"] = context["worker_setup"]
        if context.get("project_environment"):
            expected["project_environment"] = context["project_environment"]
        if context != expected:
            raise SkillError("ordinary_method_workspace_mismatch", "Workspace is not the original dispatch invocation.")
        # Existing retained-baseline and Git identity checks own this decision.
        self.workers._worker_runtime_cwd({**requests._metadata(self.job_id, *self._identity()),
                                         "sandbox_context": expected})
        _, operator, _, _ = requests._services(self.workers)
        encoded = canonical_json(expected).encode()
        with requests._request_lock(operator, self.job_id) as fd:
            name = self.job_id + ".ordinary-workspace.json"
            try:
                saved = regular_file(fd, name, maximum=16384)[0]
            except FileNotFoundError:
                write_new(fd, name, encoded)
                os.fsync(fd)
            else:
                if saved != encoded:
                    raise SkillError("ordinary_method_workspace_mismatch", "Invocation workspace cannot change on replay.")
        self.workspace_context = deepcopy(expected)

    def runtime_context(self):
        from .worker_requests import _metadata
        job = _metadata(self.job_id, *self._identity())
        if self.request.get("method_workspace_mode"):
            context = getattr(self, "workspace_context", None)
            if context is None:
                raise SkillError("ordinary_method_workspace_missing", "Dispatch workspace must be bound before startup.")
            self.bind_workspace(context)  # Revalidate actual retained directory at launch.
            job = {**job, "sandbox_context": context}
        return self.workers._worker_runtime_cwd(job)

    def session_binding(self, metadata, *, create=False):
        from . import worker_requests as requests
        from .filesystem import regular_file, write_new
        self.validate(self.workers.root / self.job_id,
            requests._metadata(self.job_id, *self._identity()))
        expected = {"job_id": self.job_id,
                    "run_id": self.request["run_id"],
                    "execution_id": self.request.get("execution_id") or self.request["run_id"],
                    "project_key": self.request["project_key"],
                    "session_id": metadata.get("session_id"),
                    "session_file": metadata.get("session_file")}
        if (not expected["session_id"] or not expected["session_file"]
                or any(metadata.get(key) != expected[key] for key in ("job_id", "execution_id", "project_key"))):
            raise SkillError("ordinary_method_session_mismatch", "Method session does not match its original invocation.")
        _, operator, _, _ = requests._services(self.workers)
        encoded = canonical_json(expected).encode()
        with requests._request_lock(operator, self.job_id) as fd:
            name = self.job_id + ".ordinary-session.json"
            try:
                saved = regular_file(fd, name, maximum=16384)[0]
            except FileNotFoundError:
                if not create:
                    raise SkillError("ordinary_method_session_unconfirmed", "No original method session receipt exists.") from None
                write_new(fd, name, encoded)
                os.fsync(fd)
            else:
                if saved != encoded:
                    raise SkillError("ordinary_method_session_mismatch", "Cannot resume another method session.")

    def prompt(self, *, package_root=None):
        prompt = self.provider.worker_input(self.ref, package_root=package_root).decode("utf-8")
        context = getattr(self, "workspace_context", None)
        if context and context.get("project_environment"):
            from goodteam_os.project_environment import worker_environment_instructions
            from goodteam_os.worker_setup import configured_setup_tool

            setup_workspace = self.runtime_context().runtime_cwd
            prompt += "\n\n" + worker_environment_instructions(
                context["project_environment"], workspace=setup_workspace,
                setup_tool=configured_setup_tool(self.workers.root / self.job_id, context, workspace=setup_workspace))
        return prompt

    def search_authorized(self, job_root, job):
        """Existing search allowance stays in the immutable task, not the method.

        Called immediately before new query consumption. Method revocation stops
        new work, while JobWebSearch still permits replay of already-paid evidence.
        """
        try:
            self.validate(job_root, job)
            self.provider.private_input(self.ref)
            return self.request.get("web_search") is not None
        except (SkillError, OSError):
            return False

    def resource_channel(self, *, active, startup_policy=None):
        from .pi_resources import PiResourceChannel
        if (self.manager_tools or self.readonly_tools) and startup_policy is None:
            raise SkillError("manager_tools_policy_missing", "Restricted methods require the original tool policy witness.")
        return PiResourceChannel(self.provider, self.ref, active=active, startup_policy=startup_policy)




# Historical Pi callers and receipts keep their existing entry name.
PiMethodInput = RoleMethodInput


@contextmanager
def ordinary_execution_slot(workers, job_id):
    """One existing invocation at a time; no pool, retry or new job identity."""
    from .worker_requests import _services, SKILL_JOB_RE
    from .filesystem import directory, open_lock_file
    if SKILL_JOB_RE.fullmatch(job_id) is None:
        raise SkillError("ordinary_method_identity_invalid", "Unknown method job identity.")
    _, operator, _, _ = _services(workers)
    with directory(operator / "skills" / "worker-requests") as root:
        fd = open_lock_file(root, job_id + ".ordinary-execution.lock")
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SkillError("ordinary_method_execution_busy", "This invocation already has an executor.") from exc
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

