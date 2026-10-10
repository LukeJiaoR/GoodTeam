"""Regression for an old Telegram card approving a replacement run.

Temporary model-free workflow state and a mocked Telegram transport only.
"""

import pytest

from goodteam_os import telegram, web
from goodteam_os.telegram_approval_binding import session_decision_binding
from test_approval_rationale_boundary import durable_files
from goodteam_os.state import StateStore
from test_workflow_recovery import binding_for, gate_state


def test_reply_to_prior_run_card_cannot_approve_replacement_run(tmp_path, monkeypatch):
    current = gate_state(tmp_path, "release")
    store = telegram.TelegramReviewSessionStore(tmp_path)
    store.save("old-card", {
        "session_id": "old-card", "project_key": "RECOVERY", "run_id": "prior-run",
        "gate": "release", "chat_id": 100, "message_id": 200, "items": [], "decided": False,
    })
    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "fake-token")
    sent = []
    monkeypatch.setattr(telegram, "_telegram_request", lambda token, method, params: sent.append((method, params)) or {"ok": True})
    result = telegram.process_telegram_update({"message": {
        "message_id": 201, "from": {"id": 111}, "chat": {"id": 100},
        "reply_to_message": {"message_id": 200},
        "text": "/gt approve I checked the prior-run review shown in this card.",
    }}, workspace=tmp_path, allowed_users=["111"], processed_callbacks=set())

    saved = StateStore(tmp_path).read_project_current("RECOVERY")
    assert saved.run_id == current.run_id
    assert saved.next_node == "await_release_approval", "Old review context must never approve a replacement run"
    assert not any(row["action"] == "approval_decided" for row in web.build_action_items(tmp_path, "RECOVERY"))
    assert store.get("old-card")["decided"] is False
    assert result.get("accepted") is False
    assert any(method == "sendMessage" for method, _ in sent)


# These tests use the real notification builder, state store and guarded decision
# boundary. Only Telegram's network transport is replaced.


REASON = "Model-free binding fixture: I reviewed this simulated release evidence."


def published_card(workspace, monkeypatch, *, gate="release", profile="greenfield", explicit_path=False):
    state = gate_state(workspace, gate, profile=profile)
    sent = []
    next_message_id = 200

    def transport(token, method, params):
        nonlocal next_message_id
        sent.append((method, params))
        if method == "sendMessage":
            next_message_id += 1
            return {"ok": True, "result": {"message_id": next_message_id}}
        return {"ok": True}

    monkeypatch.setenv("GOODTEAM_TG_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("GOODTEAM_TG_CHAT_ID", "100")
    monkeypatch.setattr(telegram, "_telegram_request", transport)
    notification = telegram.send_approval_notification(
        workspace=workspace, project_key=state.project_key, run_id=state.run_id, gate=gate,
        artifact_path=state.artifacts[web._approval_artifact_for_gate(gate)] if explicit_path else "")
    store = telegram.TelegramReviewSessionStore(workspace)
    return state, store.get(notification["session_id"]), sent


def command_update(session, command="approve"):
    return {"message": {"message_id": 900, "from": {"id": 111}, "chat": {"id": session["chat_id"]},
                        "reply_to_message": {"message_id": session["message_id"]},
                        "text": f"/gt {command} {REASON}"}}


def callback_update(session, action="A"):
    return {"callback_query": {"id": "bound-click", "from": {"id": 111},
            "message": {"chat": {"id": session["chat_id"]}, "message_id": session["message_id"]},
            "data": f"v1:{action}:{session['session_id']}"}}


def apply_update(workspace, update, *, processed=None, project_key=""):
    return telegram.process_telegram_update(update, workspace=workspace, allowed_users=["111"],
                                            processed_callbacks=set() if processed is None else processed,
                                            project_key=project_key)


def test_notification_captures_the_displayed_artifact_without_explicit_path(tmp_path, monkeypatch):
    state, session, _ = published_card(tmp_path, monkeypatch)
    assert session["approval_target"] == {
        "project_key": state.project_key,
        **{key.removeprefix("expected_"): value for key, value in binding_for(state).items()},
    }


def current_review_job(state, *, kind="provider", **overrides):
    artifact = web._approval_artifact_for_gate("prd")
    path = state.artifacts[artifact]
    return {"id": "review-evidence", "run_id": state.run_id, "role": "review", "approval": "prd",
        "artifact_key": artifact, "artifact_path": path, "artifact_version": web.artifact_version_for_path(path),
        "status": "completed", "adapter": "fake" if kind == "builtin" else "codex",
        "invocation_evidence": {"kind": kind, "external_invocation": kind == "provider"}, **overrides}


@pytest.mark.parametrize("kind,verdict", [(None, "caution"), ("builtin", "caution"), ("provider", "approve")])
def test_notification_matches_current_work_review_evidence(tmp_path, monkeypatch, kind, verdict):
    # Only the saved invocation input is synthetic; build the real projections,
    # card and durable binding. No provider or Telegram network call is made.
    monkeypatch.setattr(web, "_reviewer_statuses_for_state", lambda *, state, **_: (
        [] if kind is None else [current_review_job(state, kind=kind)]))
    state, session, sent = published_card(tmp_path, monkeypatch, gate="prd")
    review = next(item for item in web.build_approval_reviews(state, workspace=tmp_path) if item["approval"] == "prd")
    assert review["recommendation"]["verdict"] == verdict
    text = next(params["text"] for method, params in sent if method == "sendMessage")
    assert f" {verdict} · 阻断 0" in text
    if verdict == "caution":
        assert "Reviewer evidence is missing, simulated or unsuccessful" in text
    assert session["approval_target"]["artifact_version"] == review["artifact_version"]
    assert StateStore(tmp_path).read_project_current(state.project_key) == state
    assert not web.build_action_items(tmp_path, state.project_key)


@pytest.mark.parametrize("mismatch", ["run", "artifact"])
def test_notification_does_not_borrow_provider_evidence_from_a_different_target(tmp_path, monkeypatch, mismatch):
    monkeypatch.setattr(web, "_reviewer_statuses_for_state", lambda *, state, **_: [current_review_job(state)])
    state, _session, sent = published_card(tmp_path, monkeypatch, gate="prd")
    other = tmp_path / "different.md"
    other.write_text("# Different material without reviewer evidence")
    telegram.send_approval_notification(workspace=tmp_path, project_key=state.project_key,
        run_id="old-run" if mismatch == "run" else state.run_id, gate="prd",
        artifact_path=str(other) if mismatch == "artifact" else state.artifacts[web._approval_artifact_for_gate("prd")])
    assert " caution · 阻断 0" in sent[-1][1]["text"]
    assert StateStore(tmp_path).read_project_current(state.project_key) == state


@pytest.mark.parametrize("overrides", [
    {"artifact_version": "sha256:old"}, {"artifact_version": ""},
    {"artifact_key": "another_artifact"}, {"artifact_key": ""},
    {"artifact_path": "/old/report.md"}, {"artifact_path": ""},
    {"approval": "rfc"},
])
def test_work_and_telegram_do_not_approve_from_unbound_or_stale_reviewer(tmp_path, monkeypatch, overrides):
    monkeypatch.setattr(web, "_reviewer_statuses_for_state", lambda *, state, **_: [current_review_job(state, **overrides)])
    state, _session, sent = published_card(tmp_path, monkeypatch, gate="prd")
    review = next(item for item in web.build_approval_reviews(state, workspace=tmp_path) if item["approval"] == "prd")
    assert review["recommendation"]["verdict"] == "caution"
    assert review["reviewer_provenance"]["badge"]["kind"] == "missing"
    assert " caution · 阻断 0" in sent[-1][1]["text"]
    assert StateStore(tmp_path).read_project_current(state.project_key) == state


@pytest.mark.parametrize("command,decision", [("approve", "approved"), ("reject", "rejected"),
                                               ("changes", "changes_requested")])
def test_current_bound_text_decision_succeeds_once_with_exact_audit(tmp_path, monkeypatch, command, decision):
    state, session, _ = published_card(tmp_path, monkeypatch)
    update = command_update(session, command)
    result = apply_update(tmp_path, update, project_key="UNRELATED_DEFAULT")
    assert result["status"] == "processed" and result.get("accepted") is not False
    saved = StateStore(tmp_path).read_project_current(state.project_key)
    if command != "changes":
        assert saved.approvals["release"] == decision
    assert telegram.TelegramReviewSessionStore(tmp_path).get(session["session_id"])["decided"] is True
    apply_update(tmp_path, update)
    decisions = [a for a in web.build_action_items(tmp_path, state.project_key) if a["action"] == "approval_decided"]
    assert len(decisions) == 1
    assert decisions[0]["actor_ref"] == "telegram:user:111"
    assert decisions[0]["review_session_id"] == session["session_id"]
    assert decisions[0]["note"] == REASON
    assert decisions[0]["decision"] == decision


@pytest.mark.parametrize("entrypoint", ["command", "card", "legacy", "chooser"])
@pytest.mark.parametrize("changed", ["run", "artifact"])
def test_stale_bound_target_never_changes_durable_state(tmp_path, monkeypatch, entrypoint, changed):
    state, session, _ = published_card(tmp_path, monkeypatch)
    if changed == "run":
        state.run_id = "replacement-run"
        StateStore(tmp_path).write_project_current(state)
    else:
        from pathlib import Path
        Path(state.artifacts["release"] if "release" in state.artifacts else next(iter(state.artifacts.values()))).write_text(
            "# New unseen release evidence")
    if entrypoint == "command":
        update = command_update(session)
    else:
        update = callback_update(session)
        if entrypoint == "legacy":
            update["callback_query"]["data"] = telegram.encode_callback_data(telegram.TelegramApproval(
                session["project_key"], session["run_id"], session["gate"], "approved"))
        if entrypoint == "chooser":
            session["chooser_intents"] = {"intent": {"command": "approve", "raw_text": f"/gt approve {REASON}",
                                                    "session_id": session["session_id"], "chat_id": "100"}}
            telegram.TelegramReviewSessionStore(tmp_path).save(session["session_id"], session)
            update["callback_query"]["data"] = f"v1:Q:{session['session_id']}:A:intent"
    before = durable_files(tmp_path)
    executed = []
    monkeypatch.setattr(web, "advance_approved_workflow", lambda **kw: executed.append(kw))
    result = apply_update(tmp_path, update)
    assert result.get("accepted") is False or result["status"] == "ignored"
    assert durable_files(tmp_path) == before
    assert executed == []
    assert not web.build_action_items(tmp_path, state.project_key)


@pytest.mark.parametrize("field,value", [("project_key", "OTHER"), ("run_id", "other-run"),
                                         ("target_id", "prd"), ("artifact_version", ""),
                                         ("artifact_version", "missing"), ("target_type", "task"), ("artifact_key", "unseen-artifact")])
def test_malformed_saved_binding_is_not_repaired_from_current_state(tmp_path, monkeypatch, field, value):
    _, session, _ = published_card(tmp_path, monkeypatch)
    session["approval_target"][field] = value
    telegram.TelegramReviewSessionStore(tmp_path).save(session["session_id"], session)
    before = durable_files(tmp_path)
    result = apply_update(tmp_path, command_update(session))
    assert result["accepted"] is False and result["reason"] == "stale_approval"
    assert durable_files(tmp_path) == before


@pytest.mark.parametrize("field", ["chat", "message"])
def test_card_callback_is_bound_to_original_chat_and_message(tmp_path, monkeypatch, field):
    _, session, _ = published_card(tmp_path, monkeypatch)
    update = callback_update(session)
    if field == "chat":
        update["callback_query"]["message"]["chat"]["id"] = 999
    else:
        update["callback_query"]["message"]["message_id"] = 999
    before = durable_files(tmp_path)
    result = apply_update(tmp_path, update)
    assert result["accepted"] is False and result["reason"] == "stale_approval"
    assert durable_files(tmp_path) == before


@pytest.mark.parametrize("entrypoint", ["command", "card", "legacy"])
def test_pre_migration_card_without_version_binding_fails_closed(tmp_path, monkeypatch, entrypoint):
    _, session, _ = published_card(tmp_path, monkeypatch)
    session.pop("approval_target")
    telegram.TelegramReviewSessionStore(tmp_path).save(session["session_id"], session)
    update = command_update(session) if entrypoint == "command" else callback_update(session)
    if entrypoint == "legacy":
        update["callback_query"]["data"] = telegram.encode_callback_data(telegram.TelegramApproval(
            session["project_key"], session["run_id"], session["gate"], "approved"))
    before = durable_files(tmp_path)
    result = apply_update(tmp_path, update)
    assert result["accepted"] is False and result["reason"] == "stale_approval"
    assert durable_files(tmp_path) == before


def test_refresh_of_changed_artifact_creates_new_card_not_a_rebound_old_message(tmp_path, monkeypatch):
    from pathlib import Path
    state, old, sent = published_card(tmp_path, monkeypatch)
    Path(next(iter(state.artifacts.values()))).write_text("# Revised release evidence")
    refreshed = telegram.send_approval_notification(workspace=tmp_path, project_key=state.project_key,
                                                    run_id=state.run_id, gate="release")
    store = telegram.TelegramReviewSessionStore(tmp_path)
    new = store.get(refreshed["session_id"])
    assert new["session_id"] != old["session_id"] and new["message_id"] != old["message_id"]
    assert new["approval_target"] != old["approval_target"]
    assert store.get(old["session_id"])["superseded"] is True
    assert [method for method, _ in sent] == ["sendMessage", "sendMessage"]
    before = durable_files(tmp_path)
    apply_update(tmp_path, command_update(old))
    apply_update(tmp_path, callback_update(old))
    assert durable_files(tmp_path) == before
    assert apply_update(tmp_path, command_update(new)).get("accepted") is not False
    assert StateStore(tmp_path).read_project_current(state.project_key).current_node == "complete"


def test_unchanged_refresh_retains_exact_message_binding(tmp_path, monkeypatch):
    state, old, sent = published_card(tmp_path, monkeypatch)
    refreshed = telegram.send_approval_notification(workspace=tmp_path, project_key=state.project_key,
                                                    run_id=state.run_id, gate="release")
    assert refreshed["status"] == "refreshed" and refreshed["session_id"] == old["session_id"]
    assert telegram.TelegramReviewSessionStore(tmp_path).get(old["session_id"])["approval_target"] == old["approval_target"]
    assert [method for method, _ in sent] == ["sendMessage", "editMessageText"]


def test_changed_material_between_render_and_binding_never_receives_new_version(tmp_path, monkeypatch):
    from pathlib import Path
    state, _, _ = published_card(tmp_path, monkeypatch)
    original = telegram.capture_target

    def change_before_capture(**kwargs):
        Path(next(iter(state.artifacts.values()))).write_text("# Changed after rendered bytes were read")
        return original(**kwargs)

    monkeypatch.setattr(telegram, "capture_target", change_before_capture)
    notification = telegram.send_approval_notification(workspace=tmp_path, project_key=state.project_key,
                                                       run_id=state.run_id, gate="release")
    session = telegram.TelegramReviewSessionStore(tmp_path).get(notification["session_id"])
    assert session["approval_target"] == {}
    assert apply_update(tmp_path, command_update(session))["accepted"] is False


def test_helper_requires_every_target_field_and_never_defaults_it():
    with pytest.raises(web.ProjectApiError, match="version-bound"):
        session_decision_binding(None, chat_id=100)


def test_state_change_at_shared_boundary_is_not_hidden_by_adapter_read(tmp_path, monkeypatch):
    state, session, _ = published_card(tmp_path, monkeypatch)
    expected = session["approval_target"].copy()
    called = []

    def change_then_decide(**kwargs):
        called.append(kwargs)
        state.run_id = "concurrent-replacement"
        StateStore(tmp_path).write_project_current(state)
        return web.decide_approval(**kwargs)

    monkeypatch.setattr(telegram, "decide_approval", change_then_decide)
    result = apply_update(tmp_path, command_update(session))
    assert result["accepted"] is False and result["reason"] == "stale_approval"
    assert called[0]["expected_run_id"] == expected["run_id"]
    assert called[0]["expected_artifact_version"] == expected["artifact_version"]
    assert StateStore(tmp_path).read_project_current(state.project_key).next_node == "await_release_approval"
    assert not web.build_action_items(tmp_path, state.project_key)


@pytest.mark.parametrize("action", ["A", "C", "Y"])
def test_all_decision_callbacks_forward_binding_before_any_notes_or_session_writes(tmp_path, monkeypatch, action):
    _, session, _ = published_card(tmp_path, monkeypatch)
    session["items"] = [{"id": "release", "title": "Release", "body": "Evidence", "state": "flagged",
                         "note": "Human-authored feedback in this test"}]
    telegram.TelegramReviewSessionStore(tmp_path).save(session["session_id"], session)
    before = durable_files(tmp_path)
    artifacts_before = {str(p): p.read_bytes() for p in tmp_path.rglob("*.md")}
    calls = []

    def rejected_shared_boundary(**kwargs):
        calls.append(kwargs)
        assert durable_files(tmp_path) == before
        assert {str(p): p.read_bytes() for p in tmp_path.rglob("*.md")} == artifacts_before
        raise web.ProjectApiError("stale_approval", "Changed at the guarded boundary", 409)

    monkeypatch.setattr(telegram, "decide_approval", rejected_shared_boundary)
    result = apply_update(tmp_path, callback_update(session, action))
    assert result["accepted"] is False
    assert len(calls) == 1
    for key, value in session["approval_target"].items():
        if key == "project_key":
            assert calls[0][key] == value
        else:
            assert calls[0][f"expected_{key}"] == value
    assert durable_files(tmp_path) == before
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*.md")} == artifacts_before


@pytest.mark.parametrize("gate,artifact_key", [("code_review", "implementation_handoff"), ("qa", "qa_report")])
@pytest.mark.parametrize("existing_explicit_card", [False, True])
def test_fix_profile_approvals_command_preserves_displayed_binding(
    tmp_path, monkeypatch, gate, artifact_key, existing_explicit_card,
):
    state, session, sent = published_card(tmp_path, monkeypatch, gate=gate, profile="fix",
                                        explicit_path=existing_explicit_card)
    if existing_explicit_card:
        assert session["approval_target"]["artifact_key"] == artifact_key
        result = apply_update(tmp_path, {"message": {"message_id": 900, "from": {"id": 111},
                              "chat": {"id": 100}, "text": "/gt approvals"}}, project_key=state.project_key)
        assert result == {"status": "processed", "command": "approvals"}
    store = telegram.TelegramReviewSessionStore(tmp_path)
    refreshed = store.find_live_for_gate(state.project_key, state.run_id, gate)
    assert refreshed["session_id"] == session["session_id"]
    assert refreshed["message_id"] == session["message_id"]
    assert not refreshed.get("superseded")
    assert refreshed["approval_target"]["artifact_key"] == artifact_key
    assert refreshed["approval_target"]["artifact_version"] == web.artifact_version_for_path(state.artifacts[artifact_key])
    assert [method for method, _ in sent] == (["sendMessage", "editMessageText"] if existing_explicit_card else ["sendMessage"])
    # A genuine text decision traverses the shared guarded boundary. Rejection
    # avoids launching providers while proving the refreshed binding is usable.
    result = apply_update(tmp_path, command_update(refreshed, "reject"))
    assert result.get("accepted") is not False
    saved = StateStore(tmp_path).read_project_current(state.project_key)
    assert saved.approvals[gate] == "rejected"
    assert store.get(refreshed["session_id"])["decided"] is True
    decisions = [row for row in web.build_action_items(tmp_path, state.project_key) if row["action"] == "approval_decided"]
    assert len(decisions) == 1 and decisions[0]["review_session_id"] == refreshed["session_id"]
