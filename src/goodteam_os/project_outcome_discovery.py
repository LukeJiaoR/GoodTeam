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


class NoVerifiedDelivery(ValueError):
    """No eligible delivery exists; invalid delivery evidence is a different error."""


class DiscoveryContextChanged(ValueError):
    """Valid source facts changed; old advice must not become a current action."""


class DiscoveryPublicationPending(RuntimeError):
    """A durable decision can replay its create-once publication without a model."""


def _digest(value):
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def normalize_discovery(value):
    if value is None:
        return None
    required = {"objective", "allowed_write_domains"}
    limits = {"max_new_tasks", "max_planning_calls"}
    if (not isinstance(value, dict) or not required.issubset(value)
            or set(value) - required - limits - {"allow_investigation", "allow_planning", "allow_team_roles", "allow_undelivered_feedback", "allow_candidate_handoff", "allow_running_observation", "allow_initial_readonly_feedback", "worker_follow_up_limit", "allow_backlog_judgment", "allow_running_human_question"}):
        raise ValueError("Confirm an explicit discovery objective and paths.")
    if any(value.get(key) is not None and (type(value[key]) is not int or value[key] < 1) for key in limits):
        raise ValueError("Optional task and planning limits must be positive integers.")
    if "allow_team_roles" in value and type(value["allow_team_roles"]) is not bool:
        raise ValueError("Team Role delegation permission must be an explicit boolean.")
    if "allow_running_human_question" in value and (
            type(value["allow_running_human_question"]) is not bool
            or value["allow_running_human_question"] and value.get("allow_running_observation") is not True):
        raise ValueError("Running Human questions require explicit running observation permission.")
    if "allow_backlog_judgment" in value and type(value["allow_backlog_judgment"]) is not bool:
        raise ValueError("Backlog judgment permission must be an explicit boolean.")
    if "allow_investigation" in value and type(value["allow_investigation"]) is not bool:
        raise ValueError("Investigation permission must be an explicit boolean.")
    if "allow_planning" in value and type(value["allow_planning"]) is not bool:
        raise ValueError("Planning permission must be an explicit boolean.")
    if "allow_undelivered_feedback" in value and type(value["allow_undelivered_feedback"]) is not bool:
        raise ValueError("Undelivered feedback permission must be an explicit boolean.")
    if "allow_candidate_handoff" in value and (type(value["allow_candidate_handoff"]) is not bool
            or value["allow_candidate_handoff"] and value.get("allow_undelivered_feedback") is not True):
        raise ValueError("Candidate handoff requires explicit undelivered feedback permission.")
    if "allow_running_observation" in value and type(value["allow_running_observation"]) is not bool:
        raise ValueError("Running observation permission must be an explicit boolean.")
    if "allow_initial_readonly_feedback" in value and type(value["allow_initial_readonly_feedback"]) is not bool:
        raise ValueError("Initial read-only feedback permission must be an explicit boolean.")
    if value.get("allow_initial_readonly_feedback") is True and value.get("allow_undelivered_feedback") is not True:
        raise ValueError("Initial read-only feedback also requires undelivered feedback permission.")
    if "worker_follow_up_limit" in value:
        if (type(value["worker_follow_up_limit"]) is not int or not 1 <= value["worker_follow_up_limit"] <= 10
                or value.get("allow_running_observation") is not True):
            raise ValueError("Worker follow-up requires running observation and an explicit 1–10 total limit.")
    # Keep omitted flags omitted: existing grants retain their exact digest and
    # cannot acquire research or Planner authority on upgrade or authorization replay.
    objective = value["objective"]
    domains = normalize_write_domains(value["allowed_write_domains"])
    if (not isinstance(objective, str) or not 20 <= len(objective.strip()) <= 2000
            or domains is None or any(part in {".git", ".goodteam"} for path in domains for part in path.split("/"))):
        raise ValueError("Discovery needs a 20–2000 character objective and explicit project paths.")
    return {**value, **{key: value.get(key) for key in limits},
            "objective": objective.strip(), "allowed_write_domains": domains}


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


def _eligible_deliveries(batches, value=None):
    return [batch for batch in batches.values() if batch.get("status") == "delivered"
            and (value is None or batch.get("expected_continuation_id") == value["id"]
                 and set(batch.get("task_ids", [])).issubset(value["task_ids"]))]


def _unconsumed_deliveries(batches, value, consumed):
    return [batch for batch in _eligible_deliveries(batches, value)
            if (batch.get("delivery_receipt") or {}).get("receipt_id") not in consumed]


def verified_outcome(*, workspace, project_key, root, value=None, run_id=None):
    """Revalidate delivery at HEAD; discovery drains its original unconsumed receipts.

    Ordinary read-only planning can observe the latest project delivery without
    a discovery grant. Observation never grants follow-up execution authority.
    """
    from goodteam_os.dispatch_workspace import _git
    from goodteam_os.project_iteration_continuation import _verify_landed_batch
    from goodteam_os.tasks import project_task_intent

    batches = TaskAssistantSessionStore(workspace, project_key).read().get("project_task_batches", {})
    eligible = _eligible_deliveries(batches, value)
    # Drain original authorized receipts before reusing the latest one. Creation
    # order is not delivery order; an older batch may finish review later.
    choices = (_unconsumed_deliveries(batches, value, value.get("discovery_consumed", []))
               if value is not None and run_id is None else []) or eligible
    batch = batches.get(run_id) if run_id else max(choices, key=lambda row: row["created_at"], default=None)
    if not batch or batch not in eligible:
        raise NoVerifiedDelivery("Discovery requires a verified delivery from this authorized continuation.")
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
                or project_task_intent(task) != binding["task_intent"]):
            raise ValueError("The delivered task's original intent or result changed.")
        tasks.append({**{key: task.get(key) for key in (
            "id", "title", "description", "acceptance_criteria", "write_domains", "dependencies", "status", "delivery_verified")},
            **({"worker_id": task["worker_id"]} if "worker_id" in task else {})})
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


def _undelivered_source(*, workspace, project_key, root, value):
    from goodteam_os.project_batch_feedback import pending_batch_feedback

    source_key = value.get("batch_feedback_pending") or value.get("undelivered_feedback_anchor")
    feedback = pending_batch_feedback(workspace=workspace, project_key=project_key, root=root,
        value={**value, "batch_feedback_pending": source_key})
    if not feedback:
        raise ValueError("Undelivered discovery requires captured known terminal batch feedback.")
    if (value["discovery"].get("allow_undelivered_feedback") is not True
            and feedback.get("read_only_discovery") is not True):
        raise ValueError("Undelivered feedback requires an explicit new discovery grant.")
    return {"schema_version": "undelivered_project_source.v1", "delivery_verified": False,
            "project_head": _head(root), "batch_run_id": feedback["batch_run_id"],
            "receipt_id": feedback["receipt_id"], "observation_key": source_key}


def _backlog_source(root, project_key, value, *, authorized_tasks=False, exclude_refs=()):
    """Observe authorized seed tasks, never invent a delivery or Worker result."""
    if value["discovery"].get("allow_backlog_judgment") is not True:
        raise ValueError("Backlog judgment requires explicit new authorization.")
    known = {task["id"]: task for task in LocalTaskStore(root, project_key).list_tasks()}
    seeds = value["task_ids"] if authorized_tasks else value["seed_task_ids"]
    intents = value["task_intents"] if authorized_tasks else value["seed_task_intents"]
    if not seeds or any(identifier not in known for identifier in seeds):
        raise ValueError("The authorized backlog source is unavailable.")
    from goodteam_os.tasks import project_task_intent
    from goodteam_os.project_iteration_continuation import _intent
    tasks = []
    for identifier in sorted(seeds):
        task = known[identifier]
        if set(task.get("external_refs", [])) & set(exclude_refs):
            continue  # The reserved decision may already have published its own tasks.
        if _intent(task) != intents.get(identifier):
            raise ValueError("An authorized backlog task intent changed.")
        tasks.append({"task_id": identifier, "intent": project_task_intent(task),
            **{key: task.get(key) for key in ("status", "run_id", "delivery_verified", "delivery_receipt")},
            "dependencies": [{"task_id": dependency, "status": known.get(dependency, {}).get("status", "missing"),
                "delivery_verified": known.get(dependency, {}).get("delivery_verified", False)}
                for dependency in sorted(task.get("dependencies", []))]})
    source = {"schema_version": "authorized_backlog_context.v1" if authorized_tasks else "backlog_judgment_source.v1", "project_key": project_key,
              "continuation_id": value["id"], "authorization_digest": value["authorization_digest"],
              "delivery_verified": False, "tasks": tasks}
    return {**source, "receipt_id": _digest(source)}


def _source_receipt(packet):
    if packet.get("verified_outcome") is not None:
        return packet["verified_outcome"]["delivery"]["receipt_id"]
    if packet.get("backlog_judgment_source") is not None:
        return packet["backlog_judgment_source"]["receipt_id"]
    return packet["undelivered_source"]["receipt_id"]


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


def _authorized_task_context(root, project_key, value, *, exclude_ref=None):
    """Freeze the grant's actual task facts, not unrelated project bookkeeping."""
    from goodteam_os.tasks import project_task_intent

    return [{"task_id": task["id"], "intent": project_task_intent(task),
             **{key: task.get(key) for key in ("status", "run_id", "execution_origin", "dispatch_job_id",
                 "dispatch_run_id", "delivery_verified", "delivery_receipt")}}
            for task in sorted(LocalTaskStore(root, project_key).list_tasks(), key=lambda task: task["id"])
            if task["id"] in value["task_ids"] and not set(task.get("external_refs", [])) &
                ({exclude_ref} if isinstance(exclude_ref, str) else set(exclude_ref or []))]


def _packet(workspace, project_key, root, value, outcome, *, project_config=None, backlog_source=None):
    from goodteam_os.memory import runtime_role_memory_store
    from goodteam_os.project_iteration_planner import _project_evidence
    from goodteam_os.team_control_plane import MANAGER_ROLE, team_view_for_config

    from goodteam_os.project_environment import environment_for_task
    from goodteam_os.worker_setup import load_worker_setup

    environment = environment_for_task(root, load_worker_setup(project_config) if project_config else None)
    environment.pop("setup", None)  # The model plans a task, not installation argv or credentials.
    snapshot = rank_project_backlog(LocalTaskStore(root, project_key).list_tasks(), project_key, limit=None)
    undelivered = None if outcome is not None or backlog_source is not None else _undelivered_source(
        workspace=workspace, project_key=project_key, root=root, value=value)
    refs = [outcome["delivery"]["receipt_id"]] if outcome is not None else [(backlog_source or undelivered)["receipt_id"]]
    if outcome is not None:
        refs += [outcome["quality"][key] for key in ("validation_receipt_id", "review_receipt_id") if key in outcome["quality"]]
    packet = {"schema_version": "project_outcome_discovery_packet.v1", "project_key": project_key,
        "authorization_digest": value["authorization_digest"], "discovery": value["discovery"],
        "multiple_followups_supported": True,
        "project_environment": environment,
        "remaining_budget": {key: None if limit is None else limit - used for key, limit, used in (
            ("manager_calls", value["max_manager_calls"], value["manager_calls"]),
            ("batches", value["max_batches"], value["batches_started"]),
            ("new_tasks", value["discovery"]["max_new_tasks"], value["new_tasks_created"]))},
        "authorized_task_ids": list(value["task_ids"]),
        "authorized_task_context": _authorized_task_context(root, project_key, value),
        "authorized_done_task_ids": [task["id"] for task in LocalTaskStore(root, project_key).list_tasks()
                                     if task["id"] in value["task_ids"] and task["status"] == "done"
                                     and task.get("delivery_verified") is True],
        "backlog": snapshot, "verified_outcome": outcome,
        "allowed_source_refs": refs,
        "repository_understanding": _project_evidence(Path(workspace), project_key, Path(root), inspect_missing=True),
        "memory_pickup": runtime_role_memory_store(root).build_pickup_packet("orchestrator", query=value["discovery"]["objective"])}
    if undelivered is not None:
        packet["undelivered_source"] = undelivered
    if backlog_source is not None:
        packet["backlog_judgment_source"] = backlog_source
    if value["discovery"].get("allow_backlog_judgment") is True:
        # Delivery proves only its own tasks. Keep current authorized facts as
        # a separate input, without relabeling them as accepted execution.
        from goodteam_os.project_investigation_feedback import is_observed_investigation
        facts = _backlog_source(root, project_key, value, authorized_tasks=True)
        packet["authorized_backlog_context"] = facts
        if facts["receipt_id"] not in packet["allowed_source_refs"]:
            packet["allowed_source_refs"].append(facts["receipt_id"])
        packet["authorized_waiting_task_ids"] = [task["id"] for task in LocalTaskStore(root, project_key).list_tasks()
            if task["id"] in value["task_ids"] and not (task["status"] == "done" and task.get("delivery_verified") is True)
            and not is_observed_investigation(value, task)]
    team = team_view_for_config(Path(workspace), project_key, project_config) or {}
    all_team_roles = value["discovery"].get("allow_team_roles") is True
    allowed_roles = {"implementation", "research"} if value["discovery"].get("allow_investigation") is True else {"implementation"}
    if value["discovery"].get("allow_planning") is True:
        allowed_roles.add("planner")
    packet["role_capabilities"] = [
        {"role": role["role"], "state": role.get("state"), "capability": role.get("capability", ""),
         **({"label": role.get("label", role["role"])} if all_team_roles else {}),
         "dispatchable": role.get("state") == "enabled"
             and role.get("capability") == "ready"}
        for role in team.get("roles", []) if role.get("role") != MANAGER_ROLE
        and (all_team_roles or role.get("role") in allowed_roles)
    ]
    if value["discovery"].get("allow_investigation") is True:
        role = next((item for item in packet["role_capabilities"] if item["role"] == "research"), {})
        packet["investigation_capability"] = {
            "role": "research", "execution_policy": "read_only",
            "dispatchable": role.get("dispatchable") is True,
        }
    if value["discovery"].get("allow_planning") is True:
        role = next((item for item in packet["role_capabilities"] if item["role"] == "planner"), {})
        packet["planning_capability"] = {"role": "planner", "execution_policy": "read_only",
                                          "dispatchable": role.get("dispatchable") is True}
    if value.get("investigation_pending"):
        from goodteam_os.project_investigation_feedback import pending_investigation_feedback

        feedback = pending_investigation_feedback(workspace=workspace, project_key=project_key, root=root, value=value)
        packet["investigation_feedback"] = feedback
        packet["allowed_source_refs"].append(feedback["receipt_id"])
    from goodteam_os.project_investigation_feedback import _valid_receipt
    from goodteam_os.project_human_feedback import human_feedback, question_board, question_context

    # Earlier observations remain historical Context after their trigger has
    # been consumed. They do not assert the current HEAD or task acceptance.
    history = list((value.get("investigation_observations") or {}).values())
    if any(not _valid_receipt(value, item) for item in history):
        raise ValueError("A retained investigation observation changed.")
    if history:
        packet["investigation_history"] = history
        # Historical reports are not claims about the latest checkout. Give
        # Manager both revisions; action publication still revalidates the
        # frozen decision, current authority, task intent and delivery evidence.
        decision_head = (outcome or {}).get("project_head") or _head(root)
        packet["investigation_context"] = {
            "decision_project_head": decision_head,
            "reports": [{"receipt_id": item["receipt_id"], "source_project_head": item["project_head"],
                         "matches_decision_revision": item["project_head"] == decision_head}
                        for item in history],
            "interpretation": "Historical findings require judgment against current Context; they are not task acceptance.",
        }
        packet["allowed_source_refs"].extend(item["receipt_id"] for item in history
                                            if item["receipt_id"] not in packet["allowed_source_refs"])
    from goodteam_os.project_batch_feedback import pending_batch_feedback, valid_receipt, is_observed_discovered_readonly
    batch_history = list((value.get("batch_observations") or {}).values())
    if any(not valid_receipt(value, item) for item in batch_history):
        raise ValueError("A retained batch observation changed.")
    if batch_history:
        packet["batch_history"] = batch_history
        known_tasks = {task["id"]: task for task in LocalTaskStore(root, project_key).list_tasks()}
        packet["unresolved_batch_task_ids"] = sorted({item["task_id"] for receipt in batch_history for item in receipt["tasks"]
            if not (is_observed_discovered_readonly(value, known_tasks.get(item["task_id"], {}))
                    or known_tasks.get(item["task_id"], {}).get("status") == "canceled"
                    or known_tasks.get(item["task_id"], {}).get("status") == "done"
                    and known_tasks.get(item["task_id"], {}).get("delivery_verified") is True)})
        packet["batch_context"] = {"decision_project_head": (outcome or {}).get("project_head") or _head(root),
            "interpretation": "Known terminal results are not accepted delivery. Retain unresolved work; do not repeat original invocations."}
        packet["allowed_source_refs"].extend(item["receipt_id"] for item in batch_history)
    if value.get("batch_feedback_pending"):
        packet["batch_feedback"] = pending_batch_feedback(workspace=workspace, project_key=project_key, root=root, value=value)
    packet["human_feedback_supported"] = True
    packet["human_questions"] = question_context(value)
    packet["question_board"] = question_board({**TaskAssistantSessionStore(workspace, project_key).read(),
                                              "project_continuation": value})
    packet["allowed_source_refs"].extend(item["answer"]["receipt_id"]
        for item in packet["question_board"]["items"] if not item["current"] and item["answer"])
    answers = human_feedback(value)
    if answers:
        packet["human_feedback"] = answers
        packet["allowed_source_refs"].extend(item["receipt_id"] for item in answers)
    packet["human_answers"] = [item for item in answers
                               if item["receipt_id"] not in value.get("discovery_consumed", [])]
    packet["packet_digest"] = _digest(packet)
    return packet


def validate_discovered_task(packet, response):
    """Keep frozen single-task decisions compatible; validate a whole new set."""
    if not isinstance(response, dict) or "new_tasks" not in response:
        return _validate_discovered_single(packet, response)
    if packet.get("multiple_followups_supported") is not True or "new_task" in response:
        raise ValueError("Multiple follow-ups require a current packet and one unambiguous task field.")
    proposed = response["new_tasks"]
    maximum = packet["discovery"]["max_new_tasks"]
    if not isinstance(proposed, list) or not proposed or maximum is not None and len(proposed) > maximum:
        raise ValueError("The whole follow-up set must fit the authorized task limit.")
    common = {key: value for key, value in response.items() if key != "new_tasks"}
    validated = [_validate_discovered_single(packet, {**common, "new_task": task}) for task in proposed]
    if any(item["new_task"] is None for item in validated):
        raise ValueError("A follow-up set must contain concrete tasks; use new_task:null to stop.")
    tasks = [item["new_task"] for item in validated]
    if len({_digest(task) for task in tasks}) != len(tasks):
        raise ValueError("A follow-up set must not repeat the same task.")
    return {**{key: value for key, value in validated[0].items() if key != "new_task"}, "new_tasks": tasks}


def _selection_tasks(selection):
    return selection["new_tasks"] if "new_tasks" in selection else ([selection["new_task"]] if selection["new_task"] else [])


def _creation_refs(value, pending):
    base = _creation_ref(value, pending["packet"])
    count = len(_selection_tasks(pending["selection"])) if "selection" in pending else 1
    return [base if index == 0 else base + ":" + str(index) for index in range(count)]


def _validate_discovered_single(packet, response):
    """Validate advice against the frozen grant; no storage or execution effects."""
    fields = {"new_task", "rationale"}
    optional = {"human_question", "wait_for_human"} if packet.get("human_feedback_supported") is True else set()
    if "question_board" in packet:
        optional.add("question_updates")
    if not isinstance(response, dict) or not fields.issubset(response) or set(response) - fields - optional:
        raise ValueError("Discovery must return one bounded follow-up or an evidence-based stopping reason.")
    reason = response["rationale"]
    if not isinstance(reason, str) or not 20 <= len(reason.strip()) <= 1600:
        raise ValueError("Discovery requires a concrete outcome-based rationale.")
    task = response["new_task"]
    actions = {}
    if "human_question" in response:
        from goodteam_os.project_human_feedback import validate_human_question
        actions["human_question"] = validate_human_question(response["human_question"], allow_impact="question_board" in packet)
    updated = set()
    if "question_updates" in response:
        updates = response["question_updates"]
        captured = {item["id"] for item in packet["question_board"]["items"]}
        if not isinstance(updates, list):
            raise ValueError("Question updates must list explicit conclusions for captured questions.")
        actions["question_updates"] = []
        for update in updates:
            if (not isinstance(update, dict) or set(update) != {"question_id", "status", "reason"}
                    or not isinstance(update["question_id"], str) or update["question_id"] not in captured
                    or update["question_id"] in updated or update["status"] not in ("resolved", "not_needed")
                    or not isinstance(update["reason"], str) or not 1 <= len(update["reason"].strip()) <= 1600):
                raise ValueError("A question conclusion needs a captured identity, resolved/not_needed status and reason.")
            updated.add(update["question_id"])
            actions["question_updates"].append({**update, "reason": update["reason"].strip()})
    if "wait_for_human" in response:
        if (type(response["wait_for_human"]) is not bool
                or response["wait_for_human"] and not (actions.get("human_question")
                    or any(item["id"] not in updated for item in packet.get("human_questions", [])))):
            raise ValueError("Waiting for Human input requires a retained or newly proposed question.")
        actions["wait_for_human"] = response["wait_for_human"]
    if task is None:
        return {"new_task": None, "rationale": reason.strip(), **actions}
    fields = {"title", "description", "acceptance_criteria", "write_domains", "dependencies", "source_refs"}
    team_roles = packet["discovery"].get("allow_team_roles") is True
    investigation_allowed = packet["discovery"].get("allow_investigation") is True
    planning_allowed = packet["discovery"].get("allow_planning") is True
    optional = {"role"} if team_roles else {"task_type"} if investigation_allowed or planning_allowed else set()
    # Only frozen packets from the former member-selection contract accept
    # that field. Newly captured Role capabilities never select an instance.
    if "worker_capabilities" in packet and "role_capabilities" not in packet:
        optional.add("worker_id")
    if (not isinstance(task, dict) or not fields.issubset(task) or set(task) - fields - optional
            or not isinstance(task["title"], str) or not 5 <= len(task["title"].strip()) <= 240
            or not isinstance(task["description"], str) or not 20 <= len(task["description"].strip()) <= 4000):
        raise ValueError("A follow-up needs an explicit bounded task, not runtime commands or new permissions.")
    if team_roles:
        from goodteam_os.team_control_plane import MANAGER_ROLE

        role = task.get("role")
        if (not isinstance(role, str) or role == MANAGER_ROLE
                or not any(item.get("role") == role and item.get("dispatchable") is True
                           for item in packet.get("role_capabilities", []))):
            raise ValueError("Choose an enabled and available project Team Role.")
        investigation = task["write_domains"] == []
        planning = False
    else:
        task_type = task.get("task_type", "implementation")
        if task_type not in ("implementation", "investigation", "planning"):
            raise ValueError("Discovery task type must be implementation, investigation or planning.")
        investigation = task_type == "investigation"
        planning = task_type == "planning"
        if investigation and (not investigation_allowed
                or (packet.get("investigation_capability") or {}).get("dispatchable") is not True):
            raise ValueError("Investigation requires explicit authority and an available Researcher.")
        if planning and (not planning_allowed
                or (packet.get("planning_capability") or {}).get("dispatchable") is not True):
            raise ValueError("Planning requires explicit authority and an available Planner.")
        if (investigation or planning) and task["write_domains"] != []:
            raise ValueError("A research or planning task must be read-only and declare no write domains.")
    if "worker_id" in task:
        worker_id = task["worker_id"]
        role = "planner" if planning else "research" if investigation else "implementation"
        if (not isinstance(worker_id, str) or not worker_id.strip() or worker_id != worker_id.strip() or len(worker_id) > 256
                or not any(item.get("worker_id") == worker_id and item.get("role") == role and item.get("dispatchable") is True
                           for item in packet.get("worker_capabilities", []))):
            raise ValueError("The requested Worker is unavailable for this authorized task; no replacement was selected.")
    criteria = task["acceptance_criteria"]
    domains = [] if investigation or planning else normalize_write_domains(task["write_domains"])
    allowed = packet["discovery"]["allowed_write_domains"]
    if (not isinstance(criteria, list) or not 1 <= len(criteria) <= 12
            or any(not isinstance(item, str) or not 5 <= len(item.strip()) <= 500 for item in criteria)
            or domains is None or (
                any(not any(path == parent or path.startswith(parent + "/") for parent in allowed) for path in domains)
                or any(part in {".git", ".goodteam"} for path in domains for part in path.split("/")))):
        raise ValueError("The follow-up's acceptance criteria and write paths must fit the approved discovery scope.")
    dependencies, sources = task["dependencies"], task["source_refs"]
    done = set(packet["authorized_done_task_ids"])
    if (not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies)
            or len(set(dependencies)) != len(dependencies)
            or not set(dependencies).issubset(done & set(packet["authorized_task_ids"]))
            or not isinstance(sources, list) or not sources or any(not isinstance(item, str) for item in sources)
            or len(set(sources)) != len(sources) or not set(sources).issubset(packet["allowed_source_refs"])
            or _source_receipt(packet) not in sources):
        raise ValueError("Follow-up dependencies and source references must name authorized delivered facts.")
    feedback = packet.get("investigation_feedback")
    if feedback and feedback["receipt_id"] not in sources:
        raise ValueError("A report-driven follow-up must reference the captured investigation observation.")
    batch_feedback = packet.get("batch_feedback")
    if batch_feedback and batch_feedback["receipt_id"] not in sources:
        raise ValueError("A batch-driven follow-up must reference its captured results, including unresolved work.")
    return {"new_task": {**task, "title": task["title"].strip(), "description": task["description"].strip(),
                         "acceptance_criteria": [item.strip() for item in criteria], "write_domains": domains},
            "rationale": reason.strip(), **actions}


def _discovered_task_intent(packet, selection, creation_ref):
    """Host-owned execution profile; model text never selects permissions."""
    task = validate_discovered_task(packet, selection)["new_task"]
    if task is None:
        raise ValueError("No follow-up task was proposed.")
    team_roles = packet["discovery"].get("allow_team_roles") is True
    investigation = task["write_domains"] == [] if team_roles else task.get("task_type") == "investigation"
    planning = not team_roles and task.get("task_type") == "planning"
    intent = {key: task[key] for key in ("title", "description", "acceptance_criteria", "write_domains", "dependencies")}
    if "worker_id" in task:
        intent["worker_id"] = task["worker_id"]
    role = task["role"] if team_roles else "planner" if planning else "research" if investigation else "implementation"
    intent.update(role=role, priority="medium",
                  execution_policy="read_only" if investigation or planning else "workspace_write",
                  external_refs=[creation_ref, *task["source_refs"]], artifact_refs=list(task["source_refs"]))
    return intent


def _creation_ref(value, packet):
    return "project-discovery:" + value["id"] + ":" + packet["packet_digest"].split(":", 1)[1]


def _input_receipts(packet):
    """Coalesce captured feedback using the existing consumed-input ledger."""
    receipts = [_source_receipt(packet)]
    if packet.get("authorized_backlog_context"):
        receipts.append(packet["authorized_backlog_context"]["receipt_id"])
    if packet.get("investigation_feedback"):
        receipts.append(packet["investigation_feedback"]["receipt_id"])
    if packet.get("batch_feedback"):
        receipts.append(packet["batch_feedback"]["receipt_id"])
    receipts.extend(item["receipt_id"] for item in packet.get("human_answers", []))
    return list(dict.fromkeys(receipts))


def _budget_exhausted(store, value, reason):
    def exhausted(session):
        current = _current(session, value)
        current.update(status="exhausted", reason=reason)
        return session
    return store._mutate(exhausted)["project_continuation"]


def _revalidate(workspace, project_key, root, config, value, pending):
    from goodteam_os.project_human_feedback import validate_human_input

    packet = pending["packet"]
    changes = []
    validate_human_input(value, packet)
    if packet.get("authorized_backlog_context") is not None:
        if packet["authorized_backlog_context"] != _backlog_source(root, project_key, value,
                authorized_tasks=True, exclude_refs=_creation_refs(value, pending)):
            changes.append("Authorized backlog facts changed during discovery.")
    retained = value.get("investigation_observations") or {}
    if any(retained.get(item["task_id"]) != item for item in packet.get("investigation_history", [])):
        raise ValueError("The historical investigation Context changed during discovery.")
    if packet["packet_digest"] != _digest({key: item for key, item in packet.items() if key != "packet_digest"}):
        raise ValueError("The frozen discovery input changed.")
    if value["authority"] != authority_snapshot(workspace, project_key, Path(config), Path(root)):
        raise ValueError("Discovery configuration or runtime authority changed.")
    if packet.get("backlog_judgment_source") is not None:
        if packet.get("verified_outcome") is not None or packet.get("undelivered_source") is not None:
            raise ValueError("Backlog facts cannot be substituted for another source type.")
        if _backlog_source(root, project_key, value) != packet["backlog_judgment_source"]:
            changes.append("The authorized backlog facts changed during judgment.")
    elif packet.get("verified_outcome") is None:
        # A missing delivery is not a substitute for invalid/changed delivery evidence.
        try:
            verified_outcome(workspace=workspace, project_key=project_key, root=root, value=value)
        except NoVerifiedDelivery:
            pass
        else:
            changes.append("Delivery evidence appeared during undelivered discovery.")
        if _undelivered_source(workspace=workspace, project_key=project_key, root=root, value=value) != packet.get("undelivered_source"):
            changes.append("The undelivered source or current revision changed.")
    else:
        observed = verified_outcome(workspace=workspace, project_key=project_key, root=root, value=value,
                                    run_id=packet["verified_outcome"]["batch_run_id"])
        if observed != packet["verified_outcome"]:
            if {**observed, "project_head": packet["verified_outcome"].get("project_head")} == packet["verified_outcome"]:
                changes.append("The current project revision changed during discovery.")
            else:
                raise ValueError("The source revision or accepted outcome changed during discovery.")
    if packet.get("investigation_feedback"):
        from goodteam_os.project_investigation_feedback import pending_investigation_feedback

        feedback = pending_investigation_feedback(workspace=workspace, project_key=project_key, root=root, value=value)
        if feedback != packet["investigation_feedback"]:
            raise ValueError("The pending investigation no longer matches the reserved Manager input.")
    from goodteam_os.project_batch_feedback import pending_batch_feedback
    retained_batches = value.get("batch_observations") or {}
    if any(retained_batches.get(item["observation_key"]) != item for item in packet.get("batch_history", [])):
        raise ValueError("The historical batch Context changed during discovery.")
    if packet.get("batch_feedback") and pending_batch_feedback(
            workspace=workspace, project_key=project_key, root=root, value=value) != packet["batch_feedback"]:
        raise ValueError("Pending batch results differ from the reserved Manager input.")
    refs = set(_creation_refs(value, pending))
    original_tasks = [task for task in LocalTaskStore(root, project_key).list_tasks()
                      if not refs.intersection(task.get("external_refs", []))]
    if "authorized_task_context" in packet:
        if _authorized_task_context(root, project_key, value, exclude_ref=refs) != packet["authorized_task_context"]:
            changes.append("An authorized task changed during outcome discovery.")
    elif rank_project_backlog(original_tasks, project_key, limit=None)["version"] != pending["backlog_version"]:
        # Already-reserved older decisions keep their original contract.
        raise ValueError("The project backlog changed during outcome discovery.")
    if changes:
        # Do not classify drift until all original receipt/input/authority
        # checks have run. Invalid evidence must never clear a reservation.
        raise DiscoveryContextChanged(" ".join(changes))


def _retain_changed_discovery(store, root, project_key, config, value, pending, selection, execution, reason):
    """Settle only a returned valid decision before any question/task publication."""
    from datetime import UTC, datetime
    from goodteam_os.project_iteration_continuation import _scope_tasks

    packet = pending["packet"]
    if (packet.get("packet_digest") != _digest({key: item for key, item in packet.items() if key != "packet_digest"})
            or pending.get("packet_digest") != packet.get("packet_digest")
            or packet.get("authorization_digest") != value.get("authorization_digest")):
        raise ValueError("The frozen discovery input changed.")
    if value["authority"] != authority_snapshot(store.workspace, project_key, Path(config), Path(root)):
        raise ValueError("Discovery configuration or runtime authority changed.")
    before = _current(store.read(), value)
    try:
        _revalidate(store.workspace, project_key, root, config, before, pending)
    except DiscoveryContextChanged:
        pass  # Full integrity checks succeeded against the latest retained state.
    identifier = f"discovery-context-changed:{value['id']}:{value['manager_calls']}:{pending['packet_digest']}"
    event = {"message_id": identifier, "actor": "system", "event": "project_discovery_decision_superseded",
             "run_id": "", "content": "Completed discovery input changed; no follow-up published.",
             "details": {"pending": pending, "selection": selection, "manager_execution": execution, "reason": reason}}
    def settle(session):
        current = _current(session, value)
        if (current != before or current.get("discovery_pending") != pending or current.get("manager_calls") != value["manager_calls"]
                or "selection" in pending or "task_id" in pending or "task_ids" in pending):
            raise ValueError("The original discovery reservation changed before settlement.")
        _scope_tasks(current, LocalTaskStore(root, project_key).list_tasks())
        timeline = session.setdefault("timeline", [])
        if any(item.get("message_id") == identifier for item in timeline):
            raise ValueError("This discovery settlement was already recorded.")
        timeline.append({**event, "created_at": datetime.now(UTC).isoformat()})
        current.update(discovery_pending=None, reason="discovery_context_changed")
        return session
    try:
        return store._mutate(settle)["project_continuation"]
    except OSError:
        saved = store.read()
        record = next((item for item in saved.get("timeline", []) if item.get("message_id") == identifier), None)
        current = saved.get("project_continuation") or {}
        if (not record or any(record.get(key) != item for key, item in event.items())
                or current.get("id") != value["id"] or current.get("discovery_pending") is not None
                or current.get("manager_calls") != value["manager_calls"]):
            raise
        return current


def _discovery_packet(*, workspace, project_key, root, project_config, value):
    """Read the current feedback before reserving the shared Manager runtime."""
    backlog_source = None
    try:
        outcome = verified_outcome(workspace=workspace, project_key=project_key, root=root, value=value)
    except NoVerifiedDelivery:
        if (value["discovery"].get("allow_backlog_judgment") is True
                and not value.get("batch_feedback_pending") and not value.get("undelivered_feedback_anchor")):
            backlog_source = _backlog_source(root, project_key, value)
        elif value["discovery"].get("allow_undelivered_feedback") is not True:
            if not value.get("batch_feedback_pending") and not value.get("undelivered_feedback_anchor"):
                raise
            _undelivered_source(workspace=workspace, project_key=project_key, root=root, value=value)
        outcome = None
    return outcome, _packet(workspace, project_key, root, value, outcome, project_config=Path(project_config),
                            backlog_source=backlog_source)


def discover_next_iteration(*, workspace, project_key, root, project_config, value, manager_runner=None):
    from goodteam_os.manager_execution import manager_resource_slot

    try:
        if value.get("discovery_pending") is None:
            _, packet = _discovery_packet(workspace=workspace, project_key=project_key, root=root,
                                          project_config=project_config, value=value)
            if all(item in value.get("discovery_consumed", []) for item in _input_receipts(packet)):
                _current(TaskAssistantSessionStore(workspace, project_key).read(), value)
                return value
        # The slot protects a new/reserved Manager decision, not an idle scan.
        # Rebuild and revalidate inside the slot before reserving or invoking.
        with manager_resource_slot(Path(root)) as resource_slot:
            return _discover_next_iteration(workspace=workspace, project_key=project_key, root=root,
                project_config=project_config, value=value, manager_runner=manager_runner, resource_slot=resource_slot)
    except OSError as exc:
        saved = TaskAssistantSessionStore(workspace, project_key).read().get("project_continuation") or {}
        if saved.get("id") == value["id"] and saved.get("discovery_pending"):
            raise DiscoveryPublicationPending("The same reserved discovery input awaits publication replay.") from exc
        if (saved.get("id") == value["id"] and saved.get("status") == "active"
                and saved.get("reason") == "awaiting_human_decision"):
            from goodteam_os.project_human_feedback import pending_questions

            if pending_questions(saved):
                # The atomic question/handoff committed before its caller lost
                # the acknowledgement. Preserve the wait, not another model turn.
                return saved
        if (saved.get("id") == value["id"] and saved.get("status") == "active"
                and saved.get("reason") == "human_answer_received"
                and (saved.get("discovery_last") or {}).get("status") in {"no_followup", "waiting_for_human"}):
            # The old decision settled before acknowledgement was lost. Its
            # newer answer still belongs to the next existing service tick.
            from goodteam_os.project_human_feedback import human_feedback

            if any(item["receipt_id"] not in saved.get("discovery_consumed", []) for item in human_feedback(saved)):
                return saved
        last = saved.get("discovery_last") or {}
        if (saved.get("id") == value["id"] and saved.get("reason") == "outcome_followup_queued"
                and (saved.get("pending_batch") or {}).get("task_ids") == last.get("task_ids", [last.get("task_id")])
                and last.get("trigger_ref", last.get("outcome_receipt_id")) in saved.get("discovery_consumed", [])):
            # The atomic session replacement may have committed before a later
            # I/O error. Observe its already-queued identity; do not block or
            # reserve another planning turn because the caller lost the reply.
            raise DiscoveryPublicationPending("The committed follow-up queue awaits the next service tick.") from exc
        raise


def _discover_next_iteration(*, workspace, project_key, root, project_config, value, manager_runner=None, resource_slot=None):
    """One existing continuation step: observe, reserve, create once and queue."""
    from goodteam_os.assistant_queue import queue_manager_iteration_choice
    from goodteam_os.project_iteration_continuation import _intent
    from goodteam_os.project_iteration_planner import run_manager_iteration_choice

    store = TaskAssistantSessionStore(workspace, project_key)
    from goodteam_os.project_human_feedback import human_feedback, record_human_question, record_question_updates

    pending = value.get("discovery_pending")
    if pending is None:
        outcome, packet = _discovery_packet(workspace=workspace, project_key=project_key, root=root,
                                            project_config=project_config, value=value)
        inputs = _input_receipts(packet)
        if all(item in value.get("discovery_consumed", []) for item in inputs):
            _current(store.read(), value)
            return value  # No new feedback, including when a question is still open.
        if outcome is not None:
            _remember(root, outcome)
        if value["max_manager_calls"] is not None and value["manager_calls"] >= value["max_manager_calls"]:
            return _budget_exhausted(store, value, "approved_iteration_budget_used")
        trigger_ref = _digest(inputs)
        pending = {"packet": packet, "packet_digest": packet["packet_digest"],
                   "backlog_version": packet["backlog"]["version"], "trigger_ref": trigger_ref}
        def reserve(session):
            current = _current(session, value)
            if packet.get("undelivered_source"):
                current["undelivered_feedback_anchor"] = packet["undelivered_source"]["observation_key"]
            current.update(discovery_pending=pending, manager_calls=current["manager_calls"] + 1, reason="outcome_discovery_reserved")
            return session
        value = store._mutate(reserve)["project_continuation"]
    _current(store.read(), value)
    _revalidate(workspace, project_key, root, project_config, value, pending)
    if "selection" not in pending:
        packet = pending["packet"]
        result = (manager_runner or run_manager_iteration_choice)(project_config=Path(project_config), project_key=project_key,
            **({"resource_slot": resource_slot} if manager_runner is None else {}),
            backlog={**packet["backlog"], "planning_scope": packet["packet_digest"].split(":", 1)[1]}, context=packet)
        selection = validate_discovered_task(packet, result.get("selection"))
        try:
            _revalidate(workspace, project_key, root, project_config, value, pending)
        except DiscoveryContextChanged as exc:
            return _retain_changed_discovery(store, root, project_key, project_config, value, pending,
                selection, result.get("manager_execution", {}), str(exc))
        def decided(session):
            current = _current(session, value)
            current["discovery_pending"].update(selection=selection, manager_execution=result.get("manager_execution", {}))
            return session
        value = store._mutate(decided)["project_continuation"]
        pending = value["discovery_pending"]
    selection = validate_discovered_task(pending["packet"], pending["selection"])
    receipt_id = _source_receipt(pending["packet"])
    trigger_ref = pending.get("trigger_ref", receipt_id)
    if selection.get("human_question"):
        value = record_human_question(store, value, pending)
    value = record_question_updates(store, value, pending)
    inputs = _input_receipts(pending["packet"])
    if not _selection_tasks(selection):
        waiting = bool(selection.get("human_question") or selection.get("wait_for_human"))
        def finished(session):
            current = _current(session, value)
            consumed = list(dict.fromkeys([*current.get("discovery_consumed", []), *inputs, trigger_ref]))
            newer_answers = any(item["receipt_id"] not in consumed for item in human_feedback(current))
            unresolved_batch = bool(pending["packet"].get("unresolved_batch_task_ids"))
            backlog_only = (pending["packet"].get("backlog_judgment_source") is not None
                            or bool(pending["packet"].get("authorized_waiting_task_ids")))
            pending_delivery = bool(_unconsumed_deliveries(
                session.get("project_task_batches", {}), current, consumed))
            # A judgment about one result is not the end of another original
            # execution. Read sibling ownership in this same atomic settlement;
            # no new grant, queue, or task-completion projection is created.
            from goodteam_os.project_parallel_dispatch import pending_batches
            known = {task["id"]: task for task in LocalTaskStore(root, project_key).list_tasks()}
            execution_pending = any(batch.get("status") in {"claimed", "running"}
                for batch in pending_batches(session, current, known))
            current.update(status="active" if waiting or newer_answers or backlog_only or pending_delivery or execution_pending else "blocked" if unresolved_batch else "completed",
                reason=("human_answer_received" if newer_answers else
                        "awaiting_human_decision" if waiting else "delivery_feedback_pending" if pending_delivery
                        else "sibling_execution_pending" if execution_pending
                        else "observed_results_require_resolution" if unresolved_batch
                        else "backlog_judgment_waiting" if backlog_only else "discovery_no_followup"), discovery_pending=None,
                investigation_pending=None, batch_feedback_pending=None,
                discovery_consumed=consumed,
                discovery_last={"status": "waiting_for_human" if waiting else "no_followup",
                                "packet_digest": pending["packet_digest"],
                                "task_id": "", "outcome_receipt_id": receipt_id,
                                **({"source_kind": "backlog_judgment"} if backlog_only else {}),
                                "trigger_ref": trigger_ref,
                                "rationale": selection["rationale"]})
            return session
        try:
            return store._mutate(finished)["project_continuation"]
        except OSError:
            # Reconcile only this exact committed decision, not an older wait.
            # No new transport, refund or substitute input is authorized here.
            saved = store.read().get("project_continuation") or {}
            last = saved.get("discovery_last") or {}
            if (saved.get("id") == value["id"]
                    and saved.get("authorization_digest") == value["authorization_digest"]
                    and saved.get("discovery_pending") is None
                    and last.get("packet_digest") == pending["packet_digest"]
                    and last.get("trigger_ref") == trigger_ref
                    and last.get("outcome_receipt_id") == receipt_id
                    and last.get("status") == ("waiting_for_human" if waiting else "no_followup")
                    and last.get("rationale") == selection["rationale"]
                    and all(item in saved.get("discovery_consumed", []) for item in [*inputs, trigger_ref])):
                return saved
            raise

    # Task capacity constrains task publication, not a budgeted Manager judgment
    # about new feedback. Replaying an already-created task spends no new slot.
    if value["max_batches"] is not None and value["batches_started"] >= value["max_batches"]:
        return _budget_exhausted(store, value, "approved_iteration_budget_used")
    proposed = _selection_tasks(selection)
    recorded_ids = pending.get("task_ids", [pending["task_id"]] if pending.get("task_id") else [])
    maximum = value["discovery"]["max_new_tasks"]
    if maximum is not None and value["new_tasks_created"] + len(proposed) - len(recorded_ids) > maximum:
        return _budget_exhausted(store, value, "discovery_task_budget_used")
    refs = _creation_refs(value, pending)
    intents = [_discovered_task_intent(pending["packet"], {"new_task": task, "rationale": selection["rationale"]}, ref)
               for task, ref in zip(proposed, refs)]
    # Reject colliding writers before creating any cards. The real queue and
    # execution boundary still revalidate the actual persisted task inventory.
    if len(intents) > 1:
        from goodteam_os.project_iteration import _validate_parallel_group

        _validate_parallel_group([{"task_id": str(index), "execution_policy": intent["execution_policy"],
            "write_domains": intent["write_domains"], "write_domain_status": "declared"}
            for index, intent in enumerate(intents)])
    def publish(session):
        current = _current(session, value)
        _revalidate(workspace, project_key, root, project_config, current, pending)
        ledger = LocalTaskStore(root, project_key)
        # Only the original frozen Team-capability packet can extend the
        # legacy fixed-role publication path; public task input cannot do so.
        authorized_roles = (frozenset(role["role"] for role in pending["packet"]["role_capabilities"]
                                      if role.get("dispatchable") is True)
                            if current["discovery"].get("allow_team_roles") is True else None)
        created_tasks = ([ledger.create_task_once(creation_ref=refs[0], intent=intents[0],
                                                  authorized_roles=authorized_roles)] if len(intents) == 1
                         else ledger.create_tasks_once(items=list(zip(refs, intents)), authorized_roles=authorized_roles))
        identifiers = []
        for created in created_tasks:
            identifiers.append(created["id"])
            if created["id"] not in current["task_ids"]:
                current["task_ids"].append(created["id"])
                current["task_intents"][created["id"]] = _intent(created)
                current["discovered_task_ids"].append(created["id"])
                current["new_tasks_created"] += 1
        current["discovery_pending"].update(task_id=identifiers[0], task_ids=identifiers)
        current["discovery_last"] = {"status": "task_created", "task_id": identifiers[0], "task_ids": identifiers,
            "outcome_receipt_id": receipt_id, "trigger_ref": trigger_ref, "rationale": selection["rationale"]}
        return session
    try:
        value = store._mutate(publish)["project_continuation"]
    except OSError as exc:
        raise DiscoveryPublicationPending("The retained discovery decision awaits task publication replay.") from exc
    pending = value["discovery_pending"]
    identifiers = pending.get("task_ids", [pending["task_id"]])
    snapshot = rank_project_backlog(LocalTaskStore(root, project_key).list_tasks(), project_key, limit=None)
    choice = validate_iteration_choice(snapshot, {"selected_task_ids": identifiers, "compared_task_ids": identifiers,
                                                 "rationale": selection["rationale"]})
    event_id = "outcome-choice:" + value["id"] + ":" + pending["packet_digest"]
    choice["source_event_id"] = event_id
    store.append_event(event_id, "project_iteration_choice_recorded", details={"choice": choice,
        "manager_execution": pending.get("manager_execution", {}), "outcome_packet_digest": pending["packet_digest"],
        "execution_started": False})
    _current(store.read(), value)
    _revalidate(workspace, project_key, root, project_config, value, pending)
    queued = queue_manager_iteration_choice(workspace=workspace, project_key=project_key, root=root,
        expected_backlog_version=snapshot["version"], expected_task_ids=identifiers, expected_choice_event_id=event_id)
    if queued["queued_task_ids"] != identifiers:
        raise ValueError("Discovery's queue handoff changed its original task.")
    proposals = {item["id"]: item for item in queued["task_assistant_session"]["queued_task_proposals"]}
    ids = queued["queued_proposal_ids"]
    def queued_batch(session):
        current = _current(session, value)
        current.update(pending_batch={"task_ids": identifiers, "proposal_ids": ids,
            "expected_revisions": {item: proposals[item]["revision"] for item in ids}, "run_id": ""},
            discovery_pending=None, investigation_pending=None, batch_feedback_pending=None, batches_started=current["batches_started"] + 1,
            discovery_consumed=list(dict.fromkeys([*current.get("discovery_consumed", []), *inputs, trigger_ref])),
            reason="outcome_followup_queued")
        return session
    return store._mutate(queued_batch)["project_continuation"]

