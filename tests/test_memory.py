from goodteam_os.memory import ProjectMemoryStore, RoleMemoryStore


def test_project_memory_store_creates_default_packet(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = ProjectMemoryStore(project_root)

    path = store.ensure_project_memory("CONTENTOPS", "AI Content Studio")

    assert path == project_root / ".goodteam" / "memory" / "project-memory.md"
    content = path.read_text(encoding="utf-8")
    assert "# Project Memory: AI Content Studio" in content
    assert "## Long-Term Memory" in content
    assert "## Mid-Term Memory" in content
    assert "## Near-Term Memory" in content


def test_project_memory_store_reads_existing_packet(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = ProjectMemoryStore(project_root)
    path = store.ensure_project_memory("CONTENTOPS", "AI Content Studio")
    path.write_text("# Project Memory\n\n## Long-Term Memory\n\n- Stable fact.", encoding="utf-8")

    packet = store.read_project_memory("CONTENTOPS")

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
        project_name="AI Content Studio",
    )

    assert paths["long-term"] == project_root / ".goodteam" / "memory" / "roles" / "product-requirement" / "long-term.md"
    assert paths["mid-term"] == project_root / ".goodteam" / "memory" / "roles" / "product-requirement" / "mid-term.md"
    assert paths["short-term"] == project_root / ".goodteam" / "memory" / "roles" / "product-requirement" / "short-term.md"
    assert "# Product / Requirement Long-Term Memory" in paths["long-term"].read_text(encoding="utf-8")
    assert "AI Content Studio" in paths["short-term"].read_text(encoding="utf-8")
    assert "Current interrupted work" in paths["short-term"].read_text(encoding="utf-8")


def test_role_memory_store_does_not_overwrite_existing_memory(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    paths = store.ensure_role_memory("architect", "Architect", "AI Content Studio")
    paths["short-term"].write_text("# Custom\n\n- Resume RFC here.", encoding="utf-8")

    store.ensure_role_memory("architect", "Architect", "AI Content Studio")

    assert paths["short-term"].read_text(encoding="utf-8") == "# Custom\n\n- Resume RFC here."


def test_role_memory_store_reads_pickup_packet(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    paths = store.ensure_role_memory("planner", "Planner", "AI Content Studio")
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
    store.ensure_role_memory("architect", "Architect", "AI Content Studio")

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


def test_role_memory_store_event_texts_returns_exact_text_set(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)

    assert store.event_texts("qa") == set()

    store.append_memory_event(
        "qa",
        horizon="mid-term",
        text="QA should verify memory write-back via events.jsonl.",
        source="job-1/result.md",
    )
    store.append_memory_event(
        "qa",
        horizon="short-term",
        text="Follow up on same-run propagation in a later milestone.",
        source="job-2/result.md",
    )

    assert store.event_texts("qa") == {
        "QA should verify memory write-back via events.jsonl.",
        "Follow up on same-run propagation in a later milestone.",
    }
    assert all("/" not in text for text in store.event_texts("qa"))


def test_pickup_packet_uses_index_summaries_without_loading_long_mid_full_text(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    paths = store.ensure_role_memory("product_requirement", "Product / Requirement", "AI Content Studio")
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


def test_retrieve_relevant_events_ranks_matching_event_first(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("architect", "Architect", "AI Content Studio")
    store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="better-sqlite3 native addon ABI drift breaks tests when Node version changes.",
        source="job-1/result.md",
    )
    store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Telegram approval channel masks sensitive data before sending messages.",
        source="job-2/result.md",
    )
    store.append_memory_event(
        "architect",
        horizon="long-term",
        text="CONTENTOPS draft generation produces four platform variants per topic.",
        source="job-3/result.md",
    )

    results = store.retrieve_relevant_events(
        "architect",
        query="sqlite native addon ABI rebuild after node upgrade",
        top_k=2,
    )

    assert results, "expected at least one retrieved event"
    assert "better-sqlite3" in results[0]["text"]
    assert all("Telegram" not in item["text"] for item in results)
    assert len(results) <= 2


def test_retrieve_relevant_events_returns_empty_without_query_or_events(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("planner", "Planner", "AI Content Studio")

    assert store.retrieve_relevant_events("planner", query="anything") == []

    store.append_memory_event(
        "planner",
        horizon="short-term",
        text="Resume roadmap review for memory moat.",
        source="job-9/result.md",
    )
    assert store.retrieve_relevant_events("planner", query="   ") == []


def test_retrieved_event_carries_stable_mem_handle(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("architect", "Architect", "AI Content Studio")
    store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Use $SHELL -lc for target verification to match the login shell environment.",
        source="job-7/result.md",
    )

    first = store.retrieve_relevant_events("architect", query="shell verification login environment")
    second = store.retrieve_relevant_events("architect", query="shell verification login environment")

    assert first[0]["id"].startswith("mem:")
    assert first[0]["id"] == second[0]["id"]


def test_pickup_packet_includes_relevant_events_only_when_query_given(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("architect", "Architect", "AI Content Studio")
    store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="npm registry npmmirror intermittently returns 502/504; retry with backoff.",
        source="job-5/result.md",
    )

    without_query = store.build_pickup_packet("architect")
    assert "relevant_events" not in without_query

    with_query = store.build_pickup_packet(
        "architect",
        query="npm install fails with 504 from registry",
    )
    assert with_query["relevant_events"], "expected retrieved events in packet"
    assert "npm registry" in with_query["relevant_events"][0]["text"]
    assert with_query["relevant_events"][0]["id"].startswith("mem:")


def test_role_memory_store_rebuilds_memory_from_events(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("planner", "Planner", "AI Content Studio")
    store.append_memory_event(
        "planner",
        horizon="mid-term",
        text="Frontend auth tasks depend on backend auth.status contract.",
        source="job-456/result.md",
    )
    store.memory_path("planner", "mid-term").write_text("# Corrupted\n", encoding="utf-8")

    rebuilt = store.rebuild_role_memory("planner", "Planner", "AI Content Studio")

    assert rebuilt["event_count"] == 1
    assert "Frontend auth tasks depend" in store.memory_path("planner", "mid-term").read_text(encoding="utf-8")
    assert "Corrupted" not in store.memory_path("planner", "mid-term").read_text(encoding="utf-8")
