"""Observation input contract, not autonomous model/provider acceptance."""
from copy import deepcopy
from types import SimpleNamespace

from goodteam_os import task_assistant
from goodteam_os.project_running_observation import observe_running_batch, running_observation_context
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


def test_running_observation_deduplicates_and_preserves_budget_attempt_and_stop(tmp_path, monkeypatch):
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    value = {"id": "grant", "status": "active", "authority": "fixed", "manager_calls": 1, "max_manager_calls": 2,
             "pending_batch": {"run_id": "batch", "task_ids": ["task-a"]}}
    batch = {"run_id": "batch", "status": "running", "confirmed": True, "expected_continuation_id": "grant",
             "project_root": str(tmp_path.resolve()), "dispatch_run_id": "attempt-a"}
    store._mutate(lambda s: {**s, "project_continuation": deepcopy(value), "project_task_batches": {"batch": batch}})
    row = {"job_id": "job-a", "run_id": "attempt-a", "status": "running", "runtime_observation": {
        "available": True, "observed_at": "t1", "evidence": {"process_alive": True, "heartbeat": "t1"}}}
    observations = {"available": True, "current": [row, {**row, "job_id": "foreign", "run_id": "foreign"}]}
    def scoped_observation(*args, **kwargs):
        assert kwargs["run_id"] in {"attempt-a", "attempt-b"}
        assert kwargs["observe_runtime"] is True
        return deepcopy(observations)
    monkeypatch.setattr(task_assistant, "worker_invocation_context", scoped_observation)
    def observe(): return observe_running_batch(store=store, root=tmp_path, project_key="DEMO", value=value)
    first = observe()
    receipt = first["running_observation"]
    assert [r["job_id"] for r in receipt["packet"]["workers"]] == ["job-a"]
    assert first["manager_calls"] == 1 and first["pending_batch"] == value["pending_batch"]
    assert first["status"] == "active" and not receipt["manager_called"] and not receipt["action_executed"]
    row["runtime_observation"]["evidence"]["heartbeat"] = "t2"
    row["runtime_observation"]["observed_at"] = "t2"
    assert observe()["running_observation"] == receipt
    row["runtime_observation"]["evidence"]["process_alive"] = False
    changed = observe()
    assert changed["running_observation"]["digest"] != receipt["digest"]
    assert changed["status"] == "active" and changed["manager_calls"] == 1
    ctx = running_observation_context(store.read())
    assert ctx["current_binding"] and ctx["remaining_manager_calls"] == 1 and not ctx["automatic_consumer_available"]
    # Exhausted budget does not authorize an observation model call or stop live work.
    store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"], "manager_calls": 2}})
    assert running_observation_context(store.read())["remaining_manager_calls"] == 0
    # Recovery invalidates old evidence, and old-attempt records are not carried forward.
    store._mutate(lambda s: {**s, "project_task_batches": {"batch": {**batch, "current_dispatch_run_id": "attempt-b"}}})
    assert not running_observation_context(store.read())["current_binding"]
    assert observe()["running_observation"]["packet"]["workers"] == []
    prior = store.read()["project_continuation"]["running_observation"]
    original_mutate = store._mutate
    def stopped_before_save(operation):
        original_mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"], "status": "stopped"}})
        return original_mutate(operation)
    monkeypatch.setattr(store, "_mutate", stopped_before_save)
    stopped = observe()
    assert stopped["status"] == "stopped" and stopped["running_observation"] == prior
    assert not running_observation_context(store.read())["current_binding"]


def test_existing_tick_collects_candidate_without_new_manager_call(tmp_path, monkeypatch):
    from goodteam_os import project_iteration_continuation as c
    from contextlib import nullcontext
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    value = {"id": "grant", "status": "active", "authority": "fixed", "manager_calls": 1, "max_manager_calls": 2,
        "task_ids": ["task-a"], "batches_started": 1,
        "pending_batch": {"run_id": "batch", "task_ids": ["task-a"], "proposal_ids": ["proposal"], "expected_revisions": [1]}}
    batch = {"run_id": "batch", "status": "running", "confirmed": True, "expected_continuation_id": "grant",
             "project_root": str(tmp_path.resolve()), "dispatch_run_id": "attempt-a"}
    store._mutate(lambda s: {**s, "project_continuation": value, "project_task_batches": {"batch": batch},
        "queued_task_proposals": [{"id": "proposal", "project_task_id": "task-a"}]})
    monkeypatch.setattr(c, "goal_guard", lambda *a: nullcontext())
    monkeypatch.setattr(c, "authority_snapshot", lambda *a: "fixed")
    monkeypatch.setattr(c, "require_no_active_project_goal", lambda *a: None)
    monkeypatch.setattr(c, "active_project_run", lambda *a: None)
    task = {"id": "task-a", "status": "in_progress", "run_id": "batch"}
    monkeypatch.setattr(c, "_scope_tasks", lambda *a: ({"task-a": task}, [task]))
    monkeypatch.setattr(c, "LocalTaskStore", lambda *a: SimpleNamespace(list_tasks=lambda: [task]))
    monkeypatch.setattr(task_assistant, "worker_invocation_context", lambda *a, **k: {"available": True, "current": [], "recent_history": []})
    def forbidden_manager(**kwargs): raise AssertionError("No new Manager invocation")
    result = c.tick_project_continuation(workspace=tmp_path, root=tmp_path, project_key="DEMO", project_config=tmp_path/'demo.yaml',
        manager_runner=forbidden_manager, batch_runner=lambda **k: {"run_id": "batch"})
    assert result["status"] == "active" and result["manager_calls"] == 1
    assert result["running_observation"]["packet"]["binding"]["dispatch_run_id"] == "attempt-a"
    assert result["pending_batch"]["run_id"] == "batch"
    # Same public service tick can exercise the injected contract without
    # enabling a production Runtime or invoking idle-only planning.
    judgments = []
    def observation_runner(**kwargs):
        judgments.append(kwargs)
        return {"assessment": "wait", "rationale": "No attributable Worker response is available.", "observed_job_ids": []}
    judged = c.tick_project_continuation(workspace=tmp_path, root=tmp_path, project_key="DEMO", project_config=tmp_path/'demo.yaml',
        manager_runner=forbidden_manager, batch_runner=lambda **k: {"run_id": "batch"}, observation_runner=observation_runner)
    assert len(judgments) == 1 and judged["manager_calls"] == 2 and judged["status"] == "active"
    assert next(iter(judged["running_judgments"].values()))["status"] == "recorded"
    # The normal service tick selects the production consumer without injection.
    from goodteam_os import project_observation_runtime
    production_calls = []
    def production(**kwargs):
        production_calls.append(kwargs)
        return kwargs["value"]
    monkeypatch.setattr(project_observation_runtime, "judge_with_production_runtime", production)
    c.tick_project_continuation(workspace=tmp_path, root=tmp_path, project_key="DEMO", project_config=tmp_path/'demo.yaml',
        manager_runner=forbidden_manager, batch_runner=lambda **k: {"run_id": "batch"})
    assert len(production_calls) == 1


def test_running_human_answer_reaches_bounded_consumer_without_waiting_for_batch(tmp_path, monkeypatch):
    import pytest
    from goodteam_os.project_human_feedback import _question_id
    from goodteam_os.project_outcome_discovery import _digest
    from goodteam_os.project_observation_judgment import judge_running_observation

    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    value = {"id": "grant", "authorization_digest": "authorized", "authority": "fixed", "status": "active",
             "manager_calls": 0, "max_manager_calls": 3, "pending_batch": {"run_id": "batch", "task_ids": ["task-a"]}}
    packet = {"project_key": "DEMO"}
    packet["packet_digest"] = _digest(packet)
    question = {"question": "Which audience needs the document?", "why_human": "Owner knows the audience."}
    planning = {"packet": packet, "packet_digest": packet["packet_digest"], "selection": {"human_question": question}}
    identifier = _question_id(value, planning)
    value["human_questions"] = {identifier: {"id": identifier, "planning": planning, "answer": None}}
    batch = {"confirmed": True, "status": "running", "expected_continuation_id": "grant",
             "project_root": str(tmp_path.resolve()), "dispatch_run_id": "attempt-a"}
    store._mutate(lambda s: {**s, "project_continuation": deepcopy(value), "project_task_batches": {"batch": batch}})
    monkeypatch.setattr(task_assistant, "worker_invocation_context", lambda *a, **k: {"available": True, "current": [
        {"job_id": "job-a", "run_id": "attempt-a", "status": "running"}]})
    def observe():
        return observe_running_batch(store=store, root=tmp_path, project_key="DEMO", value=value)
    original = observe()["running_observation"]
    answer = {"question_id": identifier, "project_key": "DEMO", "continuation_id": "grant",
              "authorization_digest": "authorized", "question": question, "source_packet_digest": packet["packet_digest"],
              "message_id": "human-answer", "text": "Internal maintainers only.", "authority_changed": False}
    answer["receipt_id"] = _digest(answer)
    def save_answer(s):
        s["project_continuation"]["human_questions"][identifier]["answer"] = deepcopy(answer)
        return s
    store._mutate(save_answer)
    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        assert kwargs["observation"]["human_context"]["answers"] == [answer]
        assert kwargs["contract"]["actions_authorized"] is False
        return {"assessment": "needs_attention", "rationale": "The answer clarifies the target audience.", "observed_job_ids": ["job-a"]}
    def judge(runner=run):
        return judge_running_observation(store=store, root=tmp_path, value=value, runner=runner, authority_check=lambda: True)
    # Answer arriving after capture prevents stale advice and spends no budget.
    assert judge()["manager_calls"] == 0 and not calls
    fresh = observe()["running_observation"]
    assert fresh["digest"] != original["digest"] and fresh["packet"]["workers"] == original["packet"]["workers"]
    assert fresh["packet"]["human_context"]["questions"] == []
    result = judge()
    assert result["manager_calls"] == 1 and result["status"] == "active" and result["pending_batch"]["run_id"] == "batch"
    assert result["running_judgments"][fresh["digest"]]["status"] == "recorded"
    observe()
    assert judge()["manager_calls"] == 1 and len(calls) == 1
    # Corrupted saved answers cannot be laundered into model input.
    def corrupt(s):
        s["project_continuation"]["human_questions"][identifier]["answer"]["text"] = "Expanded authority"
        return s
    store._mutate(corrupt)
    with pytest.raises(ValueError, match="original question"):
        observe()
    assert len(calls) == 1


def test_human_observation_keeps_newest_answer_not_question_hash_order():
    from goodteam_os.project_human_feedback import _question_id
    from goodteam_os.project_outcome_discovery import _digest
    from goodteam_os.project_running_observation import human_observation_context
    value = {"id": "grant", "authorization_digest": "authorized", "human_questions": {}}
    for index in range(12):
        packet = {"project_key": "DEMO", "ordinal": index}
        packet["packet_digest"] = _digest(packet)
        question = {"question": f"Question {index}", "why_human": "Owner decision"}
        planning = {"packet": packet, "packet_digest": packet["packet_digest"], "selection": {"human_question": question}}
        identifier = _question_id(value, planning)
        answer = {"question_id": identifier, "project_key": "DEMO", "continuation_id": "grant",
            "authorization_digest": "authorized", "question": question, "source_packet_digest": packet["packet_digest"],
            "received_at": f"2026-11-11T12:{index:02}:00+00:00", "text": f"Answer {index}"}
        answer["receipt_id"] = _digest(answer)
        value["human_questions"][identifier] = {"id": identifier, "planning": planning, "answer": answer}
    # File persistence orders hash keys, not reply arrival time.
    value["human_questions"] = dict(sorted(value["human_questions"].items()))
    context = human_observation_context(value)
    assert [answer["text"] for answer in context["answers"]] == [f"Answer {i}" for i in range(2, 12)]
    assert context["omitted_answers"] == 2
