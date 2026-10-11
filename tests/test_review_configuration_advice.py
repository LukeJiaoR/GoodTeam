"""Broken explicit reviewer settings must never masquerade as an unconfigured team."""

import pytest

from goodteam_os import telegram, web
from goodteam_os.project_reviewer_profiles import normalize_reviewer_profile_config
from goodteam_os.state import StateStore
from test_workflow_recovery import gate_state


@pytest.fixture
def current_review(tmp_path, monkeypatch):
    state = gate_state(tmp_path, "security")
    key = web._approval_artifact_for_gate("security")
    path = state.artifacts[key]
    job = {"id": "current-review", "run_id": state.run_id, "approval": "security", "role": "code_review",
           "profile_id": "correctness-security", "status": "completed", "adapter": "codex",
           "artifact_key": key, "artifact_path": path, "artifact_version": web.artifact_version_for_path(path),
           "invocation_evidence": {"kind": "provider", "external_invocation": True}}
    monkeypatch.setattr(web, "_reviewer_statuses_for_state", lambda **_: [job])

    def read():
        return next(item for item in web.build_approval_reviews(state, workspace=tmp_path)
                    if item["approval"] == "security")

    return state, read


INVALID_CONFIGS = [
    {"implementation": ["correctness-security", "unknown-profile"]},
    {"implementation": ["correctness-security", "correctness-security"]},
    {"implementation": "correctness-security, correctness-security"},
    {"implementation": "['correctness-security', 'correctness-security']"},
    {"implementation": ["correctness-security", ""]},
    {"implementation": ["correctness-security", 123]},
    {"implementation": True}, {"implementation": {}}, {"implementation": None},
    {"implementation": "correctness-security,,"}, {"implementation": ","},
    {"implementation": ["correctness-security", "architecture-maintainability"],
     " implementation ": ["correctness-security"]},
    {"wrong-phase": ["correctness-security"]}, [], "correctness-security", None, True,
]


@pytest.mark.parametrize("config", INVALID_CONFIGS)
def test_invalid_explicit_configuration_cannot_fall_back_to_single_success(tmp_path, monkeypatch, current_review, config):
    state, read = current_review
    monkeypatch.setattr(web, "_reviewer_target_for_state", lambda **_: {"reviewer_profiles": config})
    review = read()
    assert review["recommendation"]["verdict"] == "caution"
    assert review["recommendation"]["blocking_count"] == 0
    assert "Repair project reviewer settings" in review["multi_reviewer_projection"]["configuration_error"]
    assert any("Repair project reviewer settings" in reason["text"] for reason in review["recommendation"]["reasons"])
    assert StateStore(tmp_path).read_project_current(state.project_key) == state
    assert not web.build_action_items(tmp_path, state.project_key)


@pytest.mark.parametrize("config", [
    {}, {"reviewer_profiles": {}}, {"reviewer_profiles": {"implementation": []}},
    {"reviewer_profiles": {"implementation": ""}},
    {"reviewer_profiles": {"implementation": "[]"}},
    {"reviewer_profiles": {"implementation": ["correctness-security"]}},
    {"reviewer_profiles": {"implementation": "correctness-security"}},
])
def test_absent_empty_or_valid_single_profile_configuration_preserves_success(monkeypatch, current_review, config):
    _state, read = current_review
    monkeypatch.setattr(web, "_reviewer_target_for_state", lambda **_: config)
    review = read()
    assert review["recommendation"]["verdict"] == "approve"
    assert review["multi_reviewer_projection"]["configuration_error"] == ""


@pytest.mark.parametrize("failure", [ValueError("private parse detail"), OSError("private path detail")])
def test_config_read_failure_is_visible_without_leaking_exception(tmp_path, monkeypatch, current_review, failure):
    state, read = current_review
    def broken(_):
        raise failure
    monkeypatch.setattr(web, "discover_project_configs", lambda _: [tmp_path / "broken.local.yaml"])
    monkeypatch.setattr(web, "load_project_target", broken)
    review = read()
    assert review["recommendation"]["verdict"] == "caution"
    reason = review["multi_reviewer_projection"]["configuration_error"]
    assert "could not be read" in reason and "private" not in reason
    assert StateStore(tmp_path).read_project_current(state.project_key) == state


def test_legacy_project_without_local_config_remains_distinct_from_read_failure(current_review):
    _state, read = current_review
    assert read()["recommendation"]["verdict"] == "approve"


def test_duplicate_diagnostics_do_not_change_execution_normalization():
    target = {"reviewer_profiles": {"implementation": ["correctness-security", "correctness-security"]}}
    assert normalize_reviewer_profile_config(target) == {"implementation": ("correctness-security",)}


def test_telegram_card_explains_configuration_repair(tmp_path, monkeypatch, current_review):
    state, _read = current_review
    monkeypatch.setattr(web, "_reviewer_target_for_state", lambda **_: {"reviewer_profiles": INVALID_CONFIGS[0]})
    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "100")
    sent = []
    monkeypatch.setattr(telegram, "_telegram_request", lambda token, method, params:
                        sent.append(params) or {"ok": True, "result": {"message_id": 200}})
    telegram.send_approval_notification(workspace=tmp_path, project_key=state.project_key,
                                        run_id=state.run_id, gate="security")
    assert " caution · 阻断 0" in sent[0]["text"]
    assert "Repair project reviewer settings" in sent[0]["text"]
    assert StateStore(tmp_path).read_project_current(state.project_key) == state
    assert not web.build_action_items(tmp_path, state.project_key)


def test_real_malformed_project_file_does_not_become_absent_configuration(tmp_path, current_review):
    _state, read = current_review
    project_dir = tmp_path / "projects"
    project_dir.mkdir()
    (project_dir / "recovery.local.yaml").write_text("unsupported yaml without a mapping separator")
    review = read()
    assert review["recommendation"]["verdict"] == "caution"
    assert "could not be read" in review["multi_reviewer_projection"]["configuration_error"]
