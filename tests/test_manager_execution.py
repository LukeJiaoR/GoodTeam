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

        def create_job(self, **spec):
            captured["spec"] = spec
            job_root = self.root / "job-manager"
            job_root.mkdir(parents=True)
            return {"id": "job-manager"}

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
        },
    }


def test_manager_interpretation_fails_when_worker_does_not_return_structured_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStore:
        def __init__(self, project_root: Path) -> None:
            self.root = project_root / ".goodteam" / "workers" / "jobs"

        def create_job(self, **spec):
            job_root = self.root / "job-manager"
            job_root.mkdir(parents=True)
            return {"id": "job-manager"}

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
