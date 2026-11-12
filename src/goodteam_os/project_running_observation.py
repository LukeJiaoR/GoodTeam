"""Deduplicated observation input on an existing continuation, not a new scheduler."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from datetime import datetime, timezone


def _binding(session, root):
    value = session.get("project_continuation") or {}
    pending = value.get("pending_batch") or {}
    batch = session.get("project_task_batches", {}).get(pending.get("run_id")) or {}
    if (value.get("status") != "active" or batch.get("confirmed") is not True
            or batch.get("expected_continuation_id") != value.get("id")
            or batch.get("project_root") != str(Path(root).resolve())
            or batch.get("status") not in {"claimed", "running"}):
        return None
    attempt = batch.get("current_dispatch_run_id") or batch.get("dispatch_run_id")
    if not attempt:
        return None
    return {"continuation_id": value["id"], "batch_run_id": pending["run_id"], "dispatch_run_id": attempt,
            "authority": value.get("authority"), "task_ids": list(pending.get("task_ids", [])),
            "project_root_digest": sha256(str(Path(root).resolve()).encode()).hexdigest()}



def human_observation_context(value):
    """Reuse validated original-grant receipts, never treat answers as authority."""
    from goodteam_os.project_human_feedback import human_feedback, question_context

    answers = sorted(human_feedback(value), key=lambda item: (item.get("received_at", ""), item["receipt_id"]))
    questions = question_context(value)
    return {"answers": answers[-10:], "questions": questions[-10:],
            "omitted_answers": max(0, len(answers) - 10),
            "omitted_questions": max(0, len(questions) - 10)}


def human_observation_current(value, packet):
    captured = packet.get("human_context")
    current = human_observation_context(value)
    # Older persisted candidates carried no Human context. They are usable only
    # when no Human evidence exists; a new tick captures any new information.
    return captured == current if captured is not None else not (current["answers"] or current["questions"])


def judgment_human_current(value, record):
    """Own newly published unanswered question is not concurrent Human input."""
    identifier = record.get("human_question_id")
    if not identifier:
        return human_observation_current(value, record["packet"])
    from goodteam_os.project_human_feedback import _validated_record
    question = _validated_record(value, (value.get("human_questions") or {}).get(identifier))
    planning = question["planning"]
    packet = planning["packet"]
    own = (planning.get("source_kind") == "running_observation"
           and packet.get("observation_id") == record["observation_digest"]
           and packet.get("observation") == record["packet"])
    if own and (question.get("answer") is not None or question.get("resolution")):
        return False
    if human_observation_current(value, record["packet"]):
        return True  # A duplicate proposal may refer to an existing captured question.
    if not own:
        return False
    without_own = {**value, "human_questions": {key: item for key, item in value["human_questions"].items() if key != identifier}}
    return human_observation_current(without_own, record["packet"])


def observe_running_batch(*, store, root, project_key, value):
    """Called only by the existing authorized tick after its authority checks.

    No Manager call is made/reserved here. A future consumer must atomically
    reserve from the original budget and revalidate this binding before use.
    """
    from goodteam_os.task_assistant import worker_invocation_context

    session = store.read()
    binding = _binding(session, root)
    if not binding or binding["continuation_id"] != value.get("id") or binding["authority"] != value.get("authority"):
        return session.get("project_continuation") or value
    context = worker_invocation_context(root, project_key, session, run_id=binding["dispatch_run_id"], observe_runtime=True)
    controls = [r["worker_control"] for r in (session.get("project_continuation") or {}).get("running_judgments", {}).values()
                if r.get("worker_control")]
    questioned_jobs = {control["job_id"] for control in controls}
    rows = []
    for row in [*context.get("current", []), *context.get("recent_history", [])]:
        if row.get("run_id") != binding["dispatch_run_id"]:
            continue
        observation = row.get("runtime_observation") or {}
        # Time/heartbeat churn and acknowledgement changes do not create a new
        # semantic wakeup input. Keep job/result state distinct from local health.
        evidence = observation.get("evidence") or {}
        rows.append({"job_id": row["job_id"], "status": row["status"],
            "worker_output": row.get("worker_output", {"available": False, "entries": []}),
            "follow_up_supported": row.get("adapter") in {"pi", "pi-rpc"} and row.get("status") == "running"
                and row.get("current_dispatch_attempt") is not False and row["job_id"] not in questioned_jobs,
            "observation_available": observation.get("available") is True,
            "observation_reason": observation.get("reason", "not_observed"),
            "runtime": {key: evidence[key] for key in ("session_status", "process_alive", "isStreaming",
                        "isCompacting", "pendingMessageCount") if key in evidence}})
    packet = {"binding": binding, "workers": sorted(rows, key=lambda row: row["job_id"]),
              "human_context": human_observation_context(session.get("project_continuation") or {}),
              "source_available": context.get("available") is True,
              "omitted_current": context.get("omitted_current", 0), "omitted_history": context.get("omitted_history", 0)}
    # Eligibility reflects our own reservation bookkeeping, not new Worker or
    # Human evidence. Keep it in the packet for validation, outside the wakeup key.
    semantic_packet = {**packet, "workers": [
        {key: value for key, value in row.items() if key != "follow_up_supported"}
        for row in packet["workers"]]}
    # Publishing our own question is not new external evidence. Keep it in the
    # next actual input, but do not buy another judgment merely to reread it.
    own_questions = {identifier for identifier, record in
        ((session.get("project_continuation") or {}).get("human_questions") or {}).items()
        if record.get("planning", {}).get("source_kind") == "running_observation"
        and record["planning"]["packet"]["observation"]["binding"] == binding}
    from goodteam_os.project_human_feedback import question_context
    wake_questions = [q for q in question_context(session.get("project_continuation") or {}) if q["id"] not in own_questions]
    semantic_packet["human_context"] = {**packet["human_context"],
        "questions": wake_questions[-10:], "omitted_questions": max(0, len(wake_questions) - 10)}
    digest = sha256(json.dumps(semantic_packet, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    def save(current_session):
        if (_binding(current_session, root) != binding
                or not human_observation_current(current_session.get("project_continuation") or {}, packet)):
            return current_session  # Stop/recovery/config replacement won the race.
        current = current_session["project_continuation"]
        previous = current.get("running_observation") or {}
        if previous.get("digest") == digest:
            return current_session
        current["running_observation"] = {"schema_version": "running_observation.v1", "digest": digest,
            "observed_at": datetime.now(timezone.utc).isoformat(), "packet": packet,
            "manager_called": False, "action_executed": False,
            "interpretation": "Changed evidence is only a wakeup candidate. No Manager call scheduled, budget reserved, "
                              "Worker reply attributed, execution completed or retry authorized."}
        return current_session
    return store._mutate(save).get("project_continuation") or value


def running_observation_context(session):
    value = session.get("project_continuation") or {}
    receipt = value.get("running_observation")
    if not receipt:
        return None
    result = deepcopy(receipt)
    binding = receipt["packet"]["binding"]
    batch = session.get("project_task_batches", {}).get(binding["batch_run_id"], {})
    result["current_binding"] = bool(batch.get("project_root") and
        _binding(session, batch["project_root"]) == binding)
    # Informational only: must never mint or extend the existing authority.
    result["remaining_manager_calls"] = max(0, value.get("max_manager_calls", 0) - value.get("manager_calls", 0))
    judgment = (value.get("running_judgments") or {}).get(receipt["digest"])
    if judgment:
        result["judgment"] = {key: deepcopy(judgment[key]) for key in (
            "status", "advice", "advice_only", "actions_executed", "worker_reply_attributed", "manager_invocation", "worker_control", "human_question_id") if key in judgment}
    result["automatic_consumer_available"] = (value.get("discovery") or {}).get("allow_running_observation") is True
    result["automatic_consumer_scope"] = "read_only_advice; follow_up only within explicit separate allowance; runtime admission required"
    result["worker_controls"] = [{key: deepcopy(control[key]) for key in (
        "control_id", "job_id", "action", "status", "rpc_accepted", "error", "worker_reply_attributed", "execution_completed") if key in control}
        for record in value.get("running_judgments", {}).values() if (control := record.get("worker_control"))][-10:]
    return result
