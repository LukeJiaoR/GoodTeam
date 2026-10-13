"""Pure legacy event identity and active/browse projections.

These functions preserve existing v1 semantics; they neither authorize memory
nor infer a new source version or lifecycle transition.
"""
import hashlib
import json

MEMORY_HORIZONS = ("long-term", "mid-term", "short-term")
MEMORY_CATEGORIES = ("learned", "caveats", "decisions", "open_questions")
IDENTITY_FIELDS = ("timestamp", "role_key", "horizon", "action", "source", "text")

def event_id(event: dict[str, object]) -> str:
    identity_payload = {key: event.get(key) for key in IDENTITY_FIELDS}
    payload = json.dumps(identity_payload, sort_keys=True, ensure_ascii=False)
    return "mem:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def active_events(events: list[dict[str, str]], *, identify=event_id) -> list[dict[str, str]]:
    superseded_ids = {
        str(event.get("supersedes", "")).strip()
        for event in events
        if isinstance(event.get("supersedes"), str) and str(event.get("supersedes", "")).strip()
    }
    return [event for event in events if identify(event) not in superseded_ids]


def is_execution_record(event: dict[str, object]) -> bool:
    source = str(event.get("source", ""))
    return source.split(":", 1)[0] == "worker_failure" or source.endswith("/provenance")


def normal_retrieval_events(events: list[dict[str, str]]) -> list[dict[str, str]]:
    return [event for event in events if not is_execution_record(event)]


def retrieval_result(score: float, event: dict[str, object], *, identifier: str) -> dict[str, object]:
    return {
        "id": identifier,
        "score": float(score),
        "horizon": event.get("horizon", ""),
        "text": event.get("text", ""),
        "source": event.get("source", ""),
        "timestamp": event.get("timestamp", ""),
    }


def browse_result(event: dict[str, object], *, active_ids: set[str], identifier: str) -> dict[str, object]:
    event_id = identifier
    text = str(event.get("text", ""))
    body = text
    timestamp = str(event.get("timestamp", ""))
    return {
        "id": event_id,
        "kind": str(event.get("horizon", "")),
        "horizon": str(event.get("horizon", "")),
        "role": str(event.get("role_key", "")),
        "timestamp": timestamp,
        "ts": timestamp,
        "body": body,
        "category": ("execution" if is_execution_record(event) else
                     event.get("category") if event.get("category") in MEMORY_CATEGORIES else "unclassified"),
        "source": str(event.get("source", "")),
        "action": str(event.get("action", "")),
        "is_superseded": event_id not in active_ids,
    }
