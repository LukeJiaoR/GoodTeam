import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from goodteam_os.memory import tokenize


SECRETISH_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|bot[_-]?token|authorization|secret|password)\s*[:=]\s*\S+"
)
MAX_SUMMARY_CHARS = 240
CANARY_SAFE_FAILURE_CLASSES = frozenset(
    {"auth", "rate_limit", "network", "timeout", "stream_disconnect", "infra"}
)
CANARY_SAFE_DISPOSITIONS = frozenset({"escalate_human"})
LLM_TRIAGE_FAILURE_CLASSES = ("ambiguous_spec", "unknown")


class FailurePatternLibrary:
    """Workspace-global failure pattern ledger.

    The library is intentionally deterministic and local. It records repeated
    worker failure signatures so later triage can measure hit rate before any
    model-driven triage agent is introduced.
    """

    def __init__(self, root: Path):
        self.root = root

    def pattern_path(self) -> Path:
        return self.root / ".goodteam" / "failure-patterns" / "patterns.json"

    def record_failure(
        self,
        *,
        failure_class: str,
        summary: str,
        disposition: str,
        role: str,
        node: str,
        source: str,
    ) -> dict[str, object]:
        safe_summary = sanitize_summary(summary)
        normalized = normalize_summary(safe_summary)
        signature = failure_signature(failure_class=failure_class, normalized_summary=normalized)
        patterns = self._read_patterns()
        now = datetime.now(UTC).isoformat()

        pattern = patterns.get(signature)
        if pattern is None:
            pattern = {
                "signature": signature,
                "classifier_hint": normalized_failure_class(failure_class),
                "recommended_disposition": normalize_disposition(disposition),
                "normalized_summary": normalized,
                "sample_summary": safe_summary,
                "hit_count": 0,
                "roles": [],
                "nodes": [],
                "sources": [],
                "first_seen_at": now,
                "last_seen_at": now,
                "routing_outcomes": empty_routing_outcome_summary(),
            }
            patterns[signature] = pattern

        pattern["hit_count"] = int(pattern.get("hit_count", 0)) + 1
        pattern["last_seen_at"] = now
        pattern["classifier_hint"] = normalized_failure_class(failure_class)
        pattern["recommended_disposition"] = normalize_disposition(disposition)
        _append_unique(pattern, "roles", role)
        _append_unique(pattern, "nodes", node)
        _append_unique(pattern, "sources", source)
        self._write_patterns(patterns)
        return dict(pattern)

    def record_routing_outcome(
        self,
        *,
        signature: str,
        applied: bool,
        disposition: str,
        deterministic_disposition: str,
        reason: str,
        outcome: str,
        source: str,
    ) -> dict[str, object]:
        patterns = self._read_patterns()
        pattern = patterns.get(str(signature))
        if pattern is None:
            return {}

        outcomes = routing_outcome_summary(pattern)
        normalized_outcome = normalize_routing_outcome(outcome)
        outcomes["total"] = int(outcomes.get("total", 0)) + 1
        outcomes[normalized_outcome] = int(outcomes.get(normalized_outcome, 0)) + 1
        outcomes["last_outcome"] = normalized_outcome
        outcomes["last_reason"] = str(reason or "")
        outcomes["last_disposition"] = normalize_disposition(disposition)
        outcomes["last_deterministic_disposition"] = normalize_disposition(deterministic_disposition)
        outcomes["last_applied"] = bool(applied)
        outcomes["last_source"] = str(source or "")
        pattern["routing_outcomes"] = outcomes
        _append_unique(pattern, "routing_outcome_sources", str(source or ""))
        self._write_patterns(patterns)
        return dict(pattern)

    def find_matches(self, query: str, *, top_k: int = 5) -> list[dict[str, object]]:
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return []
        patterns = list(self._read_patterns().values())
        scored: list[tuple[int, int, str, dict[str, object]]] = []
        for pattern in patterns:
            pattern_tokens = set(tokenize(str(pattern.get("normalized_summary", ""))))
            overlap = len(query_tokens.intersection(pattern_tokens))
            if overlap <= 0:
                continue
            scored.append(
                (
                    overlap,
                    int(pattern.get("hit_count", 0)),
                    str(pattern.get("last_seen_at", "")),
                    pattern,
                )
            )
        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [dict(pattern) for _, _, _, pattern in scored[: max(int(top_k), 0)]]

    def list_patterns(self) -> list[dict[str, object]]:
        patterns = list(self._read_patterns().values())
        return sorted(
            (dict(pattern) for pattern in patterns),
            key=lambda pattern: (int(pattern.get("hit_count", 0)), str(pattern.get("last_seen_at", ""))),
            reverse=True,
        )

    def routing_readiness_report(
        self,
        *,
        min_hits: int = 3,
        min_confirmed: int = 3,
        max_false_positive_rate: float = 0.0,
        min_llm_triage_samples: int = 5,
    ) -> dict[str, object]:
        threshold = max(int(min_hits), 1)
        quality_min_confirmed = max(int(min_confirmed), 1)
        quality_max_false_positive_rate = max(float(max_false_positive_rate), 0.0)
        llm_triage_min_samples = max(int(min_llm_triage_samples), 1)
        patterns = self.list_patterns()
        candidates = [
            shadow_routing_candidate(
                pattern,
                min_confirmed=quality_min_confirmed,
                max_false_positive_rate=quality_max_false_positive_rate,
            )
            for pattern in patterns
            if int(pattern.get("hit_count", 0)) >= threshold
        ]
        quality_gate = routing_quality_gate_summary(
            candidates,
            min_confirmed=quality_min_confirmed,
            max_false_positive_rate=quality_max_false_positive_rate,
        )
        llm_triage_candidates = llm_triage_readiness_candidates(patterns)
        llm_triage_readiness = llm_triage_readiness_summary(
            llm_triage_candidates,
            min_samples=llm_triage_min_samples,
        )
        return {
            "total_patterns": len(patterns),
            "total_hits": sum(int(pattern.get("hit_count", 0)) for pattern in patterns),
            "candidate_count": len(candidates),
            "min_hits": threshold,
            "routing_outcome_totals": routing_outcome_totals(patterns),
            "routing_quality_gate": quality_gate,
            "routing_application": "deferred",
            "routing_gate_satisfied": bool(quality_gate.get("eligible_candidate_count", 0)),
            "shadow_routing": "enabled",
            "false_positive_review_required": True,
            "llm_triage_readiness": llm_triage_readiness,
            "llm_triage_candidates": llm_triage_candidates,
            "candidate_patterns": candidates,
        }

    def _read_patterns(self) -> dict[str, dict[str, object]]:
        path = self.pattern_path()
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        patterns = data.get("patterns", {})
        if not isinstance(patterns, dict):
            return {}
        return {str(key): dict(value) for key, value in patterns.items() if isinstance(value, dict)}

    def _write_patterns(self, patterns: dict[str, dict[str, object]]) -> None:
        path = self.pattern_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": datetime.now(UTC).isoformat(),
            "patterns": patterns,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def sanitize_summary(summary: str) -> str:
    collapsed = " ".join(str(summary or "unknown failure").split())
    redacted = SECRETISH_RE.sub(r"\1: [redacted]", collapsed)
    if len(redacted) > MAX_SUMMARY_CHARS:
        return f"{redacted[:MAX_SUMMARY_CHARS].rstrip()}..."
    return redacted


def normalize_summary(summary: str) -> str:
    tokens = tokenize(summary)
    normalized_tokens = ["<num>" if token.isdigit() else token for token in tokens]
    return " ".join(normalized_tokens)


def failure_signature(*, failure_class: str, normalized_summary: str) -> str:
    payload = f"{normalized_failure_class(failure_class)}\n{normalized_summary}"
    return "fail:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def normalized_failure_class(failure_class: str) -> str:
    return str(failure_class or "unknown").strip() or "unknown"


def normalize_disposition(disposition: str) -> str:
    return str(disposition or "block").strip() or "block"


def normalize_routing_outcome(outcome: str) -> str:
    normalized = str(outcome or "shadow_only").strip()
    if normalized not in {"confirmed", "false_positive", "shadow_only"}:
        return "shadow_only"
    return normalized


def empty_routing_outcome_summary() -> dict[str, object]:
    return {
        "total": 0,
        "confirmed": 0,
        "false_positive": 0,
        "shadow_only": 0,
        "last_outcome": "",
        "last_reason": "",
        "last_disposition": "",
        "last_deterministic_disposition": "",
    }


def routing_outcome_summary(pattern: dict[str, object]) -> dict[str, object]:
    raw = pattern.get("routing_outcomes", {})
    data = dict(raw) if isinstance(raw, dict) else {}
    summary = empty_routing_outcome_summary()
    summary.update(
        {
            "total": int(data.get("total", 0) or 0),
            "confirmed": int(data.get("confirmed", 0) or 0),
            "false_positive": int(data.get("false_positive", 0) or 0),
            "shadow_only": int(data.get("shadow_only", 0) or 0),
            "last_outcome": str(data.get("last_outcome", "")),
            "last_reason": str(data.get("last_reason", "")),
            "last_disposition": str(data.get("last_disposition", "")),
            "last_deterministic_disposition": str(data.get("last_deterministic_disposition", "")),
        }
    )
    return summary


def routing_outcome_totals(patterns: list[dict[str, object]]) -> dict[str, int]:
    totals = {"total": 0, "confirmed": 0, "false_positive": 0, "shadow_only": 0}
    for pattern in patterns:
        outcomes = routing_outcome_summary(pattern)
        for key in totals:
            totals[key] += int(outcomes.get(key, 0) or 0)
    return totals


def routing_quality_summary(
    pattern: dict[str, object],
    *,
    min_confirmed: int,
    max_false_positive_rate: float,
) -> dict[str, object]:
    outcomes = routing_outcome_summary(pattern)
    confirmed = int(outcomes.get("confirmed", 0) or 0)
    false_positive = int(outcomes.get("false_positive", 0) or 0)
    reviewed_total = confirmed + false_positive
    false_positive_rate = false_positive / reviewed_total if reviewed_total else 0.0
    blockers: list[str] = []
    if confirmed < max(int(min_confirmed), 1):
        blockers.append("confirmed_outcomes_below_minimum")
    if false_positive_rate > max(float(max_false_positive_rate), 0.0):
        blockers.append("false_positive_rate_above_maximum")
    return {
        "confirmed": confirmed,
        "false_positive": false_positive,
        "false_positive_rate": false_positive_rate,
        "quality_gate_satisfied": not blockers,
        "quality_gate_blockers": blockers,
    }


def routing_quality_gate_summary(
    candidates: list[dict[str, object]],
    *,
    min_confirmed: int,
    max_false_positive_rate: float,
) -> dict[str, object]:
    eligible = [
        candidate
        for candidate in candidates
        if isinstance(candidate.get("routing_quality"), dict)
        and bool(candidate["routing_quality"].get("quality_gate_satisfied", False))
    ]
    blocked = len(candidates) - len(eligible)
    totals = routing_outcome_totals(candidates)
    return {
        "min_confirmed": max(int(min_confirmed), 1),
        "max_false_positive_rate": max(float(max_false_positive_rate), 0.0),
        "confirmed": totals["confirmed"],
        "false_positive": totals["false_positive"],
        "eligible_candidate_count": len(eligible),
        "blocked_candidate_count": blocked,
        "status": "satisfied_for_review" if eligible else "insufficient_quality_evidence",
    }


def llm_triage_readiness_candidates(patterns: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for pattern in patterns:
        failure_class = normalized_failure_class(str(pattern.get("classifier_hint", "")))
        if failure_class not in LLM_TRIAGE_FAILURE_CLASSES:
            continue
        candidate = grouped.setdefault(
            failure_class,
            {
                "failure_class": failure_class,
                "sample_count": 0,
                "roles": [],
                "nodes": [],
                "sample_signatures": [],
                "status": "candidate_for_shadow_triage",
            },
        )
        candidate["sample_count"] = int(candidate.get("sample_count", 0) or 0) + int(
            pattern.get("hit_count", 0) or 0
        )
        for role in pattern.get("roles", []):
            _append_unique(candidate, "roles", str(role))
        for node in pattern.get("nodes", []):
            _append_unique(candidate, "nodes", str(node))
        _append_unique(candidate, "sample_signatures", str(pattern.get("signature", "")))

    return sorted(
        grouped.values(),
        key=lambda candidate: (
            int(candidate.get("sample_count", 0) or 0),
            str(candidate.get("failure_class", "")),
        ),
        reverse=True,
    )


def llm_triage_readiness_summary(
    candidates: list[dict[str, object]],
    *,
    min_samples: int,
) -> dict[str, object]:
    threshold = max(int(min_samples), 1)
    candidate_sample_count = sum(int(candidate.get("sample_count", 0) or 0) for candidate in candidates)
    blockers = []
    if candidate_sample_count < threshold:
        blockers.append("insufficient_unknown_or_ambiguous_samples")
    return {
        "status": "ready_for_shadow_triage" if not blockers else "collect_more_samples",
        "triage_application": "not_started",
        "min_samples": threshold,
        "candidate_failure_classes": list(LLM_TRIAGE_FAILURE_CLASSES),
        "candidate_sample_count": candidate_sample_count,
        "blocked_candidate_count": 0,
        "blockers": blockers,
        "model_invocation": "none",
    }


def shadow_routing_candidate(
    pattern: dict[str, object],
    *,
    min_confirmed: int = 3,
    max_false_positive_rate: float = 0.0,
) -> dict[str, object]:
    candidate = dict(pattern)
    candidate["would_route_to"] = normalize_disposition(str(candidate.get("recommended_disposition", "")))
    candidate["confidence_inputs"] = {
        "hit_count": int(candidate.get("hit_count", 0)),
        "roles": [str(role) for role in candidate.get("roles", [])],
        "nodes": [str(node) for node in candidate.get("nodes", [])],
    }
    candidate["routing_outcomes"] = routing_outcome_summary(candidate)
    candidate["routing_quality"] = routing_quality_summary(
        candidate,
        min_confirmed=min_confirmed,
        max_false_positive_rate=max_false_positive_rate,
    )
    return candidate


def canary_routing_decision(
    *,
    failure_class: str,
    deterministic_disposition: str,
    pattern_match: dict[str, object] | None,
    min_hits: int = 3,
    enabled: bool = True,
) -> dict[str, object]:
    deterministic = normalize_disposition(deterministic_disposition)
    if not pattern_match:
        return {
            "applied": False,
            "disposition": deterministic,
            "reason": "no_pattern_match",
            "source": "",
        }
    if not enabled:
        return {
            "applied": False,
            "disposition": deterministic,
            "reason": "disabled",
            "source": "failure_pattern",
        }

    recommended = normalize_disposition(str(pattern_match.get("recommended_disposition", "")))
    classifier_hint = normalized_failure_class(str(pattern_match.get("classifier_hint", failure_class)))
    if int(pattern_match.get("hit_count", 0) or 0) < max(int(min_hits), 1):
        return {
            "applied": False,
            "disposition": deterministic,
            "reason": "below_min_hits",
            "source": "failure_pattern",
        }
    if classifier_hint not in CANARY_SAFE_FAILURE_CLASSES:
        return {
            "applied": False,
            "disposition": deterministic,
            "reason": "class_not_canary_safe",
            "source": "failure_pattern",
        }
    if recommended not in CANARY_SAFE_DISPOSITIONS:
        return {
            "applied": False,
            "disposition": deterministic,
            "reason": "disposition_not_canary_safe",
            "source": "failure_pattern",
        }
    return {
        "applied": True,
        "disposition": recommended,
        "reason": "canary_allowed",
        "source": "failure_pattern",
    }


def _append_unique(pattern: dict[str, object], key: str, value: str) -> None:
    normalized = str(value or "").strip()
    if not normalized:
        return
    existing = [str(item) for item in pattern.get(key, []) if str(item)]
    if normalized not in existing:
        existing.append(normalized)
    pattern[key] = existing
