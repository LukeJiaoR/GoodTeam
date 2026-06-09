from goodteam_os.failure_patterns import FailurePatternLibrary, canary_routing_decision


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


def test_failure_pattern_library_reports_routing_readiness_without_applying_patterns(tmp_path):
    library = FailurePatternLibrary(tmp_path)
    for index in range(3):
        library.record_failure(
            failure_class="network",
            summary="503 Service Unavailable from registry.npmmirror.com",
            disposition="escalate_human",
            role="implementation",
            node="implementation",
            source=f"worker_failure:run-{index}:implementation:implementation",
        )
    library.record_failure(
        failure_class="timeout",
        summary="Codex CLI timed out after 1800 seconds",
        disposition="escalate_human",
        role="qa",
        node="implementation",
        source="worker_failure:run-timeout:implementation:qa",
    )

    report = library.routing_readiness_report(min_hits=2)

    assert report["total_patterns"] == 2
    assert report["total_hits"] == 4
    assert report["candidate_count"] == 1
    assert report["routing_application"] == "deferred"
    assert report["routing_gate_satisfied"] is False
    assert report["shadow_routing"] == "enabled"
    assert report["false_positive_review_required"] is True
    assert report["candidate_patterns"][0]["classifier_hint"] == "network"
    assert report["candidate_patterns"][0]["hit_count"] == 3
    assert report["candidate_patterns"][0]["would_route_to"] == "escalate_human"
    assert report["candidate_patterns"][0]["confidence_inputs"] == {
        "hit_count": 3,
        "roles": ["implementation"],
        "nodes": ["implementation"],
    }


def test_failure_pattern_library_records_routing_outcomes_for_quality_review(tmp_path):
    library = FailurePatternLibrary(tmp_path)
    pattern = library.record_failure(
        failure_class="network",
        summary="503 Service Unavailable from registry.npmmirror.com",
        disposition="escalate_human",
        role="implementation",
        node="implementation",
        source="worker_failure:run-1:implementation:implementation",
    )

    library.record_routing_outcome(
        signature=str(pattern["signature"]),
        applied=True,
        disposition="escalate_human",
        deterministic_disposition="block",
        reason="canary_allowed",
        outcome="confirmed",
        source="triage_decided:run-1:implementation",
    )
    library.record_routing_outcome(
        signature=str(pattern["signature"]),
        applied=False,
        disposition="block",
        deterministic_disposition="block",
        reason="below_min_hits",
        outcome="shadow_only",
        source="triage_decided:run-2:implementation",
    )

    report = library.routing_readiness_report(min_hits=1)

    assert report["routing_outcome_totals"] == {
        "total": 2,
        "confirmed": 1,
        "false_positive": 0,
        "shadow_only": 1,
    }
    assert report["candidate_patterns"][0]["routing_outcomes"] == {
        "total": 2,
        "confirmed": 1,
        "false_positive": 0,
        "shadow_only": 1,
        "last_outcome": "shadow_only",
        "last_reason": "below_min_hits",
        "last_disposition": "block",
        "last_deterministic_disposition": "block",
    }


def test_failure_pattern_library_reports_routing_quality_gate_without_expanding_routing(tmp_path):
    library = FailurePatternLibrary(tmp_path)
    stable = library.record_failure(
        failure_class="network",
        summary="503 Service Unavailable from registry.npmmirror.com",
        disposition="escalate_human",
        role="implementation",
        node="implementation",
        source="worker_failure:run-stable-1:implementation:implementation",
    )
    for index in range(3):
        library.record_routing_outcome(
            signature=str(stable["signature"]),
            applied=True,
            disposition="escalate_human",
            deterministic_disposition="block",
            reason="canary_allowed",
            outcome="confirmed",
            source=f"triage_decided:stable-{index}:implementation",
        )

    noisy = library.record_failure(
        failure_class="timeout",
        summary="Codex CLI timed out after 1800 seconds",
        disposition="escalate_human",
        role="qa",
        node="implementation",
        source="worker_failure:run-noisy-1:implementation:qa",
    )
    library.record_routing_outcome(
        signature=str(noisy["signature"]),
        applied=True,
        disposition="escalate_human",
        deterministic_disposition="block",
        reason="canary_allowed",
        outcome="confirmed",
        source="triage_decided:noisy-1:implementation",
    )
    library.record_routing_outcome(
        signature=str(noisy["signature"]),
        applied=True,
        disposition="escalate_human",
        deterministic_disposition="block",
        reason="manual_review_rejected",
        outcome="false_positive",
        source="triage_decided:noisy-2:implementation",
    )

    report = library.routing_readiness_report(
        min_hits=1,
        min_confirmed=3,
        max_false_positive_rate=0.0,
    )

    assert report["routing_application"] == "deferred"
    assert report["routing_gate_satisfied"] is True
    assert report["routing_quality_gate"] == {
        "min_confirmed": 3,
        "max_false_positive_rate": 0.0,
        "confirmed": 4,
        "false_positive": 1,
        "eligible_candidate_count": 1,
        "blocked_candidate_count": 1,
        "status": "satisfied_for_review",
    }
    candidates = {pattern["classifier_hint"]: pattern for pattern in report["candidate_patterns"]}
    assert candidates["network"]["routing_quality"] == {
        "confirmed": 3,
        "false_positive": 0,
        "false_positive_rate": 0.0,
        "quality_gate_satisfied": True,
        "quality_gate_blockers": [],
    }
    assert candidates["timeout"]["routing_quality"] == {
        "confirmed": 1,
        "false_positive": 1,
        "false_positive_rate": 0.5,
        "quality_gate_satisfied": False,
        "quality_gate_blockers": [
            "confirmed_outcomes_below_minimum",
            "false_positive_rate_above_maximum",
        ],
    }


def test_failure_pattern_library_reports_llm_triage_readiness_without_invoking_models(tmp_path):
    library = FailurePatternLibrary(tmp_path)
    for index in range(3):
        library.record_failure(
            failure_class="unknown",
            summary=f"Worker returned unclear failure envelope {index}",
            disposition="block",
            role="implementation",
            node="implementation",
            source=f"worker_failure:run-unknown-{index}:implementation:implementation",
        )
    library.record_failure(
        failure_class="ambiguous_spec",
        summary="Task requirements contradicted each other during implementation",
        disposition="block",
        role="qa",
        node="implementation",
        source="worker_failure:run-ambiguous-1:implementation:qa",
    )
    library.record_failure(
        failure_class="network",
        summary="503 Service Unavailable from registry.npmmirror.com",
        disposition="escalate_human",
        role="implementation",
        node="implementation",
        source="worker_failure:run-network:implementation:implementation",
    )

    report = library.routing_readiness_report(min_hits=1, min_llm_triage_samples=3)

    assert report["llm_triage_readiness"] == {
        "status": "ready_for_shadow_triage",
        "triage_application": "not_started",
        "min_samples": 3,
        "candidate_failure_classes": ["ambiguous_spec", "unknown"],
        "candidate_sample_count": 4,
        "blocked_candidate_count": 0,
        "blockers": [],
        "model_invocation": "none",
    }
    candidates = report["llm_triage_candidates"]
    assert len(candidates) == 2
    assert candidates[0] == {
        "failure_class": "unknown",
        "sample_count": 3,
        "roles": ["implementation"],
        "nodes": ["implementation"],
        "sample_signatures": candidates[0]["sample_signatures"],
        "status": "candidate_for_shadow_triage",
    }
    assert len(candidates[0]["sample_signatures"]) == 1
    assert candidates[0]["sample_signatures"][0].startswith("fail:")
    assert candidates[1] == {
        "failure_class": "ambiguous_spec",
        "sample_count": 1,
        "roles": ["qa"],
        "nodes": ["implementation"],
        "sample_signatures": candidates[1]["sample_signatures"],
        "status": "candidate_for_shadow_triage",
    }
    assert len(candidates[1]["sample_signatures"]) == 1
    assert candidates[1]["sample_signatures"][0].startswith("fail:")
    assert [pattern["classifier_hint"] for pattern in report["candidate_patterns"]] == [
        "unknown",
        "network",
        "ambiguous_spec",
    ]


def test_failure_pattern_library_blocks_llm_triage_readiness_without_unknown_samples(tmp_path):
    library = FailurePatternLibrary(tmp_path)
    library.record_failure(
        failure_class="network",
        summary="503 Service Unavailable from registry.npmmirror.com",
        disposition="escalate_human",
        role="implementation",
        node="implementation",
        source="worker_failure:run-network:implementation:implementation",
    )

    report = library.routing_readiness_report(min_hits=1, min_llm_triage_samples=2)

    assert report["llm_triage_readiness"] == {
        "status": "collect_more_samples",
        "triage_application": "not_started",
        "min_samples": 2,
        "candidate_failure_classes": ["ambiguous_spec", "unknown"],
        "candidate_sample_count": 0,
        "blocked_candidate_count": 0,
        "blockers": ["insufficient_unknown_or_ambiguous_samples"],
        "model_invocation": "none",
    }
    assert report["llm_triage_candidates"] == []


def test_canary_routing_decision_applies_only_high_confidence_low_risk_patterns():
    pattern = {
        "signature": "fail:network",
        "classifier_hint": "network",
        "recommended_disposition": "escalate_human",
        "hit_count": 3,
    }

    decision = canary_routing_decision(
        failure_class="unknown",
        deterministic_disposition="block",
        pattern_match=pattern,
        min_hits=3,
    )

    assert decision == {
        "applied": True,
        "disposition": "escalate_human",
        "reason": "canary_allowed",
        "source": "failure_pattern",
    }

    unsafe = canary_routing_decision(
        failure_class="unknown",
        deterministic_disposition="block",
        pattern_match={**pattern, "classifier_hint": "scaffold"},
        min_hits=3,
    )
    low_confidence = canary_routing_decision(
        failure_class="unknown",
        deterministic_disposition="block",
        pattern_match={**pattern, "hit_count": 2},
        min_hits=3,
    )
    ambiguous = canary_routing_decision(
        failure_class="unknown",
        deterministic_disposition="block",
        pattern_match={**pattern, "recommended_disposition": "block"},
        min_hits=3,
    )
    disabled = canary_routing_decision(
        failure_class="unknown",
        deterministic_disposition="block",
        pattern_match=pattern,
        min_hits=3,
        enabled=False,
    )

    assert unsafe == {
        "applied": False,
        "disposition": "block",
        "reason": "class_not_canary_safe",
        "source": "failure_pattern",
    }
    assert low_confidence == {
        "applied": False,
        "disposition": "block",
        "reason": "below_min_hits",
        "source": "failure_pattern",
    }
    assert ambiguous == {
        "applied": False,
        "disposition": "block",
        "reason": "disposition_not_canary_safe",
        "source": "failure_pattern",
    }
    assert disabled == {
        "applied": False,
        "disposition": "block",
        "reason": "disabled",
        "source": "failure_pattern",
    }
