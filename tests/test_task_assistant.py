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
