"""Goal recovery must recheck ownership after acquiring the workflow lock."""
from contextlib import contextmanager
from copy import deepcopy

import pytest

from goodteam_os import workflow_mutations
from goodteam_os.goal_continuation import tick_goal
from goodteam_os.state import StateStore, create_initial_state
from test_goal_continuation import setup as setup  # noqa: F401


@pytest.mark.parametrize("replacement", ["run", "idea", "missing"])
@pytest.mark.parametrize("stopped", [False, True])
def test_goal_recovery_preserves_current_replaced_during_lock_acquisition(
        setup, monkeypatch, replacement, stopped):
    workspace, _, _, store, create = setup
    goal = create()
    goal = store.next_work(goal["id"], expected_revision=goal["revision"], idempotency_key="claim")
    work = goal["active_work"]
    if stopped:
        goal = store.stop_goal(goal["id"], expected_revision=goal["revision"], idempotency_key="stop")
    state = create_initial_state(work["run_id"], "DEMO", work["task"])
    state.current_node = "security"
    state.next_node = "await_security_approval"
    state.pending_transition = {"id": "old-operation", "status": "executing", "action": {}}
    workflow_mutations.persist_transition(workspace, state)
    states = StateStore(workspace)
    state_directory = states.project_state_dir("DEMO")
    guard = workflow_mutations.workflow_guard
    before = {}
    acquisitions = []

    @contextmanager
    def replace_at_acquisition(*args, **kwargs):
        # The Goal's first read saw its own run. Another workflow can replace
        # current before recovery obtains the separate workflow lock.
        with guard(*args, **kwargs):
            acquisitions.append(True)
            changed = deepcopy(state)
            if replacement == "missing":
                (state_directory / "current.json").unlink()
            else:
                if replacement == "run":
                    changed.run_id = "unrelated-run"
                else:
                    changed.idea = "Unrelated input under the same run ID"
                changed.pending_transition["id"] = "replacement-operation"
                workflow_mutations.persist_transition(workspace, changed)
            before.update({
                path.relative_to(state_directory): path.read_bytes()
                for path in state_directory.rglob("*.json")
            })
            yield

    monkeypatch.setattr(workflow_mutations, "workflow_guard", replace_at_acquisition)
    result = tick_goal(
        workspace, "DEMO", goal["id"],
        starter=lambda **kwargs: pytest.fail("Recovery dispatched a workflow"),
        assessor=lambda **kwargs: pytest.fail("Recovery assessed another workflow"),
    )

    assert acquisitions == [True]
    assert {
        path.relative_to(state_directory): path.read_bytes()
        for path in state_directory.rglob("*.json")
    } == before
    assert result["status"] == ("stopped" if stopped else "blocked")
    assert len(result["work_units"]) == 1
    assert result["work_units"][0]["run_id"] == work["run_id"]
    assert not list(workspace.rglob("job.json"))


@pytest.mark.parametrize("bound", [False, True])
def test_matching_and_unbound_recovery_still_interrupts_abandoned_transition(tmp_path, bound):
    state = create_initial_state("owned-run", "DEMO", "Exact owned input")
    state.current_node = "security"
    state.next_node = "await_security_approval"
    state.pending_transition = {"id": "operation", "status": "executing", "action": {}}
    workflow_mutations.persist_transition(tmp_path, state)

    expected = dict(expected_run_id=state.run_id, expected_idea=state.idea) if bound else {}
    recovered = workflow_mutations.recover_transition(tmp_path, "DEMO", **expected)

    assert recovered.pending_transition["status"] == "interrupted"
    assert recovered.next_node == "blocked"
    assert workflow_mutations.INTERRUPTED_TRANSITION in recovered.blockers
    assert StateStore(tmp_path).read_project_current("DEMO") == recovered


def test_bound_recovery_preserves_live_workflow_lock(tmp_path):
    state = create_initial_state("owned-run", "DEMO", "Exact owned input")
    state.pending_transition = {"id": "operation", "status": "executing", "action": {}}
    workflow_mutations.persist_transition(tmp_path, state)
    with workflow_mutations.workflow_guard(tmp_path, "DEMO"):
        recovered = workflow_mutations.recover_transition(
            tmp_path, "DEMO", expected_run_id=state.run_id, expected_idea=state.idea)
    assert recovered == state
    assert StateStore(tmp_path).read_project_current("DEMO") == state


@pytest.mark.parametrize("empty_field", ["expected_run_id", "expected_idea"])
def test_empty_expected_identity_does_not_disable_binding(tmp_path, empty_field):
    state = create_initial_state("owned-run", "DEMO", "Exact owned input")
    state.pending_transition = {"id": "operation", "status": "executing", "action": {}}
    workflow_mutations.persist_transition(tmp_path, state)
    expected = dict(expected_run_id=state.run_id, expected_idea=state.idea)
    expected[empty_field] = ""
    recovered = workflow_mutations.recover_transition(tmp_path, "DEMO", **expected)
    assert recovered == state
    assert StateStore(tmp_path).read_project_current("DEMO") == state
