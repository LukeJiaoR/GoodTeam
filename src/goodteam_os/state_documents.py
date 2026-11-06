"""PG JSONB storage behind the existing state schemas and service contracts.

Paths identify existing stores; they are not a second task or approval identity.
File mode remains available for unmigrated installations. In PG mode there are
no file reads, writes, or automatic fallbacks for a migrated document.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from fnmatch import fnmatch
from hashlib import sha256
from functools import lru_cache
import json
import os
from pathlib import Path

from psycopg.types.json import Jsonb

from goodteam_os.database import database, database_configured


_CONNECTION = ContextVar("goodteam_state_connection", default=None)


def document_key(path):
    return str(Path(path).resolve())


def ensure_storage_config(path):
    """A migrated workspace cannot silently reopen its stale file backup."""
    binding = _storage_binding(document_key(path))
    if binding is not None and (not database_configured() or binding.get("database_schema") !=
                               os.environ.get("GOODTEAM_DATABASE_SCHEMA", "goodteam")):
        raise ValueError("This state was migrated to PostgreSQL; restore its database configuration before use.")


@lru_cache(maxsize=1024)
def _storage_binding(key):
    # Cutover is explicitly offline. Cache its immutable startup binding rather
    # than stat every ancestor on each task/Worker polling read. The importer
    # invalidates this cache when used in the same process as legacy stores.
    for parent in Path(key).parents:
        marker = parent / ".goodteam" / "postgres-backend.json"
        if marker.is_file():
            return json.loads(marker.read_text(encoding="utf-8"))
    return None


def refresh_storage_bindings():
    _storage_binding.cache_clear()


@contextmanager
def connection():
    current = _CONNECTION.get()
    if current is not None:
        yield current
    else:
        with database().connection() as conn:
            yield conn


@contextmanager
def document_lock(path, *, blocking=True):
    if not database_configured():
        yield
        return
    key = int.from_bytes(sha256(document_key(path).encode()).digest()[:8], signed=True)
    with connection() as conn:
        token = _CONNECTION.set(conn)
        acquired = False
        try:
            if blocking:
                conn.execute("SELECT pg_advisory_lock(%s)", (key,))
                acquired = True
            else:
                acquired = conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (key,)).fetchone()["acquired"]
                if not acquired:
                    raise ValueError("Task Assistant is busy. Reload before retrying.")
            yield
        finally:
            if acquired:
                conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
            _CONNECTION.reset(token)


def _record(conn, path):
    row = conn.execute("SELECT payload,deleted FROM state_documents WHERE path=%s", (document_key(path),)).fetchone()
    if row is None and Path(path).is_file():
        raise ValueError("Existing state requires an explicit PostgreSQL import before use.")
    return row


def exists(path):
    ensure_storage_config(path)
    if not database_configured():
        return Path(path).is_file()
    with connection() as conn:
        row = _record(conn, path)
    return row is not None and not row["deleted"]


def read_json(path):
    ensure_storage_config(path)
    if not database_configured():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    with connection() as conn:
        row = _record(conn, path)
    if row is None or row["deleted"]:
        raise FileNotFoundError(str(path))
    return row["payload"]


def write_json(path, payload, *, exclusive=False):
    """PG-only write; existing file implementations retain their durability code."""
    ensure_storage_config(path)
    with connection() as conn:
        _record(conn, path)
        if exclusive:
            row = conn.execute(
                "INSERT INTO state_documents(path,payload) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING path",
                (document_key(path), Jsonb(payload)),
            ).fetchone()
            if row is None:
                raise FileExistsError(str(path))
        else:
            conn.execute("""INSERT INTO state_documents(path,payload) VALUES (%s,%s)
                ON CONFLICT(path) DO UPDATE SET payload=excluded.payload,deleted=FALSE,updated_at=clock_timestamp()""",
                (document_key(path), Jsonb(payload)))


def remove(path):
    ensure_storage_config(path)
    if not database_configured():
        Path(path).unlink()
        return
    with connection() as conn:
        row = _record(conn, path)
        if row is None or row["deleted"]:
            raise FileNotFoundError(str(path))
        conn.execute("UPDATE state_documents SET payload=NULL,deleted=TRUE,updated_at=clock_timestamp() WHERE path=%s",
                     (document_key(path),))


def modified_time(path):
    if not database_configured():
        return Path(path).stat().st_mtime
    with connection() as conn:
        row = conn.execute("SELECT updated_at FROM state_documents WHERE path=%s AND NOT deleted",
                           (document_key(path),)).fetchone()
    if row is None:
        raise FileNotFoundError(str(path))
    return row["updated_at"].timestamp()


def paths(directory, pattern="*.json", *, newest_first=False):
    ensure_storage_config(Path(directory) / "entry")
    directory = Path(directory)
    if not database_configured():
        result = list(directory.glob(pattern))
        return sorted(result, key=lambda path: path.stat().st_mtime, reverse=True) if newest_first else sorted(result)
    prefix = document_key(directory) + "/"
    with connection() as conn:
        rows = conn.execute("""SELECT path,deleted FROM state_documents WHERE starts_with(path,%s)
            ORDER BY updated_at DESC,path""", (prefix,)).fetchall()
        known = {row["path"] for row in rows}
        for legacy in directory.glob(pattern):
            if legacy.is_file() and document_key(legacy) not in known:
                raise ValueError("Existing state requires an explicit PostgreSQL import before use.")
    result = [Path(row["path"]) for row in rows if not row["deleted"]
              and row["path"][len(prefix):].count("/") == pattern.count("/")
              and fnmatch(row["path"][len(prefix):], pattern)]
    return result if newest_first else sorted(result)


def read_events(path):
    """Keep the original ordered event records behind the same store identity."""
    if not exists(path):
        return []
    if not database_configured():
        return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    value = read_json(path)
    if not isinstance(value, list) or any(not isinstance(event, dict) for event in value):
        raise ValueError("Invalid persisted event ledger.")
    return value


def append_events(path, events):
    with document_lock(path):
        write_json(path, [*read_events(path), *events])
