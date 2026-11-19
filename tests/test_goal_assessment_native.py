import json

import pytest

from test_manager_completion_planning import native_manager  # noqa: F401
from goodteam_os.goal_assessment import (
    CompletedAssessmentProtocolError, assess_goal, assessment_identity, verify_completed_assessment,
)
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


def test_native_goal_assessment_keeps_literal_evidence_and_bounded_retry_identity(native_manager):  # noqa: F811
    workspace, root, config, response, calls = native_manager
    goal = {"id": "goal-1", "project_key": "DEMO", "objective": "Keep the original feature working",
            "acceptance_criteria": ["Feature works"], "allowed_scope": ["Original feature"],
            "authority_snapshot": {"project_config": str(config), "project_root": str(root)}}
    work = {"id": "work-1", "run_id": "original-run", "criterion_indices": [0]}
    evidence = {"run:original-run:qa": "Feature works with the current regression check."}
    response.write_text(json.dumps({"requires_new_scope": False, "checks": [
        {"criterion": 0, "status": "met", "evidence_ref": "run:original-run:qa", "quote": "Feature works"}]}))
    kwargs = dict(workspace=workspace, project_config=config, project_root=root, goal=goal, work=work,
                  evidence=evidence, execution_ref={"project_key": "DEMO", "run_id": "original-run"})
    first = assess_goal(**kwargs)
    assert first["accepted_criteria"] == [0]
    before = calls.read_bytes()
    assert assess_goal(**kwargs) == first
    assert calls.read_bytes() == before

    response.write_text("A completed but malformed assessment")
    with pytest.raises(CompletedAssessmentProtocolError) as failure:
        assess_goal(**kwargs, attempt=2)
    native_id = failure.value.job_id
    assert native_id.startswith("codex:") and failure.value.code == "invalid_json"
    verify_completed_assessment(root, goal, work, 2, native_id, evidence)
    session = TaskAssistantSessionStore(workspace, "DEMO")
    def tamper(record):
        record["manager_app_server"]["requests"][assessment_identity(goal, work, 2)]["context"] = "{}"
        return record
    session._mutate(tamper)
    with pytest.raises(ValueError, match="cannot be proven"):
        verify_completed_assessment(root, goal, work, 2, native_id, evidence)
    with pytest.raises(ValueError, match="hard bound"):
        assess_goal(**kwargs, attempt=4)
