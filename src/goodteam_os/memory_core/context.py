"""Pure context/provenance projections over already captured values.

No filesystem, store, retrieval or model calls live here. Compatibility helpers
retain the existing memory_pickup.v1 representation and byte budgets.
"""
import hashlib
import json
import re

from .projections import MEMORY_HORIZONS

# Prompt-facing characters are capped independently of WAL size. Valid UTF-8
# takes at most four bytes per character, so these also bound encoded bytes.
PICKUP_SUMMARY_CHARS = 600
PICKUP_SHORT_CHARS = 1600
PICKUP_EVENT_CHARS = 1600
PICKUP_RELEVANT_CHARS = 4000
MEMORY_CONTEXT_REASONS = frozenset({
    "wal_unreadable", "wal_changed_during_read", "wal_changed_after_capture",
    "legacy_mixed_after_correction", "legacy_source_unreadable", "legacy_changed_during_read",
    "legacy_changed_after_capture", "source_unreadable", "pickup_unverifiable",
    "events_no_longer_active", "retrieval_unavailable", "history_unverifiable",
})

def summarize_markdown(content: str) -> str:
    headings = [
        line.strip("# ").strip()
        for line in content.splitlines()
        if line.startswith("#") and line.strip("# ").strip()
    ][:8]
    bullets = [
        line.strip()[2:].strip()
        for line in content.splitlines()
        if line.strip().startswith("- ")
    ][:12]
    parts = []
    if headings:
        parts.append("Headings: " + "; ".join(headings))
    if bullets:
        parts.append("Signals: " + "; ".join(bullets))
    return " | ".join(parts) if parts else content.strip()[:600]


def _bounded_context(text: str, limit: int, marker: str = "[Memory context truncated]") -> str:
    """Hard character/UTF-8 bounds, including the marker, for prompt text."""
    if len(text) <= limit and len(text.encode("utf-8")) <= limit * 4:
        return text
    suffix = "\n" + marker
    return text[:max(0, limit - len(suffix))] + suffix[:limit]


def _pickup_content_digest(packet: dict[str, object]) -> str:
    content = {key: packet.get(key) for key in ("role_key", "short_term", "summaries", "relevant_events", "event_count", "context_status", "context_reasons")}
    provenance = packet.get("provenance")
    content["provenance"] = {key: value for key, value in provenance.items() if key != "content_sha256"} if isinstance(provenance, dict) else provenance
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _sha256_value(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def normal_pickup_content(content: str) -> str:
    lines = [
        line
        for line in content.splitlines()
        if " Source: `worker_failure:" not in line
    ]
    return "\n".join(lines).rstrip()


def empty_pickup(role_key: str, reason: str) -> dict[str, object]:
    return {
        "role_key": role_key, "event_count": 0, "short_term": "",
        "summaries": {"long-term": "", "mid-term": ""}, "relevant_events": [],
        "context_status": "unavailable", "context_reasons": [reason],
    }


def pickup_provenance_valid(packet: dict, legacy_paths: dict[str, str]) -> bool:
    provenance = packet.get("provenance")
    if (not isinstance(provenance, dict) or provenance.get("schema_version") != "memory_pickup.v1"
            or not _sha256_value(provenance.get("wal_sha256"))
            or type(provenance.get("wal_event_count")) is not int or provenance["wal_event_count"] < 0
            or type(provenance.get("wal_bytes")) is not int or provenance["wal_bytes"] < 0
            or provenance.get("content_sha256") != _pickup_content_digest(packet)):
        return False
    for key in ("legacy_sources", "generated_sources"):
        if not isinstance(provenance.get(key), dict) or set(provenance[key]) != set(MEMORY_HORIZONS):
            return False
    for horizon in MEMORY_HORIZONS:
        legacy = provenance["legacy_sources"][horizon]
        generated = provenance["generated_sources"][horizon]
        if (not isinstance(legacy, dict) or legacy.get("source_kind") != "legacy_mixed"
                or legacy.get("status") not in ("compatibility_unverified", "withheld_after_correction", "unavailable")
                or legacy.get("path") != legacy_paths[horizon]
                or (legacy["status"] != "unavailable" and not _sha256_value(legacy.get("sha256")))):
            return False
        if (not isinstance(generated, dict) or generated.get("source_kind") != "wal_generated"
                or not _sha256_value(generated.get("sha256")) or not isinstance(generated.get("event_ids"), list)
                or any(not isinstance(event_id, str) or re.fullmatch(r"mem:[a-f0-9]{12}", event_id) is None
                       for event_id in generated["event_ids"])):
            return False
    return True



def generated_horizon_context(horizon: str, horizon_events: list[dict], *, short_limit: int, identify):
    # Preserve the existing 12-bullet summary budget and add a hard
    # UTF-8 cap. These dependency IDs do not claim every fact was used.
    selected = horizon_events if horizon == "short-term" else horizon_events[:12]
    generated = "\n".join(f"- {event['text']}" for event in selected)
    generated = _bounded_context(generated, short_limit if horizon == "short-term" else PICKUP_SUMMARY_CHARS)
    if horizon != "short-term":
        generated = _bounded_context(summarize_markdown(generated), PICKUP_SUMMARY_CHARS)
    source = {
        "source_kind": "wal_generated", "event_ids": [identify(event) for event in selected],
        "sha256": hashlib.sha256(generated.encode("utf-8")).hexdigest(),
    }
    return generated, source
