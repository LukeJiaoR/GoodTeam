"""Opt-in embeddings and a rebuildable PG projection of eligible role memory.

The caller supplies current, source-verified events. This index never supplies
memory text, execution state or authority. Browser memory reads do not invoke it.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

import psycopg
from psycopg import sql

from .database import database, workspace_identity
from .providers import ProviderError, ProviderStore, _NoRedirect

BATCH_SIZE = 32
MAX_NEW_EVENTS = 128
MAX_TEXT_CHARS = 1600
MAX_QUERY_CHARS = 4000
MAX_SECONDS = 30
MAX_DIMENSIONS = 16000


def ensure_index(conn):
    """Use an administrator-installed extension; never change PG privileges."""
    try:
        extension = conn.execute("""SELECT n.nspname AS schema FROM pg_extension e
            JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector'""").fetchone()
        if not extension:
            raise ProviderError("embedding_storage_unavailable",
                "Install pgvector and enable the vector extension in the GoodTeam database before enabling memory retrieval.", 409)
        schema = extension["schema"]
        conn.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS memory_embeddings (
            owner_scope TEXT NOT NULL, project_scope TEXT NOT NULL, role_key TEXT NOT NULL,
            model_key TEXT NOT NULL, event_id TEXT NOT NULL, content_sha256 TEXT NOT NULL,
            dimensions INTEGER NOT NULL CHECK (dimensions BETWEEN 1 AND 16000),
            embedding {}.vector NOT NULL,
            PRIMARY KEY (owner_scope, project_scope, role_key, model_key, event_id, content_sha256)
        )""").format(sql.Identifier(schema)))
        return schema
    except psycopg.Error:
        raise ProviderError("embedding_storage_unavailable", "Could not prepare the PostgreSQL memory index.", 503) from None


def _reference(connection):
    return {"provider_id": connection["id"], "provider_revision": connection["revision"],
            "model": connection["model"], "configuration_revision": connection["embedding_revision"],
            "protocol": "embeddings"}


def configured_retriever():
    store = ProviderStore()
    connection = store.embedding_connection()
    return PostgresMemoryRetriever(store, connection) if connection else None


class PostgresMemoryRetriever:
    def __init__(self, store, connection):
        self.store, self.connection = store, connection
        self.reference = _reference(connection)
        self.owner_scope = workspace_identity(store.root)
        self.model_key = sha256(json.dumps({**self.reference, "base_url": connection["base_url"],
            "input_policy": "memory_prefix_1600.v1"}, sort_keys=True).encode()).hexdigest()

    def _require_current(self):
        current = self.store.embedding_connection()
        if current is None or _reference(current) != self.reference:
            raise ProviderError("embedding_configuration_changed", "Memory retrieval settings changed. Try again.", 409)

    def _embed(self, texts, deadline):
        self._require_current()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("embedding_timeout", "Memory indexing reached its time limit. Indexed batches are retained.", 503)
        connection = self.connection
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if connection["api_key"]:
            headers["Authorization"] = "Bearer " + connection["api_key"]
        request = Request(connection["base_url"] + "/embeddings", headers=headers,
            data=json.dumps({"model": connection["model"], "input": texts, "encoding_format": "float"}).encode())
        try:
            with build_opener(_NoRedirect).open(request, timeout=min(10, remaining)) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError
            payload = json.loads(raw)
            data = payload["data"]
            if not isinstance(data, list) or len(data) != len(texts):
                raise ValueError
            vectors = [None] * len(texts)
            for item in data:
                index, vector = item["index"], item["embedding"]
                if (type(index) is not int or not 0 <= index < len(texts) or vectors[index] is not None
                        or not isinstance(vector, list) or not 1 <= len(vector) <= MAX_DIMENSIONS
                        or any(type(value) not in (int, float) or not math.isfinite(value)
                               or abs(value) > 3.4e38 for value in vector)
                        or not any(vector)):
                    raise ValueError
                vectors[index] = [float(value) for value in vector]
            if len({len(vector) for vector in vectors}) != 1:
                raise ValueError
            return vectors
        except HTTPError as exc:
            code = "embedding_auth_failed" if exc.code in {401, 403} else "embedding_unavailable"
            raise ProviderError(code, "Embedding request failed. Check the provider, model and embeddings endpoint.", 503) from None
        except (URLError, TimeoutError, OSError, ValueError, TypeError, KeyError, OverflowError):
            raise ProviderError("embedding_unavailable", "The embedding service was unavailable or returned invalid vectors.", 503) from None

    def rank(self, root, role_key, query, events, *, identify, minimum_score):
        """Only return IDs from this exact eligible source snapshot, never DB text."""
        try:
            return self._rank(root, role_key, query, events, identify=identify, minimum_score=minimum_score)
        except psycopg.Error:
            raise ProviderError("embedding_storage_unavailable", "The PostgreSQL memory index is unavailable.", 503) from None

    def _rank(self, root, role_key, query, events, *, identify, minimum_score):
        deadline = time.monotonic() + MAX_SECONDS
        scope = (self.owner_scope, workspace_identity(root), role_key, self.model_key)
        source = {(identify(event), sha256(str(event["text"]).encode()).hexdigest()): event for event in events}
        with database().connection() as conn:
            schema = ensure_index(conn)
            rows = conn.execute("""SELECT event_id, content_sha256, dimensions FROM memory_embeddings
                WHERE owner_scope=%s AND project_scope=%s AND role_key=%s AND model_key=%s""", scope).fetchall()
        cached = {(row["event_id"], row["content_sha256"]): row["dimensions"] for row in rows}
        missing = [key for key in source if key not in cached]
        # Bound incremental work. A later authorized pickup continues from the
        # retained batches; no scheduler, retries or unbounded background spend.
        pending = missing[:MAX_NEW_EVENTS]
        for start in range(0, len(pending), BATCH_SIZE):
            keys = pending[start:start + BATCH_SIZE]
            vectors = self._embed([str(source[key]["text"])[:MAX_TEXT_CHARS] for key in keys], deadline)
            with database().connection() as conn, conn.transaction():
                for key, vector in zip(keys, vectors):
                    conn.execute(sql.SQL("""INSERT INTO memory_embeddings VALUES
                        (%s,%s,%s,%s,%s,%s,%s,%s::{}.vector)
                        ON CONFLICT (owner_scope,project_scope,role_key,model_key,event_id,content_sha256)
                        DO UPDATE SET dimensions=EXCLUDED.dimensions, embedding=EXCLUDED.embedding""")
                        .format(sql.Identifier(schema)), (*scope, *key, len(vector), json.dumps(vector)))
                    cached[key] = len(vector)
        if len(missing) > MAX_NEW_EVENTS:
            raise ProviderError("embedding_index_incomplete", "Memory indexing is incomplete. Indexed batches are retained.", 503)
        query_vector = self._embed([query[:MAX_QUERY_CHARS]], deadline)[0]
        if any(cached[key] != len(query_vector) for key in source):
            raise ProviderError("embedding_dimensions_changed", "Embedding dimensions changed. Select the matching model again.", 409)
        identifiers = [key[0] for key in source]
        digests = [key[1] for key in source]
        with database().connection() as conn:
            rows = conn.execute(sql.SQL("""SELECT event_id, content_sha256, score FROM (
                SELECT m.event_id, m.content_sha256,
                    1 - (m.embedding OPERATOR({}.<=>) %s::{}.vector) AS score
                FROM memory_embeddings m
                JOIN unnest(%s::text[], %s::text[]) AS eligible(event_id, content_sha256)
                    ON m.event_id=eligible.event_id AND m.content_sha256=eligible.content_sha256
                WHERE owner_scope=%s AND project_scope=%s AND role_key=%s AND model_key=%s
                    AND dimensions=%s
                ) ranked WHERE score >= %s ORDER BY score DESC, event_id""")
                .format(sql.Identifier(schema), sql.Identifier(schema)),
                (json.dumps(query_vector), identifiers, digests, *scope, len(query_vector), minimum_score)).fetchall()
        self._require_current()
        return [(float(row["score"]), source[(row["event_id"], row["content_sha256"])]) for row in rows]
