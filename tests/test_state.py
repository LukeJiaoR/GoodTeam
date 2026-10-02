import json

from goodteam_os.state import StateStore, create_initial_state


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
    assert checkpoint == tmp_path / "state" / "projects" / "CONTENTOPS" / "checkpoints" / "run-1" / "start-001.json"
    assert store.latest_project_checkpoint_path("CONTENTOPS", "run-1", "start") == checkpoint
    assert store.read_project_current("CONTENTOPS").run_id == "run-1"
    assert not (tmp_path / "state" / "current.json").exists()


def test_state_store_reads_legacy_state_without_delivery_standards(tmp_path):
    path = tmp_path / "state" / "current.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        (
            '{"run_id": "run-1", "project_key": "GTO", "idea": "Build AI Team OS", '
            '"approvals": {"prd": "pending"}}'
        ),
        encoding="utf-8",
    )

    state = StateStore(root=tmp_path).read_current()

    assert state.delivery_mode == "init_project"
    assert state.delivery_standards == []


def test_state_store_disambiguates_revisited_checkpoint_nodes(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    store = StateStore(root=tmp_path)

    first = store.write_checkpoint(state)
    state.next_node = "implementation"
    second = store.write_checkpoint(state)

    assert first != second
    assert first.name == "start-001.json"
    assert second.name == "start-002.json"
    assert first.exists()
    assert second.exists()
    assert store.latest_checkpoint_path("run-1", "start") == second
    first_payload = json.loads(first.read_text(encoding="utf-8"))
    second_payload = json.loads(second.read_text(encoding="utf-8"))
    assert first_payload["checkpoint"] == {"node": "start", "sequence": 1}
    assert second_payload["checkpoint"] == {"node": "start", "sequence": 2}


def test_state_store_disambiguates_revisited_project_checkpoint_nodes(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Build an AI content studio")
    store = StateStore(root=tmp_path)

    first = store.write_project_checkpoint(state)
    state.next_node = "implementation"
    second = store.write_project_checkpoint(state)

    assert first != second
    assert first.name == "start-001.json"
    assert second.name == "start-002.json"
    assert first.exists()
    assert second.exists()
    assert store.latest_project_checkpoint_path("CONTENTOPS", "run-1", "start") == second
    first_payload = json.loads(first.read_text(encoding="utf-8"))
    second_payload = json.loads(second.read_text(encoding="utf-8"))
    assert first_payload["checkpoint"] == {"node": "start", "sequence": 1}
    assert second_payload["checkpoint"] == {"node": "start", "sequence": 2}


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


def test_state_store_lists_every_valid_project_archive_newest_first(tmp_path):
    store = StateStore(tmp_path)
    first = create_initial_state("run-first", "CONTENTOPS", "First goal")
    store.write_project_current(first)
    first_path = store.archive_project_current("CONTENTOPS", reason="cancelled")
    second = create_initial_state("run-second", "CONTENTOPS", "Second goal")
    store.write_project_current(second)
    second_path = store.archive_project_current("CONTENTOPS", reason="complete")
    invalid_path = second_path.parent / "invalid.json"
    invalid_path.write_text("not-json", encoding="utf-8")
    first_path.touch()
    second_path.touch()

    archived = store.list_project_archived("CONTENTOPS")

    assert {state.run_id for _, state in archived} == {"run-first", "run-second"}
    assert all(path != invalid_path for path, _ in archived)
