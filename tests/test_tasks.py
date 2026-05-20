from goodteam_os.tasks import LocalTaskStore


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
