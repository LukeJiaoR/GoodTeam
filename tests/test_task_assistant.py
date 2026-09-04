from importlib import import_module


class FakeReadiness:
    def __init__(self, state):
        self.state = state
        self.started = []

    def status(self, project_key, root):
        return self.state

    def start(self, project_key, root, inputs, *, idempotency_key):
        self.started.append((project_key, root, inputs, idempotency_key))
        return self.state


def test_session_projects_readiness_input_as_task_assistant_attention(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    executor = FakeReadiness({
        **readiness(),
        "lifecycle": "attention_required",
        "execution": {"id": "prep-1", "status": "needs_input", "current_step_id": "command_verification"},
        "steps": [{
            "id": "command_verification",
            "status": "needs_input",
            "input_requirements": {"kind": "command_confirmation", "commands": [{"id": "test", "command": "npm test"}]},
        }],
    })
    service = service_type()(tmp_path / "workspace", readiness_executor=executor)

    session = service.session("DEMO", root)

    assert session["attention_request"]["kind"] == "project_preparation"
    assert session["attention_request"]["step_id"] == "command_verification"
    assert session["attention_request"]["input_requirements"]["commands"][0]["command"] == "npm test"


def service_type():
    return import_module("goodteam_os.task_assistant").TaskAssistantService


def readiness(mode="existing_project", *, ready=False):
    return {
        "project_mode": mode,
        "lifecycle": "ready" if ready else "preparing",
        "schema_version": 2,
        "steps": [],
    }


def test_project_question_starts_understanding_without_proposing_workflow(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    executor = FakeReadiness(readiness())
    service = service_type()(tmp_path / "workspace", readiness_executor=executor)

    result = service.send_message(
        project_key="DEMO",
        root=root,
        text="看一下当前的项目",
        message_id="message-1",
    )

    assert result["intent"] == "project_understanding"
    assert result["session"]["proposed_task"] is None
    assert result["session"]["timeline"][-1]["event"] == "project_inspection_started"
    assert len(executor.started) == 1


def test_actionable_request_becomes_editable_proposal_without_auto_start(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    executor = FakeReadiness(readiness(ready=True))
    service = service_type()(tmp_path / "workspace", readiness_executor=executor)

    result = service.send_message(
        project_key="DEMO",
        root=root,
        text="修复登录问题，并补充回归测试",
        message_id="message-2",
    )

    assert result["intent"] == "work_request"
    assert result["workflow_started"] is False
    assert result["session"]["proposed_task"]["exact_request"] == "修复登录问题，并补充回归测试"
    assert result["session"]["timeline"][-1]["event"] == "task_proposed"


def test_actionable_request_prepares_project_while_waiting_for_confirmation(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    executor = FakeReadiness(readiness())
    service = service_type()(tmp_path / "workspace", readiness_executor=executor)

    service.send_message(
        project_key="DEMO",
        root=root,
        text="实现账号设置页",
        message_id="message-prepare",
    )

    assert executor.started == [
        ("DEMO", root, {}, "task-assistant:message-prepare"),
    ]


def test_ambiguous_message_gets_follow_up_instead_of_consequential_action(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    executor = FakeReadiness(readiness("unconfirmed"))
    service = service_type()(tmp_path / "workspace", readiness_executor=executor)

    result = service.send_message(
        project_key="DEMO",
        root=root,
        text="登录",
        message_id="message-3",
    )

    assert result["intent"] == "ambiguous"
    assert result["session"]["timeline"][-1]["event"] == "intent_clarification_requested"
    assert executor.started == []


def test_configured_task_assistant_model_drives_primary_chat_intent(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    config = tmp_path / "demo.local.yaml"
    config.write_text("key: DEMO\n", encoding="utf-8")
    executor = FakeReadiness(readiness(ready=True))
    calls = []

    def manager_turn(**values):
        calls.append(values)
        return {
            "intent": "work_request",
            "response": "我会把它整理成一个待确认任务。",
            "manager_execution": {"job_id": "job-chat"},
        }

    service = service_type()(
        tmp_path / "workspace",
        readiness_executor=executor,
        project_config=config,
        manager_turn=manager_turn,
    )

    result = service.send_message(
        project_key="DEMO",
        root=root,
        text="登录",
        message_id="message-model",
    )

    assert result["intent"] == "work_request"
    assert result["session"]["proposed_task"]["exact_request"] == "登录"
    assert result["session"]["timeline"][-1]["content"] == "我会把它整理成一个待确认任务。"
    assert calls[0]["project_config"] == config
    assert calls[0]["message_id"] == "message-model"
    assert "existing_project" in calls[0]["context"]


def test_first_message_initializes_missing_legacy_readiness(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    config = tmp_path / "demo.local.yaml"
    config.write_text("key: DEMO\n", encoding="utf-8")
    initialized = []

    class MissingReadiness:
        def status(self, project_key, project_root):
            raise ValueError(f"No readiness found for project: {project_key}")

    def initialize_existing(workspace, root_value, *, existing_configs):
        initialized.append((workspace, root_value, existing_configs))
        return {"readiness": readiness(ready=True)}

    module = import_module("goodteam_os.task_assistant")
    monkeypatch.setattr(module, "import_project", initialize_existing)
    service = service_type()(
        tmp_path / "workspace",
        readiness_executor=MissingReadiness(),
        project_config=config,
        manager_turn=lambda **values: {
            "intent": "work_request",
            "response": "我会整理成任务。",
        },
    )

    result = service.send_message(
        project_key="DEMO",
        root=root,
        text="登录",
        message_id="legacy-message",
    )

    assert result["intent"] == "work_request"
    assert initialized == [(tmp_path / "workspace", str(root), [config])]


def test_session_returns_completed_repository_understanding_as_message(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    artifact_ref = "readiness://execution-1/1/repository_understanding"
    artifact = {
        "id": artifact_ref,
        "content": {
            "technology_stack": ["Python"],
            "entry_points": ["src/app.py"],
            "commands": [{"command": "pytest"}],
            "constraints": ["Keep APIs stable."],
            "risks": ["No CI configuration was found."],
        },
    }

    class CompletedUnderstanding:
        def status(self, project_key, project_root):
            return {
                **readiness(),
                "steps": [{
                    "id": "repository_understanding",
                    "status": "complete",
                    "artifact_refs": [artifact_ref],
                }],
            }

        def artifact(self, project_key, project_root, requested_ref):
            assert requested_ref == artifact_ref
            return artifact

    workspace = tmp_path / "workspace"
    store = import_module("goodteam_os.task_assistant_session").TaskAssistantSessionStore(workspace, "DEMO")
    store.append_message("message-1", "user", "了解当前项目")
    store.append_event("reply:message-1", "project_inspection_started")
    service = service_type()(workspace, readiness_executor=CompletedUnderstanding())

    first = service.session("DEMO", root)
    repeated = service.session("DEMO", root)

    assert first["timeline"][-1]["event"] == "project_understanding_ready"
    assert "Python" in first["timeline"][-1]["content"]
    assert "src/app.py" in first["timeline"][-1]["content"]
    assert "pytest" in first["timeline"][-1]["content"]
    assert repeated == first


def test_manager_chat_receives_recent_conversation_and_pending_proposal(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    config = tmp_path / "demo.local.yaml"
    config.write_text("key: DEMO\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    store = import_module("goodteam_os.task_assistant_session").TaskAssistantSessionStore(workspace, "DEMO")
    store.append_message("message-1", "user", "实现登录，可选邮箱或手机号")
    store.append_event("reply:message-1", "task_proposed", content="请选择一种登录方式。")
    store.propose_task(["message-1"], "实现登录，可选邮箱或手机号")
    calls = []

    def manager_turn(**values):
        calls.append(values)
        return {"intent": "ambiguous", "response": "你指的是邮箱登录吗？"}

    service = service_type()(
        workspace,
        readiness_executor=FakeReadiness(readiness(ready=True)),
        project_config=config,
        manager_turn=manager_turn,
    )

    service.send_message(
        project_key="DEMO",
        root=root,
        text="就用第一个",
        message_id="message-2",
    )

    assert "实现登录，可选邮箱或手机号" in calls[0]["context"]
    assert "请选择一种登录方式。" in calls[0]["context"]
    assert "Pending proposal" in calls[0]["context"]
