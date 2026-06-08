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

    def routing_readiness_report(self, *, min_hits: int = 3) -> dict[str, object]:
        threshold = max(int(min_hits), 1)
        patterns = self.list_patterns()
        candidates = [
            shadow_routing_candidate(pattern)
            for pattern in patterns
            if int(pattern.get("hit_count", 0)) >= threshold
        ]
        return {
            "total_patterns": len(patterns),
            "total_hits": sum(int(pattern.get("hit_count", 0)) for pattern in patterns),
            "candidate_count": len(candidates),
            "min_hits": threshold,
            "routing_application": "deferred",
            "routing_gate_satisfied": False,
            "shadow_routing": "enabled",
            "false_positive_review_required": True,
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


def shadow_routing_candidate(pattern: dict[str, object]) -> dict[str, object]:
    candidate = dict(pattern)
    candidate["would_route_to"] = normalize_disposition(str(candidate.get("recommended_disposition", "")))
    candidate["confidence_inputs"] = {
        "hit_count": int(candidate.get("hit_count", 0)),
        "roles": [str(role) for role in candidate.get("roles", [])],
        "nodes": [str(node) for node in candidate.get("nodes", [])],
    }
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
