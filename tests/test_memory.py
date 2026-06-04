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


def test_pickup_packet_excludes_worker_failure_short_term_events(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("backend", "Backend", "AI Content Studio")
    store.append_memory_event(
        "backend",
        horizon="short-term",
        text="FAILURE [backend] pytest failed in setup.",
        source="worker_failure:job-123",
    )

    packet = store.build_pickup_packet("backend")

    assert "FAILURE [backend] pytest failed in setup." not in packet["short_term"]
    assert "worker_failure:job-123" not in packet["short_term"]


def test_pickup_packet_preserves_legitimate_failure_prefixed_short_term_note(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("backend", "Backend", "AI Content Studio")
    store.append_memory_event(
        "backend",
        horizon="short-term",
        text="FAILURE [backend] is the heading to keep in the human note.",
        source="job-456/result.md",
    )

    packet = store.build_pickup_packet("backend")

    assert "FAILURE [backend] is the heading to keep in the human note." in packet["short_term"]
    assert "job-456/result.md" in packet["short_term"]


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


def test_event_id_ignores_supersedes_metadata(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    base_event = {
        "timestamp": "2026-06-02T00:00:00+00:00",
        "role_key": "architect",
        "horizon": "short-term",
        "action": "add",
        "source": "job-1/result.md",
        "text": "Keep memory ids stable while adding metadata.",
    }
    superseding_event = dict(base_event)
    superseding_event["supersedes"] = "mem:oldid123456"

    assert store._event_id(base_event) == store._event_id(superseding_event)


def test_append_memory_event_core_dedup_skips_duplicate_wal_line(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)

    first = store.append_memory_event(
        "architect",
        horizon="short-term",
        text="Duplicate-safe memory append behavior.",
        source="job-dedup/result.md",
    )
    second = store.append_memory_event(
        "architect",
        horizon="short-term",
        text="Duplicate-safe memory append behavior.",
        source="job-dedup/result.md",
    )

    lines = [
        line
        for line in store.event_path("architect").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    assert first == second


def test_retrieve_relevant_events_excludes_superseded_event(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    old_event = store.append_memory_event(
        "architect",
        horizon="short-term",
        text="Use v1 rollout checklist for release gating.",
        source="job-old/result.md",
    )
    old_id = store._event_id(old_event)
    store.append_memory_event(
        "architect",
        horizon="short-term",
        text="Use v2 rollout checklist for release gating.",
        source="job-new/result.md",
        supersedes=old_id,
    )

    results = store.retrieve_relevant_events("architect", query="rollout checklist release gating", top_k=5)
    texts = [str(item["text"]) for item in results]
    assert "Use v1 rollout checklist for release gating." not in texts
    assert "Use v2 rollout checklist for release gating." in texts


def test_rebuild_role_memory_excludes_superseded_events_in_projection(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    first = store.append_memory_event(
        "planner",
        horizon="mid-term",
        text="Auth contract depends on status=v1 shape.",
        source="job-auth-1/result.md",
    )
    store.append_memory_event(
        "planner",
        horizon="mid-term",
        text="Auth contract depends on status=v2 shape.",
        source="job-auth-2/result.md",
        supersedes=store._event_id(first),
    )

    store.rebuild_role_memory("planner", "Planner", "AI Content Studio")
    content = store.memory_path("planner", "mid-term").read_text(encoding="utf-8")
    assert "status=v1 shape" not in content
    assert "status=v2 shape" in content


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


def test_pickup_packet_retrieval_excludes_superseded_events(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("architect", "Architect", "AI Content Studio")
    replaced = store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Use legacy rollout checklist v1.",
        source="job-1/result.md",
    )
    replaced_id = store.retrieve_relevant_events("architect", query="legacy rollout checklist", top_k=1)[0]["id"]
    store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Use rollout checklist v2 with release gating.",
        source="job-2/result.md",
        supersedes=replaced_id,
    )

    packet = store.build_pickup_packet("architect", query="rollout checklist release gating")

    texts = [event["text"] for event in packet["relevant_events"]]
    assert "Use rollout checklist v2 with release gating." in texts
    assert "Use legacy rollout checklist v1." not in texts
    assert replaced["text"] == "Use legacy rollout checklist v1."


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


def test_memory_index_includes_integrity_marker_and_happy_path_skips_rebuild(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("architect", "Architect", "AI Content Studio")
    store.append_memory_event(
        "architect",
        horizon="short-term",
        text="Keep the auth contract drift note in short-term memory.",
        source="job-100/result.md",
    )

    index = store.read_memory_index("architect")
    assert index["event_count"] == 1
    assert index["integrity"]["event_count"] == 1
    expected_sha = hashlib.sha256(store.event_path("architect").read_bytes()).hexdigest()
    assert index["integrity"]["events_sha256"] == expected_sha

    called = {"value": False}

    def fail_rebuild(*args, **kwargs):
        called["value"] = True
        raise AssertionError("read_memory_index should not rebuild when integrity matches WAL")

    store.rebuild_role_memory = fail_rebuild
    second = store.read_memory_index("architect")
    assert second["integrity"]["events_sha256"] == expected_sha
    assert called["value"] is False


def test_read_and_pickup_rebuild_when_index_is_tampered(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("planner", "Planner", "AI Content Studio")
    store.append_memory_event(
        "planner",
        horizon="mid-term",
        text="Frontend auth tasks depend on backend auth.status contract.",
        source="job-200/result.md",
    )
    store.memory_path("planner", "mid-term").write_text("# Corrupted\n", encoding="utf-8")
    stale_index = store.read_memory_index("planner")
    stale_index["integrity"]["events_sha256"] = "deadbeef"
    store.index_path("planner").write_text(json.dumps(stale_index, indent=2), encoding="utf-8")

    packet = store.build_pickup_packet("planner")
    mid_content = store.memory_path("planner", "mid-term").read_text(encoding="utf-8")
    assert "Frontend auth tasks depend" in mid_content
    assert "Corrupted" not in mid_content
    assert packet["event_count"] == 1

    rebuilt_index = store.read_memory_index("planner")
    expected_sha = hashlib.sha256(store.event_path("planner").read_bytes()).hexdigest()
    assert rebuilt_index["integrity"]["events_sha256"] == expected_sha


def test_read_memory_index_rebuilds_when_index_missing(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("qa", "QA", "AI Content Studio")
    store.append_memory_event(
        "qa",
        horizon="short-term",
        text="Resume flaky test triage for memory projection checks.",
        source="job-300/result.md",
    )
    store.index_path("qa").unlink()

    rebuilt = store.read_memory_index("qa")
    assert rebuilt["event_count"] == 1
    assert rebuilt["integrity"]["event_count"] == 1
    assert store.index_path("qa").is_file()


def test_read_memory_index_detects_wal_tamper_and_rebuilds_projection(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.ensure_role_memory("architect", "Architect", "AI Content Studio")
    store.append_memory_event(
        "architect",
        horizon="short-term",
        text="Initial WAL-backed memory event.",
        source="job-400/result.md",
    )
    before = store.read_memory_index("architect")
    old_sha = before["integrity"]["events_sha256"]

    tampered_event = {
        "timestamp": "2026-06-02T00:00:00+00:00",
        "role_key": "architect",
        "horizon": "short-term",
        "action": "add",
        "source": "manual-edit",
        "text": "Out-of-band WAL append should force rebuild.",
    }
    with store.event_path("architect").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(tampered_event) + "\n")

    rebuilt = store.read_memory_index("architect")
    new_sha = rebuilt["integrity"]["events_sha256"]
    assert rebuilt["event_count"] == 2
    assert rebuilt["integrity"]["event_count"] == 2
    assert new_sha != old_sha
    assert new_sha == hashlib.sha256(store.event_path("architect").read_bytes()).hexdigest()
    assert "Out-of-band WAL append should force rebuild." in store.memory_path(
        "architect", "short-term"
    ).read_text(encoding="utf-8")


def test_list_events_returns_recent_events_newest_first(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    first = store.append_memory_event(
        "architect",
        horizon="long-term",
        text="First browse event.",
        source="job-first/result.md",
    )
    second = store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Second browse event.",
        source="job-second/result.md",
    )

    events = store.list_events("architect", limit=2)

    assert [event["id"] for event in events] == [store._event_id(second), store._event_id(first)]
    assert events[0]["kind"] == "mid-term"
    assert events[0]["role"] == "architect"
    assert events[0]["timestamp"] == second["timestamp"]
    assert events[0]["ts"] == second["timestamp"]
    assert events[0]["body"] == "Second browse event."
    assert events[0]["is_superseded"] is False


def test_list_events_query_uses_retrieval_ranking(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    store.append_memory_event(
        "implementation",
        horizon="short-term",
        text="Search browse event mentions database once.",
        source="job-one/result.md",
    )
    best = store.append_memory_event(
        "implementation",
        horizon="short-term",
        text="Search browse event mentions database and sqlite.",
        source="job-best/result.md",
    )

    events = store.list_events("implementation", query="database sqlite", limit=2)

    assert events[0]["id"] == store._event_id(best)
    assert len(events) == 2


def test_list_events_flags_superseded_events_without_dropping_them(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    old_event = store.append_memory_event(
        "frontend",
        horizon="mid-term",
        text="Browse panel uses the old detail route.",
        source="job-old/result.md",
    )
    old_id = store._event_id(old_event)
    new_event = store.append_memory_event(
        "frontend",
        horizon="mid-term",
        text="Browse panel uses the new detail route.",
        source="job-new/result.md",
        supersedes=old_id,
    )

    events = store.list_events("frontend", limit=10)
    by_id = {event["id"]: event for event in events}

    assert set(by_id) == {old_id, store._event_id(new_event)}
    assert by_id[old_id]["is_superseded"] is True
    assert by_id[store._event_id(new_event)]["is_superseded"] is False


def test_list_events_caps_limit(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = RoleMemoryStore(project_root)
    for index in range(205):
        store.append_memory_event(
            "qa",
            horizon="short-term",
            text=f"Browse cap event {index}.",
            source=f"job-{index}/result.md",
        )

    events = store.list_events("qa", limit=500)

    assert len(events) == 200
