from goodteam_os.failure_patterns import FailurePatternLibrary


def test_failure_pattern_library_upserts_hits_by_signature(tmp_path):
    library = FailurePatternLibrary(tmp_path)

    first = library.record_failure(
        failure_class="network",
        summary="503 Service Unavailable from registry.npmmirror.com",
        disposition="escalate_human",
        role="implementation",
        node="implementation",
        source="worker_failure:run-1:implementation:implementation",
    )
    second = library.record_failure(
        failure_class="network",
        summary="503 service unavailable from registry.npmmirror.com",
        disposition="escalate_human",
        role="qa",
        node="implementation",
        source="worker_failure:run-2:implementation:qa",
    )

    assert first["signature"] == second["signature"]
    assert second["hit_count"] == 2
    assert second["classifier_hint"] == "network"
    assert second["recommended_disposition"] == "escalate_human"
    assert second["roles"] == ["implementation", "qa"]
    assert second["sources"] == [
        "worker_failure:run-1:implementation:implementation",
        "worker_failure:run-2:implementation:qa",
    ]


def test_failure_pattern_library_finds_best_token_overlap_match(tmp_path):
    library = FailurePatternLibrary(tmp_path)
    library.record_failure(
        failure_class="scaffold",
        summary="Cannot find module better-sqlite3 after npm install",
        disposition="block",
        role="implementation",
        node="implementation",
        source="worker_failure:run-1:implementation:implementation",
    )
    library.record_failure(
        failure_class="timeout",
        summary="Codex CLI timed out after 1800 seconds",
        disposition="escalate_human",
        role="qa",
        node="implementation",
        source="worker_failure:run-2:implementation:qa",
    )

    matches = library.find_matches("native module better sqlite3 missing after install", top_k=1)

    assert len(matches) == 1
    assert matches[0]["classifier_hint"] == "scaffold"
    assert matches[0]["recommended_disposition"] == "block"
    assert matches[0]["hit_count"] == 1


def test_failure_pattern_library_ignores_empty_query(tmp_path):
    library = FailurePatternLibrary(tmp_path)
    library.record_failure(
        failure_class="unknown",
        summary="random worker failure",
        disposition="block",
        role="implementation",
        node="implementation",
        source="worker_failure:run-1:implementation:implementation",
    )

    assert library.find_matches("") == []
