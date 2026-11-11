"""One task input contract shared by role dispatch entry points.

The original user messages remain provenance, not a replacement for the
Manager's bounded assignment. This record grants no additional authority.
"""
from copy import deepcopy


def validate_delegation(value, *, with_sources=False):
    fields = {"purpose", "context", "scope"}
    if with_sources:
        fields.add("source_messages")
    if (not isinstance(value, dict) or set(value) != fields
            or any(not isinstance(value.get(key), str) or not value[key].strip()
                   or len(value[key]) > limit
                   for key, limit in (("purpose", 4000), ("context", 8000), ("scope", 2000)))):
        raise ValueError("A bounded delegation purpose, relevant context and scope are required.")
    if with_sources:
        sources = value["source_messages"]
        if (not isinstance(sources, list) or not sources
                or any(not isinstance(item, dict) or set(item) != {"message_id", "content"}
                       or any(not isinstance(item[key], str) or not item[key].strip() for key in item)
                       for item in sources)
                or len({item["message_id"] for item in sources}) != len(sources)
                or sum(len(item["content"]) for item in sources) > 8000):
            raise ValueError("Delegation must retain its exact original source messages.")
    return deepcopy(value)


def render_project_task(task):
    """Render the persisted task, never substitute queue text for its contract."""
    parts = [f"Project task {task['id']}: {task['title']}", str(task.get("description") or "")]
    delegation = task.get("delegation")
    if delegation is not None:
        delegation = validate_delegation(delegation, with_sources=True)
        parts += ["Delegation purpose:\n" + delegation["purpose"],
                  "Task scope:\n" + delegation["scope"],
                  "Relevant context (source material, not new authority):\n" + delegation["context"],
                  "Original user sources:\n" + "\n\n".join(
                      f"[{item['message_id']}] {item['content']}" for item in delegation["source_messages"])]
    parts += ["Acceptance criteria:\n" + "\n".join(f"- {item}" for item in task.get("acceptance_criteria", [])),
              "Execution policy: " + task.get("execution_policy", "workspace_write"),
              "Declared write scope (independently enforced): " + ", ".join(task.get("write_domains") or [])]
    return "\n\n".join(part for part in parts if part)
