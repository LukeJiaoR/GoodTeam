from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from goodteam_os.manager_execution import run_manager_chat_turn
from goodteam_os.project_import import confirm_project_mode, import_project
from goodteam_os.project_readiness import assess_project_mode
from goodteam_os.project_readiness_executor import ProjectReadinessExecutor
from goodteam_os.task_assistant_session import TaskAssistantSessionStore, initial_task_assistant_session


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
        if self.project_config is not None:
            turn = self.manager_turn(
                project_config=self.project_config,
                project_key=project_key,
                message_id=message_id,
                text=str(text),
                context=_manager_chat_context(state, mode_hint, store.read()),
            )
            intent = str(turn.get("intent", ""))
            manager_response = str(turn.get("response", ""))
            manager_execution = dict(turn.get("manager_execution") or {})
        else:
            intent = classify_task_assistant_intent(text)

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
    return "\n".join(
        [
            f"Project mode: {readiness.get('project_mode', mode_hint.get('mode', 'unconfirmed'))}",
            f"Readiness lifecycle: {readiness.get('lifecycle', '')}",
            f"Recent conversation: {json.dumps(recent, ensure_ascii=False)}",
            f"Pending proposal: {json.dumps(proposal, ensure_ascii=False)}",
        ]
    )
