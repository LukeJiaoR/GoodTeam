"""Explicit self-use Codex execution, separate from verified Skill admission.

Reuse the CLI's existing login in place. Never copy credentials, create a
provider/proxy or claim a time/context bound is a request/spend ceiling. A
method-free read-only Reviewer is designed here; production admission is closed
until the complete effective native configuration can be bound to execution.
Personal-host containment and
remote request retirement are not independently verified by this adapter.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import tomllib
from urllib.parse import urlsplit

from .codex_contract import _toml
from .codex_runtime import CodexReviewAdmission
from .content import ContentSession, _bytes, _decode, _publish_immutable
from .filesystem import directory, open_lock_file, regular_file, write_new
from .models import SkillError, content_digest
from .review_process import ReviewProcess

VERSION = "0.160.0"
ADAPTER = "goodteam.codex-local-review.v1"
POLICY_SCHEMA = "goodteam.codex-local-policy.v1"
BOUNDARY_SCHEMA = "goodteam.codex-local-boundaries.v1"
MAX_INPUT = 1024 * 1024
EXEC_OPTIONS = ("exec", "--ephemeral", "--json", "--skip-git-repo-check", "--color", "never")
# Exact 0.160.0 options; source/schema are linked in the operator guide.
# Native normalization keeps the unified-exec backend enabled regardless of
# user opt-out; ShellTool is the effective gate. Do not confuse a removed or
# legacy spelling with a proven disabled feature.
DISABLED_FEATURES = (
    "shell_tool", "shell_zsh_fork", "apply_patch_freeform", "daemon_auto_start",
    "code_mode", "code_mode_only", "code_mode_host", "js_repl", "apps", "plugins", "hooks",
    "plugin_hooks", "browser_use", "in_app_browser", "computer_use", "image_generation",
    "view_image", "multi_agent", "multi_agent_v2", "agent_message_board", "search_tool",
    "tool_search", "tool_suggest", "skill_search", "skill_mcp_dependency_install", "skill_env_var_dependency_prompt",
    "memories", "external_agent_memory_import", "remote_control", "remote_models",
    "api_key_model_discovery", "responses_websockets", "responses_websockets_v2", "respect_system_proxy",
    "system_proxy_fallback", "unbounded_connection_retries", "shell_snapshot", "shell_snapshot_v2",
    "send_async_message", "send_message_to_user_async", "sleep_tool", "goals", "worktrees",
)
OPTIONS = {"approval_policy": "never", "sandbox_mode": "read-only", "web_search": "disabled",
           "model_reasoning_effort": "low",
           "project_doc_max_bytes": 0, "allow_login_shell": False, "agents.enabled": False,
           "orchestrator.mcp.enabled": False, "cloud.skills.enabled": False,
           "skills.bundled.enabled": False, "skills.include_instructions": False,
           "mcp_servers": {}, "notify": [], "features.skip_host_skill_discovery": True,
           "shell_environment_policy.inherit": "none", "analytics.enabled": False,
           **{"features." + key: False for key in DISABLED_FEATURES}}


def _private_file(path, *, maximum):
    path = Path(path).absolute()
    with directory(path.parent) as fd:
        data, info = regular_file(fd, path.name, maximum=maximum)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SkillError("local_codex_policy_unsafe", "Local authority files must be owned by this user with mode 0600.")
    return data


def _binary_digest(path):
    digest = hashlib.sha256()
    with directory(path.parent) as fd:
        binary = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(binary, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.getuid()}
                or info.st_mode & 0o022 or not info.st_mode & 0o111 or info.st_size > 512 * 1024 * 1024):
            raise SkillError("local_codex_binary_unsafe", "The explicitly selected executable is unsafe.")
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return "sha256:" + digest.hexdigest()


def _binary_stamp(path):
    with directory(path.parent) as fd:
        info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
        raise SkillError("local_codex_binary_unsafe", "The selected executable changed or became unsafe.")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_uid)


def _selection(codex, home, model, *, binary_digest=None):
    codex, home = Path(codex).absolute(), Path(home).absolute()
    if not isinstance(model, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model) is None:
        raise SkillError("local_codex_model_invalid", "Select one exact model.")
    with directory(home) as fd:
        source, config_info = regular_file(fd, "config.toml", maximum=1024 * 1024)
        if config_info.st_uid != os.getuid() or config_info.st_mode & 0o077:
            raise SkillError("local_codex_auth_unavailable", "The existing Codex configuration must be private.")
        # Stat only: the native CLI, not GoodTeam, reads the existing login.
        auth = os.stat("auth.json", dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(auth.st_mode) or auth.st_uid != os.getuid() or auth.st_mode & 0o077:
            raise SkillError("local_codex_auth_unavailable", "An existing private Codex login file is required.")
    try:
        config = tomllib.loads(source.decode("utf-8"))
        alias = config["model_provider"]
        provider = config["model_providers"][alias]
        url = urlsplit(provider["base_url"])
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise SkillError("local_codex_provider_unsupported", "An explicit existing Responses provider is required.") from exc
    allowed = {"name", "base_url", "wire_api", "requires_openai_auth", "supports_websockets", "experimental_bearer_token",
               "request_max_retries", "stream_max_retries", "stream_idle_timeout_ms"}
    if (not isinstance(alias, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", alias) is None
            or set(provider) - allowed or provider.get("wire_api") != "responses"
            or provider.get("requires_openai_auth") is not True or url.scheme != "https"
            or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment):
        raise SkillError("local_codex_provider_unsupported", "Alternate authentication or unsafe routing configuration is unsupported.")
    # Native Codex reads its existing config/auth in place. The bearer value is
    # never copied into an argument, environment, staged tree, policy or report.
    # A digest of the whole config binds edits without serializing its secrets.
    selected = {"name": "GoodTeam existing local route", "base_url": provider["base_url"],
                "wire_api": "responses", "requires_openai_auth": True, "supports_websockets": False,
                "request_max_retries": 0, "stream_max_retries": 0, "stream_idle_timeout_ms": 30000}
    identity = {"adapter": ADAPTER, "codex": str(codex), "home": str(home), "model": model,
                "binary_digest": binary_digest or _binary_digest(codex), "provider": selected,
                "provider_alias": alias, "user_config_digest": content_digest(source), "options": OPTIONS,
                "exec_options": list(EXEC_OPTIONS)}
    mcp = config.get("mcp_servers", {})
    if not isinstance(mcp, dict) or any(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) is None for key in mcp):
        raise SkillError("local_codex_provider_unsupported", "Existing MCP names cannot be safely disabled for this invocation.")
    identity["mcp_server_names"] = sorted(mcp)
    return identity


def _invocation_options(identity):
    alias = identity["provider_alias"]
    return {**OPTIONS, "model_provider": alias,
            **{"model_providers." + alias + "." + key: value for key, value in identity["provider"].items()},
            **{"mcp_servers." + name + ".enabled": False for name in identity["mcp_server_names"]}}


def _config_arguments(identity):
    return tuple(part for key, value in sorted(_invocation_options(identity).items())
                 for part in ("-c", key + "=" + _toml(value)))


def _inspect_features(identity):
    # `features list` loads the same existing configuration and overrides, but
    # cannot create a model turn. Never retain/echo raw stdout/stderr/config.
    home = Path(identity["home"])
    try:
        result = subprocess.run([identity["codex"], *_config_arguments(identity), "features", "list"],
                                cwd=home.parent, env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8",
                                "HOME": str(home.parent), "CODEX_HOME": str(home)},
                                capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SkillError("local_codex_config_unavailable", "Native configuration inspection failed without a model call.") from exc
    if result.returncode or len(result.stdout) > 64 * 1024 or len(result.stderr) > 64 * 1024:
        raise SkillError("local_codex_config_unavailable", "Native configuration inspection did not complete safely.")
    states = {}
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        fields = line.split()
        if (fields and fields[0] in {*DISABLED_FEATURES, "skip_host_skill_discovery"}
                and fields[-1] in {"true", "false"}):
            if fields[0] in states:
                raise SkillError("local_codex_features_unavailable", "Native feature inspection has ambiguous rows.")
            states[fields[0]] = fields[-1] == "true"
    if any(states.get(key) is not False for key in DISABLED_FEATURES) or states.get("skip_host_skill_discovery") is not True:
        raise SkillError("local_codex_features_unavailable", "Native execution features do not match the required local invocation gates.")
    return {"reported_disabled_features": sorted(DISABLED_FEATURES), "skip_host_skill_discovery": True,
            "tool_absence_verified": False}


def inspect_local_codex(codex, home, model):
    # Reject an unsafe, unsupported or changing executable before touching the
    # selected user's configuration/login metadata. Inspection is available even
    # when production admission is closed, so its prerequisite order matters.
    codex = Path(codex).absolute()
    before = _binary_stamp(codex)
    binary_digest = _binary_digest(codex)
    try:
        result = subprocess.run([str(codex), "--version"], env={"PATH": "/usr/bin:/bin"},
                                capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SkillError("local_codex_binary_unavailable", "Codex version inspection failed.") from exc
    if result.returncode or result.stdout.strip() != ("codex-cli " + VERSION).encode():
        raise SkillError("local_codex_version_unsupported", "This adapter supports exactly Codex 0.160.0.")
    if _binary_stamp(codex) != before:
        raise SkillError("local_codex_selection_changed", "The selected executable changed during inspection.")
    identity = _selection(codex, home, model, binary_digest=binary_digest)
    gates = _inspect_features(identity)
    if _binary_stamp(codex) != before:
        raise SkillError("local_codex_selection_changed", "The selected executable changed during inspection.")
    return {"adapter": ADAPTER, "binary_version": VERSION, "binary_digest": identity["binary_digest"],
            "selection_digest": content_digest(_bytes(identity)), "model": model, "existing_login_present": True,
            "model_turns_started": 0, "provider_request_count": None,
            "strict_skill_binding": False, "provider_request_limit": None,
            "usd_limit": None, "provider_retirement_supported": False, "native_feature_inspection": gates,
            "admission_ready": False, "effective_config_binding": "unverified"}


@dataclass(frozen=True)
class LocalLease:
    session: ContentSession
    snapshot_digest: str


class LocalCodexReviewAdmission(CodexReviewAdmission):
    """Candidate startup policy, closed without full native config binding.

    The policy is outside every Worker root and never accepted over HTTP. Its
    explicit unknown-cost acceptance is necessary; any requested request/USD
    hard ceiling is rejected. Context allocations survive restart and failures.
    The production gate rejects before subprocesses or quota allocation. A
    single user config digest and feature listing are not effective admission.
    """
    verification_kind = "local_cli"

    def _require_effective_configuration(self):
        # Native 0.160.0 merges package/MDM/system/cloud/user/profile/project/
        # runtime layers, then applies required provider definitions. An empty
        # mcp_servers table also preserves inherited entries. `features list`
        # neither attests provider/MCP inventory nor seals another exec
        # process's configuration. Do not override managed requirements, guess
        # that other layers are absent, or accept a policy JSON PASS witness.
        raise SkillError("local_codex_effective_config_unverified",
                         "Local execution is unavailable until its complete effective provider, MCP and configuration layers are bound to the native process.")

    def __init__(self, policy_path):
        self.path = Path(policy_path).absolute()
        self.policy_bytes = _private_file(self.path, maximum=32 * 1024)
        self.policy = _decode(self.policy_bytes)
        p = self.policy
        keys = {"schema_version", "authorization_id", "worker_root", "task", "codex", "codex_home", "model",
                "selection_digest", "max_contexts", "deadline_seconds", "expires_at", "accept_unknown_cost",
                "provider_request_limit", "usd_limit"}
        if (set(p) != keys or p["schema_version"] != POLICY_SCHEMA
                or not isinstance(p["authorization_id"], str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", p["authorization_id"]) is None
                or type(p["max_contexts"]) is not int or not 1 <= p["max_contexts"] <= 4
                or type(p["deadline_seconds"]) is not int or not 1 <= p["deadline_seconds"] <= 120
                or type(p["expires_at"]) is not int or not time.time() < p["expires_at"] <= time.time() + 86400
                or p["accept_unknown_cost"] is not True):
            raise SkillError("local_codex_policy_invalid", "Explicit task, finite contexts, expiry and unknown-cost acceptance are required.")
        if p["provider_request_limit"] is not None or p["usd_limit"] is not None:
            raise SkillError("local_codex_budget_unsupported", "This CLI cannot enforce provider request or USD ceilings.")
        from .task_locks import TaskSkillIdentity
        try:
            TaskSkillIdentity(**p["task"])
            if any(not isinstance(p[key], str) or not Path(p[key]).is_absolute()
                   for key in ("worker_root", "codex", "codex_home")):
                raise ValueError("absolute host paths required")
            self.root = Path(p["worker_root"]).absolute()
            self.codex, self.home = Path(p["codex"]).absolute(), Path(p["codex_home"]).absolute()
        except (TypeError, ValueError) as exc:
            raise SkillError("local_codex_policy_invalid", "The local policy has invalid task or host paths.") from exc
        if any(path.is_relative_to(self.root) for path in (self.path, self.home, self.codex)):
            raise SkillError("local_codex_policy_unsafe", "Worker storage cannot supply local authority, login or executable.")
        self._require_effective_configuration()
        inspected = inspect_local_codex(self.codex, self.home, p["model"])
        if inspected["selection_digest"] != p["selection_digest"]:
            raise SkillError("local_codex_selection_changed", "The selected binary, provider, model or invocation policy changed.")
        before = _binary_stamp(self.codex)
        self.selection = _selection(self.codex, self.home, p["model"])
        if _binary_stamp(self.codex) != before or content_digest(_bytes(self.selection)) != p["selection_digest"]:
            raise SkillError("local_codex_selection_changed", "The native selection changed during inspection.")
        self.binary_stamp = before
        self.digest = content_digest(_bytes(p))
        self.leases = {}
        self.retired = set()

    def runtime(self, workers):
        if Path(workers.project_root).absolute() != self.root:
            raise SkillError("local_codex_task_mismatch", "This local authority belongs to another Worker root.")
        return LocalCodexReviewRuntime(workers, self)

    def _current(self, snapshot):
        self._require_effective_configuration()
        p = self.policy
        if (time.time() >= p["expires_at"] or _private_file(self.path, maximum=32 * 1024) != self.policy_bytes):
            raise SkillError("local_codex_authorization_revoked", "The local exposure authorization expired or changed.")
        # The exact digest was captured at startup. Recheck anchored inode,
        # ctime/mtime/mode and routing on every poll without hashing 240MB every
        # 50ms and delaying cancellation. Any executable change fails closed.
        if (_binary_stamp(self.codex) != self.binary_stamp
                or _selection(self.codex, self.home, p["model"],
                              binary_digest=self.selection["binary_digest"]) != self.selection):
            raise SkillError("local_codex_selection_changed", "Local binary or provider selection changed.")
        request = snapshot["job_input"]["request"]
        if (snapshot["task"] != p["task"] or snapshot["subject"] != "role:code_review"
                or request["adapter"] != "codex" or request["model"] != p["model"]
                or request["execution_policy"] != "read_only" or request.get("session_policy", "new") != "new"
                or request.get("worker_session_ref")):
            raise SkillError("local_codex_task_mismatch", "Local execution requires its exact task/model and a fresh read-only Reviewer.")
        if snapshot["methods"]["items"] or snapshot["required_skill_ids"]:
            raise SkillError("local_codex_skills_unsupported", "Local CLI authority cannot activate strict bound methods.")

    def acquire(self, snapshot, *, context_id):
        self._current(snapshot)
        if re.fullmatch(r"review-[0-9a-f]{32}", context_id) is None:
            raise SkillError("codex_context_invalid", "A fresh parent-owned context is required.")
        from goodteam_os.service import service_state_dir
        operator = Path(service_state_dir()).absolute()
        if operator.is_relative_to(self.root):
            raise SkillError("local_codex_policy_unsafe", "Local authority records must be outside the Worker root.")
        # A new authorization can replenish explicitly accepted contexts, but
        # cannot silently replace an old task's provider/binary/command policy.
        selection_ref = {"task": snapshot["task"], "task_lock_ref": snapshot["task_lock_ref"],
                         "subject": snapshot["subject"], "selection_digest": self.policy["selection_digest"]}
        name = content_digest(_bytes([str(self.root), snapshot["task_lock_ref"], snapshot["subject"]]))[7:]
        with directory(operator / "codex-local-task-bindings", create=True) as fd:
            lock = open_lock_file(fd, name + ".lock")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                _publish_immutable(fd, name + ".json", _bytes(selection_ref), code="local_codex_task_runtime_changed")
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                os.close(lock)
        budget = operator / "codex-local-budgets" / self.policy["authorization_id"]
        if budget.is_relative_to(self.root):
            raise SkillError("local_codex_policy_unsafe", "The context ledger must be outside the Worker root.")
        binding = {"authorization_digest": self.digest, "snapshot_digest": content_digest(_bytes(snapshot)),
                   "context_id": context_id}
        with directory(budget, create=True) as fd:
            lock = open_lock_file(fd, "budget.lock")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    stored = regular_file(fd, "authorization.json", maximum=32 * 1024)[0]
                except FileNotFoundError:
                    stored = _bytes(self.policy)
                    write_new(fd, "authorization.json", stored)
                if stored != _bytes(self.policy):
                    raise SkillError("local_codex_policy_conflict", "An authorization identity cannot reset or change its context budget.")
                contexts = [name for name in os.listdir(fd) if name.startswith("review-") and name.endswith(".json")]
                if len(contexts) >= self.policy["max_contexts"]:
                    raise SkillError("local_codex_budget_exhausted", "The authorized CLI context budget is exhausted.")
                write_new(fd, context_id + ".json", _bytes(binding))
                os.fsync(fd)  # Consumption is durable before any external invocation.
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                os.close(lock)
        boundaries = {"schema_version": BOUNDARY_SCHEMA, "context_limit": self.policy["max_contexts"],
                      "deadline_seconds": self.policy["deadline_seconds"], "provider_request_limit": None,
                      "usd_limit": None, "unknown_cost_accepted": True, "strict_skill_binding": False,
                      "provider_retirement_supported": False}
        session = ContentSession("codex", ADAPTER, VERSION, self.selection["binary_digest"], self.policy["model"],
                                 self.digest, "local:" + context_id, json.dumps(boundaries, sort_keys=True), self.verification_kind)
        lease = LocalLease(session, binding["snapshot_digest"])
        self.leases[context_id] = lease
        return lease

    def check(self, lease, snapshot, *, context_id, operation):
        self._current(snapshot)
        if (context_id in self.retired or self.leases.get(context_id) != lease
                or content_digest(_bytes(snapshot)) != lease.snapshot_digest):
            raise SkillError("codex_context_conflict", "Local execution cannot borrow another context or input.")

    def finish(self, lease, *, context_id, termination_confirmed):
        self.retired.add(context_id)
        if self.leases.get(context_id) != lease or not termination_confirmed:
            raise SkillError("review_termination_unknown", "Local process cleanup is unconfirmed; context budget remains consumed.")
        # Consumption is NEVER refunded. A killed client cannot revoke requests
        # already accepted by this existing remote provider.


class LocalCodexReviewRuntime:
    def __init__(self, workers, admission):
        self.workers, self.admission = workers, admission
        self.leases = {}
        self.entered = set()

    def authorize(self, snapshot, *, context_id, operation):
        lease = self.leases.get(context_id)
        if lease is None:
            if operation != "prepare":
                raise SkillError("codex_context_missing", "No authorized local context exists.")
            lease = self.admission.acquire(snapshot, context_id=context_id)
            self.leases[context_id] = lease
        self.admission.check(lease, snapshot, context_id=context_id, operation=operation)
        return lease.session

    def check_current(self, provider, context_ref):
        provider.private_input(context_ref)

    def release_unstarted(self, context_id):
        # No provider route is allocated by acquire. Durable context consumption
        # stays spent even if preparation fails; restart cannot refund it.
        pass

    def process_spec(self, root, lease):
        command = [str(self.admission.codex), *EXEC_OPTIONS, "--model", lease.session.model,
                   *_config_arguments(self.admission.selection), "-"]
        return ReviewProcess(tuple(command), root / "cwd", {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8",
                             "HOME": str(self.admission.home.parent), "CODEX_HOME": str(self.admission.home)},
                             self.admission.policy["deadline_seconds"], MAX_INPUT, forbid_tools=True)

    @contextmanager
    def execution(self, record, provider, context_ref):
        from . import worker_requests as requests
        from .review_execution import materialize_review_inputs
        from .review_cancel import check_cancellation
        from .review_results import ReviewResultStore

        self.check_current(provider, context_ref)
        context_id = context_ref["context_id"]
        if context_id in self.entered:
            raise SkillError("codex_context_conflict", "A local context can launch only once.")
        self.entered.add(context_id)
        lease = self.leases[context_id]
        if record["runtime_session"] != vars(lease.session) or record["methods"]:
            raise SkillError("codex_context_conflict", "Local execution requires its method-free captured session.")
        operator = requests._services(self.workers)[1]
        parent = operator / "codex-local-inputs" / provider.job_id
        with directory(parent, create=True) as fd:
            os.mkdir(context_id, 0o700, dir_fd=fd)
        root = parent / context_id
        prompt = materialize_review_inputs(provider, context_ref, record, root / "input")
        # Sources are already captured inline in the exact prompt. Native Codex
        # reads existing credentials in place; invocation-only restrictions do
        # not edit personal config or rules. This is not strict host isolation.
        with directory(root) as fd:
            os.mkdir("cwd", 0o700, dir_fd=fd)
        # The user's existing config can contain legacy metadata rejected by
        # `--strict-config`. Preserve it, pin its full digest, and validate the
        # effective native gates before any context allocation instead.
        check_cancellation(ReviewResultStore(self.workers, provider.job_id))
        self.check_current(provider, context_ref)
        spec = self.process_spec(root, lease)
        confirmed = True
        try:
            yield spec, prompt
        except SkillError as exc:
            confirmed = exc.code != "review_termination_unknown"
            raise
        finally:
            self.admission.finish(lease, context_id=context_id, termination_confirmed=confirmed)
