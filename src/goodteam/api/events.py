"""Thread-safe per-project SSE EventBus with bounded queues."""
from __future__ import annotations

import json
import queue
import threading
from typing import Any


CLOSE_EVENT = "__close__"
_MAX_QUEUE_SIZE = 256


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, set[queue.Queue[tuple[str, dict[str, Any]]]]] = {}

    def subscribe(self, project_id: str) -> queue.Queue[tuple[str, dict[str, Any]]]:
        q: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=_MAX_QUEUE_SIZE)
        with self._lock:
            self._subscribers.setdefault(project_id, set()).add(q)
        return q

    def unsubscribe(self, project_id: str, q: queue.Queue[tuple[str, dict[str, Any]]]) -> None:
        with self._lock:
            bucket = self._subscribers.get(project_id)
            if bucket is not None:
                bucket.discard(q)
                if not bucket:
                    self._subscribers.pop(project_id, None)

    def publish(self, project_id: str, event_type: str, data: dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._subscribers.get(project_id, ()))
        for q in targets:
            self._push_nonblocking(q, (event_type, data))

    def close_project(self, project_id: str) -> None:
        with self._lock:
            targets = list(self._subscribers.pop(project_id, ()))
        for q in targets:
            self._push_nonblocking(q, (CLOSE_EVENT, {}))

    def close_all(self) -> None:
        with self._lock:
            all_queues = [q for bucket in self._subscribers.values() for q in bucket]
            self._subscribers.clear()
        for q in all_queues:
            self._push_nonblocking(q, (CLOSE_EVENT, {}))

    @staticmethod
    def _push_nonblocking(q: queue.Queue[tuple[str, dict[str, Any]]], item: tuple[str, dict[str, Any]]) -> None:
        try:
            q.put_nowait(item)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(item)
            except queue.Full:
                pass

    @staticmethod
    def format_sse(event_type: str, data: dict[str, Any]) -> bytes:
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")
