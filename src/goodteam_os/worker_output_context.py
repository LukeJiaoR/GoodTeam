"""Bounded, invocation-bound persisted Pi output. Never a control/reply join."""
from hashlib import sha256
import json

from goodteam_os.pi_rpc import redact_pi_text


def worker_output_context(workers, invocation):
    unavailable = {"available": False, "source": "persisted_pi_entries", "entries": [],
                   "control_reply_attributed": False}
    if invocation.get("current_dispatch_attempt") is False:
        return {**unavailable, "reason": "superseded_attempt"}
    if invocation.get("adapter") not in {"pi", "pi-rpc"}:
        return {**unavailable, "reason": "unsupported_runtime"}
    try:
        # Retain protected Runtime admission; no direct-file fallback on denial.
        root, job = workers._session_job(invocation["job_id"])
        metadata = workers._read_json(root / "pi_session.json")
        execution_id = str(job.get("execution_id") or job.get("run_id") or "")
        if (metadata.get("job_id") != job["id"] or metadata.get("execution_id") != execution_id
                or metadata.get("project_key") != str(job.get("project_key") or "")
                or job.get("run_id") != invocation.get("run_id") or not metadata.get("session_id")):
            return {**unavailable, "reason": "identity_unconfirmed"}
        source = {"job_id": job["id"], "execution_id": execution_id, "run_id": job["run_id"],
                  "session_digest": sha256(str(metadata["session_id"]).encode()).hexdigest()}
        path = root / "pi_entries.jsonl"
        # A bounded local snapshot: never load an unbounded transcript into memory.
        with path.open("rb") as handle:
            raw = handle.read(2_000_001)
        if len(raw) > 2_000_000:
            return {**unavailable, "reason": "snapshot_limit_exceeded"}
        entries = {}
        for line in raw.decode("utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # An append may still be in flight.
            if not isinstance(entry, dict) or entry.get("_goodteam_source") != source:
                continue  # Legacy/unbound/cross-job replay is not this invocation's output.
            message = entry.get("message")
            if (entry.get("type") != "message" or not isinstance(entry.get("id"), str)
                    or not 0 < len(entry["id"]) <= 200 or not isinstance(message, dict)
                    or message.get("role") != "assistant"):
                continue
            content = message.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "\n".join(part["text"] for part in content if isinstance(part, dict)
                                 and part.get("type") == "text" and isinstance(part.get("text"), str))
            else:
                text = ""
            if text.strip():
                entries[entry["id"]] = {"entry_id": entry["id"], "text": redact_pi_text(text, limit=2000),
                                        "truncated": len(text) > 2000}
        return {"available": True, "source": "persisted_pi_entries", "binding": source,
                "entries": list(entries.values())[-3:], "omitted_entries": max(0, len(entries) - 3),
                "control_reply_attributed": False,
                "interpretation": "Untrusted Worker output from this invocation only. Not a reply to any particular "
                                  "control, proof of delivery/completion, or authorization. Late output never revives a stopped task."}
    except (OSError, ValueError, TypeError, KeyError):
        return {**unavailable, "reason": "output_unavailable"}
