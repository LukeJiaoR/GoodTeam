"""Read-only conversation records shared by project and historical task views."""

from collections.abc import Mapping, Sequence
from typing import Any


def assistant_timeline(session: Mapping[str, object]) -> list[dict[str, Any]]:
    return [
        {
            **dict(item),
            "id": _text(item.get("message_id")) or _text(item.get("id")),
            "timestamp": _text(item.get("created_at")) or _text(item.get("timestamp")),
            "kind": _text(item.get("kind")) or "conversation",
            "details": dict(item.get("details", {}) or {}),
            "refs": list(item.get("refs", []) or []),
        }
        for item in session.get("timeline", [])
        if isinstance(item, Mapping)
    ]



def change_discussion_timeline(
    run_id: str,
    intent_commits: Sequence[Mapping[str, object]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_sources: set[tuple[str, ...]] = set()
    for intent in intent_commits:
        mission = _mapping(intent.get("mission"))
        target = _mapping(intent.get("target"))
        if _text(mission.get("id")) != run_id or _text(intent.get("kind")) != "request_changes":
            continue
        intent_id = _text(intent.get("id"))
        for event in _list(intent.get("events")):
            if not isinstance(event, Mapping):
                continue
            event_name = _text(event.get("event"))
            actor = _text(event.get("actor"))
            if not event_name or actor not in {"user", "manager"}:
                continue
            source_identity = (
                intent_id,
                _text(event.get("id")),
                actor,
                event_name,
                _text(event.get("timestamp")),
                _text(event.get("content")),
                _text(target.get("target_id")),
            )
            if source_identity in seen_sources:
                continue
            seen_sources.add(source_identity)
            items.append(
                {
                    "id": _text(event.get("id")) or f"{intent_id}:{event_name}:{len(items)}",
                    "actor": actor,
                    "run_id": run_id,
                    "kind": "intent",
                    "event": event_name,
                    "timestamp": _text(event.get("timestamp")),
                    "content": _text(event.get("content")),
                    "details": {
                        "intent_id": intent_id,
                        "intent_status": _text(intent.get("status")),
                        "gate": _text(target.get("target_id")),
                        "target_id": _text(target.get("target_id")),
                    },
                    "refs": [value for value in (intent_id, _text(target.get("target_id"))) if value],
                }
            )
    return items



def dedupe_timeline(items: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        identifier = _text(item.get("id"))
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        result.append(item)
    # Stable source order breaks ties for legacy records with equal or missing
    # timestamps; sorting by id could place the final outcome before the request.
    return sorted(result, key=lambda item: _text(item.get("timestamp")))



def _list(value):
    return list(value) if isinstance(value, (list, tuple)) else []


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _text(value):
    return str(value or "").strip()
