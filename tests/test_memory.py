from goodteam_os.memory import ProjectMemoryStore


def test_project_memory_store_creates_default_packet(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = ProjectMemoryStore(project_root)

    path = store.ensure_project_memory("EBC", "Eastern Birth Chart")

    assert path == project_root / ".goodteam" / "memory" / "project-memory.md"
    content = path.read_text(encoding="utf-8")
    assert "# Project Memory: Eastern Birth Chart" in content
    assert "## Long-Term Memory" in content
    assert "## Mid-Term Memory" in content
    assert "## Near-Term Memory" in content


def test_project_memory_store_reads_existing_packet(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = ProjectMemoryStore(project_root)
    path = store.ensure_project_memory("EBC", "Eastern Birth Chart")
    path.write_text("# Project Memory\n\n## Long-Term Memory\n\n- Stable fact.", encoding="utf-8")

    packet = store.read_project_memory("EBC")

    assert packet["path"] == str(path)
    assert "Stable fact." in packet["content"]
    assert "Stable fact." in packet["summary"]
