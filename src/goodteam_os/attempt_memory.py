"""Bounded, read-only ordinary Worker memory observations for one exact attempt.

These records establish host preparation and WAL observations, never provider
receipt, reading, comprehension, or task acceptance. No role/latest fallback.
"""
from datetime import datetime
from pathlib import Path
from uuid import UUID

from .memory_handoff import MemoryHandoffStore, canonical_bytes, digest

SCHEMA = "goodteam.attempt-memory.v1"
DISPLAY_LIMIT = 8
PROTECTED_PREFIXES = ("job-input-", "job-correction-", "job-skill-")


def _time(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("invalid_observation_time")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("invalid_observation_time")
    return parsed.isoformat()


def _digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("invalid_observation_digest")
    return value


def _id(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("invalid_observation_id")
    return value


def _count(value):
    if type(value) is not int or not 0 <= value <= 2 ** 53 - 1:
        raise ValueError("invalid_observation_count")
    return value


def _preparation(row):
    validation = row["validation"]
    context = validation.get("context_status")
    if context not in {"available", "partial", "unavailable"}:
        context = "unknown"
    if not isinstance(row["citations"], list) or not isinstance(validation.get("rendered_sources"), list):
        raise ValueError("invalid_observation_sources")
    if type(row["capture_packet_match"]) is not bool:
        raise ValueError("invalid_observation_match")
    return {
        "id": _id(row["prepared_id"]), "prepared_at": _time(row["prepared_at"]),
        "input_digest": _digest(row["input_digest"]), "fragment_digest": _digest(row["fragment_digest"]),
        "packet_digest": _digest(row["packet_digest"]) if row["packet_digest"] is not None else None,
        "capture_packet_match": row["capture_packet_match"], "context_status": context,
        "citation_count": len(row["citations"]),
        "rendered_source_count": len(validation["rendered_sources"]),
    }


def _writeback(row):
    receipt = row["receipt"]
    if (row["kind"] not in {"result", "provenance"}
            or receipt.get("schema_version") != "memory_writeback.v1"
            or receipt.get("outcome") not in {"completed", "skipped", "unknown", "partial", "known_not_committed"}
            or type(receipt.get("counts_complete")) is not bool):
        raise ValueError("invalid_writeback_observation")
    return {
        "id": _id(row["writeback_id"]), "kind": row["kind"],
        "observed_at": _time(receipt["observed_at"]), "outcome": receipt["outcome"],
        "result_digest": _digest(receipt["result_sha256"]), "counts_complete": receipt["counts_complete"],
        **{key: _count(receipt[key]) for key in (
            "parsed", "attempted", "appended", "replayed", "deduplicated", "unattempted", "omitted_observations",
        )},
    }


def _project_snapshot(capture, receipts):
    identity = {key: value for key, value in capture.items()
                if key not in {"schema_version", "packet_digest", "captured_at"}}
    capture_ref = digest(canonical_bytes(capture))
    prepared = {}
    transports = {}
    writebacks = []
    identifiers = set()
    for row in receipts:
        if (row.get("schema_version") != "worker_memory_receipt.v1"
                or any(row.get(key) != value for key, value in identity.items())
                or any(row.get(key) != identity.get(key) for key in ("project_key", "execution_id"))):
            raise ValueError("observation_identity_mismatch")
        stage = row.get("stage")
        if stage == "worker_input_prepared":
            if row.get("capture_ref") != capture_ref or row.get("observation") != "host_input_prepared":
                raise ValueError("preparation_binding_mismatch")
            item = _preparation(row)
            if item["capture_packet_match"] != (item["packet_digest"] == capture["packet_digest"]):
                raise ValueError("preparation_packet_match_mismatch")
            prepared[digest(canonical_bytes(row))] = item
        elif stage == "transport_attempt":
            source = prepared.get(row.get("prepared_ref"))
            if (source is None or row.get("prepared_id") != source["id"]
                    or row.get("observation") != "host_call_entry"):
                raise ValueError("transport_binding_mismatch")
            item = {"id": _id(row["transport_id"])}
            _time(row["attempted_at"])
            transports[digest(canonical_bytes(row))] = row["prepared_ref"]
        elif stage == "memory_writeback":
            source, transport = row.get("prepared_ref"), row.get("transport_ref")
            if (row.get("capture_ref") != capture_ref or (source is not None and source not in prepared)
                    or (transport is not None and (source is None or transports.get(transport) != source))):
                raise ValueError("writeback_binding_mismatch")
            item = _writeback(row)
            writebacks.append(item)
        else:
            raise ValueError("unsupported_observation")
        if item["id"] in identifiers:
            raise ValueError("duplicate_observation")
        identifiers.add(item["id"])
    preparations = list(prepared.values())
    return {
        "status": "observed", "reason": "", "capture": {
            "captured_at": _time(capture["captured_at"]), "packet_digest": _digest(capture["packet_digest"]),
        },
        "preparation_count": len(preparations), "preparations": preparations[-DISPLAY_LIMIT:],
        "omitted_preparations": max(0, len(preparations) - DISPLAY_LIMIT),
        "transport_attempt_count": len(transports),
        "writeback_count": len(writebacks), "writebacks": writebacks[-DISPLAY_LIMIT:],
        "omitted_writebacks": max(0, len(writebacks) - DISPLAY_LIMIT),
    }


def attempt_memory_observations(project_root: Path | None, *, project_key: str, run_id: str, job: dict) -> dict:
    """Accept only a job already selected by the caller's exact run projection."""
    result = {
        "schema_version": SCHEMA, "project_key": project_key, "run_id": run_id,
        "attempt_id": job.get("id", ""), "role": job.get("role", ""), "adapter": job.get("adapter", ""),
        "read_only": True, "status": "unknown", "reason": "not_recorded",
        "provider_receipt": "unknown", "provider_read": "unknown",
    }
    if (job.get("run_id") != run_id or (job.get("project_key") and job["project_key"] != project_key)):
        return {**result, "status": "unavailable", "reason": "scope_mismatch"}
    identifier = job.get("id")
    if not isinstance(identifier, str) or not identifier.startswith("job-") or identifier.startswith(PROTECTED_PREFIXES):
        return {**result, "reason": "unsupported_attempt"}
    if project_root is None:
        return result
    try:
        snapshot = MemoryHandoffStore(project_root).inspection_snapshot(job)
        if snapshot is None:
            return result
        return {**result, **_project_snapshot(*snapshot)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Never disclose store paths, raw context, private errors, or fallback
        # citations from mutable job status when retained proof is unreadable.
        return {**result, "status": "unavailable", "reason": "records_unavailable"}
