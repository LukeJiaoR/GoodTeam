"""One report-to-decision contract with deterministic storage/dispatch seams.

This exercises production continuation/discovery/observation logic. In-memory
stores, terminal-evidence seams and model/dispatch responses are fixtures, not
real database, process-isolation or autonomous-model acceptance evidence.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from goodteam_os import project_investigation_feedback as feedback
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os import project_outcome_discovery as discovery
from goodteam_os import project_iteration_planner as planner
from goodteam_os import assistant_queue, dispatch_workspace, goal_continuation, memory, memory_storage
from goodteam_os import project_iteration_execution as execution
from goodteam_os import project_iteration_recovery as recovery
from goodteam_os import task_assistant_session, tasks, team_control_plane, workflow_recovery


def test_investigation_report_reenters_original_loop_once_without_delivery_promotion(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    config = tmp_path / "project.yaml"
    config.write_text("key: DEMO\n")
    run_id, dispatch_id = "task-batch-" + "a" * 64, "dispatch-" + "b" * 64
    head, authority = "c" * 40, {"fixture": "original-authority"}
    fields = continuation._INTENT_FIELDS
    monkeypatch.setattr(execution, "TASK_INTENT_FIELDS", fields)
    seed = dict(id="DEMO-1", title="Delivered seed", description="The first real delivery is a supplied fixture.",
                role="implementation", dependencies=[], acceptance_criteria=["Seed exists"],
                write_domains=["src"], execution_policy="workspace_write", external_refs=[],
                status="done", delivery_verified=True, run_id="seed-run")
    research = dict(id="DEMO-2", title="Check a missing fact", description="Investigate the exact current entry point.",
                    role="research", dependencies=[seed["id"]], acceptance_criteria=["Identify the entry point"],
                    write_domains=[], execution_policy="read_only", external_refs=["discovery-fixture"],
                    status="review", delivery_verified=False, run_id=run_id, execution_origin="task_assistant_batch",
                    dispatch_job_id="research-job", dispatch_run_id=dispatch_id)
    rows = [deepcopy(seed), deepcopy(research)]
    value = dict(id="original-grant", status="active", reason="awaiting_verified_delivery", task_ids=[row["id"] for row in rows],
                 task_intents={row["id"]: continuation._intent(row) for row in rows},
                 seed_task_ids=[seed["id"]], seed_task_intents={seed["id"]: continuation._intent(seed)},
                 approved_backlog_version="sha256:" + "d" * 64, authority=authority,
                 max_batches=4, max_manager_calls=5, batches_started=2, manager_calls=2,
                 new_tasks_created=1, discovered_task_ids=[research["id"]],
                 discovery=dict(objective="Resolve the entry-point uncertainty and improve the delivered project.",
                                allowed_write_domains=["src"], max_new_tasks=3, max_planning_calls=5,
                                allow_investigation=True), discovery_pending=None,
                 discovery_consumed=["delivery-seed"], planning_backlog_version="",
                 pending_batch=dict(task_ids=[research["id"]], proposal_ids=["research-proposal"],
                                    expected_revisions={"research-proposal": 1}, run_id=run_id))
    value["authorization_digest"] = discovery.authorization_digest(value)
    batch = dict(run_id=run_id, dispatch_run_id=dispatch_id, confirmed=True, status="review",
                 project_root=str(root), expected_continuation_id=value["id"], expected_authority=authority,
                 task_ids=[research["id"]], task_bindings=[dict(task_id=research["id"],
                 task_intent={key: research.get(key) for key in fields})],
                 tracks=[dict(project_task_id=research["id"], role="research", execution_policy="read_only")])
    state = dict(project_continuation=deepcopy(value), project_task_batches={run_id: deepcopy(batch)},
                 timeline=[], queued_task_proposals=[])

    class Session:
        def __init__(self, *_):
            pass

        def read(self):
            return deepcopy(state)

        def _mutate(self, operation):
            result = operation(deepcopy(state))
            state.clear()
            state.update(result)
            return self.read()

        def append_event(self, identifier, event, *, actor="task_assistant", content="", details=None, **kwargs):
            row = dict(message_id=identifier, event=event, actor=actor, content=content, details=details or {})
            previous = next((item for item in state["timeline"] if item["message_id"] == identifier), None)
            if previous is not None:
                assert previous == row
            else:
                state["timeline"].append(row)

    class Ledger:
        def __init__(self, *_):
            pass

        def list_tasks(self):
            return deepcopy(rows)

        def create_task_once(self, *, creation_ref, intent):
            previous = next((row for row in rows if creation_ref in row.get("external_refs", [])), None)
            if previous:
                assert all(previous[key] == val for key, val in intent.items())
                return deepcopy(previous)
            row = dict(id="DEMO-" + str(len(rows) + 1), **deepcopy(intent), status="todo", run_id="",
                       delivery_verified=False)
            rows.append(row)
            return deepcopy(row)

    for module in (continuation, discovery, task_assistant_session):
        monkeypatch.setattr(module, "TaskAssistantSessionStore", Session)
    for module in (continuation, discovery, tasks):
        monkeypatch.setattr(module, "LocalTaskStore", Ledger)
    for module in (continuation, discovery, goal_continuation):
        monkeypatch.setattr(module, "authority_snapshot", lambda *_: deepcopy(authority))
    from contextlib import nullcontext
    monkeypatch.setattr(continuation, "goal_guard", lambda *_: nullcontext())
    monkeypatch.setattr(continuation, "active_project_run", lambda *_: None)
    monkeypatch.setattr(continuation, "require_no_active_project_goal", lambda *_: None)
    monkeypatch.setattr(workflow_recovery, "linked_parent", lambda *_, **__: None)
    monkeypatch.setattr(planner, "_require_planning_idle", lambda *_: None)
    monkeypatch.setattr(planner, "_project_evidence", lambda *_, **__: {"status": "unavailable"})
    monkeypatch.setattr(discovery, "_head", lambda *_: head)
    monkeypatch.setattr(continuation, "_require_clean_checkout", lambda *_: None)
    monkeypatch.setattr(memory, "runtime_role_memory_store", lambda *_: SimpleNamespace(build_pickup_packet=lambda *_, **__: {}))
    monkeypatch.setattr(team_control_plane, "team_view_for_config", lambda *_: {
        "roles": [{"role": "research", "state": "enabled", "workers": [{"dispatchable": True}]}]})
    monkeypatch.setattr(discovery, "_remember", lambda *_: None)
    outcome = {"batch_run_id": "seed-run", "delivery": {"receipt_id": "delivery-seed"}, "quality": {}}
    monkeypatch.setattr(discovery, "verified_outcome", lambda **_: deepcopy(outcome))

    def rank(items, *_args, **_kwargs):
        return {"version": discovery._digest(items), "candidates": [
            {"task_id": row["id"], "eligible_to_plan": row["status"] == "todo"} for row in items]}

    monkeypatch.setattr(discovery, "rank_project_backlog", rank)
    monkeypatch.setattr(discovery, "validate_iteration_choice", lambda _snapshot, choice: dict(choice))
    artifacts = tmp_path / "dispatch"
    artifacts.mkdir()
    report = dict(run_id=dispatch_id, project_root=str(root), all_ok=True, jobs=[dict(job_id="research-job",
                  role="research", project_task_id=research["id"], outcome="ok", attribution_status="captured")])
    (artifacts / "report.json").write_text(json.dumps(report))
    result = root / ".goodteam/workers/jobs/research-job/result.md"
    result.parent.mkdir(parents=True)
    result.write_text("The observed HTTP entry point is api.py; independent verification remains outstanding.\n")

    def read_bytes(path, *, maximum=2 * 1024 * 1024):
        raw = Path(path).read_bytes() if Path(path).exists() else None
        if raw is not None and len(raw) > maximum:
            raise ValueError("Fixture storage limit exceeded")
        return raw

    terminal = dict(unknown_task_ids=[], preserved_task_ids=[research["id"]], can_abandon=True, version="terminal-fixture-v1")
    captured = dict(changed_files=[])
    monkeypatch.setattr(memory_storage, "read_bytes", read_bytes)
    monkeypatch.setattr(dispatch_workspace, "dispatch_artifact_root", lambda *_: artifacts)
    monkeypatch.setattr(dispatch_workspace, "prepare_dispatch_workspace", lambda *_: SimpleNamespace(metadata={"original_head": head}))
    monkeypatch.setattr(dispatch_workspace, "verify_dispatch_result", lambda *_: deepcopy(captured))
    monkeypatch.setattr(recovery, "project_batch_recovery_evidence", lambda **_: deepcopy(terminal))
    monkeypatch.setattr(recovery, "effective_dispatch_id", lambda row: row["dispatch_run_id"])
    initial, original_rows = deepcopy(state), deepcopy(rows)
    original_report, original_terminal = deepcopy(report), deepcopy(terminal)
    calls, starts = [], []
    fail_publication = [False]
    stop_during_decision = [False]
    change_during_decision = [False]
    stop_after_feedback = [False]

    def manager(**kwargs):
        calls.append(deepcopy(kwargs["context"]))
        packet = kwargs["context"]
        observation = packet["investigation_feedback"]
        if observation.get("report_status") in {"missing", "empty"}:
            assert not observation["report"].strip()
        else:
            assert "api.py" in observation["report"]
        assert observation["task_acceptance"] == "not_assessed" and not observation["delivery_verified"]
        assert packet["authorized_done_task_ids"] == [seed["id"]]
        assert observation["receipt_id"] in packet["allowed_source_refs"]
        if stop_during_decision[0]:
            state["project_continuation"].update(status="stopped", reason="owner_stopped")
        if change_during_decision[0]:
            result.write_text("A replaced report must invalidate the reserved decision.")
        if stop_after_feedback[0]:
            return {"selection": {"new_task": None, "rationale":
                    "The retained failure has no useful authorized follow-up; the project is not declared complete."}}
        if observation.get("worker_status") == "failed" or observation.get("report_status") in {"missing", "empty"}:
            return {"selection": {"new_task": dict(title="Resolve the unverified entry question", description=
                    "Use an alternative read-only method to resolve the entry point; prior failed or absent output is not proof.",
                    acceptance_criteria=["Return current entry evidence and any unresolved limitations"],
                    task_type="investigation", write_domains=[], dependencies=[seed["id"]],
                    source_refs=["delivery-seed", observation["receipt_id"]]),
                    "rationale": "The incomplete investigation requires a focused alternative, not a blind retry of the old job."}}
        return {"selection": {"new_task": dict(title="Improve observed HTTP entry", description=
                "Use the unverified investigation finding about api.py to improve and verify the bounded HTTP entry.",
                acceptance_criteria=["The current HTTP entry is verified"], write_domains=["src/api.py"],
                dependencies=[seed["id"]], source_refs=["delivery-seed", observation["receipt_id"]]),
                "rationale": "The returned report identifies a concrete entry point to verify and improve."}}

    def queue(**kwargs):
        identifier = kwargs["expected_task_ids"][0]
        proposal = dict(id="followup-proposal", revision=1, project_task_id=identifier)
        if not state["queued_task_proposals"]:
            state["queued_task_proposals"].append(proposal)
        if fail_publication[0]:
            fail_publication[0] = False
            raise OSError("fixture: queue committed, acknowledgement lost")
        return dict(queued_task_ids=[identifier], queued_proposal_ids=[proposal["id"]], task_assistant_session=deepcopy(state))

    def dispatch(**kwargs):
        identity = tuple(kwargs["proposal_ids"])
        if identity not in starts:
            starts.append(identity)
            rows[-1].update(status="in_progress", run_id="next-run")
        return {"run_id": "next-run"}

    monkeypatch.setattr(assistant_queue, "queue_manager_iteration_choice", queue)
    def tick():
        return continuation.tick_project_continuation(workspace=tmp_path, project_key="DEMO", root=root,
                                                     project_config=config, manager_runner=manager, batch_runner=dispatch)

    # Publication interruption keeps the frozen report, decision and reserved
    # call. A later tick publishes without another model turn or duplicate card.
    fail_publication[0] = True
    assert tick()["reason"] == "discovery_publication_pending"
    assert len(calls) == 1 and not starts and len(rows) == 3
    assert state["project_continuation"]["manager_calls"] == 3
    next_value = tick()
    assert next_value["status"] == "active" and len(calls) == len(starts) == 1
    assert next_value["batches_started"] == 3 and next_value["new_tasks_created"] == 2
    assert next_value["investigation_pending"] is None
    assert rows[1] == research and state["project_task_batches"][run_id] == batch
    assert feedback.is_observed_investigation(next_value, rows[1])
    assert "delivery-seed" in next_value["discovery_consumed"]
    assert calls[0]["investigation_feedback"]["receipt_id"] in next_value["discovery_consumed"]
    tick()
    assert len(calls) == len(starts) == 1 and len(rows) == 3

    def reset():
        state.clear()
        state.update(deepcopy(initial))
        rows[:] = deepcopy(original_rows)
        calls.clear()
        starts.clear()
        report.clear()
        report.update(deepcopy(original_report))
        (artifacts / "report.json").write_text(json.dumps(report))
        terminal.clear()
        terminal.update(deepcopy(original_terminal))
        captured["changed_files"] = []
        result.write_text("The observed HTTP entry point is api.py; independent verification remains outstanding.\n")

    def fail_investigation(report_status="partial"):
        reset()
        rows[1]["status"] = "blocked"
        state["project_task_batches"][run_id]["status"] = "blocked"
        report["all_ok"] = False
        report["jobs"][0].update(status="failed", outcome="needs_human",
                                 summary="The Researcher exited with an error after partial observation.")
        (artifacts / "report.json").write_text(json.dumps(report))
        terminal.update(preserved_task_ids=[], retryable_task_ids=[research["id"]], version="failed-terminal-fixture-v1")
        if report_status == "missing":
            result.unlink()
        elif report_status == "empty":
            result.write_text("")

    # A terminal failed research attempt is feedback, not success and not an
    # implicit retry. The original failure, delivered seed and reports survive.
    for report_status in ("partial", "missing", "empty"):
        fail_investigation(report_status)
        before_rows, before_batch = deepcopy(rows), deepcopy(state["project_task_batches"][run_id])
        fail_publication[0] = True
        assert tick()["reason"] == "discovery_publication_pending"
        assert len(calls) == 1 and not starts
        observed = calls[0]["investigation_feedback"]
        assert observed["worker_status"] == "failed" and observed["task_status"] == "blocked"
        assert observed["worker_outcome"] == "needs_human" and observed["report_status"] == report_status
        if report_status == "missing":
            assert observed["report_digest"] is None
        else:
            assert observed["report_digest"].startswith("sha256:")
        resumed = tick()
        assert resumed["status"] == "active" and len(calls) == len(starts) == 1
        assert rows[:2] == before_rows and state["project_task_batches"][run_id] == before_batch
        assert rows[-1]["role"] == "research" and rows[-1]["execution_policy"] == "read_only"
        assert rows[-1]["dependencies"] == [seed["id"]]
        assert feedback.is_observed_investigation(resumed, rows[1])
        assert resumed["manager_calls"] == 3 and resumed["new_tasks_created"] == 2
        tick()
        assert len(calls) == len(starts) == 1 and len(rows) == 3

    fail_investigation()
    stop_after_feedback[0] = True
    assert tick()["reason"] == "discovery_no_followup" and len(calls) == 1 and not starts
    assert rows[1]["status"] == "blocked" and not rows[1]["delivery_verified"]
    tick()
    assert len(calls) == 1
    stop_after_feedback[0] = False

    # Failed feedback cannot manufacture process retirement, replace captured
    # workspace evidence, bypass quality work or revive stopped grants.
    fail_investigation()
    terminal["unknown_task_ids"] = [research["id"]]
    assert tick()["status"] == "blocked" and not calls and not starts
    fail_investigation()
    captured["changed_files"] = ["README.md"]
    assert tick()["status"] == "blocked" and not calls and not starts
    fail_investigation()
    report["jobs"][0]["attribution_status"] = "unknown"
    (artifacts / "report.json").write_text(json.dumps(report))
    assert tick()["status"] == "blocked" and not calls and not starts
    fail_investigation()
    terminal["can_abandon"] = False  # A quality/verification process is not quiescent.
    assert tick()["status"] == "blocked" and not calls and not starts
    fail_investigation()
    state["project_continuation"]["manager_calls"] = 5
    assert tick()["status"] == "exhausted" and not calls and not starts
    fail_investigation()
    state["project_continuation"].update(status="stopped", reason="owner_stopped")
    assert tick()["status"] == "stopped" and not calls and not starts
    fail_investigation()
    stop_during_decision[0] = True
    assert tick()["status"] == "stopped" and len(calls) == 1 and not starts and len(rows) == 2
    stop_during_decision[0] = False
    fail_investigation()
    change_during_decision[0] = True
    assert tick()["status"] == "blocked" and len(calls) == 1 and not starts and len(rows) == 2
    change_during_decision[0] = False

    reset()
    change_during_decision[0] = True
    assert tick()["status"] == "blocked"
    assert len(calls) == 1 and not starts and len(rows) == 2
    assert state["project_continuation"]["manager_calls"] == 3
    change_during_decision[0] = False
    reset()
    stop_during_decision[0] = True
    assert tick()["status"] == "stopped"
    assert len(calls) == 1 and not starts and len(rows) == 2
    stop_during_decision[0] = False
    reset()
    state["project_continuation"]["manager_calls"] = 5
    assert tick()["status"] == "exhausted" and not calls and not starts
    reset()
    terminal["unknown_task_ids"] = [research["id"]]
    assert tick()["status"] == "blocked" and not calls and not starts
    terminal["unknown_task_ids"] = []
    reset()
    captured["changed_files"] = ["README.md"]
    assert tick()["status"] == "blocked" and not calls and not starts
    captured["changed_files"] = []
    reset()
    result.unlink()
    # A completed Worker without a report proves termination, not findings.
    assert tick()["status"] == "active" and len(calls) == len(starts) == 1
    assert calls[0]["investigation_feedback"]["report_status"] == "missing"
    assert calls[0]["investigation_feedback"]["report"] == ""
    assert rows[:2] == original_rows  # No path promotes research to done.
    reset()
    result.write_bytes(b"\xff")
    assert tick()["status"] == "blocked" and not calls and not starts

    # Observation references inform decisions; they never count as completed
    # task dependencies or permit a response to omit the actual new feedback.
    selection = {"new_task": dict(title="Verify reported entry", description="Verify the entry inferred from the supplied report.",
                 acceptance_criteria=["Entry checked"], write_domains=["src/api.py"], dependencies=[seed["id"]],
                 source_refs=["delivery-seed"]), "rationale": "Use current evidence to decide the next bounded task."}
    packet = dict(discovery=value["discovery"], allowed_source_refs=["delivery-seed", "report-ref"],
                  authorized_done_task_ids=[seed["id"]], authorized_task_ids=value["task_ids"],
                  verified_outcome=outcome, investigation_feedback={"receipt_id": "report-ref"})
    with pytest.raises(ValueError, match="observation"):
        discovery.validate_discovered_task(packet, selection)
    selection["new_task"]["source_refs"].append("report-ref")
    selection["new_task"]["dependencies"].append(research["id"])
    with pytest.raises(ValueError, match="dependencies"):
        discovery.validate_discovered_task(packet, selection)
