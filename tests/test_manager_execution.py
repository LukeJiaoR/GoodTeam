import json
from pathlib import Path
import pytest

from goodteam_os import manager_execution


def _project_config(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = tmp_path / "demo.local.yaml"
    config.write_text(
        "\n".join(
            [
                "name: Demo",
                "key: DEMO",
                "repos:",
                f"  root: {project_root}",
                "worker_backend:",
                "  mode: codex",
                "workers:",
                "  orchestrator: claude",
                "role_model_overrides:",
                "  orchestrator: claude:claude-opus-4-5",
                "worker_type_settings:",
                "  orchestrator:",
                "    reasoning_depth: high",
                '    additional_prompt: "Prefer concise risk language."',
                "    desired_instances: 7",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config


def test_manager_interpretation_runs_one_configured_orchestrator_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeStore:
        def __init__(self, project_root: Path) -> None:
            captured["project_root"] = project_root
            self.root = project_root / ".goodteam" / "workers" / "jobs"

        def create_or_reuse_job(self, **spec):
            captured["spec"] = spec
            job_root = self.root / "job-manager"
            job_root.mkdir(parents=True)
            return {"id": "job-manager", "status": "queued"}, True

        def run_job(self, job_id: str):
            result_path = self.root / job_id / "result.md"
            result_path.write_text(
                "```json\n"
                + json.dumps(
                    {
                        "summary": "Keep Google as the only provider and retain the abstraction.",
                        "items": [
                            "Ship Google only.",
                            "Keep the provider interface generic.",
                        ],
                    }
                )
                + "\n```\n\n## Learned\n\n- The request is intentionally narrow.\n",
                encoding="utf-8",
            )
            return {
                "status": "completed",
                "result_path": str(result_path),
                "runtime_model": "claude-opus-4-5",
            }

    monkeypatch.setattr(manager_execution, "WorkerJobStore", FakeStore)
    request = "Ship Google only.\nKeep the provider interface generic."

    interpretation = manager_execution.run_manager_interpretation(
        project_config=_project_config(tmp_path),
        run_id="run-1",
        project_key="DEMO",
        intent_id="intent-1",
        change_request=request,
        context="Approval: PRD",
    )

    assert captured["project_root"] == tmp_path / "project"
    spec = captured["spec"]
    assert spec["role"] == "orchestrator"
    assert spec["adapter"] == "claude"
    assert spec["model"] == "claude-opus-4-5"
    assert spec["reasoning_depth"] == "high"
    assert spec["additional_prompt"] == "Prefer concise risk language."
    assert spec["desired_instances"] == 1
    assert spec["execution_policy"] == "read_only"
    assert spec["idempotency_key"] == "manager-intent:intent-1"
    assert request in spec["task"]
    assert interpretation == {
        "schema_version": "intent_interpretation.v1",
        "source": "manager_worker",
        "summary": "Keep Google as the only provider and retain the abstraction.",
        "items": [
            {"kind": "explicit_request", "text": "Ship Google only."},
            {"kind": "explicit_request", "text": "Keep the provider interface generic."},
        ],
        "request": request,
        "manager_execution": {
            "job_id": "job-manager",
            "role": "orchestrator",
            "adapter": "claude",
            "model": "claude-opus-4-5",
            "reasoning_depth": "high",
            "execution_policy": "read_only",
            "runtime_sandbox_mode": "unknown",
            "runtime_approval_policy": "unknown",
            "runtime_unsafe_mode": True,
            "runtime_policy_warning": True,
            "runtime_policy_warning_reason": "Runtime policy evidence was unavailable.",
        },
    }


def test_manager_interpretation_fails_when_worker_does_not_return_structured_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStore:
        def __init__(self, project_root: Path) -> None:
            self.root = project_root / ".goodteam" / "workers" / "jobs"

        def create_or_reuse_job(self, **spec):
            job_root = self.root / "job-manager"
            job_root.mkdir(parents=True)
            return {"id": "job-manager", "status": "queued"}, True

        def run_job(self, job_id: str):
            result_path = self.root / job_id / "result.md"
            result_path.write_text("I think this looks fine.", encoding="utf-8")
            return {"status": "completed", "result_path": str(result_path)}

    monkeypatch.setattr(manager_execution, "WorkerJobStore", FakeStore)

    with pytest.raises(manager_execution.ManagerExecutionError, match="structured interpretation"):
        manager_execution.run_manager_interpretation(
            project_config=_project_config(tmp_path),
            run_id="run-1",
            project_key="DEMO",
            intent_id="intent-1",
            change_request="Ship Google only.",
            context="Approval: PRD",
        )


def test_manager_chat_turn_runs_configured_orchestrator_and_returns_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeStore:
        def __init__(self, project_root: Path) -> None:
            self.root = project_root / ".goodteam" / "workers" / "jobs"

        def create_or_reuse_job(self, **spec):
            captured["spec"] = spec
            (self.root / "job-chat").mkdir(parents=True)
            return {"id": "job-chat", "status": "queued"}, True

        def run_job(self, job_id: str):
            result_path = self.root / job_id / "result.md"
            result_path.write_text(
                json.dumps(
                    {
                        "intent": "work_request",
                        "response": "我会先把登录问题整理成一个可确认的任务。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return {
                "status": "completed",
                "result_path": str(result_path),
                "runtime_model": "claude-opus-4-5",
            }

    monkeypatch.setattr(manager_execution, "WorkerJobStore", FakeStore)

    turn = manager_execution.run_manager_chat_turn(
        project_config=_project_config(tmp_path),
        project_key="DEMO",
        message_id="message-1",
        text="登录",
        context="Project mode: existing_project",
    )

    spec = captured["spec"]
    assert spec["role"] == "orchestrator"
    assert spec["adapter"] == "claude"
    assert spec["model"] == "claude-opus-4-5"
    assert spec["reasoning_depth"] == "high"
    assert spec["additional_prompt"] == "Prefer concise risk language."
    assert spec["desired_instances"] == 1
    assert spec["phase"] == "task_assistant_chat"
    assert spec["execution_policy"] == "read_only"
    assert spec["idempotency_key"] == "task-assistant-turn:message-1"
    assert turn["intent"] == "work_request"
    assert turn["response"] == "我会先把登录问题整理成一个可确认的任务。"
    assert turn["manager_execution"]["job_id"] == "job-chat"


def test_manager_chat_turn_resolves_default_team_setting_to_explicit_supported_reasoning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A default Team runtime setting must still resolve a valid runtime.

    ``default`` is not a worker-executable reasoning value; ``_manager_settings``
    maps it to ``high``, the current documented portable default. That literal
    is an implementation choice that may vary by provider/model later — the
    contract under test is (1) the resolved job never runs with the raw
    ``default`` value, and (2) the reported execution evidence matches the
    runtime the job actually used.
    """
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = tmp_path / "default.local.yaml"
    config.write_text(
        f"key: DEMO\nrepos:\n  root: {project_root}\nworker_backend:\n  mode: codex\n",
        encoding="utf-8",
    )
    captured = {}

    class FakeStore:
        def __init__(self, root: Path) -> None:
            self.root = root / ".goodteam" / "workers" / "jobs"

        def create_or_reuse_job(self, **spec):
            captured.update(spec)
            (self.root / "job-chat").mkdir(parents=True)
            return {"id": "job-chat", "status": "queued"}, True

        def run_job(self, job_id: str):
            result_path = self.root / job_id / "result.md"
            result_path.write_text(
                '{"intent":"ambiguous","response":"请再说明一下目标。"}',
                encoding="utf-8",
            )
            return {"status": "completed", "result_path": str(result_path)}

    monkeypatch.setattr(manager_execution, "WorkerJobStore", FakeStore)

    turn = manager_execution.run_manager_chat_turn(
        project_config=config,
        project_key="DEMO",
        message_id="message-default",
        text="登录",
        context="Project mode: existing_project",
    )

    # Current portable default in `_manager_settings`; not a safety property.
    assert captured["reasoning_depth"] == "high"
    assert turn["manager_execution"]["reasoning_depth"] == captured["reasoning_depth"]


def test_manager_chat_turn_resolves_orchestrator_model_from_selected_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = tmp_path / "profile.local.yaml"
    config.write_text(
        f"key: DEMO\nrepos:\n  root: {project_root}\nmodel_profile: anthropic\nworker_backend:\n  mode: anthropic\n",
        encoding="utf-8",
    )
    captured = {}

    class FakeStore:
        def __init__(self, root: Path) -> None:
            self.root = root / ".goodteam" / "workers" / "jobs"

        def create_or_reuse_job(self, **spec):
            captured.update(spec)
            (self.root / "job-chat").mkdir(parents=True)
            return {"id": "job-chat", "status": "queued"}, True

        def run_job(self, job_id: str):
            result_path = self.root / job_id / "result.md"
            result_path.write_text(
                '{"intent":"ambiguous","response":"请再说明一下目标。"}',
                encoding="utf-8",
            )
            return {"status": "completed", "result_path": str(result_path)}

    monkeypatch.setattr(manager_execution, "WorkerJobStore", FakeStore)

    manager_execution.run_manager_chat_turn(
        project_config=config,
        project_key="DEMO",
        message_id="message-profile",
        text="登录",
        context="Project mode: existing_project",
    )

    assert captured["adapter"] == "anthropic"
    assert captured["model"] == "opus"


def test_manager_chat_turn_reuses_completed_job_for_message_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs: dict[str, dict[str, object]] = {}
    run_calls = []

    class FakeStore:
        def __init__(self, project_root: Path) -> None:
            self.root = project_root / ".goodteam" / "workers" / "jobs"

        def create_or_reuse_job(self, **spec):
            key = str(spec["idempotency_key"])
            if key in jobs:
                return jobs[key], False
            job_root = self.root / "job-chat"
            job_root.mkdir(parents=True)
            job = {"id": "job-chat", "status": "queued", **spec}
            jobs[key] = job
            return job, True

        def run_job(self, job_id: str):
            run_calls.append(job_id)
            result_path = self.root / job_id / "result.md"
            result_path.write_text(
                '{"intent":"work_request","response":"已整理为待确认任务。"}',
                encoding="utf-8",
            )
            result = {"id": job_id, "status": "completed", "result_path": str(result_path)}
            jobs["task-assistant-turn:message-retry"] = result
            return result

    monkeypatch.setattr(manager_execution, "WorkerJobStore", FakeStore)
    values = {
        "project_config": _project_config(tmp_path),
        "project_key": "DEMO",
        "message_id": "message-retry",
        "text": "修复登录",
        "context": "Project mode: existing_project",
    }

    first = manager_execution.run_manager_chat_turn(**values)
    repeated = manager_execution.run_manager_chat_turn(**values)

    assert repeated == first
    assert run_calls == ["job-chat"]


def test_project_question_is_a_valid_read_only_chat_intent():
    from goodteam_os.manager_execution import _manager_chat_task, _parse_chat_turn

    parsed = _parse_chat_turn('{"intent":"project_question","response":"This project uses Git."}')
    assert parsed == {"intent": "project_question", "response": "This project uses Git."}
    prompt = _manager_chat_task("他有git吗")
    assert "git rev-parse --is-inside-work-tree" in prompt
    assert "do not initialize, fetch, commit" in prompt
    assert "current project" in prompt
