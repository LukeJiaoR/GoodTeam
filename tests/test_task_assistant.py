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
