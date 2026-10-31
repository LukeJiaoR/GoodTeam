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
                        "task_intake": {"ready": True, "missing_decisions": [], "source_message_ids": ["message-1"]},
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
                json.dumps({"intent": "work_request", "response": "已整理为待确认任务。",
                            "task_intake": {"ready": True, "missing_decisions": [], "source_message_ids": ["message-retry"]}}),
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

