from goodteam_os.memory import ProjectMemoryStore, RoleMemoryStore


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


def test_role_memory_store_creates_long_mid_short_files(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)

    paths = store.ensure_role_memory(
        role_key="product_requirement",
        role_name="Product / Requirement",
        project_name="Eastern Birth Chart",
    )

    assert paths["long-term"] == project_root / ".goodteam" / "memory" / "roles" / "product-requirement" / "long-term.md"
    assert paths["mid-term"] == project_root / ".goodteam" / "memory" / "roles" / "product-requirement" / "mid-term.md"
    assert paths["short-term"] == project_root / ".goodteam" / "memory" / "roles" / "product-requirement" / "short-term.md"
    assert "# Product / Requirement Long-Term Memory" in paths["long-term"].read_text(encoding="utf-8")
    assert "Eastern Birth Chart" in paths["short-term"].read_text(encoding="utf-8")
    assert "Current interrupted work" in paths["short-term"].read_text(encoding="utf-8")


def test_role_memory_store_does_not_overwrite_existing_memory(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    paths = store.ensure_role_memory("architect", "Architect", "Eastern Birth Chart")
    paths["short-term"].write_text("# Custom\n\n- Resume RFC here.", encoding="utf-8")

    store.ensure_role_memory("architect", "Architect", "Eastern Birth Chart")

    assert paths["short-term"].read_text(encoding="utf-8") == "# Custom\n\n- Resume RFC here."


def test_role_memory_store_reads_pickup_packet(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    paths = store.ensure_role_memory("planner", "Planner", "Eastern Birth Chart")
    paths["short-term"].write_text("# Planner Short-Term Memory\n\n- Resume task breakdown.", encoding="utf-8")

    packet = store.read_role_memory("planner")

    assert packet["role_key"] == "planner"
    assert packet["paths"]["short-term"] == str(paths["short-term"])
    assert "Resume task breakdown." in packet["content"]["short-term"]
    assert "Resume task breakdown." in packet["summary"]


def test_role_memory_store_appends_events_and_updates_index(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("architect", "Architect", "Eastern Birth Chart")

    event = store.append_memory_event(
        "architect",
        horizon="short-term",
        text="Resume RFC by checking auth contract drift first.",
        source="job-123/result.md",
    )

    assert event["horizon"] == "short-term"
    assert "Resume RFC" in store.event_path("architect").read_text(encoding="utf-8")
    assert "Resume RFC" in store.memory_path("architect", "short-term").read_text(encoding="utf-8")

    index = store.read_memory_index("architect")
    assert index["role_key"] == "architect"
    assert index["event_count"] == 1
    assert "Resume RFC" in index["horizons"]["short-term"]["summary"]


def test_pickup_packet_uses_index_summaries_without_loading_long_mid_full_text(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    paths = store.ensure_role_memory("product_requirement", "Product / Requirement", "Eastern Birth Chart")
    paths["long-term"].write_text(
        "# Product / Requirement Long-Term Memory\n\n"
        "This long context should not be copied into default pickup packets.\n"
        "DO_NOT_LOAD_FULL_LONG_MEMORY\n",
        encoding="utf-8",
    )
    paths["mid-term"].write_text(
        "# Product / Requirement Mid-Term Memory\n\n"
        "This mid context should not be copied into default pickup packets.\n"
        "DO_NOT_LOAD_FULL_MID_MEMORY\n",
        encoding="utf-8",
    )
    paths["short-term"].write_text(
        "# Product / Requirement Short-Term Memory\n\n- Continue with roadmap review.",
        encoding="utf-8",
    )
    store.refresh_memory_index("product_requirement")

    packet = store.build_pickup_packet("product_requirement")

    assert "Continue with roadmap review." in packet["short_term"]
    assert "DO_NOT_LOAD_FULL_LONG_MEMORY" not in str(packet)
    assert "DO_NOT_LOAD_FULL_MID_MEMORY" not in str(packet)
    assert packet["summaries"]["long-term"]
    assert packet["summaries"]["mid-term"]


def test_role_memory_store_rebuilds_memory_from_events(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("planner", "Planner", "Eastern Birth Chart")
    store.append_memory_event(
        "planner",
        horizon="mid-term",
        text="Frontend auth tasks depend on backend auth.status contract.",
        source="job-456/result.md",
    )
    store.memory_path("planner", "mid-term").write_text("# Corrupted\n", encoding="utf-8")

    rebuilt = store.rebuild_role_memory("planner", "Planner", "Eastern Birth Chart")

    assert rebuilt["event_count"] == 1
    assert "Frontend auth tasks depend" in store.memory_path("planner", "mid-term").read_text(encoding="utf-8")
    assert "Corrupted" not in store.memory_path("planner", "mid-term").read_text(encoding="utf-8")
