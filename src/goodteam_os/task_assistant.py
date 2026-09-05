from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from goodteam_os.manager_execution import run_manager_chat_turn
from goodteam_os.project_import import confirm_project_mode, import_project
from goodteam_os.project_readiness import assess_project_mode
from goodteam_os.project_readiness_executor import ProjectReadinessExecutor
from goodteam_os.task_assistant_session import TaskAssistantSessionStore, initial_task_assistant_session
from goodteam_os.team_control_plane import (
    MANAGER_ROLE,
    TeamControlPlaneError,
    execute_team_commands,
    role_label,
)


_WORK_TERMS = (
    "修复",
    "实现",
    "新增",
    "添加",
    "修改",
    "删除",
    "开发",
    "构建",
    "fix ",
    "implement",
    "add ",
    "change ",
    "remove ",
    "build ",
    "create ",
)
_UNDERSTANDING_TERMS = (
    "看一下",
    "看看",
    "了解",
    "分析",
    "介绍",
    "当前项目",
    "项目结构",
    "understand",
    "inspect",
    "summarize",
    "review this project",
)


class TaskAssistantService:
    def __init__(
        self,
        workspace: Path,
        *,
        readiness_executor: Any | None = None,
        confirm_mode: Callable[[Path, str, Path, str], dict[str, object]] = confirm_project_mode,
        project_config: Path | None = None,
        manager_turn: Callable[..., dict[str, object]] = run_manager_chat_turn,
    ):
        self.workspace = Path(workspace)
        self.readiness = readiness_executor or ProjectReadinessExecutor(self.workspace)
        self.confirm_mode = confirm_mode
        self.project_config = Path(project_config) if project_config is not None else None
        self.manager_turn = manager_turn

    def session(self, project_key: str, root: Path) -> dict[str, object]:
        store = TaskAssistantSessionStore(self.workspace, project_key)
        session = store.read()
        if not session["timeline"]:
            session = initial_task_assistant_session(project_key, assess_project_mode(root))
        try:
            readiness = self.readiness.status(project_key, root)
        except ValueError as exc:
            if not str(exc).startswith("No readiness found for project:"):
                raise
            return session
        session = self._append_completed_understanding(
            store,
            session,
            readiness,
            project_key,
            root,
        )
        return self._with_readiness_attention(session, readiness)

    def send_message(
        self,
        *,
        project_key: str,
        root: Path,
        text: str,
        message_id: str,
    ) -> dict[str, object]:
        if not str(text).strip():
            raise ValueError("Task Assistant message must not be blank")
        store = TaskAssistantSessionStore(self.workspace, project_key)
        mode_hint = assess_project_mode(root)
        store.initialize_context(mode_hint)
        store.append_message(message_id, "user", str(text))
        state = self._readiness_state(project_key, root)
        manager_response = ""
        manager_execution: dict[str, object] = {}
        team_view = self.team_view(project_key)
        team_commands: list[dict[str, object]] = []
        team_authorization = ""
        if self.project_config is not None and not self._manager_runtime_is_builtin():
            turn = self.manager_turn(
                project_config=self.project_config,
                project_key=project_key,
                message_id=message_id,
                text=str(text),
                context=_manager_chat_context(state, mode_hint, store.read(), team_view=team_view),
            )
            intent = str(turn.get("intent", ""))
            manager_response = str(turn.get("response", ""))
            manager_execution = dict(turn.get("manager_execution") or {})
            team_commands = [dict(item) for item in turn.get("team_commands") or [] if isinstance(item, dict)]
            team_authorization = str(turn.get("team_authorization", ""))
        else:
            # Deterministic path: builtin manager runtime (fake/noop/human)
            # cannot classify language, so the bounded classifier owns intent
            # and explicit team commands. Real-model turns stay above.
            detection = detect_team_commands(text, team_view)
            if detection is not None:
                intent = "team_management"
                team_commands = detection.commands
                team_authorization = "user_authorized"
                manager_response = detection.reply
            else:
                intent = classify_task_assistant_intent(text)

        if intent == "team_management" and team_commands:
            session = self._handle_team_management(
                store,
                project_key=project_key,
                message_id=message_id,
                text=str(text),
                commands=team_commands,
                authorization=team_authorization,
                manager_response=manager_response,
                manager_execution=manager_execution,
            )
            return {
                "task_assistant_session": session,
                "session": session,
                "intent": intent,
                "workflow_started": False,
                "run_id": "",
            }
        if intent == "team_management":
            # A team turn without executable commands degrades to clarification.
            intent = "ambiguous"

        if detect_task_scoped_team_preference(text) and team_view is not None:
            # Task-scoped capability preferences are traceable but never
            # persist as Team mutations; the classified intent still flows.
            store.append_event(
                f"preference:{message_id}",
                "team_preference_recorded",
                content=str(text),
                details={"scope": "task", "note": "Task-scoped preference; persistent Team configuration was not changed."},
            )

        if intent == "project_understanding":
            state = self._resolve_confident_mode(project_key, root, state, mode_hint)
            if str(state.get("lifecycle")) != "ready" and not isinstance(state.get("execution"), dict):
                state = self.readiness.start(
                    project_key,
                    root,
                    {},
                    idempotency_key=f"task-assistant:{message_id}",
                )
            session = store.append_event(
                f"reply:{message_id}",
                "project_inspection_started",
                content=manager_response,
                details={
                    "readiness_lifecycle": str(state.get("lifecycle", "")),
                    "manager_execution": manager_execution,
                },
            )
            session = self._append_completed_understanding(
                store,
                session,
                state,
                project_key,
                root,
            )
        elif intent == "work_request":
            state = self._resolve_confident_mode(project_key, root, state, mode_hint)
            if str(state.get("lifecycle")) != "ready" and not isinstance(state.get("execution"), dict):
                inputs = (
                    {"goal": str(text).strip(), "brief": str(text).strip()}
                    if state.get("project_mode") == "new_project"
                    else {}
                )
                self.readiness.start(
                    project_key,
                    root,
                    inputs,
                    idempotency_key=f"task-assistant:{message_id}",
                )
            session = store.read()
            proposal = dict(session.get("proposed_task") or {})
            if message_id not in list(proposal.get("source_message_ids") or []):
                session = store.propose_task([message_id], str(text))
            session = store.append_event(
                f"reply:{message_id}",
                "task_proposed",
                content=manager_response,
                details={
                    "proposal_id": str(dict(session.get("proposed_task") or {}).get("id", "")),
                    "manager_execution": manager_execution,
                },
            )
        else:
            session = store.append_event(
                f"reply:{message_id}",
                "intent_clarification_requested",
                content=manager_response,
                details={"manager_execution": manager_execution},
            )

        return {
            "task_assistant_session": session,
            "session": session,
            "intent": intent,
            "workflow_started": False,
            "run_id": "",
        }

    def team_view(self, project_key: str) -> dict[str, object] | None:
        """Normalized Team capability view, or None when unavailable.

        A chat turn is an explicit team touchpoint: the state lazily migrates
        here once, and every planning turn afterwards sees current truth.
        """
        if self.project_config is None:
            return None
        try:
            from goodteam_os.team_control_plane import team_view_for_config

            return team_view_for_config(self.workspace, project_key, self.project_config)
        except (OSError, ValueError):
            return None

    def _manager_runtime_is_builtin(self) -> bool:
        """True when the Task Assistant runtime cannot classify language.

        Builtin runtimes (fake/noop/human) never produce the structured chat
        contract, so chat turns fall back to the bounded deterministic
        classifier instead of failing with an unparseable worker result.
        """
        if self.project_config is None:
            return True
        if self.manager_turn is not run_manager_chat_turn:
            # An injected turn callable owns classification (tests, harnesses).
            return False
        try:
            from goodteam_os.graph import resolve_role_adapter
            from goodteam_os.projects import load_project_target
            from goodteam_os.workers import BUILTIN_WORKER_MODES

            adapter = resolve_role_adapter(load_project_target(self.project_config), MANAGER_ROLE)
        except (OSError, ValueError):
            return False
        return adapter in BUILTIN_WORKER_MODES

    def _handle_team_management(
        self,
        store: TaskAssistantSessionStore,
        *,
        project_key: str,
        message_id: str,
        text: str,
        commands: list[dict[str, object]],
        authorization: str,
        manager_response: str,
        manager_execution: dict[str, object],
    ) -> dict[str, object]:
        """Apply or stage one team decision, per the authorization contract.

        Explicit user intent mutates the Team directly. A Manager-proposed
        change waits for explicit user approval and never mutates on its own.
        """
        if authorization == "user_authorized":
            try:
                outcome = execute_team_commands(
                    self.workspace,
                    project_key,
                    commands,
                    actor="user",
                    surface="manager_conversation",
                    reason=str(text)[:300],
                    source_ref={"message_id": message_id},
                    command_key=f"team:{message_id}",
                    config_path=self.project_config,
                )
            except TeamControlPlaneError as exc:
                session = store.append_event(
                    f"team-decision:{message_id}",
                    "team_decision_failed",
                    content=manager_response,
                    details={
                        "commands": commands,
                        "error": str(exc),
                        "manager_execution": manager_execution,
                    },
                )
                return session
            if outcome.get("replayed"):
                # Idempotent conversation retry: the applied decision event is
                # already on the timeline; never append a divergent duplicate.
                return store.read()
            return store.append_event(
                f"team-decision:{message_id}",
                "team_decision_applied",
                content=manager_response,
                details={
                    "decision_id": outcome["decision_id"],
                    "team_revision": outcome["team_revision"],
                    "commands": commands,
                    "manager_execution": manager_execution,
                },
            )
        session = store.propose_team_decision(
            commands=commands,
            reason=str(text)[:300],
            source_message_id=message_id,
        )
        pending = dict(session.get("pending_team_decision") or {})
        return store.append_event(
            f"team-decision:{message_id}",
            "team_decision_proposed",
            content=manager_response,
            details={
                "proposal_id": str(pending.get("id", "")),
                "commands": commands,
                "manager_execution": manager_execution,
            },
        )

    def confirm_pending_team_decision(
        self,
        *,
        project_key: str,
        proposal_id: str,
    ) -> dict[str, object]:
        """Execute a Manager-proposed team decision after explicit approval."""
        store = TaskAssistantSessionStore(self.workspace, project_key)
        session = store.read()
        pending = dict(session.get("pending_team_decision") or {})
        if pending.get("id") != str(proposal_id) or pending.get("status") != "pending":
            raise ValueError("Team decision proposal is unavailable")
        commands = [dict(item) for item in pending.get("commands") or []]
        reason = str(pending.get("reason", ""))
        try:
            outcome = execute_team_commands(
                self.workspace,
                project_key,
                commands,
                actor="user",
                surface="manager_conversation",
                reason=reason,
                source_ref={"proposal_id": str(proposal_id), "source_message_id": str(pending.get("source_message_id", ""))},
                command_key=f"team-confirm:{proposal_id}",
                config_path=self.project_config,
            )
        except TeamControlPlaneError as exc:
            session = store.set_pending_team_decision_status(str(proposal_id), "failed")
            session = store.append_event(
                f"team-decision-result:{proposal_id}",
                "team_decision_failed",
                content=str(exc),
                details={"proposal_id": str(proposal_id), "commands": commands},
            )
            raise ValueError(str(exc)) from exc
        store.take_pending_team_decision(str(proposal_id))
        return store.append_event(
            f"team-decision-result:{proposal_id}",
            "team_decision_applied",
            content="",
            details={
                "proposal_id": str(proposal_id),
                "decision_id": outcome["decision_id"],
                "team_revision": outcome["team_revision"],
                "commands": commands,
                "confirmed_by_user": True,
            },
        )

    def dismiss_pending_team_decision(self, *, project_key: str, proposal_id: str) -> dict[str, object]:
        store = TaskAssistantSessionStore(self.workspace, project_key)
        session = store.read()
        pending = dict(session.get("pending_team_decision") or {})
        if pending.get("id") != str(proposal_id) or pending.get("status") != "pending":
            raise ValueError("Team decision proposal is unavailable")
        store.take_pending_team_decision(str(proposal_id))
        return store.append_event(
            f"team-decision-result:{proposal_id}",
            "team_decision_dismissed",
            content="",
            details={"proposal_id": str(proposal_id), "commands": list(pending.get("commands") or [])},
        )

    def _readiness_state(self, project_key: str, root: Path) -> dict[str, object]:
        try:
            return self.readiness.status(project_key, root)
        except ValueError as exc:
            if not str(exc).startswith("No readiness found for project:"):
                raise
        imported = import_project(
            self.workspace,
            str(root),
            existing_configs=[self.project_config] if self.project_config is not None else None,
        )
        return dict(imported["readiness"])

    def _append_completed_understanding(
        self,
        store: TaskAssistantSessionStore,
        session: dict[str, object],
        readiness: dict[str, object],
        project_key: str,
        root: Path,
    ) -> dict[str, object]:
        step = next(
            (
                item for item in readiness.get("steps", [])
                if isinstance(item, dict)
                and item.get("id") == "repository_understanding"
                and item.get("status") == "complete"
            ),
            None,
        )
        if not isinstance(step, dict):
            return session
        artifact_refs = list(step.get("artifact_refs", []) or [])
        if not artifact_refs:
            return session
        artifact_ref = str(artifact_refs[-1])
        pending = [
            item for item in session.get("timeline", [])
            if isinstance(item, dict) and item.get("event") == "project_inspection_started"
        ]
        completed_sources = {
            str(dict(item.get("details") or {}).get("source_message_id", ""))
            for item in session.get("timeline", [])
            if isinstance(item, dict) and item.get("event") == "project_understanding_ready"
        }
        unanswered = [item for item in pending if str(item.get("message_id", "")) not in completed_sources]
        if not unanswered:
            return session
        artifact = self.readiness.artifact(project_key, root, artifact_ref)
        content = _format_repository_understanding(dict(artifact.get("content") or {}))
        for item in unanswered:
            source_message_id = str(item.get("message_id", ""))
            session = store.append_event(
                f"understanding:{source_message_id}",
                "project_understanding_ready",
                content=content,
                details={
                    "source_message_id": source_message_id,
                    "artifact_ref": artifact_ref,
                },
            )
        return session

    def _resolve_confident_mode(
        self,
        project_key: str,
        root: Path,
        state: dict[str, object],
        hint: dict[str, object],
    ) -> dict[str, object]:
        if (
            state.get("project_mode") == "unconfirmed"
            and hint.get("confidence") == "high"
            and hint.get("mode") in {"new_project", "existing_project"}
        ):
            return self.confirm_mode(self.workspace, project_key, root, str(hint["mode"]))
        return state

    @staticmethod
    def _with_readiness_attention(
        session: dict[str, object],
        readiness: dict[str, object],
    ) -> dict[str, object]:
        execution = readiness.get("execution")
        if not isinstance(execution, dict) or execution.get("status") != "needs_input":
            return session
        step_id = str(execution.get("current_step_id", ""))
        step = next(
            (
                item for item in readiness.get("steps", [])
                if isinstance(item, dict) and item.get("id") == step_id
            ),
            {},
        )
        requirements = step.get("input_requirements")
        if not isinstance(requirements, dict):
            return session
        return {
            **session,
            "attention_request": {
                "id": f"project-preparation:{execution.get('id', '')}:{step_id}",
                "kind": "project_preparation",
                "reason": str(requirements.get("kind", "project_preparation")),
                "step_id": step_id,
                "input_requirements": dict(requirements),
                "actions": ["continue_project_preparation"],
            },
        }


def classify_task_assistant_intent(text: str) -> str:
    normalized = f" {str(text).strip().lower()} "
    if any(term in normalized for term in _WORK_TERMS):
        return "work_request"
    if any(term in normalized for term in _UNDERSTANDING_TERMS):
        return "project_understanding"
    return "ambiguous"


# ----------------------------------------------------- team command detection

_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "research": ("research", "researcher", "调研", "研究员"),
    "product_requirement": ("product requirement", "product", "pm", "产品", "需求"),
    "architect": ("architect", "架构师", "架构"),
    "review": ("review", "审查"),
    "planner": ("planner", "计划"),
    "ui": ("ui", "designer", "设计"),
    "implementation": ("implementation", "builder", "实现", "开发"),
    "frontend": ("frontend", "front end", "前端"),
    "backend": ("backend", "back end", "后端"),
    "qa": ("qa",),
    "code_review": ("code review", "code_review", "code reviewer", "代码审查", "代码评审", "评审"),
    "release": ("release", "发布"),
    "retro_memory": ("retro", "memory", "复盘", "记忆"),
}
# Person/capability suffixes that keep an add-worker phrase role-shaped
# (“加个研究员”, “add a qa engineer”) while rejecting object nouns
# (“添加一个开发文档”).
_ROLE_PERSON_SUFFIXES = ("师", "员", "工程师", "人员", "worker", "engineer", "capability", "role", "能力")

_DISABLE_ROLE_RE = re.compile(
    r"(?:把|将)?\s*(?P<target>[\w /-]+?)\s*(?:以后)?(?:都)?(?:不要|关掉|关闭|禁用|停用|去掉|disable|turn off)"
    r"|(?:disable|turn off|remove)\s+(?P<target_en>[\w /-]+)",
    re.IGNORECASE,
)
_ENABLE_ROLE_RE = re.compile(
    r"(?:启用|开启|加回|enable|turn on)\s*(?P<target>[\w /-]+)"
    r"|(?:把|将)?\s*(?P<target2>[\w /-]+?)\s*(?:启用|开启|enable)",
    re.IGNORECASE,
)
_ADD_WORKER_RE = re.compile(
    r"(?:加个|加一个|添加一个|增加一个|新增一个|加一名|hire|add)\s*(?P<target>[\w /-]+)",
    re.IGNORECASE,
)
_PAUSE_WORKER_RE = re.compile(
    r"(?:暂停|停掉|pause|inactivate|deactivate)\s*(?P<target>[\w /-]+)"
    r"|(?:把|将)\s*(?P<target2>[\w /-]+?)\s*(?:暂停|停掉|pause)",
    re.IGNORECASE,
)
_RESUME_WORKER_RE = re.compile(
    r"(?:恢复|激活|resume|reactivate|activate)\s*(?P<target>[\w /-]+)"
    r"|(?:让|把)\s*(?P<target2>[\w /-]+?)\s*(?:恢复|激活|resume|reactivate|activate)",
    re.IGNORECASE,
)
_PROVIDER_RE = re.compile(r"\b(codex|claude|anthropic|gemini|opencode|agy|antigravity|pi|fake|noop|human)\b", re.IGNORECASE)
# A preference scoped to the current task/run must never persist as a Team
# mutation; both the deterministic detector and the Manager contract guard it.
_TASK_SCOPED_MARKERS = (
    "这次", "本次", "这一轮", "这轮", "这个任务", "此任务", "这个需求",
    "this time", "this run", "this task", "just this", "for now only",
)
_PERSISTENT_MARKERS = ("以后", "长期", "项目里", "团队", "from now on", "always", "permanently", "team")


@dataclass
class TeamCommandDetection:
    commands: list[dict[str, object]] = field(default_factory=list)
    reply: str = ""


def _is_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _match_role(target: str) -> str:
    cleaned = str(target or "").strip().lower().strip("把将的")
    if not cleaned:
        return ""
    for role, aliases in _ROLE_ALIASES.items():
        for alias in aliases:
            if alias in cleaned:
                return role
    return ""


def _match_role_strict(target: str) -> str:
    """Role match that only accepts role-shaped phrases.

    Add-worker utterances must name a role or role+person suffix exactly;
    object nouns that merely contain a role word (“开发文档”) never match.
    """
    cleaned = str(target or "").strip().lower()
    for article in ("a ", "an ", "one ", "一个", "一名", "一名"):
        if cleaned.startswith(article):
            cleaned = cleaned[len(article):]
    cleaned = cleaned.strip(" 的")
    for role, aliases in _ROLE_ALIASES.items():
        for alias in aliases:
            if cleaned == alias:
                return role
            if cleaned.startswith(alias) and cleaned[len(alias):].strip() in _ROLE_PERSON_SUFFIXES:
                return role
    return ""


def _match_worker_name(target: str, team_view: dict[str, object] | None) -> dict[str, object] | None:
    if team_view is None:
        return None
    cleaned = str(target or "").strip().lower()
    if not cleaned:
        return None
    for role_entry in list(team_view.get("roles") or []):
        for worker in list(dict(role_entry).get("workers") or []):
            name = str(dict(worker).get("name", "")).lower()
            identifier = str(dict(worker).get("id", "")).lower()
            if cleaned and (cleaned in name or cleaned == identifier):
                return dict(worker)
    return None


def detect_team_commands(text: str, team_view: dict[str, object] | None) -> TeamCommandDetection | None:
    """Deterministic detection of explicitly requested persistent Team changes.

    This bounded parser covers direct utterances like “加个 Researcher”,
    “把 Research 关掉”, “暂停 Builder A”, or “让 Reviewer 改用 claude” without
    a model. It never maps task-scoped preferences to team mutations and never
    invents commands from manager-side suggestions.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if any(marker in lowered or marker in raw for marker in _TASK_SCOPED_MARKERS):
        return None

    cjk = _is_cjk(raw)

    pause = _PAUSE_WORKER_RE.search(raw)
    if pause:
        target = pause.group("target") or pause.group("target2") or ""
        worker = _match_worker_name(target, team_view)
        if worker is not None:
            return TeamCommandDetection(
                commands=[{"kind": "inactivate_worker", "worker_id": worker["id"]}],
                reply=(f"好的，已暂停 {worker['name']}。" if cjk else f"Paused {worker['name']}."),
            )

    resume = _RESUME_WORKER_RE.search(raw)
    if resume:
        target = resume.group("target") or resume.group("target2") or ""
        worker = _match_worker_name(target, team_view)
        if worker is not None:
            return TeamCommandDetection(
                commands=[{"kind": "activate_worker", "worker_id": worker["id"]}],
                reply=(f"好的，已重新激活 {worker['name']}。" if cjk else f"Reactivated {worker['name']}."),
            )

    provider = _PROVIDER_RE.search(raw)
    if provider and re.search(r"(?:改用|换成|换用|改成|切换到|switch to|change to|use)", lowered):
        provider_value = provider.group(1).lower()
        provider_value = "anthropic" if provider_value == "claude" else provider_value
        worker = None
        for candidate in re.finditer(r"(?:让|把)\s*([\w /-]{2,}?)\s*(?:改用|换成|换用|改成|切换到)", raw):
            worker = _match_worker_name(candidate.group(1), team_view)
            if worker is not None:
                break
        if worker is not None:
            return TeamCommandDetection(
                commands=[{"kind": "change_worker_runtime", "worker_id": worker["id"], "provider": provider_value}],
                reply=(
                    f"好的，已让 {worker['name']} 改用 {provider_value}。"
                    if cjk
                    else f"Switched {worker['name']} to {provider_value}."
                ),
            )

    add = _ADD_WORKER_RE.search(raw)
    if add:
        role = _match_role_strict(add.group("target"))
        if role and role != MANAGER_ROLE:
            label = role_label(role)
            return TeamCommandDetection(
                commands=[{"kind": "initialize_worker", "role": role, "ensure_role_enabled": True}],
                reply=(
                    f"好的，已为团队添加一名 {label} Worker。"
                    if cjk
                    else f"Added a new {label} worker to the team."
                ),
            )

    disable = _DISABLE_ROLE_RE.search(raw)
    if disable:
        role = _match_role(disable.group("target") or disable.group("target_en") or "")
        if role and role != MANAGER_ROLE:
            label = role_label(role)
            return TeamCommandDetection(
                commands=[{"kind": "disable_role", "role": role}],
                reply=(
                    f"好的，已禁用 {label} 能力，相关 Worker 已暂停。"
                    if cjk
                    else f"Disabled the {label} capability; its active workers are paused."
                ),
            )

    enable = _ENABLE_ROLE_RE.search(raw)
    if enable:
        role = _match_role(enable.group("target") or enable.group("target2") or "")
        if role and role != MANAGER_ROLE:
            label = role_label(role)
            return TeamCommandDetection(
                commands=[{"kind": "enable_role", "role": role}],
                reply=(
                    f"好的，已启用 {label} 能力。"
                    if cjk
                    else f"Enabled the {label} capability."
                ),
            )
    return None


def detect_task_scoped_team_preference(text: str) -> bool:
    """True when the message scopes a capability preference to the current task."""
    raw = str(text or "").strip()
    lowered = raw.lower()
    scoped = any(marker in lowered or marker in raw for marker in _TASK_SCOPED_MARKERS)
    mentions_capability = any(
        alias in raw.lower() or alias in raw
        for aliases in _ROLE_ALIASES.values()
        for alias in aliases
    )
    return scoped and mentions_capability


def _format_repository_understanding(content: dict[str, object]) -> str:
    def strings(values: object, *, key: str = "") -> list[str]:
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for value in values[:12]:
            if isinstance(value, dict) and key:
                text = str(value.get(key, "") or "").strip()
            else:
                text = str(value or "").strip()
            if text:
                result.append(text)
        return result

    sections = [
        ("技术栈 / Stack", strings(content.get("technology_stack"))),
        ("入口 / Entry points", strings(content.get("entry_points"))),
        ("命令 / Commands", strings(content.get("commands"), key="command")),
        ("约束 / Constraints", strings(content.get("constraints"))),
        ("风险 / Risks", strings(content.get("risks"))),
    ]
    lines = [f"{label}: {', '.join(values)}" for label, values in sections if values]
    return "\n".join(lines) or "项目扫描已完成，但没有发现可总结的项目元数据。"


def _manager_chat_context(
    readiness: dict[str, object],
    mode_hint: dict[str, object],
    session: dict[str, object],
    *,
    team_view: dict[str, object] | None = None,
) -> str:
    recent = []
    for item in list(session.get("timeline", []) or [])[-12:]:
        if not isinstance(item, dict):
            continue
        recent.append(
            {
                "actor": str(item.get("actor", "")),
                "event": str(item.get("event", "")),
                "content": str(item.get("content", ""))[:2_000],
            }
        )
    proposal = dict(session.get("proposed_task") or {})
    lines = [
        f"Project mode: {readiness.get('project_mode', mode_hint.get('mode', 'unconfirmed'))}",
        f"Readiness lifecycle: {readiness.get('lifecycle', '')}",
        f"Recent conversation: {json.dumps(recent, ensure_ascii=False)}",
        f"Pending proposal: {json.dumps(proposal, ensure_ascii=False)}",
    ]
    if team_view is not None:
        lines.append(f"Team capability summary: {json.dumps(_compact_team_view(team_view), ensure_ascii=False)}")
    return "\n".join(lines)


def _compact_team_view(team_view: dict[str, object]) -> dict[str, object]:
    """Normalized capability facts for planning; not full team configuration."""
    roles = []
    for entry in list(team_view.get("roles") or []):
        role = dict(entry)
        roles.append(
            {
                "role": role.get("role", ""),
                "state": role.get("state", ""),
                "capability": role.get("capability", ""),
                "workers": [
                    {
                        "id": worker.get("id", ""),
                        "name": worker.get("name", ""),
                        "lifecycle": worker.get("lifecycle", ""),
                        "dispatchable": worker.get("dispatchable", False),
                    }
                    for worker in list(role.get("workers") or [])
                ],
            }
        )
    manager = dict(team_view.get("manager") or {})
    return {
        "revision": team_view.get("revision", 0),
        "roles": roles,
        "manager": {"role": manager.get("role", ""), "runtime": dict(manager.get("runtime") or {})},
    }
