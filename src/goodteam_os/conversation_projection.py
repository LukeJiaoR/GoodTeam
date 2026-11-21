"""[INPUT]: 项目会话消息与时间戳。
[OUTPUT]: 给 Work 提供顺序稳定的会话记录。
[POS]: Task Assistant 的只读投影。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""

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


def _text(value):
    return str(value or "").strip()
