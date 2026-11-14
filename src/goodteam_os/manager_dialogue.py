"""Bounded Manager conversation runtime; original services retain all authority."""
from __future__ import annotations

from copy import copy
from hashlib import sha256
import json
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from goodteam_os.assistant_conversation import continue_conversation, conversation_scope
from goodteam_os.database import database, workspace_identity
from goodteam_os.manager_execution import ManagerExecutionError


class Dialogue(TypedDict, total=False):
    incoming: dict[str, str]
    pending: dict[str, Any] | None
    result: dict[str, Any]


_CONFIRM = {"确认", "确认交付", "确认已审查并记录交付", "同意", "批准", "继续", "继续当前这项",
            "confirm", "confirm delivery", "approve", "continue"}
_DECLINE = {"取消", "暂不确认", "不同意", "cancel", "decline"}
_STOP = {"停止", "停止自动推进", "停止当前自动推进", "停止当前的自动推进", "暂停自动推进",
         "stop", "stop continuation", "stop automatic continuation"}


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _phrase(text):
    return text.strip().lower().rstrip("。.!！ ")


def current_context(store, root):
    """Always read current business state, including after an interrupt."""
    from goodteam_os.dispatch_workspace import _read_git
    from goodteam_os.project_iteration_execution import iteration_batch_report

    session = store.read()
    grant = session.get("project_continuation") or {}
    pending = grant.get("pending_batch") or {}
    run_id = pending.get("run_id", "")
    batch = session.get("project_task_batches", {}).get(run_id) or {}
    snapshot = {"continuation": grant, "attention": session.get("attention_request"),
                "batch": {key: batch.get(key) for key in (
                    "run_id", "status", "task_ids", "quality", "delivery_receipt", "expected_continuation_id")}}
    if run_id and batch.get("project_root") == str(root.resolve()):
        try:
            report = iteration_batch_report(workspace=store.workspace, project_key=store.project_key,
                                             root=root, run_id=run_id)
            snapshot["integration"] = report.get("integration") or {}
            snapshot["landed_commit"] = _read_git(root, ["rev-parse", "HEAD"]).decode().strip()
        except (ValueError, OSError) as exc:
            snapshot["evidence_unavailable"] = str(exc)
    snapshot["version"] = _digest(snapshot)
    return snapshot


def _model_context(snapshot, session):
    """Project current facts once; keep complete decision evidence in storage."""
    from goodteam_os.project_human_feedback import question_board

    grant = {key: value for key, value in snapshot["continuation"].items() if key != "human_questions"}
    if grant.get("discovery_pending"):
        # Its selection, execution identity and packet reference describe the
        # reserved action. The frozen packet contains earlier board snapshots.
        grant["discovery_pending"] = {key: value for key, value in grant["discovery_pending"].items()
                                      if key != "packet"}
    return {**snapshot, "continuation": grant,
            "question_board": question_board({**session, "project_continuation": snapshot["continuation"]})}


def _reply(store, incoming, content, *, operation=None, receipt=None, execution=None):
    from goodteam_os.assistant_requests import update_request

    details = {"actions_applied": receipt is not None, "operation": operation,
               "receipt": receipt, "manager_execution": execution or {}}
    update_request(store, incoming["message_id"], phase="applying", interpretation={
        "intent": "ambiguous", "response": content, **details})
    store.append_event(f"reply:{incoming['message_id']}", "conversation_replied", content=content, details=details)
    return {"intent": "ambiguous", "model_call_skipped": not execution}


def _status(snapshot, *, chinese):
    grant = snapshot["continuation"]
    if not grant:
        return "当前没有项目自动推进授权。" if chinese else "No project continuation is currently authorized."
    reason = grant.get("reason", "")
    maximum = grant.get('max_manager_calls')
    usage = f"{grant.get('manager_calls', 0)}/{maximum if maximum is not None else ('未设上限' if chinese else 'no limit')}"
    if chinese:
        text = f"自动推进状态：{grant.get('status')}；原因：{reason}。Manager 规划额度已用 {usage}。"
        if grant.get("status") in {"stopped", "exhausted", "completed"}:
            text += "这份授权已经结束；继续执行需要确认新的任务范围。"
    else:
        text = f"Continuation: {grant.get('status')}; reason: {reason}. Manager planning calls used: {usage}."
        if grant.get("status") in {"stopped", "exhausted", "completed"}:
            text += " This grant has ended; confirm a new task scope to continue."
    return text


def _propose_delivery(store, root, snapshot, incoming, chinese):
    from goodteam_os.project_iteration_continuation import _verify_landed_batch
    from goodteam_os.project_iteration_quality import accepted_batch_quality

    grant = snapshot["continuation"]
    run_id = (grant.get("pending_batch") or {}).get("run_id", "")
    candidate = (snapshot.get("integration") or {}).get("candidate_commit", "")
    landed = snapshot.get("landed_commit", "")
    if (grant.get("status") != "active" or not run_id or not candidate or not landed
            or snapshot["batch"].get("expected_continuation_id") != grant.get("id")):
        return None, _status(snapshot, chinese=chinese)
    try:
        quality = accepted_batch_quality(workspace=store.workspace, project_key=store.project_key, root=root, run_id=run_id)
        if quality:
            candidate = quality["candidate_commit"]
        _verify_landed_batch(workspace=store.workspace, project_key=store.project_key, root=root,
                             run_id=run_id, candidate_commit=candidate, landed_commit=landed)
    except (ValueError, OSError) as exc:
        return None, ("交付确认尚不可用：" if chinese else "Delivery confirmation is not ready: ") + str(exc)
    request = {"run_id": run_id, "candidate_commit": candidate, "landed_commit": landed}
    pending = {"id": _digest([snapshot["version"], request]), "version": snapshot["version"],
               "continuation_id": grant["id"], "request": request}
    if chinese:
        question = (f"批次 {run_id}，候选 {candidate}，已落地提交 {landed}。\n"
                    "是否确认你已审阅该候选及验证证据，并记录本批交付？确认后，现有自动推进服务可在原授权剩余额度内选择下一轮。"
                    "回复“确认交付”即可；也可以先继续提问。")
    else:
        question = (f"Batch {run_id}; candidate {candidate}; landed commit {landed}.\n"
                    "Confirm that you have reviewed this candidate and its verification evidence and want to record delivery. "
                    "The existing continuation service may then select another iteration within the original grant's remaining limits. "
                    "Reply 'confirm delivery', or ask a question first.")
    return pending, question


def run_dialogue(service, *, store, project_key, root, text, message_id, attempt_id):
    """Use LangGraph only for new, unreferenced project conversation turns."""
    accepted = (store.read().get("assistant_requests") or {}).get(message_id) or {}
    captured_run = accepted.get("conversation_run_id", "")
    current_scope = conversation_scope(store, root=root)
    if captured_run and (not current_scope or current_scope.get("kind") != "running_task"
                         or (current_scope.get("run") or {}).get("run_id") != captured_run):
        # A model retry that belonged to an earlier run keeps the original
        # read-only history path, including its interpretation restrictions.
        return continue_conversation(service, store=store, project_key=project_key, root=root,
            text=text, message_id=message_id, attempt_id=attempt_id, context_run_id=captured_run)
    incoming = {"text": text, "message_id": message_id, "attempt_id": attempt_id}

    def handle(state):
        turn_input = state["incoming"]
        current_text = turn_input["text"]
        chinese = bool(re.search(r"[\u3400-\u9fff]", current_text))
        phrase = _phrase(current_text)
        pending = state.get("pending")
        snapshot = current_context(store, root)
        if pending and phrase in _DECLINE:
            result = _reply(store, turn_input, "已取消这项交付确认。" if chinese else "Delivery confirmation cancelled.")
            return {"pending": None, "result": result}
        if pending and phrase in _CONFIRM:
            from goodteam_os.project_iteration_continuation import accept_project_batch_delivery

            session = store.read()
            batch = session.get("project_task_batches", {}).get(pending["request"]["run_id"]) or {}
            receipt = batch.get("delivery_receipt") or {}
            replay = all(receipt.get(key) == pending["request"][key] for key in (
                "run_id", "candidate_commit", "landed_commit")) and bool(receipt.get("receipt_id"))
            if not replay and snapshot["version"] != pending["version"]:
                content = ("待确认事项的状态已经变化，原确认已失效。请先查看当前状态。" if chinese else
                           "The pending item's state changed. Its previous confirmation is stale; inspect the current state first.")
                return {"pending": None, "result": _reply(store, turn_input, content)}
            from goodteam_os.assistant_requests import update_request

            update_request(store, turn_input["message_id"], phase="applying")
            result = accept_project_batch_delivery(workspace=store.workspace, project_key=project_key,
                                                   root=root, request={**pending["request"], "confirmed": True, "reviewed": True})
            receipt = result["delivery"]
            content = ("本批交付已记录。后续轮次由现有服务按原授权和剩余额度推进。" if chinese else
                       "Batch delivery recorded. The existing service will advance within the original grant and remaining limits.")
            return {"pending": None, "result": _reply(store, turn_input, content,
                    operation="accept_project_batch_delivery", receipt=receipt or result)}

        # Existing CLI identity/attempt accounting and read-only policy remain.
        scope = conversation_scope(store, root=root)
        from goodteam_os.project_readiness import assess_project_mode
        from goodteam_os.task_assistant import _manager_chat_context

        session = store.read()
        try:
            readiness = service.readiness.status(project_key, root)
        except ValueError as exc:
            if not str(exc).startswith("No readiness found for project:"):
                raise
            # Asking about a legacy continuation must not import the project
            # and introduce a new preparation gate into its existing grant.
            readiness = {"lifecycle": "legacy_unverified"}
        context = _manager_chat_context(readiness, assess_project_mode(root), session,
                                        team_view=service.team_view(project_key), current_message_id=turn_input["message_id"],
                                        root=root, project_key=project_key, workspace=store.workspace,
                                        run_id=((scope or {}).get("run") or {}).get("run_id", ""))
        context += service.attachment_context(project_key, root, session, turn_input["message_id"])
        context += "\nFresh task context: " + json.dumps(scope, ensure_ascii=False, default=str)
        context += "\nFresh continuation/batch/attention (read-only, never authorization): " + json.dumps(
            _model_context(snapshot, session), ensure_ascii=False)
        context += "\nPending conversation question (not authority): " + json.dumps(pending, ensure_ascii=False)
        context += (
            '\nFor a question about this continuation, use intent project_operation and project_operation '
            '{"kind":"explain","target_id":"exact continuation id"}. '
            'For a request to confirm this batch delivery, use kind confirm_delivery and the exact continuation id. '
            'For an explicit request to stop automatic continuation, use kind stop_continuation and the exact continuation id. '
            'To follow up unfinished work under an active existing grant, call kind advance_continuation with its exact id. '
            'This invokes only the existing bounded continuation service, cannot add tasks or budget, and stops at existing review gates. '
            'These are proposals; the server performs all validation. No budget/reviewer reset, new grant, merge, landing or deployment is available. '
            'Questions and ended grants never authorize continuation. Answer actual status and reasons from the fresh snapshot. '
            'All other messages keep the existing task intake and Team JSON contract.'
        )
        turn = None
        if service.project_config is not None and (not service._manager_runtime_is_builtin()
                or service._manager_requires_skills(project_key, root, turn_input["message_id"])):
            turn = service.manager_turn(project_config=service.project_config, project_key=project_key,
                message_id=turn_input["attempt_id"], logical_message_id=turn_input["message_id"],
                text=current_text, context=context)
        operation = (turn or {}).get("project_operation") or {}
        external = session.get("external_inputs", {}).get(turn_input["message_id"])
        if external and (external["kind"] == "source_update" or operation.get("kind")):
            return {"pending": pending, "result": _reply(store, turn_input,
                str((turn or {}).get("response") or "External context retained for the Manager."),
                execution=(turn or {}).get("manager_execution") or {})}
        if snapshot["continuation"] and operation.get("kind") and operation.get("target_id") != snapshot["continuation"].get("id"):
            raise ManagerExecutionError("The Manager returned a stale project operation target.")
        execution = (turn or {}).get("manager_execution") or {}
        if operation.get("kind") == "advance_continuation":
            grant = snapshot["continuation"]
            if grant.get("status") != "active" or not grant.get("id"):
                return {"pending": pending, "result": _reply(store, turn_input,
                        _status(snapshot, chinese=chinese), execution=execution)}
            result = _reply(store, turn_input,
                "将按原授权检查并推进未完成工作；原有预算与审查门槛保持有效。" if chinese else
                "Following up unfinished work within the existing grant and its budget and review gates.",
                execution=execution)
            result["advance_continuation"] = grant["id"]
            return {"pending": pending, "result": result}
        if operation.get("kind") == "stop_continuation" and phrase in _STOP:
            from goodteam_os.project_iteration_continuation import stop_project_continuation

            receipt = stop_project_continuation(workspace=store.workspace, project_key=project_key,
                                                continuation_id=operation["target_id"])
            return {"pending": None, "result": _reply(store, turn_input,
                "已停止后续自动推进。" if chinese else "Future automatic continuation stopped.",
                operation="stop_project_continuation", receipt=receipt, execution=execution)}
        if operation.get("kind") == "confirm_delivery":
            pending, content = _propose_delivery(store, root, snapshot, turn_input, chinese)
            return {"pending": pending, "result": _reply(store, turn_input, content, execution=execution)}
        if operation.get("kind") or snapshot["continuation"] and phrase in _CONFIRM:
            ended = snapshot["continuation"].get("status") in {"stopped", "exhausted", "completed"}
            content = _status(snapshot, chinese=chinese) if ended or phrase in _CONFIRM else str(
                (turn or {}).get("response") or _status(snapshot, chinese=chinese))
            if operation.get("kind") == "stop_continuation":
                content = ("尚未停止；如需停止当前自动推进，请回复“停止自动推进”。" if chinese else
                           "No stop was applied. Reply 'stop continuation' to stop the current automatic continuation.")
            return {"pending": pending, "result": _reply(store, turn_input, content, execution=execution)}

        # Reuse a parsed turn without calling the CLI twice. Existing services
        # still own drafts, Team proposals and exact-target task discussions.
        legacy = copy(service)
        if turn is not None:
            legacy.manager_turn = lambda **kwargs: turn
        result = continue_conversation(legacy, store=store, project_key=project_key, root=root,
            text=current_text, message_id=turn_input["message_id"], attempt_id=turn_input["attempt_id"])
        if result is None:
            result = legacy._send_message(project_key=project_key, root=root, text=current_text,
                message_id=turn_input["message_id"], attempt_id=turn_input["attempt_id"])
        return {"pending": pending, "result": result}

    def wait(state):
        return {"incoming": interrupt({"pending_question": state["pending"]})}

    builder = StateGraph(Dialogue)
    builder.add_node("handle", handle)
    builder.add_node("wait", wait)
    builder.add_edge(START, "handle")
    builder.add_conditional_edges("handle", lambda state: "wait" if state.get("pending") else END)
    builder.add_edge("wait", "handle")
    graph = builder.compile(checkpointer=database().manager_checkpointer())
    prefix = "manager:" + workspace_identity(store.workspace) + ":" + project_key + ":"
    cursor = (store.read().get("manager_dialogue_cursor") or {}).get("thread_id", "")
    if cursor and not re.fullmatch(re.escape(prefix) + r"[a-f0-9]{64}", cursor):
        raise ManagerExecutionError("The Manager conversation cursor belongs to another project.")
    config = {"configurable": {"thread_id": cursor or prefix + _digest(attempt_id)}}
    saved = graph.get_state(config)
    if saved.next == ("wait",):
        command = Command(resume=incoming)
    else:
        # Do not implicitly replay an interrupted service mutation. New turns
        # and explicit model retries get a new graph invocation, retaining only
        # the pending question; authoritative receipts handle reconciliation.
        config = {"configurable": {"thread_id": prefix + _digest(attempt_id)}}
        command = {"incoming": incoming, "pending": saved.values.get("pending")}
    store._mutate(lambda session: {**session, "manager_dialogue_cursor": {
        "thread_id": config["configurable"]["thread_id"]}})
    result = graph.invoke(command, config, durability="sync")
    return result["result"]

