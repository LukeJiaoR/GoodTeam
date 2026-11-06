"""Observed Codex invocation usage, separate from execution/approval authority."""
from __future__ import annotations

import json
from pathlib import Path
import psycopg
from uuid import uuid4

from goodteam_os.database import database, database_configured, workspace_identity
from goodteam_os.state_documents import ensure_storage_config, bind_postgres_backend
from goodteam_os.service import service_state_dir

_COUNTERS = ("input_tokens", "cached_input_tokens", "output_tokens")


def codex_usage(stdout):
    totals = dict.fromkeys(_COUNTERS, 0)
    turns = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in _COUNTERS):
            continue
        if usage["cached_input_tokens"] > usage["input_tokens"]:
            continue
        turns += 1
        for key in totals:
            totals[key] += usage[key]
    return {**totals, "total_tokens": totals["input_tokens"] + totals["output_tokens"], "turns": turns} if turns else None


def record_codex_usage(job, *, model, provider, started_at, status, stdout):
    usage = codex_usage(stdout)
    root = Path(service_state_dir()).expanduser().resolve() / "providers"
    if not database_configured():
        return {"token_usage": usage, "usage_recorded": False}
    try:
        ensure_storage_config(root / "connections.json")
        with database().connection() as conn, conn.transaction():
            bind_postgres_backend(root)
            conn.execute("INSERT INTO provider_usage VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (uuid4().hex, workspace_identity(root), "codex", provider.get("id", "local"),
                 provider.get("name", "Local Codex"), model, str(job.get("project_key", "")), job["id"],
                 started_at, status, *((usage or {}).get(key) for key in _COUNTERS)))
        return {"token_usage": usage, "usage_recorded": True}
    except (OSError, ValueError, psycopg.Error):
        # A telemetry failure must never rerun a successfully completed model call.
        return {"token_usage": usage, "usage_recorded": False}


def usage_summary():
    root = Path(service_state_dir()).expanduser().resolve() / "providers"
    ensure_storage_config(root / "connections.json")
    rows = []
    if database_configured():
        with database().connection() as conn:
            rows = conn.execute("""SELECT agent, provider_id, MAX(provider_name) AS provider_name, model,
                COUNT(*) AS invocations, COUNT(input_tokens) AS reported_invocations,
                SUM(input_tokens)::bigint AS input_tokens, SUM(cached_input_tokens)::bigint AS cached_input_tokens,
                SUM(output_tokens)::bigint AS output_tokens, SUM(input_tokens + output_tokens)::bigint AS total_tokens
                FROM provider_usage WHERE scope=%s GROUP BY agent, provider_id, model ORDER BY provider_id, model""",
                (workspace_identity(root),)).fetchall()
    totals = {key: sum(row[key] or 0 for row in rows) for key in
              ("invocations", "reported_invocations", *_COUNTERS, "total_tokens")}
    if not totals["reported_invocations"]:
        for key in (*_COUNTERS, "total_tokens"):
            totals[key] = None
    return {"configured": database_configured(), "totals": totals, "by_model": rows, "source": "codex_exec_turn_completed",
            "scope": "GoodTeam invocations recorded since provider statistics were enabled"}
