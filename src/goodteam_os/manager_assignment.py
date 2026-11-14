"""Manager callable assignment adapter; existing task cards and queue own work.

Called under the conversation operation lock. A model can select a capability
and queue an exact user request, never confirm spending or grant itself scope.
"""
from datetime import UTC, datetime
from hashlib import sha256

from goodteam_os.tasks import LocalTaskStore, task_evidence_digest


def queue_assignment(service, *, store, root, message_id, sources, action):
    if "worker_id" in action:
        raise ValueError("Manager assignments select a Role; execution handles are returned after job creation.")
    from goodteam_os.project_iteration import normalize_write_domains

    # Use the same canonical scope for card creation, retry comparison and
    # queue publication. Otherwise valid `src/` becomes `src` in the card and
    # fails the comparison below after the card has already been persisted.
    domains = action.get("write_domains")
    policy = action.get("execution_policy")
    if policy == "read_only" and domains == []:
        normalized = []
    elif policy == "workspace_write":
        normalized = normalize_write_domains(domains)
    else:
        normalized = None
    if normalized is None:
        raise ValueError("The assignment requires valid write domains for its execution policy.")
    action = {**action, "write_domains": normalized}
    # Intake only saves unconfirmed future work. Active Goal/Workflow/resource
    # exclusion belongs to the existing execution admission, not this queue.
    team = service.team_view(store.project_key) or {}
    role = next((item for item in team.get("roles", []) if item.get("role") == action["role"]), {})
    # The capability projection already checks the explicitly configured Runtime
    # source. A Role need not own permanently rostered members to be callable.
    if role.get("state") != "enabled" or role.get("capability") != "ready":
        raise ValueError("The requested Worker capability is unavailable; no assignment was queued.")
    text = "\n\n".join(item["content"] for item in sources)
    if not 1 <= len(text) <= 8000:
        raise ValueError("The assignment exceeds the existing task queue input limit.")
    delegation = None
    if "delegation" in action:
        from goodteam_os.task_delegation import validate_delegation

        delegation = {**validate_delegation(action["delegation"]),
                      "source_messages": [{"message_id": item["message_id"], "content": item["content"]} for item in sources]}
    identity = sha256(f"{store.project_key}:{message_id}".encode()).hexdigest()
    proposal_id = identity[:32]
    reference = "manager-message:" + identity
    tasks = LocalTaskStore(root, store.project_key)
    # A crash between card and queue publication reuses the original card.
    matches = [item for item in tasks.list_tasks() if reference in item.get("external_refs", [])]
    if len(matches) > 1:
        raise ValueError("Assignment source is ambiguous.")
    dependencies = action.get("dependencies", [])
    known = {task["id"]: task for task in tasks.list_tasks()}
    if (not isinstance(dependencies, list) or any(not isinstance(item, str) or item not in known for item in dependencies)
            or len(set(dependencies)) != len(dependencies)):
        raise ValueError("Assignment dependencies must name existing project tasks.")
    if not matches and action["write_domains"] == [".goodteam/environment"]:
        from goodteam_os.worker_setup import load_worker_setup

        if not service.project_config or not load_worker_setup(service.project_config):
            raise ValueError("The project has no configured installation; Planner must determine its actual environment requirements first.")
    from goodteam_os.assistant_queue import _project_task_request

    def preparation_request(task):
        return _project_task_request({**task,
            "description": "Prepare the configured project environment. Report installation results and remaining environment problems; do not implement the dependent development request."})

    prerequisites = []
    session = store.read()
    proposals = [*session.get("queued_task_proposals", [])]
    current = session.get("proposed_task")
    if current:
        proposals.append(current)
    def discarded_preparation(task):
        if task.get("status") == "done":
            return False
        linked = [item for item in proposals if item.get("project_task_id") == task["id"]]
        return bool(linked) and all(item.get("status") in {"cancelled", "canceled"} for item in linked)
    if not matches and action["execution_policy"] == "workspace_write" and action["write_domains"] != [".goodteam/environment"]:
        from goodteam_os.worker_setup import load_worker_setup

        setup = load_worker_setup(service.project_config) if service.project_config else None
        if setup and not any(known[item].get("write_domains") == [".goodteam/environment"] for item in dependencies):
            existing = [task for task in known.values() if task.get("write_domains") == [".goodteam/environment"]
                        and task["status"] not in {"canceled", "cancelled", "abandoned"}
                        and not discarded_preparation(task)]
            if existing:
                preparation = existing[-1]
            else:
                preparation = tasks.create_task(
                    title="Prepare the shared project environment",
                    description="Prepare the configured project environment. Report installation results and remaining environment problems; do not implement the dependent development request.",
                    role=action["role"],
                    acceptance_criteria=["The configured restricted project installation succeeds and retains its exit status and logs."],
                    external_refs=[reference + ":environment"], execution_policy="workspace_write",
                    write_domains=[".goodteam/environment"],
                    delegation={"purpose": "Prepare the single project environment required by this original assignment.",
                                "context": "Run only the existing configured setup through the restricted installer.",
                                "scope": "Shared project environment preparation only; the original development task is retained separately.",
                                "source_messages": [{"message_id": item["message_id"], "content": item["content"]} for item in sources]})
                preparation_id = sha256((identity + ":environment").encode()).hexdigest()[:32]
                prerequisites.append({"id": preparation_id, "exact_request": preparation_request(preparation), "revision": 1,
                    "source_message_ids": [item["message_id"] for item in sources],
                    "source_event_id": "reply:" + message_id, "status": "queued", "confirmed": False,
                    "run_id": "", "created_at": datetime.now(UTC).isoformat(), "project_task_id": preparation["id"],
                    "project_task_digest": task_evidence_digest(preparation), "source": "manager_conversation"})
            dependencies = [*dependencies, preparation["id"]]
    if matches:
        # Reuse only the task's existing automatic prerequisite on retry; do not
        # rewrite its frozen original intent after assignment.
        automatic = [item for item in matches[0].get("dependencies", [])
                     if item not in dependencies and item in known
                     and known[item].get("write_domains") == [".goodteam/environment"]]
        dependencies = [*dependencies, *automatic]
    task = matches[0] if matches else tasks.create_task(
        title=text.splitlines()[0][:180], description=text, role=action["role"],
        acceptance_criteria=action["acceptance_criteria"], external_refs=[reference],
        execution_policy=action["execution_policy"], write_domains=action["write_domains"] or None,
        delegation=delegation, dependencies=dependencies,
    )
    if (task["description"] != text or task["role"] != action["role"]
            or task.get("execution_policy", "workspace_write") != action["execution_policy"]
            or task.get("write_domains", []) != action["write_domains"]
            or task.get("delegation") != delegation
            or task.get("dependencies", []) != dependencies
            or task.get("worker_id") is not None
            or task["acceptance_criteria"] != action["acceptance_criteria"]):
        raise ValueError("Assignment retry differs from its original task input.")
    for dependency in dependencies:
        preparation = tasks.get_task(dependency)
        if reference + ":environment" in preparation.get("external_refs", []) and not prerequisites:
            prerequisites.append({"id": sha256((identity + ":environment").encode()).hexdigest()[:32],
                "exact_request": preparation_request(preparation), "revision": 1,
                "source_message_ids": [item["message_id"] for item in sources],
                "source_event_id": "reply:" + message_id, "status": "queued", "confirmed": False,
                "run_id": "", "created_at": preparation["created_at"], "project_task_id": preparation["id"],
                "project_task_digest": task_evidence_digest(preparation), "source": "manager_conversation"})
    action = {**action, "dependencies": dependencies}
    proposal = {"id": proposal_id, "exact_request": text, "revision": 1,
        "source_message_ids": [item["message_id"] for item in sources],
        "source_event_id": "reply:" + message_id, "status": "queued", "confirmed": False,
        "run_id": "", "created_at": datetime.now(UTC).isoformat(), "project_task_id": task["id"],
        "project_task_digest": task_evidence_digest(task), "source": "manager_conversation",
        "web_search": task.get("web_search"),
        "assignment": {key: action[key] for key in ("role", "execution_policy", "write_domains", "acceptance_criteria", "delegation", "dependencies") if key in action}}

    def append(session):
        queue = session.setdefault("queued_task_proposals", [])
        for prerequisite in prerequisites:
            previous_prerequisite = next((item for item in queue if item["id"] == prerequisite["id"]), None)
            if previous_prerequisite is None:
                queue.append(prerequisite)
            elif previous_prerequisite["project_task_id"] != prerequisite["project_task_id"]:
                raise ValueError("Environment prerequisite queue identity conflicts with its original task.")
        previous = next((item for item in queue if item["id"] == proposal_id), None)
        if previous is None:
            queue.append(proposal)
        elif previous["project_task_id"] != task["id"] or previous["exact_request"] != text:
            raise ValueError("Assignment queue identity conflicts with its original input.")
        return session
    store._mutate(append)
    return proposal

