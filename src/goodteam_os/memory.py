import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from .memory_core.context import (
    MEMORY_CONTEXT_REASONS, PICKUP_EVENT_CHARS, PICKUP_RELEVANT_CHARS, PICKUP_SHORT_CHARS, PICKUP_SUMMARY_CHARS,
    _bounded_context as _bounded_context, _pickup_content_digest as _pickup_content_digest,
    _sha256_value as _sha256_value, summarize_markdown as summarize_markdown,
    LegacyExecutionContextError, empty_pickup, generated_horizon_context, normal_pickup_content, pickup_provenance_valid,
)
from .memory_core.projections import (
    IDENTITY_FIELDS as IDENTITY_FIELDS, MEMORY_HORIZONS as MEMORY_HORIZONS,
    MEMORY_CATEGORIES, MEMORY_RETRIEVAL_POLICY, active_events, browse_result, event_id as legacy_event_id, normal_retrieval_events, retrieval_result,
)
from .memory_storage import (
    MAX_WAL_BYTES, MemoryStorageError, atomic_write, read_bytes, role_lock, serialized_role,
)

try:  # BM25 is a declared dependency; degrade gracefully if unavailable.
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover - exercised only without the optional wheel
    BM25Okapi = None


COSINE_FLOOR = 0.05  # keep weak/noisy matches out while retaining clear semantic hits

_TOKEN_RE = re.compile(r"[^\W_]+")
_HAN_RE = re.compile(r"([\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]+)")


def slug(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(part for part in cleaned.split("-") if part) or "project"


def _wal_prefix_matches(raw: bytes, count: int, digest: str, size: int | None = None) -> bool:
    """Check an available append-only checkpoint, including its exact bytes."""
    if size is not None:
        prefix = raw[:size]
        return (len(prefix) == size and sum(bool(line.strip()) for line in prefix.splitlines()) == count
                and hashlib.sha256(prefix).hexdigest() == digest)
    # Old indexes have count+digest but no byte length. Preserve blank lines
    # while finding that record boundary; never reserialize historical JSON.
    prefix_digest = hashlib.sha256()
    seen = 0
    if count == 0 and hashlib.sha256(b"").hexdigest() == digest:
        return True
    for line in raw.splitlines(keepends=True):
        seen += bool(line.strip())
        # A pre-upgrade WAL may end in a complete JSON record without a final
        # newline. Appending the separator preserves those exact old bytes.
        before_line = prefix_digest.copy()
        prefix_digest.update(line)
        if seen > count:
            return False
        if seen == count and prefix_digest.hexdigest() == digest:
            return True
        if seen == count and line.endswith(b"\n"):
            for ending in (1, 2) if line.endswith(b"\r\n") else (1,):
                candidate = before_line.copy()
                candidate.update(line[:-ending])
                if candidate.hexdigest() == digest:
                    return True
    return False


def tokenize(text: str) -> list[str]:
    # Han text usually has no word-separating spaces. Character/bigram terms
    # allow phrase lookup inside a sentence without a downloaded segmenter.
    # Other Unicode words retain their boundaries, including mixed Latin/Han.
    tokens = []
    for part in _HAN_RE.split(text.casefold()):
        if _HAN_RE.fullmatch(part):
            tokens.extend(part)
            tokens.extend(part[index:index + 2] for index in range(len(part) - 1))
        else:
            tokens.extend(_TOKEN_RE.findall(part))
    return tokens


def _bm25_scores(corpus: list[list[str]], query_tokens: list[str]) -> list[float]:
    """Rank corpus docs against the query. Uses BM25 when available, else a
    deterministic token-overlap score so retrieval still works (and stays
    testable) without the optional wheel."""
    if not query_tokens or not any(corpus):
        # BM25 cannot compute average document length/IDF for a zero-term
        # corpus. Punctuation-only notes and queries are valid empty results.
        return [0.0] * len(corpus)
    if BM25Okapi is not None:
        return list(BM25Okapi(corpus).get_scores(query_tokens))
    wanted = set(query_tokens)
    return [float(sum(1 for token in doc if token in wanted)) for doc in corpus]


def default_embedder():
    """Return a fastembed-backed callable, or None if the optional wheel is
    absent (callers then fall back to BM25). Local/offline; never network-sends
    memory text to a third party."""
    try:  # Optional dependency; mirror BM25 import behavior.
        from fastembed import TextEmbedding
    except ImportError:  # pragma: no cover - exercised only when fastembed extra is absent
        return None

    model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

    def embed(texts):
        return [list(map(float, vector)) for vector in model.embed(list(texts))]

    embed.model_name = "bge-small-en-v1.5"
    embed.dim = 384
    return embed


class ProjectMemoryStore:
    def __init__(self, project_root: Path):
        self.project_root = project_root

    def memory_path(self, project_key: str = "") -> Path:
        return self.project_root / ".goodteam" / "memory" / "project-memory.md"

    def ensure_project_memory(self, project_key: str, project_name: str = "") -> Path:
        path = self.memory_path(project_key)
        if path.is_file():
            return path
        name = project_name.strip() or project_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"""# Project Memory: {name}

## Long-Term Memory

- Stable product, architecture, ownership, deployment, and workflow facts live here.

## Mid-Term Memory

- Active initiatives, decisions in progress, unresolved risks, and current team conventions live here.

## Near-Term Memory

- Current run goals, recent handoffs, next actions, and reviewer feedback live here.

## Update Rules

- Keep secrets out of memory.
- Prefer durable facts with source references.
- Move repeated near-term facts into mid-term or long-term sections when they stabilize.
""",
            encoding="utf-8",
        )
        return path

    def read_project_memory(self, project_key: str) -> dict[str, str]:
        path = self.memory_path(project_key)
        if not path.is_file():
            return {"path": str(path), "content": "", "summary": ""}
        content = path.read_text(encoding="utf-8")
        return {
            "path": str(path),
            "content": content,
            "summary": summarize_markdown(content),
        }


class RoleMemoryStore:
    def __init__(self, project_root: Path, embedder=None):
        self.project_root = project_root
        self.embedder = embedder

    def role_root(self, role_key: str) -> Path:
        return self.project_root / ".goodteam" / "memory" / "roles" / slug(role_key)

    def memory_path(self, role_key: str, horizon: str) -> Path:
        if horizon not in MEMORY_HORIZONS:
            raise ValueError(f"Unknown memory horizon: {horizon}")
        return self.role_root(role_key) / f"{horizon}.md"

    def event_path(self, role_key: str) -> Path:
        return self.role_root(role_key) / "events.jsonl"

    def index_path(self, role_key: str) -> Path:
        return self.role_root(role_key) / "index.json"

    def embeddings_path(self, role_key: str) -> Path:
        return self.role_root(role_key) / "embeddings.jsonl"

    @serialized_role
    def ensure_role_memory(
        self,
        role_key: str,
        role_name: str = "",
        project_name: str = "",
    ) -> dict[str, Path]:
        paths = {horizon: self.memory_path(role_key, horizon) for horizon in MEMORY_HORIZONS}
        for horizon, path in paths.items():
            if read_bytes(path) is not None:
                continue
            atomic_write(path,
                self._default_memory_content(
                    role_key=role_key,
                    role_name=role_name,
                    project_name=project_name,
                    horizon=horizon,
                ).encode("utf-8"),
            )
        return paths

    def ensure_all_role_memories(
        self,
        roles: list[dict[str, str]],
        project_name: str = "",
    ) -> dict[str, dict[str, Path]]:
        return {
            str(role["key"]): self.ensure_role_memory(
                role_key=str(role["key"]),
                role_name=str(role.get("name", role["key"])),
                project_name=project_name,
            )
            for role in roles
        }

    def _empty_role_memory(self, role_key: str) -> dict[str, object]:
        return {
            "role_key": role_key, "source_kind": "legacy_mixed", "machine_context": False,
            "paths": {horizon: str(self.memory_path(role_key, horizon)) for horizon in MEMORY_HORIZONS},
            "content": {horizon: "" for horizon in MEMORY_HORIZONS}, "summary": "",
        }

    def _empty_memory_index(self, role_key: str) -> dict[str, object]:
        empty_hash = hashlib.sha256(b"").hexdigest()
        return {
            "role_key": role_key, "event_count": 0, "events_path": str(self.event_path(role_key)),
            "integrity": {"event_count": 0, "events_sha256": empty_hash},
            "projection_policy": "wal_generated_legacy_preserved",
            "retrieval_policy": MEMORY_RETRIEVAL_POLICY,
            "horizons": {horizon: {
                "path": str(self.memory_path(role_key, horizon)), "source_kind": "wal_generated",
                "event_ids": [], "sha256": empty_hash, "chars": 0, "summary": "",
                "summary_sha256": empty_hash, "updated_at": "",
            } for horizon in MEMORY_HORIZONS},
        }

    def _empty_wal_snapshot(self, role_key: str, *, checkpoint=None):
        if checkpoint is not None and not _wal_prefix_matches(b"", *checkpoint):
            raise MemoryStorageError("memory_history_checkpoint_mismatch")
        return [], hashlib.sha256(b"").hexdigest(), b""

    def _empty_memory_sources(self, role_key: str):
        return self._empty_wal_snapshot(role_key), {horizon: b"" for horizon in MEMORY_HORIZONS}

    def _empty_embedding_rows(self, role_key: str) -> list[dict]:
        return []

    def _empty_legacy_source_matches(self, role_key: str, horizon: str, source: dict) -> bool:
        return source.get("sha256") == hashlib.sha256(b"").hexdigest()

    @serialized_role(create=False, missing="_empty_role_memory")
    def read_role_memory(self, role_key: str) -> dict[str, object]:
        paths = {horizon: self.memory_path(role_key, horizon) for horizon in MEMORY_HORIZONS}
        content = {
            horizon: (read_bytes(path) or b"").decode("utf-8")
            for horizon, path in paths.items()
        }
        combined = "\n\n".join(value for value in content.values() if value)
        return {
            "role_key": role_key,
            "source_kind": "legacy_mixed", "machine_context": False,
            "paths": {horizon: str(path) for horizon, path in paths.items()},
            "content": content,
            "summary": summarize_markdown(combined),
        }

    @serialized_role
    def append_memory_event(
        self,
        role_key: str,
        *,
        horizon: str,
        text: str,
        source: str = "",
        action: str = "add",
        supersedes: str = "",
        category: str = "",
    ) -> dict[str, str]:
        for value in (role_key, horizon, text, source, action, supersedes, category):
            if not isinstance(value, str):
                raise ValueError("Memory event fields must be strings.")
            value.encode("utf-8")
        if horizon not in MEMORY_HORIZONS:
            raise ValueError(f"Unknown memory horizon: {horizon}")
        if category and category not in MEMORY_CATEGORIES:
            raise ValueError(f"Unknown memory category: {category}")
        if action != "add":
            raise ValueError(f"Unsupported memory event action: {action}")
        if not text.strip():
            raise ValueError("Memory event text is required.")
        if not isinstance(supersedes, str):
            raise ValueError("Memory event supersedes must be a string.")

        all_events, _, raw = self._wal_snapshot(role_key)
        # Persist the last verified prefix before any new WAL commit. This
        # independent anchor survives missing/corrupt derived index files.
        self._write_checkpoint(role_key, all_events, raw)
        self.ensure_role_memory(role_key)
        supersedes_value = supersedes.strip()
        for existing in all_events:
            if (
                str(existing.get("role_key", "")) == role_key
                and str(existing.get("horizon", "")) == horizon
                and str(existing.get("action", "")) == action
                and str(existing.get("source", "")) == source
                and str(existing.get("text", "")) == text.strip()
                and str(existing.get("supersedes", "")) == supersedes_value
            ):
                # A previous process may have died after WAL commit and before
                # publication. A retry repairs projections without duplicating.
                self.rebuild_role_memory(role_key)
                return existing
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "role_key": role_key,
            "horizon": horizon,
            "action": action,
            "source": source,
            "text": text.strip(),
        }
        if category:
            event["category"] = category
        if supersedes_value:
            event["supersedes"] = supersedes_value
        separator = b"\n" if raw and not raw.endswith(b"\n") else b""
        committed = raw + separator + (json.dumps(event) + "\n").encode("utf-8")
        atomic_write(self.event_path(role_key), committed, maximum=MAX_WAL_BYTES)
        self._write_checkpoint(role_key, [*all_events, event], committed)
        self.rebuild_role_memory(role_key)
        return event

    def checkpoint_path(self, role_key: str) -> Path:
        return self.role_root(role_key) / "checkpoint.json"

    def _write_checkpoint(self, role_key: str, events: list[dict], raw: bytes) -> None:
        self._write_json(self.checkpoint_path(role_key), {
            "schema_version": "memory_checkpoint.v1", "event_count": len(events),
            "events_sha256": hashlib.sha256(raw).hexdigest(), "wal_bytes": len(raw),
        })

    @serialized_role
    def refresh_memory_index(self, role_key: str) -> dict[str, object]:
        events, digest, raw = self._wal_snapshot(role_key)
        self._write_checkpoint(role_key, events, raw)
        self.ensure_role_memory(role_key)
        active = self._normal_retrieval_events(self._active_events(events))
        horizons = {}
        for horizon in MEMORY_HORIZONS:
            path = self.memory_path(role_key, horizon)
            content = (read_bytes(path) or b"").decode("utf-8")
            selected = [event for event in active if event["horizon"] == horizon][:12]
            generated = _bounded_context("\n".join(f"- {event['text']}" for event in selected), PICKUP_SUMMARY_CHARS)
            summary = _bounded_context(summarize_markdown(generated), PICKUP_SUMMARY_CHARS)
            horizons[horizon] = {
                "path": str(path), "source_kind": "wal_generated",
                "event_ids": [self._event_id(event) for event in selected],
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "chars": len(content), "summary": summary,
                "summary_sha256": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        index = {
            "role_key": role_key, "event_count": len(events),
            "events_path": str(self.event_path(role_key)),
            "integrity": {"event_count": len(events), "events_sha256": digest},
            "horizons": horizons,
            "projection_policy": "wal_generated_legacy_preserved",
            "retrieval_policy": MEMORY_RETRIEVAL_POLICY,
        }
        self._write_json(self.index_path(role_key), index)
        return index

    @serialized_role(create=False, missing="_empty_memory_index")
    def read_memory_index(self, role_key: str) -> dict[str, object]:
        # Validate before attempting repair: losing an index must not erase
        # checkpoint evidence or make a truncated WAL authoritative.
        self._wal_snapshot(role_key)
        try:
            raw = read_bytes(self.index_path(role_key))
            index = json.loads(raw) if raw is not None else None
        except (UnicodeError, ValueError):
            index = None
        if not isinstance(index, dict) or not self._projection_matches_wal(role_key, index):
            return self.rebuild_role_memory(role_key)
        return index

    def event_texts(self, role_key: str) -> set[str]:
        return {str(event.get("text", "")) for event in self._read_events(role_key)}

    def find_event(self, event_id: str) -> dict | None:
        memory_root = self.project_root / ".goodteam" / "memory" / "roles"
        if not memory_root.is_dir():
            return None
        for role_dir in sorted(path for path in memory_root.iterdir() if path.is_dir()):
            role_key = role_dir.name
            for event in self._read_events(role_key):
                if self._event_id(event) == event_id:
                    return event
        return None

    def event_supersede_info(self, event_id: str) -> dict[str, object] | None:
        memory_root = self.project_root / ".goodteam" / "memory" / "roles"
        if not memory_root.is_dir():
            return None
        for role_dir in sorted(path for path in memory_root.iterdir() if path.is_dir()):
            events = self._read_events(role_dir.name)
            event_ids = [self._event_id(event) for event in events]
            if event_id not in event_ids:
                continue
            event_index = event_ids.index(event_id)
            active_events = self._active_events(events)
            active_ids = {self._event_id(event) for event in active_events}
            superseded_by = [
                superseding_id
                for index, (superseding_id, event) in enumerate(zip(event_ids, events))
                if index > event_index
                and superseding_id in active_ids
                and str(event.get("supersedes", "")).strip() == event_id
            ]
            return {
                "supersedes": str(events[event_index].get("supersedes", "")).strip(),
                "superseded_by": superseded_by,
                "is_superseded": event_id not in active_ids,
            }
        return None

    def retrieve_relevant_events(
        self,
        role_key: str,
        query: str,
        *,
        top_k: int = 5,
        horizons: tuple[str, ...] | None = None,
    ) -> list[dict[str, object]]:
        """Return top-K events by lexical or explicitly configured semantic relevance.

        Multi-character Han queries rank lexical term coverage before a bounded
        BM25 tie-break; other lexical queries retain BM25 ranking. Scores are
        retrieval ordering signals, not confidence or proof the memory was used.

        Each result carries a stable ``mem:<id>`` handle so artifacts can cite
        the exact event a fact came from (grounded citations, M8)."""
        if not query.strip():
            return []
        query_tokens = tokenize(query)
        events, digest, _ = self._wal_snapshot(role_key)
        events = self._active_events(events)
        events = self._normal_retrieval_events(events)
        if horizons:
            events = [event for event in events if event.get("horizon") in horizons]
        if not events:
            return []
        ranked = self._rank_events(role_key, query, query_tokens=query_tokens, events=events)
        if not self._wal_snapshot_matches(role_key, digest):
            raise MemoryStorageError("memory_revision_changed_retry")
        results: list[dict[str, object]] = []
        for score, event in ranked:
            results.append(self._retrieval_result(float(score), event))
            if len(results) >= top_k:
                break
        return results

    def list_events(self, role_key, *, query="", limit=50, include_execution=False) -> list[dict]:
        limit_value = min(max(int(limit), 0), 200)
        if limit_value == 0:
            return []

        events, digest, _ = self._wal_snapshot(str(role_key))
        if not events:
            return []

        active_ids = {self._event_id(event) for event in self._active_events(events)}
        if not include_execution:
            events = self._normal_retrieval_events(events)
        query_text = str(query).strip()
        if query_text:
            query_tokens = tokenize(query_text)
            ranked = self._rank_events(str(role_key), query_text, query_tokens=query_tokens, events=events)
            selected = [event for _, event in ranked[:limit_value]]
        else:
            selected = sorted(events, key=lambda event: str(event.get("timestamp", "")), reverse=True)[:limit_value]

        if not self._wal_snapshot_matches(str(role_key), digest):
            raise MemoryStorageError("memory_revision_changed_retry")
        return [self._browse_result(event, active_ids=active_ids) for event in selected]

    def _wal_bytes(self, role_key: str) -> bytes:
        return read_bytes(self.event_path(role_key), maximum=MAX_WAL_BYTES) or b""

    def _has_wal_history(self, role_key: str) -> bool:
        """Use old artifacts only as loss evidence, never as fallback context.

        Legacy-only roles may never have had a WAL. A prior nonempty index or
        generated event section means an empty/missing WAL cannot prove that
        history (including corrections) is absent. Unreadable evidence also
        cannot authorize that downgrade.
        """
        index_path = self.index_path(role_key)
        try:
            raw = read_bytes(index_path)
            if raw is not None:
                index = json.loads(raw)
                integrity = index.get("integrity") if isinstance(index, dict) else None
                if (not isinstance(integrity, dict) or type(index.get("event_count")) is not int
                        or index["event_count"] != 0 or type(integrity.get("event_count")) is not int
                        or integrity["event_count"] != 0
                        or integrity.get("events_sha256") != hashlib.sha256(b"").hexdigest()):
                    return True
            for horizon in MEMORY_HORIZONS:
                path = self.memory_path(role_key, horizon)
                raw = read_bytes(path)
                if raw is not None and any(line.strip() == "## Memory Events"
                                         for line in raw.decode("utf-8").splitlines()):
                    return True
        except (OSError, UnicodeError, ValueError):
            return True
        return False

    def _wal_checkpoint(self, role_key: str) -> tuple[int, str] | None:
        try:
            index = json.loads(read_bytes(self.index_path(role_key)) or b"null")
        except (OSError, UnicodeError, ValueError):
            return None
        integrity = index.get("integrity") if isinstance(index, dict) else None
        if not isinstance(integrity, dict):
            return None
        count, digest = integrity.get("event_count"), integrity.get("events_sha256")
        if (type(count) is not int or count < 0 or type(index.get("event_count")) is not int
                or index["event_count"] != count
                or not _sha256_value(digest)):
            return None
        return count, digest

    def _storage_checkpoint(self, role_key: str) -> tuple[int, str, int] | None:
        raw = read_bytes(self.checkpoint_path(role_key))
        if raw is None:
            return None
        try:
            checkpoint = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise MemoryStorageError("memory_checkpoint_unreadable") from exc
        if (not isinstance(checkpoint, dict) or checkpoint.get("schema_version") != "memory_checkpoint.v1"
                or type(checkpoint.get("event_count")) is not int or checkpoint["event_count"] < 0
                or not _sha256_value(checkpoint.get("events_sha256"))
                or type(checkpoint.get("wal_bytes")) is not int or checkpoint["wal_bytes"] < 0):
            raise MemoryStorageError("memory_checkpoint_unreadable")
        return checkpoint["event_count"], checkpoint["events_sha256"], checkpoint["wal_bytes"]

    @serialized_role(create=False, missing="_empty_wal_snapshot")
    def _wal_snapshot(self, role_key: str, *, checkpoint: tuple[int, str, int] | None = None) -> tuple[list[dict[str, str]], str, bytes]:
        raw = self._wal_bytes(role_key)
        events = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        if not events and self._has_wal_history(role_key):
            raise ValueError("Memory WAL history is unavailable")
        for event in events:
            if (not isinstance(event, dict)
                    or any(not isinstance(event.get(key), str) for key in IDENTITY_FIELDS)
                    or slug(event["role_key"]) != slug(role_key)
                    or event["horizon"] not in MEMORY_HORIZONS or event["action"] != "add"
                    or not isinstance(event.get("supersedes", ""), str)):
                raise ValueError("Invalid memory WAL event")
            for value in event.values():
                if isinstance(value, str):
                    value.encode("utf-8")
        # Available checkpoints detect truncation/rewrites, not an external
        # rollback of the WAL and every piece of historical evidence together.
        durable = self._storage_checkpoint(role_key)
        known = self._wal_checkpoint(role_key)
        if raw.strip() and durable is None and known is None:
            raise MemoryStorageError("memory_history_unverifiable_restore_checkpoint_or_confirm_import")
        if durable is not None and not _wal_prefix_matches(raw, *durable):
            raise MemoryStorageError("memory_history_checkpoint_mismatch")
        if known is not None and not _wal_prefix_matches(raw, *known):
            raise ValueError("Memory WAL does not preserve its known history")
        if checkpoint is not None and not _wal_prefix_matches(raw, *checkpoint):
            raise ValueError("Memory WAL does not preserve the captured history")
        return events, hashlib.sha256(raw).hexdigest(), raw

    def _wal_snapshot_matches(self, role_key: str, digest: str) -> bool:
        _, current, _ = self._wal_snapshot(role_key)
        return current == digest

    def _empty_pickup(self, role_key: str, reason: str) -> dict[str, object]:
        return empty_pickup(role_key, reason)

    @serialized_role(create=False, missing="_empty_memory_sources")
    def _capture_memory_sources(self, role_key: str):
        snapshot = self._wal_snapshot(role_key)
        legacy = {}
        for horizon in MEMORY_HORIZONS:
            try:
                legacy[horizon] = read_bytes(self.memory_path(role_key, horizon)) or b""
            except (OSError, ValueError):
                legacy[horizon] = None
        return snapshot, legacy

    def build_pickup_packet(
        self,
        role_key: str,
        *,
        max_short_chars: int = PICKUP_SHORT_CHARS,
        query: str = "",
        top_k: int = 5,
    ) -> dict[str, object]:
        """Derive bounded machine context without rewriting mixed human notes.

        Generated text depends on one WAL snapshot. Legacy markdown has no
        event-level provenance, so any correction makes it unsafe to inject.
        Files are captured under the role lock; scoring runs outside it and
        the captured revision is checked again before context is returned.
        """
        try:
            (events, digest, wal_bytes), legacy_bytes = self._capture_memory_sources(role_key)
        except MemoryStorageError as exc:
            reason = "history_unverifiable" if str(exc).startswith(("memory_history_unverifiable", "memory_checkpoint_unreadable")) else "wal_unreadable"
            return self._empty_pickup(role_key, reason)
        except (OSError, UnicodeError, ValueError):
            return self._empty_pickup(role_key, "wal_unreadable")
        active = self._normal_retrieval_events(self._active_events(events))
        has_correction = any(event.get("supersedes", "").strip() for event in events)
        reasons = ["legacy_mixed_after_correction"] if has_correction else []
        legacy_sources = {}
        generated_sources = {}
        summaries = {}
        short_content = ""
        short_limit = min(PICKUP_SHORT_CHARS, max(0, int(max_short_chars)))
        for horizon in MEMORY_HORIZONS:
            horizon_events = [event for event in active if event["horizon"] == horizon]
            generated, generated_sources[horizon] = generated_horizon_context(
                horizon, horizon_events, short_limit=short_limit, identify=self._event_id,
            )
            path = self.memory_path(role_key, horizon)
            legacy = ""
            source = {"source_kind": "legacy_mixed", "path": str(path.relative_to(self.project_root)),
                      "status": "withheld_after_correction" if has_correction else "compatibility_unverified"}
            try:
                raw = legacy_bytes[horizon]
                if raw is None:
                    raise ValueError("Legacy memory source unavailable")
                source["sha256"] = hashlib.sha256(raw).hexdigest()
                if not has_correction:
                    content = self._normal_pickup_content(
                        raw.decode("utf-8"), events=[event for event in events if event["horizon"] == horizon],
                    )
                    legacy = content if horizon == "short-term" else summarize_markdown(content)
            except LegacyExecutionContextError:
                source["status"] = "withheld_execution_unverifiable"
                reasons.append("legacy_execution_unverifiable")
            except (OSError, UnicodeError, ValueError):
                source["status"] = "unavailable"
                reasons.append("legacy_source_unreadable")
            legacy_sources[horizon] = source
            combined = generated + ("\n\nLegacy notes (unclassified):\n" + legacy if legacy else "")
            if horizon == "short-term":
                short_content = _bounded_context(combined, short_limit, "[Short-term memory truncated]")
            else:
                summaries[horizon] = _bounded_context(combined, PICKUP_SUMMARY_CHARS)
        packet = {
            "role_key": role_key,
            "paths": {horizon: str(self.memory_path(role_key, horizon)) for horizon in MEMORY_HORIZONS},
            "event_count": len(events), "short_term": short_content, "summaries": summaries,
            "context_status": "partial" if reasons else "available",
            "context_reasons": sorted(set(reasons)),
            "provenance": {"schema_version": "memory_pickup.v1", "retrieval_policy": MEMORY_RETRIEVAL_POLICY,
                           "wal_sha256": digest,
                           "wal_event_count": len(events), "wal_bytes": len(wal_bytes),
                           "generated_sources": generated_sources, "legacy_sources": legacy_sources},
        }
        if query.strip():
            try:
                ranked = self._rank_events(role_key, query, query_tokens=tokenize(query), events=active) if active else []
            except (OSError, UnicodeError, ValueError, TypeError):
                ranked = []
                packet["context_status"] = "partial"
                packet["context_reasons"] = sorted(set(reasons + ["retrieval_unavailable"]))
            relevant = []
            remaining = PICKUP_RELEVANT_CHARS
            for score, event in ranked[:max(0, top_k)]:
                if remaining <= 0:
                    break
                result = self._retrieval_result(score, event)
                result["text"] = _bounded_context(str(result["text"]), min(PICKUP_EVENT_CHARS, remaining))
                remaining -= len(result["text"])
                relevant.append(result)
            packet["relevant_events"] = relevant
        try:
            with role_lock(self.role_root(role_key), create=False) as present:
                if not present:
                    empty_digest = hashlib.sha256(b"").hexdigest()
                    if digest != empty_digest or any(source.get("sha256") != empty_digest for source in legacy_sources.values()):
                        return self._empty_pickup(role_key, "source_unreadable")
                else:
                    if not self._wal_snapshot_matches(role_key, digest):
                        return self._empty_pickup(role_key, "wal_changed_during_read")
                    if any(source.get("status") == "compatibility_unverified"
                           and not self._legacy_source_matches(role_key, horizon, source)
                           for horizon, source in legacy_sources.items()):
                        return self._empty_pickup(role_key, "legacy_changed_during_read")
        except (OSError, ValueError):
            return self._empty_pickup(role_key, "source_unreadable")
        packet["provenance"]["content_sha256"] = _pickup_content_digest(packet)
        return packet

    @serialized_role(create=False, missing="_empty_legacy_source_matches")
    def _legacy_source_matches(self, role_key: str, horizon: str, source: dict) -> bool:
        # Derive the local path; never follow a path supplied by a saved packet.
        path = self.memory_path(role_key, horizon)
        raw = read_bytes(path) or b""
        return source.get("sha256") == hashlib.sha256(raw).hexdigest()

    def _pickup_provenance_valid(self, role_key: str, packet: dict) -> bool:
        legacy_paths = {horizon: str(self.memory_path(role_key, horizon).relative_to(self.project_root))
                        for horizon in MEMORY_HORIZONS}
        return pickup_provenance_valid(packet, legacy_paths)

    def validate_pickup_packet(self, packet: object) -> dict[str, object]:
        """Filter a frozen packet; never retrieve or add a newer fact at use time."""
        if not isinstance(packet, dict):
            return self._empty_pickup("", "pickup_unverifiable")
        role_key = packet.get("role_key")
        if not isinstance(role_key, str) or not role_key:
            return self._empty_pickup("", "pickup_unverifiable")
        provenance = packet.get("provenance")
        checkpoint = None
        if (isinstance(provenance, dict) and _sha256_value(provenance.get("wal_sha256"))
                and type(provenance.get("wal_event_count")) is int and provenance["wal_event_count"] >= 0
                and type(provenance.get("wal_bytes")) is int and provenance["wal_bytes"] >= 0):
            checkpoint = (provenance["wal_event_count"], provenance["wal_sha256"], provenance["wal_bytes"])
        try:
            events, digest, _ = self._wal_snapshot(role_key, checkpoint=checkpoint)
        except MemoryStorageError as exc:
            reason = "history_unverifiable" if str(exc).startswith(("memory_history_unverifiable", "memory_checkpoint_unreadable")) else "wal_unreadable"
            return self._empty_pickup(role_key, reason)
        except (OSError, UnicodeError, ValueError):
            return self._empty_pickup(role_key, "wal_unreadable")
        active = {self._event_id(event): event
                  for event in self._normal_retrieval_events(self._active_events(events))}
        result = dict(packet)
        original = packet.get("relevant_events", [])
        relevant = original if isinstance(original, list) else []
        retained = []
        remaining = PICKUP_RELEVANT_CHARS
        for event in relevant:
            if not isinstance(event, dict) or remaining <= 0:
                continue
            event_id = event.get("id")
            source = active.get(event_id) if isinstance(event_id, str) else None
            text = event.get("text")
            if not source or not isinstance(text, str) or not text:
                continue
            # An ID alone does not attest a saved body. Accept an exact event
            # text or the bounded prefix emitted at capture, never substitute
            # the current event's text into a historical packet.
            if text != source["text"] and text != _bounded_context(source["text"], len(text)):
                continue
            if any(key in event and event[key] != source[key] for key in ("horizon", "source", "timestamp")):
                continue
            retained.append({**event, "text": _bounded_context(text, min(PICKUP_EVENT_CHARS, remaining))})
            remaining -= len(retained[-1]["text"])
        result["relevant_events"] = retained
        original_reasons = packet.get("context_reasons", [])
        reasons = [reason for reason in original_reasons if isinstance(reason, str) and reason in MEMORY_CONTEXT_REASONS] if isinstance(original_reasons, list) else []
        invalidate = False
        if isinstance(provenance, dict) and provenance.get("retrieval_policy") != MEMORY_RETRIEVAL_POLICY:
            reasons.append("pickup_policy_changed")
        if not self._pickup_provenance_valid(role_key, packet):
            reasons.append("pickup_unverifiable")
            invalidate = True
        elif provenance.get("wal_sha256") != digest:
            reasons.append("wal_changed_after_capture")
            invalidate = True
        else:
            try:
                sources = provenance.get("legacy_sources")
                if not isinstance(sources, dict) or set(sources) != set(MEMORY_HORIZONS):
                    raise ValueError("Invalid legacy source manifest")
                if any(not isinstance(source, dict) for source in sources.values()):
                    raise ValueError("Invalid legacy source manifest")
                if any(source.get("status") == "compatibility_unverified"
                       and not self._legacy_source_matches(role_key, horizon, source)
                       for horizon, source in sources.items()):
                    reasons.append("legacy_changed_after_capture")
                    invalidate = True
            except (OSError, ValueError):
                reasons.append("legacy_source_unreadable")
                invalidate = True
        if len(retained) != len(relevant):
            reasons.append("events_no_longer_active")
            invalidate = True
        if invalidate:
            result["summaries"] = {"long-term": "", "mid-term": ""}
            result["short_term"] = ""
        result["context_reasons"] = sorted(set(reasons))
        result["context_status"] = ("unavailable" if packet.get("context_status") == "unavailable"
                                    else "partial" if reasons else "available")
        try:
            if not self._wal_snapshot_matches(role_key, digest):
                return self._empty_pickup(role_key, "wal_changed_during_read")
        except (OSError, ValueError):
            return self._empty_pickup(role_key, "source_unreadable")
        return result

    @serialized_role
    def rebuild_role_memory(
        self,
        role_key: str,
        role_name: str = "",
        project_name: str = "",
    ) -> dict[str, object]:
        """Repair generated views while retaining mixed legacy/human bytes.

        Old event text may remain in markdown for audit. Machine summaries and
        pickup context depend only on active WAL events after a correction.
        """
        events, _, raw = self._wal_snapshot(role_key)
        self._write_checkpoint(role_key, events, raw)
        self.ensure_role_memory(role_key, role_name, project_name)
        self._repair_memory_projection(role_key, self._active_events(events))
        return self.refresh_memory_index(role_key)

    def _default_memory_content(
        self,
        *,
        role_key: str,
        role_name: str,
        project_name: str,
        horizon: str,
    ) -> str:
        display_name = role_name.strip() or role_key
        project_label = project_name.strip() or "Configured Project"
        horizon_label = {
            "long-term": "Long-Term",
            "mid-term": "Mid-Term",
            "short-term": "Short-Term",
        }[horizon]
        guidance = {
            "long-term": "Stable role-specific project facts, contracts, ownership rules, and durable conventions live here.",
            "mid-term": "Active initiatives, in-progress decisions, current risks, and role conventions live here.",
            "short-term": "Current interrupted work, pickup notes, next actions, blockers, and recent handoffs live here.",
        }[horizon]
        return f"""# {display_name} {horizon_label} Memory

Project: {project_label}
Role: `{role_key}`

## Purpose

- {guidance}

## Pickup Notes

- No role-specific notes recorded yet.

## Update Rules

- Keep secrets out.
- Prefer dated bullets with source references.
- Move stable facts upward from short-term to mid-term or long-term when they stop being temporary.
"""

    @serialized_role
    def _append_event_to_memory(self, role_key: str, event: dict[str, str]) -> None:
        self._repair_memory_projection(role_key, [event])

    def _repair_memory_projection(self, role_key: str, events: list[dict[str, str]]) -> None:
        # Batch replay once per horizon, rather than rewrite the entire markdown
        # for every WAL event. The lock is owned by append/rebuild callers.
        for horizon in MEMORY_HORIZONS:
            path = self.memory_path(role_key, horizon)
            raw = read_bytes(path)
            if raw is None:
                raw = self._default_memory_content(role_key=role_key, role_name=role_key,
                                                   project_name="", horizon=horizon).encode("utf-8")
            content = raw.decode("utf-8")
            section = content.split("\n## Memory Events\n", 1)[-1]
            lines = set(section.splitlines())
            rendered = "\n" + section + ("" if section.endswith("\n") else "\n")
            additions = []
            for event in events:
                if event["horizon"] != horizon:
                    continue
                source = f" Source: `{event['source']}`." if event.get("source") else ""
                entry = f"- {event['text']}{source}"
                if entry in lines or ("\n" in entry and "\n" + entry + "\n" in rendered):
                    continue
                additions.append(entry + "\n")
                lines.add(entry)
            if not additions:
                continue
            separator = "" if not content or content.endswith("\n") else "\n"
            heading = "" if "## Memory Events\n" in content else "\n## Memory Events\n\n"
            atomic_write(path, raw + (separator + heading + "".join(additions)).encode("utf-8"))

    def _event_id(self, event: dict[str, object]) -> str:
        return legacy_event_id(event)

    def _active_events(self, events: list[dict[str, str]]) -> list[dict[str, str]]:
        return active_events(events, identify=self._event_id)

    def _normal_retrieval_events(self, events: list[dict[str, str]]) -> list[dict[str, str]]:
        return normal_retrieval_events(events)

    def _normal_pickup_content(self, content: str, *, events: list[dict]) -> str:
        return normal_pickup_content(content, events=events)

    def _retrieval_result(self, score: float, event: dict[str, object]) -> dict[str, object]:
        return retrieval_result(score, event, identifier=self._event_id(event))

    def _browse_result(self, event: dict[str, object], *, active_ids: set[str]) -> dict[str, object]:
        return browse_result(event, active_ids=active_ids, identifier=self._event_id(event))

    def _rank_events(
        self,
        role_key: str,
        query: str,
        *,
        query_tokens: list[str],
        events: list[dict[str, str]],
    ) -> list[tuple[float, dict[str, str]]]:
        vectors = self._embed_events(role_key, events)
        if vectors is not None:
            query_vector = self.embedder([query])[0]
            scored = [(self._cosine(query_vector, vector), event) for vector, event in zip(vectors, events)]
            candidates = [(score, event) for score, event in scored if score >= COSINE_FLOOR]
            return sorted(candidates, key=lambda pair: pair[0], reverse=True)

        han_phrase_query = any(len(token) > 1 and _HAN_RE.fullmatch(token) for token in query_tokens)
        if han_phrase_query:
            # A shared character such as 库 must not make 库房 relevant to
            # 数据库迁移. Keep single-character lookup for a one-character query.
            query_tokens = [token for token in query_tokens
                            if len(token) != 1 or not _HAN_RE.fullmatch(token)]
        corpus = [tokenize(str(event.get("text", ""))) for event in events]
        scores = _bm25_scores(corpus, query_tokens)
        wanted = set(query_tokens)
        # Gate relevance on token overlap (BM25 gives terms that appear in every
        # document a zero/negative IDF, which would drop legitimate matches in a
        # tiny corpus); use the BM25 score only to rank the overlapping events.
        candidates = []
        for score, event, doc in zip(scores, events, corpus):
            overlap = wanted.intersection(doc)
            if not overlap:
                continue
            if han_phrase_query:
                # Phrase coverage precedes BM25's small-corpus negative-IDF
                # length bias. The fractional score is only a bounded tie-break,
                # not confidence; it cannot outrank another matched query term.
                score = len(overlap) + 0.5 + score / (2 * (1 + abs(score)))
            candidates.append((score, event))
        return sorted(candidates, key=lambda pair: pair[0], reverse=True)

    def _embed_events(self, role_key: str, events: list[dict[str, str]]) -> list[list[float]] | None:
        if self.embedder is None:
            return None

        model_name = str(getattr(self.embedder, "model_name", "embedder"))
        embed_dim = getattr(self.embedder, "dim", None)
        cache_path = self.embeddings_path(role_key)
        cached: dict[str, list[float]] = {}
        for row in self._embedding_rows(role_key):
            if row.get("model") == model_name:
                cached[str(row["id"])] = row["vector"]

        event_ids = [self._event_id(event) for event in events]
        missing_ids: list[str] = []
        missing_texts: list[str] = []
        for event_id, event in zip(event_ids, events):
            if event_id in cached:
                continue
            missing_ids.append(event_id)
            missing_texts.append(str(event.get("text", "")))

        if missing_texts:
            vectors = self.embedder(missing_texts)
            if len(vectors) != len(missing_ids):
                raise ValueError("Embedder returned a vector count that does not match inputs.")
            additions = []
            for event_id, vector in zip(missing_ids, vectors):
                normalized = [float(value) for value in vector]
                cached[event_id] = normalized
                additions.append({"id": event_id, "model": model_name,
                                  "dim": int(embed_dim) if embed_dim is not None else len(normalized),
                                  "vector": normalized})
            # Merge only after model work has completed, under the same role
            # lock as all file publication. This cache never authorizes events.
            with role_lock(self.role_root(role_key)):
                rows = {(row["model"], row["id"]): row for row in self._embedding_rows(role_key)}
                rows.update({(row["model"], row["id"]): row for row in additions})
                raw = "".join(json.dumps(row) + "\n" for row in rows.values()).encode("utf-8")
                atomic_write(cache_path, raw, maximum=MAX_WAL_BYTES)

        return [cached[event_id] for event_id in event_ids]

    @serialized_role(create=False, missing="_empty_embedding_rows")
    def _embedding_rows(self, role_key: str) -> list[dict]:
        raw = read_bytes(self.embeddings_path(role_key), maximum=MAX_WAL_BYTES) or b""
        try:
            rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
            valid = []
            for row in rows:
                if (isinstance(row, dict) and isinstance(row.get("id"), str)
                        and isinstance(row.get("model"), str) and isinstance(row.get("vector"), list)):
                    valid.append({**row, "vector": [float(value) for value in row["vector"]]})
            return valid
        except (UnicodeError, ValueError, TypeError):
            return []  # Derived cache only; valid WAL remains authoritative.

    def _cosine(self, left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        dot = sum(float(a) * float(b) for a, b in zip(left, right))
        left_norm = sum(float(a) * float(a) for a in left) ** 0.5
        right_norm = sum(float(b) * float(b) for b in right) ** 0.5
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return dot / (left_norm * right_norm)

    def _read_events(self, role_key: str) -> list[dict[str, str]]:
        return self._wal_snapshot(role_key)[0]

    def _wal_integrity(self, role_key: str, *, events=None) -> tuple[int, str]:
        snapshot, digest, _ = self._wal_snapshot(role_key)
        return len(snapshot), digest

    def _projection_matches_wal(self, role_key: str, index: dict[str, object]) -> bool:
        events, digest, _ = self._wal_snapshot(role_key)
        integrity = index.get("integrity")
        if (not isinstance(integrity, dict) or integrity.get("event_count") != len(events)
                or index.get("event_count") != len(events) or integrity.get("events_sha256") != digest
                or index.get("projection_policy") != "wal_generated_legacy_preserved"
                or index.get("retrieval_policy") != MEMORY_RETRIEVAL_POLICY):
            return False
        horizons = index.get("horizons")
        if not isinstance(horizons, dict):
            return False
        for horizon in MEMORY_HORIZONS:
            source = horizons.get(horizon)
            raw = read_bytes(self.memory_path(role_key, horizon)) or b""
            if (not isinstance(source, dict) or source.get("sha256") != hashlib.sha256(raw).hexdigest()
                    or source.get("source_kind") != "wal_generated" or not isinstance(source.get("summary"), str)
                    or source.get("summary_sha256") != hashlib.sha256(source["summary"].encode("utf-8")).hexdigest()):
                return False
        return True

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        atomic_write(path, json.dumps(payload, indent=2).encode("utf-8"))
