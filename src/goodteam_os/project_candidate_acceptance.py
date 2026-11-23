"""[INPUT]: 项目续跑、已观察批次、隔离候选和 Manager 决定。
[OUTPUT]: 绑定候选版本和观察来源的 Manager 接受凭据。
[POS]: 将 Manager 决定交给既有 Git 交付路径。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from goodteam_os.dispatch_workspace import _git
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


def candidate_target(workspace, project_key, root, batches, feedback):
    """解析本轮观察所对应的写入候选。"""
    from goodteam_os.project_iteration_quality import _input

    batch = batches[feedback["batch_run_id"]]
    if any(track.get("execution_policy") == "standard" for track in batch["tracks"]):
        if batch["status"] != "review" or any(row["worker_status"] != "completed" for row in feedback["tasks"]):
            return None
        selected, _ = _input(workspace, project_key, root, batch)
    else:
        selected = (batch.get("candidate_source") or {}).get("selected")
        if not selected:
            return None
    candidate = batches[selected["batch_run_id"]]
    if (candidate["status"] != "review" or candidate.get("manager_acceptance")
            or (candidate.get("quality") or {}).get("required")):
        return None
    return {"candidate_run_id": candidate["run_id"], "candidate_commit": selected["candidate_commit"]}


def record_candidate_acceptance(*, workspace, project_key, root, value, pending):
    """保存 Manager 对候选的判断，供已有交付路径消费。"""
    from goodteam_os.project_iteration_continuation import _digest
    from goodteam_os.project_iteration_quality import project_batch_quality_evidence
    from goodteam_os.project_outcome_discovery import _current, _input_receipts

    packet = pending["packet"]
    feedback = packet["batch_feedback"]
    store = TaskAssistantSessionStore(workspace, project_key)
    batches = store.read().get("project_task_batches", {})
    target = candidate_target(workspace, project_key, root, batches, feedback)
    if not target or target != packet.get("delivery_candidate"):
        raise ValueError("The candidate changed before Manager acceptance.")
    receipt = {**target, "continuation_id": value["id"], "observation_receipt_id": feedback["receipt_id"],
               "decision_packet_digest": packet["packet_digest"],
               "rationale": pending["selection"]["rationale"]}
    receipt["receipt_id"] = _digest(receipt)

    # 修正批次继承原候选。Manager 的决定涵盖这段交付链。
    chain = {}
    run_id = target["candidate_run_id"]
    while run_id:
        candidate = batches.get(run_id, {})
        if (run_id in chain or candidate.get("status") != "review"
                or candidate.get("expected_continuation_id") != value["id"]
                or candidate.get("expected_authority") != value["authority"]
                or candidate.get("manager_acceptance")
                or project_batch_quality_evidence(workspace=workspace, project_key=project_key,
                                                  root=root, run_id=run_id)["required"]):
            raise ValueError("Manager acceptance must refer to an undelivered candidate in the current continuation.")
        chain[run_id] = candidate
        run_id = ((candidate.get("candidate_source") or {}).get("selected") or {}).get("batch_run_id")

    def record(session):
        current = _current(session, value)
        jobs = session["project_task_batches"]
        if (current.get("discovery_pending") != pending
                or jobs.get(feedback["batch_run_id"]) != batches[feedback["batch_run_id"]]
                or any(jobs.get(identifier) != batch for identifier, batch in chain.items())):
            raise ValueError("The candidate or its observed batch changed before recording.")
        for identifier in chain:
            jobs[identifier]["manager_acceptance"] = receipt
        if current.get("undelivered_feedback_anchor") == feedback["observation_key"]:
            current.pop("undelivered_feedback_anchor")
        current.update(discovery_pending=None, batch_feedback_pending=None, reason="manager_acceptance_recorded",
                       discovery_consumed=list(dict.fromkeys([*current.get("discovery_consumed", []),
                                                             *_input_receipts(packet), pending["trigger_ref"]])),
                       discovery_last={"status": "candidate_accepted", "outcome_receipt_id": receipt["receipt_id"],
                                       "rationale": receipt["rationale"]})
        return session

    try:
        return store._mutate(record)["project_continuation"]
    except OSError:
        saved = store.read()
        current = saved.get("project_continuation") or {}
        if (current.get("id") != value["id"] or current.get("discovery_pending") is not None
                or any(saved.get("project_task_batches", {}).get(identifier, {}).get("manager_acceptance") != receipt
                       for identifier in chain)):
            raise
        return current


def manager_acceptance_evidence(*, workspace, project_key, root, run_id):
    """交付时核对 Manager 凭据、候选身份和修正祖先。"""
    from goodteam_os.project_iteration_continuation import _digest
    from goodteam_os.project_iteration_quality import _input

    batches = TaskAssistantSessionStore(workspace, project_key).read()["project_task_batches"]
    batch = batches[run_id]
    receipt = batch["manager_acceptance"]
    candidate = batches[receipt["candidate_run_id"]]
    if (receipt["receipt_id"] != _digest({key: item for key, item in receipt.items() if key != "receipt_id"})
            or candidate.get("manager_acceptance") != receipt
            or any(item.get("expected_continuation_id") != receipt["continuation_id"] for item in (batch, candidate))):
        raise ValueError("The Manager acceptance receipt changed.")
    selected, source = _input(workspace, project_key, root, candidate)
    original, _ = _input(workspace, project_key, root, batch) if batch != candidate else (selected, source)
    head = receipt["candidate_commit"]
    if (selected["candidate_commit"] != head or
            _git(root, ["merge-base", "--is-ancestor", original["candidate_commit"], head], check=False).returncode):
        raise ValueError("The accepted candidate no longer descends from the original batch.")
    return {"acceptance_status": "accepted_by_manager", "decision_receipt_id": receipt["receipt_id"],
            "candidate_commit": head, "candidate_path": source["integration"]["candidate_path"]}
