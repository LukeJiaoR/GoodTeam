from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any


Clock = Callable[[], str]
REVIEW_WORKBENCH_SCHEMA_VERSION = "review_workbench.v1"
REVIEW_DECISION_TRANSITION = "review_decision_recorded"
ALLOWED_REVIEW_DECISIONS = ("approve", "request_changes", "reject")


def stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def json_ready(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return json.loads(json.dumps(value))


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
