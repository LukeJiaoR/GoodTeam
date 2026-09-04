from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from goodteam_os.project_import import confirm_project_mode
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
    ):
        self.workspace = Path(workspace)
        self.readiness = readiness_executor or ProjectReadinessExecutor(self.workspace)
        self.confirm_mode = confirm_mode

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
        intent = classify_task_assistant_intent(text)
        state = self.readiness.status(project_key, root)

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
                details={"readiness_lifecycle": str(state.get("lifecycle", ""))},
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
                details={"proposal_id": str(dict(session.get("proposed_task") or {}).get("id", ""))},
            )
        else:
            session = store.append_event(f"reply:{message_id}", "intent_clarification_requested")

        return {
            "task_assistant_session": session,
            "session": session,
            "intent": intent,
            "workflow_started": False,
            "run_id": "",
        }

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
