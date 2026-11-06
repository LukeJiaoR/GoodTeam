"""Explicit offline import of existing task, conversation, Workflow and Goal state."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile

from psycopg import sql
from psycopg.types.json import Jsonb

from goodteam_os.database import database
from goodteam_os.goals import GoalStore
from goodteam_os import postgres_goals
from goodteam_os.state_documents import document_key, refresh_storage_bindings


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key in migration source.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-finite value in migration source.")


def _sources(workspace, project_roots, registry):
    state = workspace / "state"
    paths = set()
    if (state / "current.json").is_file():
        paths.add(state / "current.json")
    paths.update((state / "checkpoints").glob("**/*.json"))
    for path in (registry, state / "operator" / "queue.json", state / "operator" / "events.ndjson"):
        if path.is_file():
            paths.add(path)
    for directory in (state / "projects").glob("*"):
        for name in ("task-assistant-session.json", "current.json", "intent-commits.json",
                     "readiness.json", "team-state.json", "team-events.jsonl"):
            if (directory / name).is_file():
                paths.add(directory / name)
        paths.update((directory / "archived").glob("*.json"))
        paths.update((directory / "checkpoints").glob("**/*.json"))
    for root in project_roots:
        path = root / ".goodteam" / "tasks" / "tasks.json"
        if path.is_file():
            paths.add(path)
        for name in ("task.json", "run.json", "events.jsonl"):
            paths.update((root / ".goodteam" / "executions").glob("*/" + name))
    return sorted(paths)


def migrate(*, workspace, project_roots, offline_confirmed=False, registry_path=None):
    if offline_confirmed is not True:
        raise ValueError("Stop services and workers using this state before importing it.")
    workspace = Path(workspace).resolve()
    roots = [Path(root).resolve() for root in project_roots]
    from goodteam_os.project_registry import get_default_registry_path

    registry = Path(registry_path or get_default_registry_path()).resolve()
    source_paths = _sources(workspace, roots, registry)
    sources = []
    for path in source_paths:
        if path.is_symlink():
            raise ValueError("A migration source must be a regular state file.")
        raw = path.read_bytes()
        def parse(value):
            return json.loads(value, object_pairs_hook=_object, parse_constant=_invalid_constant)

        if path.suffix in {".jsonl", ".ndjson"}:
            payload = [parse(line) for line in raw.splitlines() if line.strip()]
            if any(not isinstance(event, dict) for event in payload):
                raise ValueError("An event import source must contain JSON objects.")
        else:
            payload = parse(raw)
        # Jsonb encoding must reject overflow-to-infinity before any import.
        json.dumps(payload, allow_nan=False)
        sources.append((path, raw, payload, sha256(raw).hexdigest()))
    backend = database()
    imported = 0
    with backend.connection() as conn, conn.transaction():
        for path, raw, payload, digest in sources:
            key = document_key(path)
            conn.execute("SELECT pg_advisory_xact_lock(%s)",
                         (int.from_bytes(sha256(key.encode()).digest()[:8], signed=True),))
            previous = conn.execute("SELECT source_digest FROM state_imports WHERE path=%s", (key,)).fetchone()
            if previous:
                if previous["source_digest"] != digest:
                    raise ValueError("An imported source changed; existing PostgreSQL state was preserved.")
                continue
            if conn.execute("SELECT 1 FROM state_documents WHERE path=%s", (key,)).fetchone():
                raise ValueError("PostgreSQL already contains this state; import will not overwrite it.")
            conn.execute("INSERT INTO state_documents(path,payload) VALUES (%s,%s)", (key, Jsonb(payload)))
            conn.execute("INSERT INTO state_imports(path,source_digest) VALUES (%s,%s)", (key, digest))
            imported += 1
        if any(path.read_bytes() != raw for path, raw, _payload, _digest in sources):
            raise ValueError("State changed during migration; stop its writers before retrying.")
        goal_count = _migrate_goals(workspace, conn)
    marker = {"schema_version": "postgres_store.v1", "database_schema": backend.schema}
    for root in set([workspace, *roots, registry.parent]):
        target = root / ".goodteam" / "postgres-backend.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(marker, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    refresh_storage_bindings()
    return {"schema_version": "postgres_import.v1", "documents_imported": imported,
            "documents_checked": len(sources), "goals_imported": goal_count,
            "sources_preserved": True}


def _migrate_goals(workspace, connection):
    path = workspace / "state" / "goals.sqlite3"
    if not path.is_file():
        return 0
    # SQLite is read-only and exists only on this legacy import path.
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as legacy:
        legacy.row_factory = sqlite3.Row
        legacy.execute("BEGIN")
        if legacy.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported legacy Goal ledger.")
        if legacy.execute("PRAGMA quick_check").fetchone()[0] != "ok" or legacy.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Legacy Goal ledger integrity failed.")
        tables = {row[0] for row in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != set(postgres_goals.TABLES):
            raise ValueError("Incomplete legacy Goal ledger.")
        source = {}
        for name in postgres_goals.TABLES:
            order = "rowid" if name in {"goals", "outbox"} else "1"
            source[name] = [dict(row) for row in legacy.execute(f"SELECT *,rowid FROM {name} ORDER BY {order}")]
        validator = object.__new__(GoalStore)
        validator.workspace, validator.project_key, validator.path = workspace, "", path
        expected = [validator._snapshot(legacy, row["id"]) for row in source["goals"]]
    digest = sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    postgres_goals.initialize(validator, importing=True)
    with postgres_goals.transaction(validator, write=True, existing_connection=connection) as db:
        parent = sql.Identifier(database().schema)
        previous = db.conn.execute(sql.SQL("SELECT source_digest FROM {}.state_imports WHERE path=%s").format(parent),
                                   (str(path.resolve()),)).fetchone()
        if previous:
            if previous["source_digest"] != digest:
                raise ValueError("Imported Goal source changed; PostgreSQL state was preserved.")
            return 0
        if any(db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in postgres_goals.TABLES):
            raise ValueError("PostgreSQL already contains Goals; import will not overwrite them.")
        for name, rows in source.items():
            for row in rows:
                columns = [column for column in row if column != "rowid" or name in {"goals", "outbox"}]
                db.execute(f"INSERT INTO {name}({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                           tuple(row[column] for column in columns))
            if name in {"goals", "outbox"}:
                db.execute(f"SELECT setval(pg_get_serial_sequence('{name}','rowid'), COALESCE(MAX(rowid),0)+1, false) FROM {name}")
        observed = [validator._snapshot(db, row["id"]) for row in source["goals"]]
        if observed != expected:
            raise ValueError("Goal import did not preserve the complete ledger snapshot.")
        db.conn.execute(sql.SQL("INSERT INTO {}.state_imports(path,source_digest) VALUES (%s,%s)").format(parent),
                        (str(path.resolve()), digest))
    return len(expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--project-root", action="append", default=[], type=Path)
    parser.add_argument("--registry", type=Path, help="Defaults to the application's configured project registry.")
    parser.add_argument("--offline-confirmed", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(workspace=args.workspace, project_roots=args.project_root,
                             offline_confirmed=args.offline_confirmed, registry_path=args.registry), ensure_ascii=False))


if __name__ == "__main__":
    main()
