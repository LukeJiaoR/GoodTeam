"""Advisory recommendation tests with model-free, exactly bound reviewer records."""

from copy import deepcopy

import pytest

from goodteam_os import telegram, web
from goodteam_os.state import StateStore
from test_workflow_recovery import gate_state


@pytest.fixture
def review_case(tmp_path, monkeypatch):
    state = gate_state(tmp_path, "security")
    key = web._approval_artifact_for_gate("security")
    path = state.artifacts[key]
    profiles = ["correctness-security", "architecture-maintainability"]
    target = {"reviewer_profiles": {"implementation": profiles}}
    jobs = [{"id": f"review-{index}", "run_id": state.run_id, "role": "code_review",
             "approval": "security", "profile_id": profile, "status": "completed", "adapter": "codex",
             "artifact_key": key, "artifact_path": path, "artifact_version": web.artifact_version_for_path(path),
             "updated_at": f"2026-10-10T23:0{2 - index}:00Z",
             "invocation_evidence": {"kind": "provider", "external_invocation": True}}
            for index, profile in enumerate(profiles)]
    monkeypatch.setattr(web, "_reviewer_target_for_state", lambda **_: target)
    monkeypatch.setattr(web, "_reviewer_statuses_for_state", lambda **_: jobs)

    def read():
        return next(item for item in web.build_approval_reviews(state, workspace=tmp_path)
                    if item["approval"] == "security")

    return state, jobs, profiles, target, read


@pytest.mark.parametrize("case,verdict", [
    ("failed", "caution"), ("missing", "caution"), ("queued", "caution"),
    ("all_completed", "approve"), ("single_configured", "approve"),
    ("recovered", "approve"), ("stale_failure", "approve"),
])
def test_recommendation_respects_every_current_configured_reviewer(tmp_path, review_case, case, verdict):
    state, jobs, profiles, _target, read = review_case
    if case in {"failed", "queued"}:
        jobs[1]["status"] = case
    elif case == "missing":
        jobs.pop()
    elif case == "single_configured":
        profiles.pop()
        jobs.pop()
    elif case in {"recovered", "stale_failure"}:
        jobs.append({**jobs[1], "id": "old-failure", "status": "failed", "updated_at": "2026-10-10T22:00:00Z",
                     **({"artifact_version": "sha256:older"} if case == "stale_failure" else {})})
    review = read()
    assert review["recommendation"]["verdict"] == verdict
    assert review["recommendation"]["blocking_count"] == 0
    assert StateStore(tmp_path).read_project_current(state.project_key) == state
    assert not web.build_action_items(tmp_path, state.project_key)


@pytest.mark.parametrize("tamper", [
    "missing_aggregate", "empty_aggregate", "malformed_aggregate", "wrong_counts", "empty_reviewers",
    "missing_profile", "failed_status", "missing_policy", "failed_provenance", "wrong_gate",
    "missing_profiles", "invalid_profiles", "duplicate_profiles", "null_projection", "wrong_run",
    "stale_artifact", "missing_status",
])
def test_unknown_or_inconsistent_projection_cannot_recommend_approval(tmp_path, review_case, tamper):
    state, _jobs, _profiles, _target, read = review_case
    review = deepcopy(read())
    projection = review["multi_reviewer_projection"]
    if tamper == "missing_aggregate":
        projection.pop("aggregate_policy")
    elif tamper == "empty_aggregate":
        projection["aggregate_policy"] = {}
    elif tamper == "malformed_aggregate":
        projection["aggregate_policy"] = "ok"
    elif tamper == "wrong_counts":
        projection["aggregate_policy"]["state_counts"] = {"ok": 1}
    elif tamper == "empty_reviewers":
        projection["reviewers"] = []
    elif tamper == "missing_profile":
        projection["reviewers"].pop()
        projection["aggregate_policy"]["state_counts"] = {"ok": 1}
    elif tamper == "failed_status":
        projection["reviewers"][1]["status"]["status"] = "failed"
    elif tamper == "missing_policy":
        projection["reviewers"][1].pop("policy")
    elif tamper == "failed_provenance":
        projection["reviewers"][1]["provenance"]["invocation"]["status"] = "failed"
    elif tamper == "wrong_gate":
        projection["approval"] = "release"
    elif tamper == "missing_profiles":
        projection.pop("configured_profile_ids")
    elif tamper == "invalid_profiles":
        projection["configured_profile_ids"] = "correctness-security"
    elif tamper == "duplicate_profiles":
        projection["configured_profile_ids"].append(projection["configured_profile_ids"][0])
    elif tamper == "null_projection":
        review["multi_reviewer_projection"] = None
    elif tamper == "wrong_run":
        projection["reviewers"][1]["status"]["run_id"] = "older-run"
    elif tamper == "stale_artifact":
        projection["reviewers"][1]["status"]["artifact_version"] = "sha256:older"
    else:
        projection["reviewers"][1]["status"] = None
    result = web.build_approval_recommendation(state=state, workspace=tmp_path,
        review=review, evidence_snapshot=review["evidence_snapshot"])
    assert result["verdict"] == "caution"
    assert result["blocking_count"] == 0


@pytest.mark.parametrize("second_status,verdict", [("failed", "caution"), ("missing", "caution"),
                                                   ("completed", "approve")])
def test_telegram_card_uses_the_same_aggregate_advice(tmp_path, monkeypatch, review_case, second_status, verdict):
    state, jobs, _profiles, _target, read = review_case
    if second_status == "missing":
        jobs.pop()
    else:
        jobs[1]["status"] = second_status
    sent = []
    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "100")
    monkeypatch.setattr(telegram, "_telegram_request", lambda token, method, params:
                        sent.append((method, params)) or {"ok": True, "result": {"message_id": 200}})
    notification = telegram.send_approval_notification(workspace=tmp_path, project_key=state.project_key,
                                                        run_id=state.run_id, gate="security")
    review = read()
    assert review["recommendation"]["verdict"] == verdict
    assert f" {verdict} · 阻断 0" in sent[0][1]["text"]
    if verdict == "caution":
        assert "Current reviewer evidence is incomplete or inconsistent" in sent[0][1]["text"]
    session = telegram.TelegramReviewSessionStore(tmp_path).get(notification["session_id"])
    assert session["approval_target"]["artifact_version"] == review["artifact_version"]
    assert StateStore(tmp_path).read_project_current(state.project_key) == state
    assert not web.build_action_items(tmp_path, state.project_key)


@pytest.mark.parametrize("policy", [
    {"mode": "advisory"}, {"mode": "strict"},
    {"mode": "strict", "blockers": ["failed_invocation", "missing_evidence", "non_provider_evidence"]},
])
@pytest.mark.parametrize("status,verdict", [("completed", "approve"), ("failed", "caution")])
def test_valid_custom_policy_preserves_success_and_failed_advice(review_case, policy, status, verdict):
    _state, jobs, _profiles, target, read = review_case
    target["review_evidence_policy"] = policy
    jobs[1]["status"] = status
    review = read()
    assert review["recommendation"]["verdict"] == verdict
    assert review["recommendation"]["blocking_count"] == 0
    second_policy = review["multi_reviewer_projection"]["reviewers"][1]["policy"]
    assert second_policy["severity"] == ("ok" if status == "completed" else
                                        "blocker" if "failed_invocation" in policy.get("blockers", []) else "warning")
