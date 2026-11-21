"""Saving later work never admits it into an already active Goal."""
from types import SimpleNamespace

from goodteam_os.goals import GoalStore
from goodteam_os.manager_assignment import queue_assignment
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


def test_active_goal_keeps_later_assignment_without_starting_or_changing_authority(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    goals = GoalStore(tmp_path, "DEMO")
    goal = goals.create_goal(objective="Finish A", acceptance_criteria=["A reviewed"],
        allowed_scope=["A only"], max_work_units=1, confirmed=True, idempotency_key="A")
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    service = SimpleNamespace(workspace=tmp_path, team_view=lambda _: {"roles": [
        {"role": "research", "state": "enabled", "capability": "ready", "workers": [{"dispatchable": True}]}]})
    action = dict(role="research", execution_policy="read_only", write_domains=[],
                  acceptance_criteria=["Summarize B"],
                  delegation=dict(purpose="Investigate B later", context="A is still active", scope="Read only B"))
    args = dict(service=service, store=store, root=root, message_id="B",
                sources=[dict(message_id="B", content="Save B for later")], action=action)
    proposal = queue_assignment(**args)
    queue_assignment(**args)  # same source reuses the original card and proposal
    session = store.read()
    assert len(session["queued_task_proposals"]) == len(LocalTaskStore(root, "DEMO").list_tasks()) == 1
    assert session["queued_task_proposals"][0]["id"] == proposal["id"]
    assert not proposal["confirmed"] and not proposal["run_id"]
    assert not session["proposed_task"] and not session.get("project_task_batches")
    assert goals.get_goal(goal["id"]) == goal
    assert LocalTaskStore(root, "DEMO").get_task(proposal["project_task_id"])["status"] == "todo"
