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
from goodteam_os import project_iteration_recovery as recovery
from goodteam_os import task_assistant_session, tasks, team_control_plane, workflow_recovery
# Load API aliases before fixture stores are patched, independent of test order.
from goodteam_os import web


def test_mixed_terminal_results_reenter_manager_without_acceptance_or_retry(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    config = tmp_path / "project.yaml"
    config.write_text("key: DEMO\n")
    run_id, dispatch_id = "task-batch-" + "a" * 64, "dispatch-" + "b" * 64
    head, authority = "c" * 40, {"fixture": "original-authority"}
    seed = dict(id="DEMO-1", title="Delivered seed", description="The first real delivery is a supplied fixture.",
                role="implementation", dependencies=[], acceptance_criteria=["Seed exists"],
                write_domains=["src"], execution_policy="workspace_write", external_refs=[],
                status="done", delivery_verified=True, run_id="seed-run")
    research = dict(id="DEMO-2", title="Check a missing fact", description="Investigate the exact current entry point.",
                    role="research", dependencies=[seed["id"]], acceptance_criteria=["Identify the entry point"],
                    write_domains=[], execution_policy="read_only", external_refs=["discovery-fixture"],
                    status="review", delivery_verified=False, run_id=run_id, execution_origin="task_assistant_batch",
                    dispatch_job_id="research-job", dispatch_run_id=dispatch_id)
    research.update(status="blocked")
    companion = {**deepcopy(research), "id": "DEMO-3", "title": "Successful companion",
        "role": "implementation", "execution_policy": "workspace_write", "write_domains": ["src/other.py"],
        "status": "review", "dispatch_job_id": "success-job"}
    rows = [deepcopy(seed), deepcopy(research), deepcopy(companion)]
    value = dict(id="original-grant", status="active", reason="awaiting_verified_delivery", task_ids=[row["id"] for row in rows],
                 task_intents={row["id"]: continuation._intent(row) for row in rows},
                 seed_task_ids=[seed["id"]], seed_task_intents={seed["id"]: continuation._intent(seed)},
                 approved_backlog_version="sha256:" + "d" * 64, authority=authority,
                 max_batches=4, max_manager_calls=5, batches_started=2, manager_calls=2,
                 new_tasks_created=1, discovered_task_ids=[],
                 discovery=dict(objective="Resolve the entry-point uncertainty and improve the delivered project.",
                                allowed_write_domains=["src"], max_new_tasks=3, max_planning_calls=5,
                                allow_investigation=True), discovery_pending=None,
                 discovery_consumed=["delivery-seed"], planning_backlog_version="",
                 pending_batch=dict(task_ids=[research["id"], companion["id"]], proposal_ids=["research-proposal"],
                                    expected_revisions={"research-proposal": 1}, run_id=run_id))
    value["authorization_digest"] = discovery.authorization_digest(value)
    batch = dict(run_id=run_id, dispatch_run_id=dispatch_id, confirmed=True, status="blocked",
                 project_root=str(root), expected_continuation_id=value["id"], expected_authority=authority,
                 task_ids=[research["id"], companion["id"]],
                 task_bindings=[dict(task_id=t["id"], task_intent=tasks.project_task_intent(t)) for t in rows[1:]],
                 tracks=[dict(project_task_id=t["id"], role=t["role"], execution_policy=t["execution_policy"]) for t in rows[1:]])
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
    report = dict(run_id=dispatch_id, project_root=str(root), all_ok=False, jobs=[
        dict(job_id="research-job", role="research", project_task_id=research["id"], outcome="needs_human", status="failed", attribution_status="captured"),
        dict(job_id="success-job", role="implementation", project_task_id=companion["id"], outcome="ok", attribution_status="captured")])
    (artifacts / "report.json").write_text(json.dumps(report))
    result = root / ".goodteam/workers/jobs/research-job/result.md"
    result.parent.mkdir(parents=True)
    result.write_text("The observed HTTP entry point is api.py; independent verification remains outstanding.\n")

    def read_bytes(path, *, maximum=2 * 1024 * 1024):
        raw = Path(path).read_bytes() if Path(path).exists() else None
        if raw is not None and len(raw) > maximum:
            raise ValueError("Fixture storage limit exceeded")
        return raw

    terminal = dict(unknown_task_ids=[], retryable_task_ids=[research["id"]], preserved_task_ids=[companion["id"]], can_abandon=True, version="terminal-fixture-v1")
    captured = dict(changed_files=[])
    monkeypatch.setattr(memory_storage, "read_bytes", read_bytes)
    monkeypatch.setattr(dispatch_workspace, "dispatch_artifact_root", lambda *_: artifacts)
    monkeypatch.setattr(dispatch_workspace, "prepare_dispatch_workspace", lambda *_: SimpleNamespace(source_head=lambda: head))
    monkeypatch.setattr(dispatch_workspace, "verify_dispatch_result", lambda *_: deepcopy(captured))
    monkeypatch.setattr(recovery, "project_batch_recovery_evidence", lambda **_: deepcopy(terminal))
    monkeypatch.setattr(recovery, "effective_dispatch_id", lambda row: row["dispatch_run_id"])
    initial, original_rows = deepcopy(state), deepcopy(rows)
    original_report, original_terminal, original_outcome = deepcopy(report), deepcopy(terminal), deepcopy(outcome)
    calls, starts = [], []
    fail_publication = [False]
    stop_during_decision = [False]
    change_during_decision = [False]
    stop_after_feedback = [False]
    unrelated_change = [False]
    related_change = [False]

    undelivered_mode = [False]

    def manager(**kwargs):
        calls.append(deepcopy(kwargs["context"]))
        packet = kwargs["context"]
        observation = packet.get("batch_feedback") or packet["batch_history"][-1]
        if undelivered_mode[0]:
            assert not packet["authorized_done_task_ids"]
            assert packet["verified_outcome"] is None
            assert packet["undelivered_source"]["delivery_verified"] is False
        else:
            assert seed["id"] in packet["authorized_done_task_ids"]
        assert observation["receipt_id"] in packet["allowed_source_refs"]
        assert len(observation["tasks"]) == 2
        assert all(not t["delivery_verified"] and t["task_acceptance"] == "not_assessed" for t in observation["tasks"])
        if unrelated_change[0]:
            rows.append({**deepcopy(seed), "id": "OTHER-1", "title": "Unrelated project progress"})
        if related_change[0]:
            rows[2]["acceptance_criteria"] = ["Different work contract"]
        if stop_during_decision[0]:
            state["project_continuation"]["status"] = "stopped"
        if change_during_decision[0]:
            result.write_text("tampered report")
        if stop_after_feedback[0]:
            return {"selection": {"new_task": None, "rationale": "No safe authorized follow-up; unresolved results still require a decision."}}
        return {"selection": {"new_task": dict(title="Resolve the missing fact", description="Read-only independent diagnostic of the partial result; do not retry original jobs.",
            task_type="investigation", acceptance_criteria=["Return evidence and remaining uncertainty"],
            write_domains=[], dependencies=[] if undelivered_mode[0] else [seed["id"]], source_refs=[observation["receipt_id"]] if undelivered_mode[0] else ["delivery-seed", observation["receipt_id"]]),
            "rationale": "Inspect the partial result while retaining the successful companion for independent review."}}

    manager_action = manager

    def queue(**kwargs):
        identifier = kwargs["expected_task_ids"][0]
        proposal = dict(id="followup-" + identifier, revision=1, project_task_id=identifier)
        if not any(item["id"] == proposal["id"] for item in state["queued_task_proposals"]):
            state["queued_task_proposals"].append(proposal)
        if fail_publication[0]:
            fail_publication[0] = False
            raise OSError("fixture: queue committed, acknowledgement lost")
        return dict(queued_task_ids=[identifier], queued_proposal_ids=[proposal["id"]], task_assistant_session=deepcopy(state))

    def dispatch(**kwargs):
        identity = tuple(kwargs["proposal_ids"])
        proposal = next(item for item in state["queued_task_proposals"] if item["id"] == identity[0])
        task = next(item for item in rows if item["id"] == proposal["project_task_id"])
        next_run = "next-run-" + task["id"]
        if identity not in starts:
            starts.append(identity)
            task.update(status="in_progress", run_id=next_run)
            state["project_task_batches"][next_run] = dict(
                run_id=next_run, status="running", expected_continuation_id=value["id"],
                task_ids=[task["id"]], tracks=[dict(project_task_id=task["id"], role=task["role"],
                execution_policy=task["execution_policy"])])
        return {"run_id": next_run}

    monkeypatch.setattr(assistant_queue, "queue_manager_iteration_choice", queue)
    def tick():
        return continuation.tick_project_continuation(workspace=tmp_path, project_key="DEMO", root=root,
                                                     project_config=config, manager_runner=manager_action, batch_runner=dispatch)

    def reset():
        state.clear()
        state.update(deepcopy(initial))
        rows[:] = deepcopy(original_rows)
        outcome.clear()
        outcome.update(deepcopy(original_outcome))
        calls.clear()
        starts.clear()
        result.write_text("Partial observation about api.py")

    fail_publication[0] = True
    assert tick()["reason"] == "discovery_publication_pending"
    assert len(calls) == 1 and not starts
    assert tick()["status"] == "active" and len(calls) == len(starts) == 1
    receipt = calls[0]["batch_feedback"]
    assert [item["worker_status"] for item in receipt["tasks"]] == ["failed", "completed"]
    assert [item["report_status"] for item in receipt["tasks"]] == ["partial", "missing"]
    assert rows[:3] == original_rows and state["project_task_batches"][run_id] == batch
    assert receipt["receipt_id"] in state["project_continuation"]["discovery_consumed"]
    tick()
    assert len(calls) == len(starts) == 1

    # A later diagnostic/delivery trigger must not erase older unresolved results.
    grant = state["project_continuation"]
    grant.update(pending_batch=None, reason="awaiting_human_decision")
    rows[-1].update(status="done", delivery_verified=True)
    outcome["delivery"]["receipt_id"] = "delivery-diagnostic"
    stop_after_feedback[0] = True
    assert tick()["reason"] == "observed_results_require_resolution"
    assert len(calls) == 2 and not calls[-1].get("batch_feedback")
    assert calls[-1]["unresolved_batch_task_ids"] == [research["id"], companion["id"]]
    # Explicit resolution in the real ledger removes that blocker, not history deletion.
    for task in rows[1:3]:
        task.update(status="done", delivery_verified=True)
    grant = state["project_continuation"]
    grant.update(status="active", reason="awaiting_human_decision")
    outcome["delivery"]["receipt_id"] = "delivery-resolved"
    assert tick()["status"] == "completed"

    reset()
    stop_after_feedback[0] = True
    assert tick()["reason"] == "observed_results_require_resolution"
    assert state["project_continuation"]["status"] == "blocked"
    tick()
    assert len(calls) == 1 and not starts and rows == original_rows
    stop_after_feedback[0] = False

    reset()
    terminal["unknown_task_ids"] = [research["id"]]
    assert tick()["status"] == "blocked" and not calls and not starts
    assert state["project_continuation"]["pending_batch"] is not None
    terminal["unknown_task_ids"] = []

    reset()
    change_during_decision[0] = True
    assert tick()["status"] == "blocked" and len(calls) == 1 and not starts
    change_during_decision[0] = False
    reset()
    stop_during_decision[0] = True
    assert tick()["status"] == "stopped" and not starts
    stop_during_decision[0] = False

    reset()
    rows[1]["dispatch_job_id"] = "wrong-job"
    assert tick()["status"] == "blocked" and not calls and not starts
    reset()
    unrelated_change[0] = True
    assert tick()["status"] == "active" and len(calls) == len(starts) == 1
    assert "OTHER-1" not in calls[0]["authorized_task_ids"]
    unrelated_change[0] = False
    reset()
    related_change[0] = True
    assert tick()["status"] == "blocked" and len(calls) == 1 and not starts
    related_change[0] = False
    reset()
    def no_seed(**kwargs):
        raise discovery.NoVerifiedDelivery("No verified delivery seed")
    monkeypatch.setattr(discovery, "verified_outcome", no_seed)
    assert tick()["status"] == "blocked" and not calls and not starts
    assert rows == original_rows


    # A newly opted-in grant may judge real terminal feedback without pretending
    # the failed seed was delivered. The exact same publication/replay path runs.
    reset()
    undelivered_mode[0] = True
    rows[0].update(status="blocked", delivery_verified=False)
    grant = state["project_continuation"]
    grant["discovery"]["allow_undelivered_feedback"] = True
    grant["authorization_digest"] = discovery.authorization_digest(grant)
    fail_publication[0] = True
    assert tick()["reason"] == "discovery_publication_pending"
    assert len(calls) == 1 and not starts
    assert tick()["status"] == "active" and len(calls) == len(starts) == 1
    packet = calls[0]
    assert "NO accepted" in planner._discovery_prompt(packet)
    assert packet["undelivered_source"]["receipt_id"] == packet["batch_feedback"]["receipt_id"]
    assert rows[-1]["dependencies"] == []
    assert not rows[0]["delivery_verified"]
    proposed = {"new_task": dict(title="Bounded next diagnosis", description="Inspect the captured failure without retrying the original invocation.",
        task_type="investigation", acceptance_criteria=["Report original failure evidence"],
        write_domains=[], dependencies=[seed["id"]], source_refs=[packet["batch_feedback"]["receipt_id"]]),
        "rationale": "Use original feedback, not invented delivery."}
    with pytest.raises(ValueError):
        discovery.validate_discovered_task(packet, proposed)
    # Missing captured evidence cannot create an empty-project discovery source.
    with pytest.raises(ValueError):
        discovery._undelivered_source(workspace=tmp_path, project_key="DEMO", root=root,
            value={**grant, "batch_feedback_pending": None})


    reset()
    undelivered_mode[0] = True
    rows[0].update(status="blocked", delivery_verified=False)
    grant = state["project_continuation"]
    grant["discovery"]["allow_undelivered_feedback"] = True
    grant["authorization_digest"] = discovery.authorization_digest(grant)
    def invalid_delivery(**kwargs):
        raise ValueError("Existing delivery receipt cannot be verified")
    monkeypatch.setattr(discovery, "verified_outcome", invalid_delivery)
    assert tick()["status"] == "blocked" and not calls and not starts


    # The same no-seed grant still cannot cross liveness, Stop or changed-source
    # boundaries, and a decline leaves unresolved work blocked (not complete).
    monkeypatch.setattr(discovery, "verified_outcome", no_seed)
    for mode in ("unknown", "stop", "changed_report", "decline", "budget"):
        reset()
        rows[0].update(status="blocked", delivery_verified=False)
        grant = state["project_continuation"]
        grant["discovery"]["allow_undelivered_feedback"] = True
        grant["authorization_digest"] = discovery.authorization_digest(grant)
        if mode == "unknown":
            terminal["unknown_task_ids"] = [research["id"]]
        elif mode == "stop":
            stop_during_decision[0] = True
        elif mode == "changed_report":
            change_during_decision[0] = True
        elif mode == "decline":
            stop_after_feedback[0] = True
        elif mode == "budget":
            grant["manager_calls"] = grant["max_manager_calls"]
        observed = tick()
        assert not starts
        if mode == "stop":
            assert observed["status"] == "stopped"
        elif mode == "budget":
            assert observed["status"] == "exhausted" and not calls
        else:
            assert observed["status"] == "blocked"
        if mode == "unknown":
            assert not calls
        terminal["unknown_task_ids"] = []
        stop_during_decision[0] = change_during_decision[0] = stop_after_feedback[0] = False

    # Ask/answer consumes pending batch trigger, but not its verified source
    # anchor. The next real Manager tick can still decide against that source.
    reset()
    rows[0].update(status="blocked", delivery_verified=False)
    grant = state["project_continuation"]
    grant["discovery"]["allow_undelivered_feedback"] = True
    grant["authorization_digest"] = discovery.authorization_digest(grant)
    def ask_manager(**kwargs):
        calls.append(deepcopy(kwargs["context"]))
        return {"selection": {"new_task": None, "rationale": "The original failure needs the owner's scope decision.",
            "human_question": {"question": "Should the diagnostic cover the internal client only?",
                "why_human": "The client audience is not determined by the failed invocation."}}}
    manager_action = ask_manager
    assert tick()["reason"] == "awaiting_human_decision"
    grant = state["project_continuation"]
    assert not grant["batch_feedback_pending"] and grant["undelivered_feedback_anchor"]
    record = next(iter(grant["human_questions"].values()))
    answer = dict(question_id=record["id"], project_key="DEMO", continuation_id=grant["id"],
        authorization_digest=grant["authorization_digest"],
        question=record["planning"]["selection"]["human_question"],
        source_packet_digest=record["planning"]["packet_digest"], message_id="fixture-human-answer",
        text="Internal client only.", authority_changed=False, received_at="fixture")
    answer["receipt_id"] = discovery._digest(answer)
    record["answer"] = answer
    grant["reason"] = "human_answer_received"
    manager_action = manager
    assert tick()["status"] == "active" and len(calls) == 2 and len(starts) == 1
    assert calls[-1]["human_answers"] == [answer]
    assert calls[-1]["undelivered_source"] == calls[0]["undelivered_source"]
    assert not calls[-1].get("batch_feedback")

    # The same retained anchor survives a read-only feedback-only source lookup;
    # source evidence is still checked, not trusted merely because it is history.
    grant = state["project_continuation"]
    source = discovery._undelivered_source(workspace=tmp_path, project_key="DEMO", root=root,
        value={**grant, "investigation_pending": "new-read-only-result"})
    assert source == calls[0]["undelivered_source"]
    result.write_text("changed original terminal evidence")
    with pytest.raises(ValueError):
        discovery._undelivered_source(workspace=tmp_path, project_key="DEMO", root=root,
            value={**grant, "investigation_pending": "new-read-only-result"})
