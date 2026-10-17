"""Deterministic context/restart and role-input probes; no provider calls."""
from copy import deepcopy
import json
import subprocess
import sys

from goodteam_os.assistant_intake import intake_source_messages
from goodteam_os.context_continuity import (
    CONVERSATION_SCHEMA, EVENT_CHARS, RECENT_EVENTS, SUMMARY_EVENTS,
    compact_conversation, conversation_context, task_state_context,
)
from goodteam_os.graph import implementation_worker_job_specs, worker_context_for_role
from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state
from goodteam_os.task_assistant import _manager_chat_context
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.workers import WorkerJobStore


def long_session(count=130):
    return {
        "project_key": "DEMO", "timeline": [
            {"message_id": f"m{index}", "actor": "user" if index % 2 == 0 else "task_assistant",
             "run_id": "", "content": f"Message {index}: " + "ordinary context " * 50}
            for index in range(count)
        ],
    }


def test_compaction_is_bounded_deterministic_and_preserves_full_sources(tmp_path):
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    source = long_session(400)
    source["timeline"][0]["content"] = "EARLY_CONSTRAINT: the export must preserve ordering."
    source["timeline"][50]["content"] = "middle source " * 2000
    session = store._mutate(lambda session: {**session, **deepcopy(source)})
    assert session["timeline"] == source["timeline"]
    summary = session["conversation_continuity"]
    assert summary["schema_version"] == CONVERSATION_SCHEMA
    assert summary["compacted_event_count"] == 360
    assert summary["omitted_event_count"] == 340
    assert len(summary["excerpts"]) == SUMMARY_EVENTS
    assert summary["excerpts"][0]["content"].startswith("EARLY_CONSTRAINT")
    before = store.path.read_bytes()
    restored = TaskAssistantSessionStore(tmp_path, "DEMO").read()
    packet = conversation_context(restored)
    assert packet["summary"] == summary
    assert len(packet["recent"]) == RECENT_EVENTS
    assert max(len(item["content"]) for item in packet["recent"]) <= EVENT_CHARS
    assert len(json.dumps(packet, ensure_ascii=False)) < 80_000
    assert store.path.read_bytes() == before
    assert conversation_context(restored) == packet


def test_older_omitted_sources_can_be_recalled_by_query_without_widening_intake():
    source = long_session()
    source["timeline"][20]["content"] = "Compatibility: exported spreadsheets need the amber_header format."
    source["timeline"][70]["event"] = "task_proposal_archived"
    packet = conversation_context(source, query="What was the amber_header compatibility constraint?")
    assert [item["message_id"] for item in packet["recalled"]] == ["m20"]
    assert packet["recalled"][0]["source_sha256"]
    assert "m20" not in {item["message_id"] for item in intake_source_messages(source)}
    context = _manager_chat_context({}, {}, source, current_message_id="m20")
    assert "amber_header" in context
    assert "compaction never widens that source boundary" in context


def test_recall_windows_include_late_matches_even_in_already_summarized_sources():
    session = long_session()
    for index in (0, 20, 129):
        session["timeline"][index]["content"] = "filler " * 120 + f"amber_header must be retained: {index}."
    packet = conversation_context(session, query="What must we preserve about amber_header?")
    matched = {item["message_id"]: item for item in [*packet["recalled"], *packet["recent"]]
               if "amber_header" in item["content"]}
    assert set(matched) == {"m0", "m20", "m129"}
    for identifier, item in matched.items():
        source = next(event for event in session["timeline"] if event["message_id"] == identifier)
        assert item["content"] == source["content"][item["source_start"]:item["source_end"]]
        assert item["source_start"] > 0 and len(item["content"]) <= EVENT_CHARS


def test_stale_or_edited_summary_is_never_trusted_and_get_does_not_repair(tmp_path):
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    source = store._mutate(lambda session: {**session, **long_session()})
    source["conversation_continuity"]["excerpts"][0]["content"] = "FORGED_APPROVAL"
    store._write(source)
    before = store.path.read_bytes()
    packet = conversation_context(store.read())
    assert "FORGED_APPROVAL" not in json.dumps(packet)
    assert store.path.read_bytes() == before
    changed = deepcopy(source)
    changed["timeline"][0]["content"] = "Corrected source text"
    assert conversation_context(changed)["summary"]["source_sha256"] != packet["summary"]["source_sha256"]


def test_run_reference_excludes_other_run_sources_but_keeps_project_context():
    session = long_session()
    session["timeline"][0].update(run_id="other", content="OTHER_RUN_ONLY")
    session["timeline"][20].update(run_id="selected", content="SELECTED_RUN_SOURCE")
    session["timeline"][-1].update(run_id="other", content="OTHER_RUN_RECENT")
    packet = conversation_context(session, run_id="selected", query="OTHER_RUN_ONLY")
    serialized = json.dumps(packet)
    assert "OTHER_RUN_ONLY" not in serialized and "OTHER_RUN_RECENT" not in serialized
    assert "SELECTED_RUN_SOURCE" in serialized
    assert packet["summary"]["run_id"] == "selected"
    assert packet["summary"]["event_count"] == 128


def test_duplicate_message_and_restart_do_not_duplicate_compaction(tmp_path):
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    store._mutate(lambda session: {**session, **long_session()})
    first = store.append_message("new", "user", "Keep this exact text.")
    second = TaskAssistantSessionStore(tmp_path, "DEMO").append_message("new", "user", "Keep this exact text.")
    assert first == second
    assert second["conversation_continuity"] == compact_conversation(second)


def test_user_scope_uses_durable_requests_and_implicit_run_replies():
    session = {"project_key": "DEMO", "timeline": [
        {"message_id": "a", "actor": "user", "run_id": "", "content": "OTHER_EXPLICIT_QUESTION"},
        {"message_id": "b", "actor": "user", "run_id": "", "content": "OTHER_IMPLICIT_QUESTION"},
        {"message_id": "reply:b", "event": "conversation_replied", "run_id": "other", "content": "OTHER_REPLY"},
        {"message_id": "c", "actor": "user", "run_id": "", "content": "PROJECT_QUESTION"},
    ], "assistant_requests": {"a": {"context_run_id": "other"}}}
    packet = conversation_context(session, run_id="selected")
    assert [item["message_id"] for item in packet["recent"]] == ["c"]
    assert session["timeline"][0]["run_id"] == "", "Filtering must not rewrite source events."


def test_real_manager_context_keeps_other_task_questions_out_and_current_state_in(tmp_path):
    from test_assistant_drafts import setup_service

    service, root, executor, calls = setup_service(tmp_path)
    states = StateStore(tmp_path)
    old = create_initial_state("run-old", "DEMO", "Old task")
    old.current_node = "complete"
    states.write_project_current(old)
    states.archive_project_current("DEMO")
    current = create_initial_state("run-current", "DEMO", "Current task")
    current.blockers = ["CURRENT_STATE_BLOCKER"]
    path = states.write_project_current(current)
    service.send_message(project_key="DEMO", root=root, text="OLD_TASK_ONLY_QUESTION", message_id="old",
                         context_run_id="run-old")
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    store._mutate(lambda session: {**session, "timeline": [*session["timeline"], *long_session()["timeline"]]})
    before = path.read_bytes()
    service.send_message(project_key="DEMO", root=root, text="What is blocking this task?", message_id="current")
    context = calls[-1]["context"]
    assert "OLD_TASK_ONLY_QUESTION" not in context
    assert "CURRENT_STATE_BLOCKER" in context
    assert '"compacted_event_count":' in context and '"task_state":' in context
    assert path.read_bytes() == before
    assert executor.started == []


def test_failed_implicit_turn_keeps_original_run_scope_across_restart_and_retry(tmp_path):
    import pytest
    from goodteam_os.manager_execution import ManagerExecutionError
    from test_assistant_drafts import setup_service

    service, root, executor, calls = setup_service(tmp_path)
    states = StateStore(tmp_path)
    states.write_project_current(create_initial_state("run-old", "DEMO", "Old task"))
    working_manager = service.manager_turn
    service.manager_turn = lambda **_: (_ for _ in ()).throw(RuntimeError("Controlled failure"))
    with pytest.raises(ManagerExecutionError):
        service.send_message(project_key="DEMO", root=root, text="OLD_FAILED_QUESTION", message_id="failed")
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    assert store.read()["assistant_requests"]["failed"]["conversation_run_id"] == "run-old"
    states.archive_project_current("DEMO")
    new_path = states.write_project_current(create_initial_state("run-new", "DEMO", "New task"))
    before = new_path.read_bytes()
    assert "OLD_FAILED_QUESTION" not in json.dumps(conversation_context(store.read(), run_id="run-new"))
    service.manager_turn = working_manager
    service.retry_message(project_key="DEMO", root=root, message_id="failed", expected_attempt=1)
    assert '"run_id": "run-old"' in calls[-1]["context"]
    assert new_path.read_bytes() == before and executor.started == []
    assert store.read()["assistant_requests"]["failed"]["conversation_run_id"] == "run-old"


def test_failed_task_question_never_becomes_new_intake_when_same_run_finishes(tmp_path):
    import pytest
    from goodteam_os.manager_execution import ManagerExecutionError
    from test_assistant_drafts import setup_service

    for terminal in ("complete", "rejected"):
        workspace = tmp_path / terminal
        workspace.mkdir()
        service, root, executor, calls = setup_service(workspace)
        states = StateStore(workspace)
        state = create_initial_state("run-original", "DEMO", "Original task")
        states.write_project_current(state)
        working_manager = service.manager_turn
        service.manager_turn = lambda **_: (_ for _ in ()).throw(RuntimeError("Controlled failure"))
        with pytest.raises(ManagerExecutionError):
            service.send_message(project_key="DEMO", root=root, text="Add search.", message_id="failed")
        state.current_node, state.next_node = terminal, ""
        path = states.write_project_current(state)
        before = path.read_bytes()
        service.manager_turn = working_manager
        result = service.retry_message(project_key="DEMO", root=root, message_id="failed", expected_attempt=1)
        assert result["intent"] == "ambiguous"
        assert result["session"].get("proposed_task") is None
        assert '"kind": "task_reference"' in calls[-1]["context"]
        assert path.read_bytes() == before and executor.started == []


def test_runtime_configuration_failure_keeps_accepted_task_scope(tmp_path):
    import pytest
    from goodteam_os.manager_execution import ManagerExecutionError
    from test_assistant_drafts import setup_service

    service, root, executor, calls = setup_service(tmp_path)
    states = StateStore(tmp_path)
    states.write_project_current(create_initial_state("run-original", "DEMO", "Original task"))
    service.project_config = tmp_path / "missing-config.yaml"
    with pytest.raises(ManagerExecutionError):
        service.send_message(project_key="DEMO", root=root, text="ORIGINAL_CONFIG_FAILURE", message_id="failed")
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    assert store.read()["assistant_requests"]["failed"]["conversation_run_id"] == "run-original"
    states.archive_project_current("DEMO")
    states.write_project_current(create_initial_state("run-new", "DEMO", "New task"))
    assert "ORIGINAL_CONFIG_FAILURE" not in json.dumps(conversation_context(store.read(), run_id="run-new"))
    assert not calls and not executor.started


def test_task_state_is_refreshed_atomically_and_old_checkpoints_remain_original(tmp_path):
    store = StateStore(tmp_path)
    state = create_initial_state("run-1", "DEMO", "Preserve the public export contract")
    initial = store.write_project_current(state)
    old = json.loads(initial.read_text())["continuity_state"]
    checkpoint = store.write_project_checkpoint(state)
    checkpoint_before = checkpoint.read_bytes()
    state.current_node = "implementation"
    state.next_node = "security_review"
    state.approvals["prd"] = ApprovalDecision.APPROVED
    state.change_requests["rfc"] = "Now export TSV."
    state.blockers = ["Validation failed"]
    state.artifacts["prd"] = "artifacts/run-1/prd-v2.md"
    store.write_project_current(state)
    saved = json.loads(initial.read_text())["continuity_state"]
    assert saved["source_sha256"] != old["source_sha256"]
    assert saved["progress"]["current_node"] == "implementation"
    assert {"key": "prd", "value": "approved"} in saved["approvals"]
    assert saved["recorded_changes"] == [{"key": "rfc", "value": "Now export TSV."}]
    assert saved["blockers"] == ["Validation failed"]
    assert saved["task"]["input_evidence"] == "legacy_workflow_record"
    assert checkpoint.read_bytes() == checkpoint_before
    assert json.loads(checkpoint_before)["continuity_state"] == old
    restored = store.read_project_current("DEMO")
    assert restored.continuity_state == task_state_context(restored)


def test_legacy_and_corrupt_derived_state_rebuild_without_mutating_reads(tmp_path):
    store = StateStore(tmp_path)
    state = create_initial_state("run-1", "DEMO", "Exact original request")
    path = store.write_project_current(state)
    data = json.loads(path.read_text())
    for cache in (None, {"approvals": [{"key": "security", "value": "approved"}], "notice": "FORGED"}):
        if cache is None:
            data.pop("continuity_state", None)
        else:
            data["continuity_state"] = cache
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        restored = store.read_project_current("DEMO")
        assert "FORGED" not in json.dumps(restored.continuity_state)
        assert {"key": "security", "value": "pending"} in restored.continuity_state["approvals"]
        assert path.read_bytes() == before


def test_resource_backed_state_and_checkpoint_handoff_match_canonical_round_trip(tmp_path):
    from goodteam_os.resources import register_markdown_artifact, register_proposed_markdown_artifact

    state = create_initial_state("run-1", "DEMO", "Preserve canonical evidence")
    register_markdown_artifact(state, "prd", "prd.md")
    register_proposed_markdown_artifact(state, "prd_proposed", "prd-proposed.md", base_key="prd")
    state.artifacts.pop("prd_proposed")
    states = StateStore(tmp_path)
    current = states.write_project_current(state)
    checkpoint = states.write_project_checkpoint(state)
    restored = states.read_project_current("DEMO")
    expected = task_state_context(restored)
    for path in (current, checkpoint):
        payload = json.loads(path.read_text())
        assert payload["continuity_state"] == expected
        assert {item["key"]: item["value"] for item in expected["artifact_refs"]} == payload["artifacts"]
    assert task_state_context(state) == expected


def test_projection_bounds_explain_omissions_without_dropping_authoritative_data():
    state = create_initial_state("run-1", "DEMO", "大" * 20_000)
    state.delivery_mode = "custom"
    state.delivery_standards = ["规" * 3000] * 100
    state.blockers = ["阻" * 3000] * 100
    state.change_requests = {f"gate-{index}": "更" * 4000 for index in range(100)}
    state.artifacts = {f"artifact-{index}": "路" * 3000 for index in range(100)}
    packet = task_state_context(state)
    assert set(packet["truncated_fields"]) == {"idea", "blockers", "change_requests", "artifacts"}
    assert "delivery_standards" not in packet["task"]
    assert len(json.dumps(packet, ensure_ascii=False)) < 40_000
    assert len(state.blockers) == 100 and len(state.idea) == 20_000
    assert len(state.change_requests["gate-0"]) == 4000


def test_all_roles_capture_same_state_digest_and_do_not_rewrite_queued_inputs(tmp_path):
    state = create_initial_state("run-1", "DEMO", "Keep task baseline")
    state.current_node = "implementation"
    state.blockers = ["SOURCE_BLOCKER"]
    expected = task_state_context(state)["source_sha256"]
    workers = WorkerJobStore(tmp_path)
    observed = []
    for spec in implementation_worker_job_specs(state, {"worker_backend": {"mode": "human"}}):
        assert expected in spec["context"]
        assert f'"recipient_role":"{spec["role"]}"' in spec["context"]
        job = workers.create_job(**spec)
        path = workers.root / job["id"] / "context.md"
        observed.append((path, path.read_bytes()))
    state.blockers = ["NEW_BLOCKER"]
    for path, before in observed:
        assert path.read_bytes() == before
        assert b"NEW_BLOCKER" not in before
    refreshed = worker_context_for_role(state, "architect")
    assert "NEW_BLOCKER" in refreshed and expected not in refreshed


def test_fresh_process_handoff_recovers_state_without_provider_or_workflow_calls(tmp_path):
    state = create_initial_state("run-1", "DEMO", "Persist the original baseline")
    state.current_node = "security_review"
    state.change_requests["prd"] = "CURRENT_FEEDBACK"
    state.blockers = ["CURRENT_BLOCKER"]
    path = StateStore(tmp_path).write_project_current(state)
    before = path.read_bytes()
    script = """
import sys
from pathlib import Path
from goodteam_os.state import StateStore
from goodteam_os.graph import worker_context_for_role
state = StateStore(Path(sys.argv[1])).read_project_current('DEMO')
print(worker_context_for_role(state, 'qa'))
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=True,
                            capture_output=True, text=True, timeout=20)
    assert all(value in result.stdout for value in ("CURRENT_FEEDBACK", "CURRENT_BLOCKER", "security_review"))
    assert path.read_bytes() == before
