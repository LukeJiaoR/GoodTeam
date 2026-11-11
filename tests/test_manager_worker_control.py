"""Original chat routes exact explicit controls; Runtime responses are fixtures."""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from goodteam_os import assistant_requests, task_assistant, task_assistant_session, workers


def test_project_chat_control_preserves_target_receipt_and_no_resend(tmp_path, monkeypatch):
    state = {"assistant_requests": {}, "timeline": [], "project_task_batches": {
        "batch": {"expected_continuation_id": "grant"}}, "project_continuation": {"id": "grant", "status": "active"}}
    invocation = dict(job_id="job-a", run_id="dispatch-a", batch_run_id="batch", adapter="pi",
                      current_dispatch_attempt=True, status="running")
    current = [invocation, {**invocation, "job_id": "job-b"}]
    in_mutation = [False]
    stop_before_admit = [False]
    stop_after_enqueue = [False]
    class Store:
        def __init__(self, *args): pass
        def read(self): return deepcopy(state)
        def _mutate(self, operation):
            if operation.__name__ == "admit" and stop_before_admit[0]:
                state["project_continuation"]["status"] = "stopped"
            in_mutation[0] = True
            try:
                saved = operation(deepcopy(state))
                state.clear()
                state.update(saved)
                return self.read()
            finally:
                in_mutation[0] = False
        def append_message(self, message_id, role, text):
            state["timeline"].append(dict(message_id=message_id, role=role, content=text))
        def append_event(self, message_id, event, *, content, details):
            state["timeline"].append(dict(message_id=message_id, event=event, content=content, details=details))
    monkeypatch.setattr(task_assistant_session, "TaskAssistantSessionStore", Store)
    monkeypatch.setattr(assistant_requests, "conversation_lock", lambda *_: nullcontext())
    monkeypatch.setattr(task_assistant, "worker_invocation_context", lambda *args: {"current": deepcopy(current)})
    calls, durable = [], {}
    reply = {"accepted": True, "control_state": "rpc_accepted"}
    failure = [False]
    class Jobs:
        def __init__(self, *args): pass
        def find_project_session_control(self, control_id, *, project_key): return durable.get(control_id)
        def session_control_result(self, *args):
            assert not in_mutation[0]
            if stop_after_enqueue[0]:
                state["project_continuation"]["status"] = "stopped"
                return {"accepted": True, "control_state": "rpc_accepted"}
            return deepcopy(reply)
        def session_control(self, job_id, action, **kwargs):
            assert in_mutation[0] and kwargs["wait_for_ack"] is False
            calls.append((job_id, action, kwargs))
            if failure[0]:
                raise ValueError("skill_runtime_not_connected")
            durable[kwargs["control_id"]] = (job_id, {**kwargs, "action": action})
            return deepcopy(reply)
    monkeypatch.setattr(workers, "WorkerJobStore", Jobs)
    service = SimpleNamespace(workspace=tmp_path, session=lambda *args, **kwargs: deepcopy(state))
    def send(text, identifier):
        return assistant_requests.execute_message(service, project_key="DEMO", root=tmp_path,
                                                   text=text, message_id=identifier)
    send("/worker job-a steer Check the original task evidence", "message-1")
    assert len(calls) == 1 and calls[0][0] == "job-a"
    receipt = state["timeline"][-1]["details"]["worker_control"]
    assert receipt["control_state"] == "rpc_accepted" and not receipt["execution_completed"]
    assert not receipt["worker_reply_received"]
    send("/worker job-a steer Check the original task evidence", "message-1")
    assert len(calls) == 1
    with pytest.raises(ValueError, match="different"):
        send("/worker job-b steer Different target", "message-1")
    assert len(calls) == 1

    send("/worker foreign-job steer Not this project", "foreign")
    assert state["timeline"][-1]["details"]["worker_control"]["control_state"] == "rejected"
    assert len(calls) == 1
    invocation["current_dispatch_attempt"] = False
    send("/worker job-a follow_up Old attempt", "old")
    assert len(calls) == 1
    invocation["current_dispatch_attempt"] = True
    state["project_continuation"]["status"] = "stopped"
    send("/worker job-a steer Do not revive", "stopped")
    assert len(calls) == 1
    # An explicit abort can stop the still-live process after continuation Stop.
    send("/worker job-a abort User requests process termination", "abort")
    assert len(calls) == 2 and calls[-1][1] == "abort"
    assert state["project_continuation"]["status"] == "stopped"
    state["project_continuation"]["status"] = "active"
    invocation["adapter"] = "codex"
    send("/worker job-a steer Unsupported Runtime", "unsupported")
    assert len(calls) == 2 and "does not support" in state["timeline"][-1]["content"]
    invocation["adapter"] = "pi"
    reply.update(accepted=False, control_state="queued")
    send("/worker job-a follow_up A bounded question", "queued")
    assert len(calls) == 3 and state["timeline"][-1]["details"]["actions_applied"] is False
    stop_after_enqueue[0] = True
    send("/worker job-a follow_up Admitted before Stop", "late-ack")
    assert state["project_continuation"]["status"] == "stopped"
    assert state["timeline"][-1]["details"]["worker_control"]["control_state"] == "rpc_accepted"
    stop_after_enqueue[0] = False
    state["project_continuation"]["status"] = "active"
    stop_before_admit[0] = True
    before = len(calls)
    send("/worker job-a steer Stop wins admission ordering", "stop-before-admit")
    assert len(calls) == before
    stop_before_admit[0] = False
    state["project_continuation"]["status"] = "active"
    failure[0] = True
    send("/worker job-a steer Protected input is still protected", "admission")
    assert len(calls) == 5 and "skill_runtime_not_connected" in state["timeline"][-1]["content"]
    send("/worker job-a steer Protected input is still protected", "admission")
    assert len(calls) == 5  # no downgrade or automatic uncertain retry
    current.clear()
    send("/worker job-a follow_up Historical job", "ended")
    assert len(calls) == 5


def test_pi_enqueue_and_receipt_wait_are_separate_without_skipping_input_admission(tmp_path, monkeypatch):
    store = workers.WorkerJobStore(tmp_path)
    job_root = store.root / "job-a"
    job_root.mkdir(parents=True)
    job = {"id": "job-a", "run_id": "dispatch-a"}
    admissions = []
    monkeypatch.setattr(store, "_session_job", lambda job_id: (job_root, job))
    monkeypatch.setattr(store, "admit_task_input", lambda job_id, value: admissions.append(job_id))
    monkeypatch.setattr(store, "session_status", lambda job_id: {"job_id": job_id, "session_status": "running", "process_alive": True})
    monkeypatch.setenv("GOODTEAM_PI_CONTROL_ACK_TIMEOUT", "0")
    result = store.session_control("job-a", "steer", message="Bounded update", control_id="control-test-abcdefgh",
                                   wait_for_ack=False)
    assert admissions == ["job-a"] and result["control_state"] == "queued"
    queue = (job_root / "pi_control_queue.jsonl").read_bytes()
    result = store.session_control_result("job-a", "control-test-abcdefgh", "steer")
    assert result["control_state"] == "queued" and (job_root / "pi_control_queue.jsonl").read_bytes() == queue
    store._append_pi_control_ack(job_root, "control-test-abcdefgh", "rpc_accepted")
    result = store.session_control_result("job-a", "control-test-abcdefgh", "steer")
    assert result["control_state"] == "rpc_accepted" and result["accepted"] is True
    assert (job_root / "pi_control_queue.jsonl").read_bytes() == queue
