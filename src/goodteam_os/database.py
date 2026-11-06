"""Shared PostgreSQL configuration for application data and Manager checkpoints."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from hashlib import sha256
import os
from pathlib import Path
import re
from threading import Lock

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def database_configured() -> bool:
    return bool(os.environ.get("GOODTEAM_DATABASE_URL", "").strip())


def provider_environment(environment=None):
    """External tools and project commands never receive control-plane DB access."""
    source = os.environ if environment is None else environment
    return {key: value for key, value in source.items()
            if key not in {"GOODTEAM_DATABASE_URL", "GOODTEAM_DATABASE_SCHEMA", "GOODTEAM_TEST_DATABASE_URL"}}


def workspace_identity(workspace: Path) -> str:
    return sha256(str(Path(workspace).resolve()).encode()).hexdigest()


def database() -> PostgresDatabase:
    """One configuration and pool; a failed PG connection never selects a file DB."""
    url = os.environ.get("GOODTEAM_DATABASE_URL", "").strip()
    schema = os.environ.get("GOODTEAM_DATABASE_SCHEMA", "goodteam")
    if not url:
        raise ValueError("GOODTEAM_DATABASE_URL is required for PostgreSQL persistence.")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("GOODTEAM_DATABASE_SCHEMA must be a PostgreSQL schema name.")
    return _database(url, schema)


@lru_cache(maxsize=8)
def _database(url: str, schema: str) -> PostgresDatabase:
    return PostgresDatabase(url, schema)


class PostgresDatabase:
    def __init__(self, url: str, schema: str):
        self.schema = schema
        with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
            with conn.transaction():
                key = int.from_bytes(sha256(("goodteam-schema:" + schema).encode()).digest()[:8], signed=True)
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
                conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
                conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
                conn.execute("""CREATE TABLE IF NOT EXISTS state_documents (
                    path TEXT PRIMARY KEY, payload JSONB, deleted BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
                conn.execute("""CREATE TABLE IF NOT EXISTS state_imports (
                    path TEXT PRIMARY KEY, source_digest TEXT NOT NULL,
                    imported_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
                conn.execute("""CREATE TABLE IF NOT EXISTS provider_usage (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, agent TEXT NOT NULL,
                    provider_id TEXT NOT NULL, provider_name TEXT NOT NULL, model TEXT NOT NULL,
                    project_key TEXT NOT NULL, job_id TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL,
                    status TEXT NOT NULL, input_tokens BIGINT, cached_input_tokens BIGINT, output_tokens BIGINT
                )""")
                conn.execute("CREATE INDEX IF NOT EXISTS provider_usage_scope ON provider_usage(scope)")
        self.pool = ConnectionPool(
            url, min_size=0, max_size=16, timeout=10, max_waiting=32, open=True,
            kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 5,
                    "prepare_threshold": 0, "options": f"-c search_path={schema}",
                    "application_name": "goodteam"},
        )
        self._setup_lock = Lock()
        self._checkpointer = None

    @contextmanager
    def connection(self):
        with self.pool.connection() as conn:
            yield conn

    def manager_checkpointer(self):
        from langgraph.checkpoint.postgres import PostgresSaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        with self._setup_lock:
            if self._checkpointer is None:
                with self.connection() as conn:
                    # Coordinate setup across backend processes without a
                    # transaction around CREATE INDEX CONCURRENTLY.
                    conn.execute("SELECT pg_advisory_lock(%s)", (742994186,))
                    try:
                        PostgresSaver(conn).setup()
                    finally:
                        conn.execute("SELECT pg_advisory_unlock(%s)", (742994186,))
                self._checkpointer = PostgresSaver(
                    self.pool,
                    serde=JsonPlusSerializer(allowed_json_modules=[], allowed_msgpack_modules=[]),
                )
            return self._checkpointer
