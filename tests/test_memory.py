import hashlib
import json
import re

from goodteam_os.memory import ProjectMemoryStore, RoleMemoryStore


_FAKE_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _fake_embedder(model_name: str):
    concept_axes = {
        # Storage/database concept
        "database": 0,
        "storage": 0,
        "sqlite": 0,
        "postgres": 0,
        "persistence": 0,
        # Runtime/environment concept
        "runtime": 1,
        "engine": 1,
        "node": 1,
        "python": 1,
        # Binary compatibility concept
        "binary": 2,
        "abi": 2,
        "compatibility": 2,
        "addon": 2,
        "extension": 2,
        # Messaging concept
        "telegram": 3,
        "messaging": 3,
        "notification": 3,
        # Draft/content concept
        "draft": 4,
        "content": 4,
        "platform": 4,
    }
    dim = 16

    def embed(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * dim
            for token in _FAKE_TOKEN_RE.findall(text.lower()):
                axis = concept_axes.get(token)
                if axis is not None:
                    vector[axis] += 1.0
                else:
                    bucket = 6 + (hashlib.sha256(token.encode("utf-8")).digest()[0] % (dim - 6))
                    vector[bucket] += 0.05
            vectors.append(vector)
        return vectors

    embed.model_name = model_name
    embed.dim = dim
    return embed


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


def test_semantic_retrieval_with_embedder_handles_lexical_disjoint_query(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()

    bm25_store = RoleMemoryStore(project_root)
    embedded_store = RoleMemoryStore(project_root, embedder=_fake_embedder("fake-semantic"))
    bm25_store.ensure_role_memory("architect", "Architect", "AI Content Studio")
    bm25_store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="sqlite native addon ABI drift breaks tests when Node version changes.",
        source="job-1/result.md",
    )
    bm25_store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Telegram approval channel masks sensitive data before sending messages.",
        source="job-2/result.md",
    )
    query = "database extension compatibility regression after runtime upgrade"

    bm25_results = bm25_store.retrieve_relevant_events("architect", query=query, top_k=2)
    embedded_results = embedded_store.retrieve_relevant_events("architect", query=query, top_k=2)

    assert bm25_results == []
    assert embedded_results
    assert "sqlite native addon ABI drift" in embedded_results[0]["text"]


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


def test_retrieved_event_carries_stable_mem_handle_on_embedding_path(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root, embedder=_fake_embedder("fake-handle"))
    store.ensure_role_memory("architect", "Architect", "AI Content Studio")
    store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="database compatibility note for extension runtime drift.",
        source="job-8/result.md",
    )

    first = store.retrieve_relevant_events("architect", query="storage extension compatibility runtime")
    second = store.retrieve_relevant_events("architect", query="storage extension compatibility runtime")

    assert first[0]["id"].startswith("mem:")
    assert first[0]["id"] == second[0]["id"]


def test_embedding_cache_is_projection_and_regenerates_after_delete(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root, embedder=_fake_embedder("fake-cache"))
    store.ensure_role_memory("architect", "Architect", "AI Content Studio")
    store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="database engine extension compatibility migration note.",
        source="job-9/result.md",
    )
    store.append_memory_event(
        "architect",
        horizon="short-term",
        text="telegram messaging retry backoff reminder.",
        source="job-10/result.md",
    )

    first = store.retrieve_relevant_events(
        "architect",
        query="runtime compatibility for database extension",
        top_k=2,
    )
    cache_path = store.embeddings_path("architect")
    assert cache_path.is_file()

    cache_path.unlink()
    second = store.retrieve_relevant_events(
        "architect",
        query="runtime compatibility for database extension",
        top_k=2,
    )

    assert first == second
    assert cache_path.is_file()


def test_embedding_cache_ignores_rows_from_other_model(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store_a = RoleMemoryStore(project_root, embedder=_fake_embedder("fake-a"))
    store_b = RoleMemoryStore(project_root, embedder=_fake_embedder("fake-b"))
    store_a.ensure_role_memory("architect", "Architect", "AI Content Studio")
    store_a.append_memory_event(
        "architect",
        horizon="mid-term",
        text="database runtime compatibility issue with addon.",
        source="job-11/result.md",
    )

    _ = store_a.retrieve_relevant_events("architect", query="database runtime compatibility")
    first_cache_rows = [
        json.loads(line)
        for line in store_a.embeddings_path("architect").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(row["model"] == "fake-a" for row in first_cache_rows)

    _ = store_b.retrieve_relevant_events("architect", query="database runtime compatibility")
    second_cache_rows = [
        json.loads(line)
        for line in store_b.embeddings_path("architect").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(row["model"] == "fake-b" for row in second_cache_rows)


def test_default_off_matches_bm25_retrieval_output(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    baseline = RoleMemoryStore(project_root)
    explicit_none = RoleMemoryStore(project_root, embedder=None)
    baseline.ensure_role_memory("architect", "Architect", "AI Content Studio")
    baseline.append_memory_event(
        "architect",
        horizon="mid-term",
        text="better-sqlite3 native addon ABI drift breaks tests when Node version changes.",
        source="job-12/result.md",
    )
    baseline.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Telegram approval channel masks sensitive data before sending messages.",
        source="job-13/result.md",
    )
    query = "sqlite native addon ABI rebuild after node upgrade"

    expected = baseline.retrieve_relevant_events("architect", query=query, top_k=2)
    actual = explicit_none.retrieve_relevant_events("architect", query=query, top_k=2)

    assert actual == expected


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
