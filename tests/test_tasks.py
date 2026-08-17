from goodteam_os.state import create_initial_state
from goodteam_os.tasks import LocalTaskStore, build_task_board, serialize_task_board, sync_stage_tasks


def test_local_task_store_creates_project_task_ledger(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = LocalTaskStore(project_root)

    task = store.create_task(
        title="Review takeover context",
        description="Confirm project memory and source docs are enough.",
        role="product_requirement",
        acceptance_criteria=["Context sources are listed.", "Missing docs are called out."],
    )

    assert task["id"] == "GT-1"
    assert task["status"] == "todo"
    assert task["role"] == "product_requirement"
    ledger = project_root / ".goodteam" / "tasks" / "tasks.json"
    assert ledger.is_file()
    assert store.list_tasks()[0]["title"] == "Review takeover context"


def test_local_task_store_updates_task_status(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = LocalTaskStore(project_root)
    task = store.create_task(title="Draft PRD", description="", role="product")

    updated = store.update_task(task["id"], status="in_progress", artifact_refs=["takeover"])

    assert updated["status"] == "in_progress"
    assert updated["artifact_refs"] == ["takeover"]
    assert store.get_task("GT-1")["status"] == "in_progress"


def test_local_task_store_rejects_unknown_status(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = LocalTaskStore(project_root)
    task = store.create_task(title="Draft PRD", description="", role="product")

    try:
        store.update_task(task["id"], status="maybe")
    except ValueError as exc:
        assert "Unknown task status" in str(exc)
    else:
        raise AssertionError("Expected unknown status to fail")


def test_task_board_maps_local_task_statuses_to_lightweight_kanban(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = LocalTaskStore(project_root)
    backlog = store.create_task(title="Run CONTENTOPS smoke", description="", role="qa", priority="high")
    doing = store.create_task(title="Verify GOODTEAM-SELF smoke", description="", role="qa")
    blocked = store.create_task(title="Fix failed smoke", description="", role="qa")
    done = store.create_task(title="Record smoke evidence", description="", role="release")
    review = store.create_task(title="Review smoke report", description="", role="review")
    canceled = store.create_task(title="Old smoke run", description="", role="qa")

    store.update_task(doing["id"], status="in_progress")
    store.update_task(blocked["id"], status="blocked")
    store.update_task(done["id"], status="done")
    store.update_task(review["id"], status="review")
    store.update_task(canceled["id"], status="canceled")

    board = build_task_board(store.list_tasks())

    assert [task["id"] for task in board["backlog"]] == [backlog["id"], review["id"]]
    assert [task["id"] for task in board["doing"]] == [doing["id"]]
    assert [task["id"] for task in board["blocked"]] == [blocked["id"]]
    assert [task["id"] for task in board["done"]] == [done["id"]]
    assert canceled["id"] not in [task["id"] for column in board.values() for task in column]


def test_serialize_task_board_outputs_stable_summary(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = LocalTaskStore(project_root)
    task = store.create_task(
        title="Run CONTENTOPS smoke",
        description="CONTENTOPS must leave a smoke report.",
        role="qa",
        priority="high",
        acceptance_criteria=["latest.json exists"],
        artifact_refs=["state/smoke-runs/latest.json"],
    )

    payload = serialize_task_board(build_task_board(store.list_tasks()))

    assert payload["counts"] == {"backlog": 1, "doing": 0, "blocked": 0, "done": 0}
    assert payload["columns"]["backlog"][0]["id"] == task["id"]
    assert payload["columns"]["backlog"][0]["title"] == "Run CONTENTOPS smoke"
    assert payload["columns"]["backlog"][0]["acceptance_criteria"] == ["latest.json exists"]


def test_sync_stage_tasks_is_idempotent_and_maps_workflow_progress(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = LocalTaskStore(project_root)
    state = create_initial_state("run-1", "CONTENTOPS", "Build content workflow")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"

    sync_stage_tasks(state, store)
    sync_stage_tasks(state, store)

    tasks = store.list_tasks()
    assert len(tasks) == 5
    by_ref = {task["external_refs"][0]: task for task in tasks}
    assert by_ref["stage:product_requirement"]["status"] == "in_progress"
    assert by_ref["stage:architect"]["status"] == "todo"

    state.artifacts["prd_proposed"] = "/tmp/prd.proposed.md"
    sync_stage_tasks(state, store)
    assert store.get_task(by_ref["stage:product_requirement"]["id"])["status"] == "review"

    state.artifacts.pop("prd_proposed")
    state.approvals["prd"] = "approved"
    state.current_node = "rfc_review_gate"
    state.next_node = "await_rfc_approval"
    sync_stage_tasks(state, store)

    board = build_task_board(store.list_tasks())
    assert by_ref["stage:product_requirement"]["id"] in [task["id"] for task in board["done"]]
    assert by_ref["stage:architect"]["id"] in [task["id"] for task in board["doing"]]
    assert len(store.list_tasks()) == 5


def test_sync_stage_tasks_seeds_future_stages_during_takeover(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = LocalTaskStore(project_root)
    state = create_initial_state("run-1", "CONTENTOPS", "Adopt existing project")
    state.approvals["takeover"] = "pending"
    state.current_node = "takeover_review_gate"
    state.next_node = "await_takeover_approval"

    sync_stage_tasks(state, store)

    assert {task["status"] for task in store.list_tasks()} == {"todo"}


def test_sync_stage_tasks_replaces_and_clears_stage_artifact_refs(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = LocalTaskStore(project_root)
    state = create_initial_state("run-1", "CONTENTOPS", "Build content workflow")
    state.approvals["prd"] = "approved"
    state.artifacts["prd"] = "/tmp/prd-v1.md"

    sync_stage_tasks(state, store)
    prd_task = next(task for task in store.list_tasks() if "stage:product_requirement" in task["external_refs"])

    assert store.get_task(prd_task["id"])["artifact_refs"] == ["/tmp/prd-v1.md"]

    state.artifacts["prd"] = "/tmp/prd-v2.md"
    sync_stage_tasks(state, store)

    assert store.get_task(prd_task["id"])["artifact_refs"] == ["/tmp/prd-v2.md"]

    state.artifacts.pop("prd")
    sync_stage_tasks(state, store)

    assert store.get_task(prd_task["id"])["artifact_refs"] == []


def test_task_id_migration_updates_dependencies_and_prevents_collision(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = LocalTaskStore(project_root)

    t1 = store.create_task(title="Task 1", description="", role="product_requirement")
    t2 = store.create_task(title="Task 2", description="", role="architect", dependencies=[t1["id"]])

    assert t1["id"] == "GT-1"
    assert t2["id"] == "GT-2"
    assert store.get_task("GT-2")["dependencies"] == ["GT-1"]

    # Migrate GT-1 to DEMO-1
    store.update_task("GT-1", id="DEMO-1")

    # Dependency in GT-2 is automatically updated to DEMO-1
    assert store.get_task("GT-2")["dependencies"] == ["DEMO-1"]
    assert store.get_task("DEMO-1")["title"] == "Task 1"

    # Collision guard: attempting to update GT-2 to already-existing DEMO-1 is rejected/ignored
    store.update_task("GT-2", id="DEMO-1")
    assert store.get_task("GT-2")["id"] == "GT-2"

    # Creating a new task with project_key avoids collision
    store_demo = LocalTaskStore(project_root, project_key="DEMO")
    t3 = store_demo.create_task(title="Task 3", description="", role="implementation")
    assert t3["id"] == "DEMO-3"
