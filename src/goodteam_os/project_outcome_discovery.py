"""Bounded follow-up discovery from verified delivery, using existing stores.

The continuation session retains one planning/publication outbox. LocalTaskStore
owns every task; the existing Manager, queue, dispatch and delivery own execution.
"""
from hashlib import sha256
import json
from pathlib import Path

from goodteam_os.goal_continuation import authority_snapshot
from goodteam_os.project_iteration import normalize_write_domains, rank_project_backlog, validate_iteration_choice
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


class DiscoveryPublicationPending(RuntimeError):
    """A durable decision can replay its create-once publication without a model."""


def _digest(value):
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def normalize_discovery(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
            "objective", "allowed_write_domains", "max_new_tasks", "max_planning_calls"}:
        raise ValueError("Confirm an explicit discovery objective, paths and finite task/planning budgets.")
    objective = value["objective"]
    domains = normalize_write_domains(value["allowed_write_domains"])
    if (not isinstance(objective, str) or not 20 <= len(objective.strip()) <= 2000
            or domains is None or any(part in {".git", ".goodteam"} for path in domains for part in path.split("/"))
            or type(value["max_new_tasks"]) is not int or not 1 <= value["max_new_tasks"] <= 50
            or type(value["max_planning_calls"]) is not int or not 1 <= value["max_planning_calls"] <= 100):
        raise ValueError("Discovery needs a 20–2000 character objective, explicit project paths, 1–50 new tasks and 1–100 planning calls.")
    return {**value, "objective": objective.strip(), "allowed_write_domains": domains}


def authorization_digest(value):
    return _digest({key: value[key] for key in (
        "id", "seed_task_ids", "seed_task_intents", "approved_backlog_version", "discovery", "authority",
        "max_batches", "max_manager_calls")})


def _current(session, original):
    value = session.get("project_continuation") or {}
    if (value.get("id") != original["id"] or value.get("status") != "active"
            or value.get("authorization_digest") != authorization_digest(value)
            or value["authorization_digest"] != original["authorization_digest"]):
        raise ValueError("Project discovery was stopped, replaced or its approved scope changed.")
    return value


def _head(root):
    from goodteam_os.dispatch_workspace import _read_git
    return _read_git(Path(root), ["rev-parse", "HEAD^{commit}"]).decode().strip()


def verified_outcome(*, workspace, project_key, root, value, run_id=None):
    """Revalidate original delivery/quality and its retained Git content at HEAD."""
    from goodteam_os.dispatch_workspace import _git
    from goodteam_os.project_iteration_continuation import _verify_landed_batch
    from goodteam_os.project_iteration_execution import TASK_INTENT_FIELDS

    batches = TaskAssistantSessionStore(workspace, project_key).read().get("project_task_batches", {})
    eligible = [batch for batch in batches.values() if batch.get("status") == "delivered"
                and batch.get("expected_continuation_id") == value["id"]
                and set(batch.get("task_ids", [])).issubset(value["task_ids"])]
    batch = batches.get(run_id) if run_id else max(eligible, key=lambda row: row["created_at"], default=None)
    if not batch or batch not in eligible:
        raise ValueError("Discovery requires a verified delivery from this authorized continuation.")
    receipt = batch.get("delivery_receipt") or {}
    if (receipt.get("receipt_id") != _digest({key: item for key, item in receipt.items() if key != "receipt_id"})
            or receipt.get("producer") != "human_confirmed_candidate_delivery"
            or receipt.get("verification") != "landed_git_ancestor_and_content"
            or receipt.get("run_id") != batch["run_id"] or receipt.get("reviewed") is not True):
        raise ValueError("The accepted project delivery receipt cannot be verified.")
    known = {task["id"]: task for task in LocalTaskStore(root, project_key).list_tasks()}
    tasks = []
    for binding in batch["task_bindings"]:
        task = known.get(binding["task_id"], {})
        if (task.get("status") != "done" or task.get("delivery_verified") is not True
                or task.get("run_id") != batch["run_id"] or task.get("delivery_receipt") != receipt
                or {key: task.get(key) for key in TASK_INTENT_FIELDS} != binding["task_intent"]):
            raise ValueError("The delivered task's original intent or result changed.")
        tasks.append({key: task.get(key) for key in (
            "id", "title", "description", "acceptance_criteria", "write_domains", "dependencies", "status", "delivery_verified")})
    head = _head(root)
    if _git(Path(root), ["merge-base", "--is-ancestor", receipt["landed_commit"], head], check=False).returncode:
        raise ValueError("The accepted delivery is no longer in this project's current history.")
    report, quality = _verify_landed_batch(workspace=workspace, project_key=project_key, root=Path(root),
        run_id=batch["run_id"], candidate_commit=receipt["candidate_commit"], landed_commit=head)
    if receipt.get("dispatch_run_id") != report["run_id"]:
        raise ValueError("The delivery receipt references another effective dispatch.")
    if quality:
        observed = {key: quality[key] for key in (
            "input_digest", "execution_id", "validation_receipt_id", "review_receipt_id", "candidate_commit")}
        if receipt.get("quality") != observed:
            raise ValueError("The original delivered quality receipt changed.")
        checks = {key: quality[key] for key in (
            "required", "status", "check_status", "review_status", "acceptance_status", "validation_receipt_id", "review_receipt_id")}
    else:
        if receipt.get("quality"):
            raise ValueError("The accepted quality evidence is missing.")
        checks = {"required": False, "status": "not_configured", "check_status": "not_configured",
                  "review_status": "human_attested", "acceptance_status": "human_confirmed_delivery"}
    return {"schema_version": "verified_project_outcome.v1", "project_root": str(Path(root).resolve()),
            "project_head": head, "batch_run_id": batch["run_id"], "delivery": receipt,
            "tasks": tasks, "integration": report["integration"], "quality": checks}


def _remember(root, outcome):
    from goodteam_os.memory import RoleMemoryStore

    receipt = outcome["delivery"]
    # This is execution/acceptance provenance, not model-generated knowledge.
    # Normal memory retrieval keeps its existing execution filter unchanged.
    text = json.dumps({"verified_delivery": receipt["receipt_id"], "task_ids": [task["id"] for task in outcome["tasks"]],
                       "candidate_commit": receipt["candidate_commit"], "landed_commit": receipt["landed_commit"],
                       "quality": outcome["quality"]}, sort_keys=True, ensure_ascii=False)
    RoleMemoryStore(root).append_memory_event("orchestrator", horizon="short-term", text=text,
        source="execution:project-delivery:" + receipt["receipt_id"] + ":final_provenance")


def _packet(workspace, project_key, root, value, outcome):
    from goodteam_os.memory import RoleMemoryStore
    from goodteam_os.project_iteration_planner import _project_evidence

    snapshot = rank_project_backlog(LocalTaskStore(root, project_key).list_tasks(), project_key, limit=None)
    refs = [outcome["delivery"]["receipt_id"]]
    refs += [outcome["quality"][key] for key in ("validation_receipt_id", "review_receipt_id") if key in outcome["quality"]]
    packet = {"schema_version": "project_outcome_discovery_packet.v1", "project_key": project_key,
        "authorization_digest": value["authorization_digest"], "discovery": value["discovery"],
        "authorized_task_ids": list(value["task_ids"]),
        "authorized_done_task_ids": [task["id"] for task in LocalTaskStore(root, project_key).list_tasks()
                                     if task["id"] in value["task_ids"] and task["status"] == "done"
                                     and task.get("delivery_verified") is True],
        "backlog": snapshot, "verified_outcome": outcome,
        "allowed_source_refs": refs, "repository_understanding": _project_evidence(Path(workspace), project_key, Path(root)),
        "memory_pickup": RoleMemoryStore(root).build_pickup_packet("orchestrator", query=value["discovery"]["objective"])}
    packet["packet_digest"] = _digest(packet)
    return packet


def validate_discovered_task(packet, response):
    """Validate advice against the frozen grant; no storage or execution effects."""
    if not isinstance(response, dict) or set(response) != {"new_task", "rationale"}:
        raise ValueError("Discovery must return one bounded follow-up or an evidence-based stopping reason.")
    reason = response["rationale"]
    if not isinstance(reason, str) or not 20 <= len(reason.strip()) <= 1600:
        raise ValueError("Discovery requires a concrete outcome-based rationale.")
    task = response["new_task"]
    if task is None:
        return {"new_task": None, "rationale": reason.strip()}
    fields = {"title", "description", "acceptance_criteria", "write_domains", "dependencies", "source_refs"}
    if (not isinstance(task, dict) or set(task) != fields
            or not isinstance(task["title"], str) or not 5 <= len(task["title"].strip()) <= 240
            or not isinstance(task["description"], str) or not 20 <= len(task["description"].strip()) <= 4000):
        raise ValueError("A follow-up needs an explicit bounded task, not runtime commands or new permissions.")
    criteria = task["acceptance_criteria"]
    domains = normalize_write_domains(task["write_domains"])
    allowed = packet["discovery"]["allowed_write_domains"]
    if (not isinstance(criteria, list) or not 1 <= len(criteria) <= 12
            or any(not isinstance(item, str) or not 5 <= len(item.strip()) <= 500 for item in criteria)
            or domains is None or any(not any(path == parent or path.startswith(parent + "/") for parent in allowed) for path in domains)
            or any(part in {".git", ".goodteam"} for path in domains for part in path.split("/"))):
        raise ValueError("The follow-up's acceptance criteria and write paths must fit the approved discovery scope.")
    dependencies, sources = task["dependencies"], task["source_refs"]
    done = set(packet["authorized_done_task_ids"])
    if (not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies)
            or len(set(dependencies)) != len(dependencies)
            or not set(dependencies).issubset(done & set(packet["authorized_task_ids"]))
            or not isinstance(sources, list) or not sources or any(not isinstance(item, str) for item in sources)
            or len(set(sources)) != len(sources) or not set(sources).issubset(packet["allowed_source_refs"])
            or packet["verified_outcome"]["delivery"]["receipt_id"] not in sources):
        raise ValueError("Follow-up dependencies and source references must name authorized delivered facts.")
    return {"new_task": {**task, "title": task["title"].strip(), "description": task["description"].strip(),
                         "acceptance_criteria": [item.strip() for item in criteria], "write_domains": domains},
            "rationale": reason.strip()}


def _creation_ref(value, packet):
    return "project-discovery:" + value["id"] + ":" + packet["packet_digest"].split(":", 1)[1]


def _revalidate(workspace, project_key, root, config, value, pending):
    packet = pending["packet"]
    if packet["packet_digest"] != _digest({key: item for key, item in packet.items() if key != "packet_digest"}):
        raise ValueError("The frozen discovery input changed.")
    if value["authority"] != authority_snapshot(workspace, project_key, Path(config), Path(root)):
        raise ValueError("Discovery configuration or runtime authority changed.")
    observed = verified_outcome(workspace=workspace, project_key=project_key, root=root, value=value,
                                run_id=packet["verified_outcome"]["batch_run_id"])
    if observed != packet["verified_outcome"]:
        raise ValueError("The source revision or accepted outcome changed during discovery.")
    ref = _creation_ref(value, packet)
    original_tasks = [task for task in LocalTaskStore(root, project_key).list_tasks() if ref not in task.get("external_refs", [])]
    if rank_project_backlog(original_tasks, project_key, limit=None)["version"] != pending["backlog_version"]:
        raise ValueError("The project backlog changed during outcome discovery.")


def discover_next_iteration(*, workspace, project_key, root, project_config, value, manager_runner=None):
    try:
        return _discover_next_iteration(workspace=workspace, project_key=project_key, root=root,
            project_config=project_config, value=value, manager_runner=manager_runner)
    except OSError as exc:
        saved = TaskAssistantSessionStore(workspace, project_key).read().get("project_continuation") or {}
        if saved.get("id") == value["id"] and saved.get("discovery_pending"):
            raise DiscoveryPublicationPending("The same reserved discovery input awaits publication replay.") from exc
        last = saved.get("discovery_last") or {}
        if (saved.get("id") == value["id"] and saved.get("reason") == "outcome_followup_queued"
                and (saved.get("pending_batch") or {}).get("task_ids") == [last.get("task_id")]
                and last.get("outcome_receipt_id") in saved.get("discovery_consumed", [])):
            # The atomic session replacement may have committed before a later
            # I/O error. Observe its already-queued identity; do not block or
            # reserve another planning turn because the caller lost the reply.
            raise DiscoveryPublicationPending("The committed follow-up queue awaits the next service tick.") from exc
        raise


def _discover_next_iteration(*, workspace, project_key, root, project_config, value, manager_runner=None):
    """One existing continuation step: observe, reserve, create once and queue."""
    from goodteam_os.assistant_queue import queue_manager_iteration_choice
    from goodteam_os.project_iteration_continuation import _intent
    from goodteam_os.project_iteration_planner import _require_planning_idle, run_manager_iteration_choice

    store = TaskAssistantSessionStore(workspace, project_key)
    pending = value.get("discovery_pending")
    if pending is None:
        outcome = verified_outcome(workspace=workspace, project_key=project_key, root=root, value=value)
        _remember(root, outcome)
        if value["batches_started"] >= value["max_batches"] or value["manager_calls"] >= value["max_manager_calls"]:
            def exhausted(session):
                current = _current(session, value)
                current.update(status="exhausted", reason="approved_iteration_budget_used")
                return session
            return store._mutate(exhausted)["project_continuation"]
        if value["new_tasks_created"] >= value["discovery"]["max_new_tasks"]:
            def full(session):
                current = _current(session, value)
                current.update(status="exhausted", reason="discovery_task_budget_used")
                return session
            return store._mutate(full)["project_continuation"]
        if outcome["delivery"]["receipt_id"] in value.get("discovery_consumed", []):
            raise ValueError("This accepted delivery already supplied its bounded follow-up.")
        packet = _packet(workspace, project_key, root, value, outcome)
        pending = {"packet": packet, "packet_digest": packet["packet_digest"], "backlog_version": packet["backlog"]["version"]}
        def reserve(session):
            current = _current(session, value)
            current.update(discovery_pending=pending, manager_calls=current["manager_calls"] + 1, reason="outcome_discovery_reserved")
            return session
        value = store._mutate(reserve)["project_continuation"]
    _current(store.read(), value)
    _require_planning_idle(Path(workspace), project_key)
    _revalidate(workspace, project_key, root, project_config, value, pending)
    if "selection" not in pending:
        packet = pending["packet"]
        result = (manager_runner or run_manager_iteration_choice)(project_config=Path(project_config), project_key=project_key,
            backlog={**packet["backlog"], "planning_scope": packet["packet_digest"].split(":", 1)[1]}, context=packet)
        selection = validate_discovered_task(packet, result.get("selection"))
        _revalidate(workspace, project_key, root, project_config, value, pending)
        def decided(session):
            current = _current(session, value)
            current["discovery_pending"].update(selection=selection, manager_execution=result.get("manager_execution", {}))
            return session
        value = store._mutate(decided)["project_continuation"]
        pending = value["discovery_pending"]
    selection = validate_discovered_task(pending["packet"], pending["selection"])
    receipt_id = pending["packet"]["verified_outcome"]["delivery"]["receipt_id"]
    if selection["new_task"] is None:
        def finished(session):
            current = _current(session, value)
            current.update(status="completed", reason="discovery_no_followup", discovery_pending=None,
                discovery_consumed=[*current.get("discovery_consumed", []), receipt_id],
                discovery_last={"status": "no_followup", "task_id": "", "outcome_receipt_id": receipt_id,
                                "rationale": selection["rationale"]})
            return session
        return store._mutate(finished)["project_continuation"]
    task = selection["new_task"]
    creation_ref = _creation_ref(value, pending["packet"])
    intent = {key: task[key] for key in ("title", "description", "acceptance_criteria", "write_domains", "dependencies")}
    intent.update(role="implementation", priority="medium", execution_policy="workspace_write",
                  external_refs=[creation_ref, *task["source_refs"]], artifact_refs=list(task["source_refs"]))
    def publish(session):
        current = _current(session, value)
        _revalidate(workspace, project_key, root, project_config, current, pending)
        created = LocalTaskStore(root, project_key).create_task_once(creation_ref=creation_ref, intent=intent)
        if created["id"] not in current["task_ids"]:
            current["task_ids"].append(created["id"])
            current["task_intents"][created["id"]] = _intent(created)
            current["discovered_task_ids"].append(created["id"])
            current["new_tasks_created"] += 1
        current["discovery_pending"]["task_id"] = created["id"]
        current["discovery_last"] = {"status": "task_created", "task_id": created["id"],
            "outcome_receipt_id": receipt_id, "rationale": selection["rationale"]}
        return session
    try:
        value = store._mutate(publish)["project_continuation"]
    except OSError as exc:
        raise DiscoveryPublicationPending("The retained discovery decision awaits task publication replay.") from exc
    pending = value["discovery_pending"]
    identifier = pending["task_id"]
    snapshot = rank_project_backlog(LocalTaskStore(root, project_key).list_tasks(), project_key, limit=None)
    choice = validate_iteration_choice(snapshot, {"selected_task_ids": [identifier], "compared_task_ids": [identifier],
                                                 "rationale": selection["rationale"]})
    event_id = "outcome-choice:" + value["id"] + ":" + pending["packet_digest"]
    choice["source_event_id"] = event_id
    store.append_event(event_id, "project_iteration_choice_recorded", details={"choice": choice,
        "manager_execution": pending.get("manager_execution", {}), "outcome_packet_digest": pending["packet_digest"],
        "execution_started": False})
    _current(store.read(), value)
    _revalidate(workspace, project_key, root, project_config, value, pending)
    queued = queue_manager_iteration_choice(workspace=workspace, project_key=project_key, root=root,
        expected_backlog_version=snapshot["version"], expected_task_ids=[identifier], expected_choice_event_id=event_id)
    if queued["queued_task_ids"] != [identifier]:
        raise ValueError("Discovery's queue handoff changed its original task.")
    proposals = {item["id"]: item for item in queued["task_assistant_session"]["queued_task_proposals"]}
    ids = queued["queued_proposal_ids"]
    def queued_batch(session):
        current = _current(session, value)
        current.update(pending_batch={"task_ids": [identifier], "proposal_ids": ids,
            "expected_revisions": {item: proposals[item]["revision"] for item in ids}, "run_id": ""},
            discovery_pending=None, batches_started=current["batches_started"] + 1,
            discovery_consumed=[*current.get("discovery_consumed", []), receipt_id], reason="outcome_followup_queued")
        return session
    return store._mutate(queued_batch)["project_continuation"]
