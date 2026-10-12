"""Private observations of the legacy Worker memory write-back boundary.

The result/provenance wrappers keep their parsing and source policies. This
module only observes those writes; the Worker handoff store owns persistence
and job/transport identity. Receipts contain no source strings or content.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .memory_core.projections import MEMORY_HORIZONS
from .memory_storage import role_lock

if TYPE_CHECKING:
    from .memory import RoleMemoryStore


# Repeated result headings are intentionally not capped by the legacy parser.
# Bound metadata independently, without changing what that parser writes.
MAX_RECEIPT_OBSERVATIONS = 128


def _matches_append(event: object, role_key: str, horizon: str, text: str, source: str) -> bool:
    return (isinstance(event, dict)
            and set(event) == {"timestamp", "role_key", "horizon", "action", "source", "text"}
            and isinstance(event.get("timestamp"), str)
            and event.get("role_key") == role_key
            and event.get("horizon") == horizon
            and event.get("action") == "add"
            and event.get("source") == source
            and event.get("text") == text.strip())


def _reconcile_append(store: RoleMemoryStore, role_key: str, *, before: bytes,
                      horizon: str, text: str, source: str) -> tuple[str, dict | None]:
    """Read once while the caller still holds the role lock; never repair/retry.

    Checkpoints/projections may be exactly what failed after the WAL replace.
    The captured, verified pre-WAL bytes plus one exact matching serialized
    append can establish that commit independently of those derived writes.
    Anything else is unknown, not evidence of zero committed events.
    """
    try:
        after = store._wal_bytes(role_key)
        if after == before:
            return "known_not_committed", None
        prefix = before + (b"\n" if before and not before.endswith(b"\n") else b"")
        if not after.startswith(prefix):
            return "unknown", None
        suffix = after[len(prefix):]
        event = json.loads(suffix)
        if (_matches_append(event, role_key, horizon, text, source)
                and suffix == (json.dumps(event) + "\n").encode("utf-8")):
            # This is a WAL replacement observed under the lock, not a promise
            # of filesystem durability after an operating-system fsync error.
            return "committed", event
    except Exception:
        pass
    return "unknown", None


def write_back_events(
    store: RoleMemoryStore,
    role_key: str,
    *,
    items: list[tuple[str, str]],
    source: str,
    result_bytes: bytes,
    include_parsed: bool = True,
) -> tuple[dict, dict]:
    """Return the compatible status and a bounded, content-free observation.

    Source equality means this exact wrapper's job source, never a guessed
    source attributed to the new job. Text dedup remains role-wide and includes
    superseded events, as in the legacy wrappers. Neither replay nor cross-job
    text dedup makes a new WAL write or repairs a projection.
    """
    receipt = {
        "schema_version": "memory_writeback.v1",
        "observed_at": "",
        "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "parsed": len(items),
        "attempted": 0,
        "appended": 0,
        "skipped_duplicates": 0,
        "replayed": 0,
        "deduplicated": 0,
        "unattempted": len(items),
        "outcome": "skipped",
        "new_event_ids": [],
        "replayed_event_ids": [],
        "duplicate_event_ids": [],
        "observations": [],
        "omitted_observations": 0,
    }
    observations_digest = hashlib.sha256()
    failure = ""
    processed = 0

    def observe(index: int, horizon: str, outcome: str, *, event: dict | None = None,
                projection: str = "unobserved") -> None:
        observation = {
            "item_index": index,
            "requested_horizon": horizon if horizon in MEMORY_HORIZONS else "unknown",
            "outcome": outcome,
            "projection": projection,
        }
        identifier = store._event_id(event) if event is not None else None
        if identifier is not None:
            observation["event_id"] = identifier
            try:
                timestamp = datetime.fromisoformat(event.get("timestamp", ""))
                if timestamp.utcoffset() is not None:
                    # Canonicalize parsed dates; never copy a free-form WAL
                    # value into metadata or substitute observation time.
                    observation["event_timestamp"] = timestamp.isoformat()
            except (TypeError, ValueError, OverflowError):
                pass
            event_horizon = event.get("horizon")
            observation["event_horizon"] = event_horizon if event_horizon in MEMORY_HORIZONS else "unknown"
            observation["relation"] = {
                "committed": "new_wal_commit",
                "replayed": "same_source_text_replay",
                "deduplicated": "other_source_text_dedup",
            }[outcome]
        # Include every observation in this digest, even when its display is
        # omitted by the metadata bound. It is not a digest of the raw result.
        observations_digest.update((json.dumps(observation, sort_keys=True) + "\n").encode("utf-8"))
        if len(receipt["observations"]) >= MAX_RECEIPT_OBSERVATIONS:
            receipt["omitted_observations"] += 1
            return
        receipt["observations"].append(observation)
        destination = {"committed": "new_event_ids", "replayed": "replayed_event_ids",
                       "deduplicated": "duplicate_event_ids"}.get(outcome)
        if identifier is not None and destination is not None and identifier not in receipt[destination]:
            receipt[destination].append(identifier)

    if items:
        try:
            # append_memory_event and _wal_snapshot use the same reentrant
            # thread/process lock, so no other compliant writer can enter the
            # gap between dedup, append and failure reconciliation.
            with role_lock(store.role_root(role_key)):
                events, _, before = store._wal_snapshot(role_key)
                by_text: dict[str, list[dict]] = {}
                for event in events:
                    by_text.setdefault(str(event.get("text", "")), []).append(event)
                for index, (horizon, text) in enumerate(items):
                    matches = by_text.get(text, [])
                    if matches:
                        same_source = next((event for event in matches if event.get("source") == source), None)
                        event = same_source if same_source is not None else matches[0]
                        relation = "replayed" if same_source is not None else "deduplicated"
                        receipt[relation] += 1
                        receipt["skipped_duplicates"] += 1
                        observe(index, horizon, relation, event=event)
                        processed += 1
                        continue
                    receipt["attempted"] += 1
                    try:
                        event = store.append_memory_event(role_key, horizon=horizon, text=text, source=source)
                    except Exception:
                        outcome, event = _reconcile_append(
                            store, role_key, before=before, horizon=horizon, text=text, source=source,
                        )
                        if outcome == "committed":
                            receipt["appended"] += 1
                        observe(index, horizon, outcome, event=event)
                        processed += 1
                        failure = outcome
                        break
                    # Use the actual returned event identity, not a freshly
                    # generated event or IDs guessed from result content.
                    receipt["appended"] += 1
                    observe(index, horizon, "committed", event=event, projection="complete")
                    processed += 1
                    by_text.setdefault(text, []).append(event)
                    # Capture the committed prefix for the next append. A
                    # failure here cannot erase the just-observed returned ID.
                    before = store._wal_bytes(role_key)
        except Exception:
            failure = "observation_unavailable"

    receipt["unattempted"] = len(items) - processed
    receipt["observations_sha256"] = observations_digest.hexdigest()
    receipt["observed_at"] = datetime.now(UTC).isoformat()
    receipt["counts_complete"] = failure not in {"unknown", "observation_unavailable"}
    if failure:
        receipt["failure_outcome"] = failure
        receipt["outcome"] = ("unknown" if failure in {"unknown", "observation_unavailable"}
                              else "partial" if receipt["appended"] else "known_not_committed")
    else:
        receipt["outcome"] = "completed" if receipt["appended"] else "skipped"

    status = {
        "status": "error" if failure else "ok" if receipt["appended"] else "skipped",
        "source": source,
        "appended": receipt["appended"],
        "skipped_duplicates": receipt["skipped_duplicates"],
    }
    if include_parsed:
        status["parsed"] = len(items)
        if not items:
            status["reason"] = "no_sections"
    if failure:
        status["error"] = "memory_writeback_incomplete"
    return status, receipt
