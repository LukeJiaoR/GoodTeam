"""Bounded local Runtime observation for Manager conversations, never control."""
from datetime import datetime, timezone


def runtime_observation(workers, invocation):
    from goodteam_os.workers import SESSION_BACKENDS

    source = "WorkerJobStore.native_events" if invocation.get("adapter") == "codex" else "WorkerJobStore.session_status"
    observation = {"read_only": True, "source": source,
        "observed_at": datetime.now(timezone.utc).isoformat(), "available": False,
        "interpretation": "Local process/heartbeat evidence only, not provider progress, a Worker reply, "
                          "accepted delivery or authorization to retry. Snapshot may change immediately. "
                          "Control acknowledgements are separate and do not prove execution."}
    if invocation.get("current_dispatch_attempt") is False:
        return {**observation, "reason": "superseded_attempt"}
    if invocation.get("adapter") == "codex":
        try:
            snapshot = workers.native_events(invocation["job_id"], tail=True)
            if (snapshot.get("job_id") != invocation["job_id"]
                    or snapshot.get("run_id") != invocation.get("run_id")):
                return {**observation, "reason": "invocation_identity_mismatch"}
            owned = snapshot.get("capabilities", {}).get("process_owned") is True
            evidence = {"session_status": snapshot.get("status", "unknown"),
                        "heartbeat": snapshot.get("heartbeat", ""), "process_owned": owned}
            if owned:
                evidence["process_alive"] = True
            # Absence from this host's registry is unknown, never proof of death.
            return {**observation, "available": True, "evidence": evidence}
        except (OSError, ValueError, TypeError, KeyError):
            return {**observation, "reason": "native_observation_unavailable"}
    if invocation.get("adapter") not in SESSION_BACKENDS:
        return {**observation, "reason": "runtime_observation_unsupported"}
    try:
        status = workers.session_status(invocation["job_id"])
        if status.get("job_id") != invocation["job_id"]:
            return {**observation, "reason": "invocation_identity_mismatch"}
        # No session IDs, provider identity, runtime paths, raw transcript or
        # control queue content enter the Manager prompt. Existing session
        # admission is retained; denied observations never use a fallback.
        evidence = {key: status[key] for key in (
            "session_status", "process_alive", "heartbeat", "isStreaming",
            "isCompacting", "pendingMessageCount") if key in status}
        return {**observation, "available": True, "evidence": evidence}
    except (OSError, ValueError, TypeError):
        # Unknown is not dead/failed. In particular protected Runtime admission
        # failures must not become a false process-death or auto-retry signal.
        return {**observation, "reason": "session_observation_unavailable"}
