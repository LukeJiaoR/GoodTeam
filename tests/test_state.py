from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state


def test_create_initial_state_has_pending_approvals():
    state = create_initial_state(
        run_id="run-1",
        project_key="GTO",
        idea="Build AI Team OS",
    )

    assert state.run_id == "run-1"
    assert state.project_key == "GTO"
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
