from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state


def test_create_initial_state_has_pending_approvals():
    state = create_initial_state(
        run_id="run-1",
        project_key="GTO",
        idea="Build AI Team OS",
    )

    assert state.run_id == "run-1"
    assert state.project_key == "GTO"
    assert state.delivery_mode == "init_project"
    assert state.approvals["prd"] == ApprovalDecision.PENDING
    assert state.next_node == "product_requirement"


def test_state_store_writes_current_and_checkpoint(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    store = StateStore(root=tmp_path)

    current = store.write_current(state)
    checkpoint = store.write_checkpoint(state)

    assert current.exists()
    assert checkpoint.exists()
    assert current.read_text(encoding="utf-8").count('"run_id": "run-1"') == 1
    assert "state/checkpoints/run-1" in str(checkpoint)


def test_state_store_writes_project_scoped_current_and_checkpoint(tmp_path):
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build an AI content studio for TikTok, Reddit, X, and LinkedIn post production.",
    )
    store = StateStore(root=tmp_path)

    current = store.write_project_current(state)
    checkpoint = store.write_project_checkpoint(state)

    assert current == tmp_path / "state" / "projects" / "CONTENTOPS" / "current.json"
    assert checkpoint == tmp_path / "state" / "projects" / "CONTENTOPS" / "checkpoints" / "run-1" / "start.json"
    assert store.read_project_current("CONTENTOPS").run_id == "run-1"
    assert not (tmp_path / "state" / "current.json").exists()


def test_state_store_archives_and_clears_project_current(tmp_path):
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build an AI content studio for TikTok, Reddit, X, and LinkedIn post production.",
    )
    store = StateStore(root=tmp_path)
    store.write_project_current(state)

    archive = store.archive_project_current("CONTENTOPS", reason="cancelled")

    assert archive == tmp_path / "state" / "projects" / "CONTENTOPS" / "archived" / "run-1-cancelled.json"
    assert archive.exists()
    assert store.read_project_current("CONTENTOPS") is None
