import hashlib
import json
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

try:  # BM25 is a declared dependency; degrade gracefully if unavailable.
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover - exercised only without the optional wheel
    BM25Okapi = None


MEMORY_HORIZONS = ("long-term", "mid-term", "short-term")
IDENTITY_FIELDS = ("timestamp", "role_key", "horizon", "action", "source", "text")
# Prompt-facing characters are capped independently of WAL size. Valid UTF-8
# takes at most four bytes per character, so these also bound encoded bytes.
PICKUP_SUMMARY_CHARS = 600
PICKUP_SHORT_CHARS = 1600
PICKUP_EVENT_CHARS = 1600
PICKUP_RELEVANT_CHARS = 4000
MEMORY_CONTEXT_REASONS = frozenset({
    "wal_unreadable", "wal_changed_during_read", "wal_changed_after_capture",
    "legacy_mixed_after_correction", "legacy_source_unreadable", "legacy_changed_during_read",
    "legacy_changed_after_capture", "source_unreadable", "pickup_unverifiable",
    "events_no_longer_active", "retrieval_unavailable",
})
COSINE_FLOOR = 0.05  # keep weak/noisy matches out while retaining clear semantic hits

_TOKEN_RE = re.compile(r"[^\W_]+")
_HAN_RE = re.compile(r"([\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]+)")


def slug(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(part for part in cleaned.split("-") if part) or "project"


def summarize_markdown(content: str) -> str:
    headings = [
        line.strip("# ").strip()
        for line in content.splitlines()
        if line.startswith("#") and line.strip("# ").strip()
    ][:8]
    bullets = [
        line.strip()[2:].strip()
        for line in content.splitlines()
        if line.strip().startswith("- ")
    ][:12]
    parts = []
    if headings:
        parts.append("Headings: " + "; ".join(headings))
    if bullets:
        parts.append("Signals: " + "; ".join(bullets))
    return " | ".join(parts) if parts else content.strip()[:600]



def _bounded_context(text: str, limit: int, marker: str = "[Memory context truncated]") -> str:
    """Hard character/UTF-8 bounds, including the marker, for prompt text."""
    if len(text) <= limit and len(text.encode("utf-8")) <= limit * 4:
        return text
    suffix = "\n" + marker
    return text[:max(0, limit - len(suffix))] + suffix[:limit]


def _pickup_content_digest(packet: dict[str, object]) -> str:
    content = {key: packet.get(key) for key in ("role_key", "short_term", "summaries", "relevant_events", "event_count", "context_status", "context_reasons")}
    provenance = packet.get("provenance")
    content["provenance"] = {key: value for key, value in provenance.items() if key != "content_sha256"} if isinstance(provenance, dict) else provenance
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


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
        prefix_digest.update(line)
        if seen > count:
            return False
        if seen == count and prefix_digest.hexdigest() == digest:
            return True
    return False


def _sha256_value(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


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

    def ensure_role_memory(
        self,
        role_key: str,
        role_name: str = "",
        project_name: str = "",
    ) -> dict[str, Path]:
        paths = {horizon: self.memory_path(role_key, horizon) for horizon in MEMORY_HORIZONS}
        for horizon, path in paths.items():
            if path.is_file():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                self._default_memory_content(
                    role_key=role_key,
                    role_name=role_name,
                    project_name=project_name,
                    horizon=horizon,
                ),
                encoding="utf-8",
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

    def read_role_memory(self, role_key: str) -> dict[str, object]:
        paths = {horizon: self.memory_path(role_key, horizon) for horizon in MEMORY_HORIZONS}
        content = {
            horizon: path.read_text(encoding="utf-8") if path.is_file() else ""
            for horizon, path in paths.items()
        }
        combined = "\n\n".join(value for value in content.values() if value)
        return {
            "role_key": role_key,
            "paths": {horizon: str(path) for horizon, path in paths.items()},
            "content": content,
            "summary": summarize_markdown(combined),
        }

    def append_memory_event(
        self,
        role_key: str,
        *,
        horizon: str,
        text: str,
        source: str = "",
        action: str = "add",
        supersedes: str = "",
    ) -> dict[str, str]:
        if horizon not in MEMORY_HORIZONS:
            raise ValueError(f"Unknown memory horizon: {horizon}")
        if action != "add":
            raise ValueError(f"Unsupported memory event action: {action}")
        if not text.strip():
            raise ValueError("Memory event text is required.")
        if not isinstance(supersedes, str):
            raise ValueError("Memory event supersedes must be a string.")

        self.ensure_role_memory(role_key)
        all_events = self._read_events(role_key)
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
                return existing
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "role_key": role_key,
            "horizon": horizon,
            "action": action,
            "source": source,
            "text": text.strip(),
        }
        if supersedes_value:
            event["supersedes"] = supersedes_value
        event_path = self.event_path(role_key)
        event_path.parent.mkdir(parents=True, exist_ok=True)
        with event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
        self._append_event_to_memory(role_key, event)
        self.refresh_memory_index(role_key)
        return event

    def refresh_memory_index(self, role_key: str) -> dict[str, object]:
        self.ensure_role_memory(role_key)
        events = self._read_events(role_key)
        event_count, events_sha256 = self._wal_integrity(role_key, events=events)
        horizons = {}
        for horizon in MEMORY_HORIZONS:
            path = self.memory_path(role_key, horizon)
            content = path.read_text(encoding="utf-8") if path.is_file() else ""
            horizons[horizon] = {
                "path": str(path),
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "chars": len(content),
                "summary": summarize_markdown(content),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        index = {
            "role_key": role_key,
            "event_count": len(events),
            "events_path": str(self.event_path(role_key)),
            "integrity": {
                "event_count": event_count,
                "events_sha256": events_sha256,
            },
            "horizons": horizons,
        }
        self._write_json(self.index_path(role_key), index)
        return index

    def read_memory_index(self, role_key: str) -> dict[str, object]:
        path = self.index_path(role_key)
        if not path.is_file():
            return self.rebuild_role_memory(role_key)
        index = json.loads(path.read_text(encoding="utf-8"))
        if not self._projection_matches_wal(role_key, index):
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
        events = self._read_events(role_key)
        events = self._active_events(events)
        events = self._normal_retrieval_events(events)
        if horizons:
            events = [event for event in events if event.get("horizon") in horizons]
        if not events:
            return []
        ranked = self._rank_events(role_key, query, query_tokens=query_tokens, events=events)
        results: list[dict[str, object]] = []
        for score, event in ranked:
            results.append(self._retrieval_result(float(score), event))
            if len(results) >= top_k:
                break
        return results

    def list_events(self, role_key, *, query="", limit=50) -> list[dict]:
        limit_value = min(max(int(limit), 0), 200)
        if limit_value == 0:
            return []

        events = self._read_events(str(role_key))
        if not events:
            return []

        active_ids = {self._event_id(event) for event in self._active_events(events)}
        query_text = str(query).strip()
        if query_text:
            query_tokens = tokenize(query_text)
            ranked = self._rank_events(str(role_key), query_text, query_tokens=query_tokens, events=events)
            selected = [event for _, event in ranked[:limit_value]]
        else:
            selected = sorted(events, key=lambda event: str(event.get("timestamp", "")), reverse=True)[:limit_value]

        return [self._browse_result(event, active_ids=active_ids) for event in selected]

    def _wal_bytes(self, role_key: str) -> bytes:
        path = self.event_path(role_key)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return b""
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Memory WAL is not a regular file")
        return path.read_bytes()

    def _has_wal_history(self, role_key: str) -> bool:
        """Use old artifacts only as loss evidence, never as fallback context.

        Legacy-only roles may never have had a WAL. A prior nonempty index or
        generated event section means an empty/missing WAL cannot prove that
        history (including corrections) is absent. Unreadable evidence also
        cannot authorize that downgrade.
        """
        index_path = self.index_path(role_key)
        try:
            if index_path.exists():
                index = json.loads(index_path.read_text(encoding="utf-8"))
                integrity = index.get("integrity") if isinstance(index, dict) else None
                if (not isinstance(integrity, dict) or type(index.get("event_count")) is not int
                        or index["event_count"] != 0 or type(integrity.get("event_count")) is not int
                        or integrity["event_count"] != 0
                        or integrity.get("events_sha256") != hashlib.sha256(b"").hexdigest()):
                    return True
            for horizon in MEMORY_HORIZONS:
                path = self.memory_path(role_key, horizon)
                if path.exists() and any(line.strip() == "## Memory Events"
                                         for line in path.read_text(encoding="utf-8").splitlines()):
                    return True
        except (OSError, UnicodeError, ValueError):
            return True
        return False

    def _wal_checkpoint(self, role_key: str) -> tuple[int, str] | None:
        try:
            index = json.loads(self.index_path(role_key).read_text(encoding="utf-8"))
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
        known = self._wal_checkpoint(role_key)
        if known is not None and not _wal_prefix_matches(raw, *known):
            raise ValueError("Memory WAL does not preserve its known history")
        if checkpoint is not None and not _wal_prefix_matches(raw, *checkpoint):
            raise ValueError("Memory WAL does not preserve the captured history")
        return events, hashlib.sha256(raw).hexdigest(), raw

    def _wal_snapshot_matches(self, role_key: str, digest: str) -> bool:
        raw = self._wal_bytes(role_key)
        if not raw.strip() and self._has_wal_history(role_key):
            return False
        return hashlib.sha256(raw).hexdigest() == digest

    def _empty_pickup(self, role_key: str, reason: str) -> dict[str, object]:
        return {
            "role_key": role_key, "event_count": 0, "short_term": "",
            "summaries": {"long-term": "", "mid-term": ""}, "relevant_events": [],
            "context_status": "unavailable", "context_reasons": [reason],
        }

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
        This is optimistic read validation, not a cross-process transaction.
        """
        try:
            events, digest, wal_bytes = self._wal_snapshot(role_key)
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
            # Preserve the existing 12-bullet summary budget and add a hard
            # UTF-8 cap. These dependency IDs do not claim every fact was used.
            selected = horizon_events if horizon == "short-term" else horizon_events[:12]
            generated = "\n".join(f"- {event['text']}" for event in selected)
            generated = _bounded_context(generated, short_limit if horizon == "short-term" else PICKUP_SUMMARY_CHARS)
            if horizon != "short-term":
                generated = _bounded_context(summarize_markdown(generated), PICKUP_SUMMARY_CHARS)
            generated_sources[horizon] = {
                "source_kind": "wal_generated", "event_ids": [self._event_id(event) for event in selected],
                "sha256": hashlib.sha256(generated.encode("utf-8")).hexdigest(),
            }
            path = self.memory_path(role_key, horizon)
            legacy = ""
            source = {"source_kind": "legacy_mixed", "path": str(path.relative_to(self.project_root)),
                      "status": "withheld_after_correction" if has_correction else "compatibility_unverified"}
            try:
                raw = path.read_bytes() if path.is_file() else b""
                source["sha256"] = hashlib.sha256(raw).hexdigest()
                if not has_correction:
                    content = self._normal_pickup_content(raw.decode("utf-8"))
                    legacy = content if horizon == "short-term" else summarize_markdown(content)
            except (OSError, UnicodeError):
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
            "provenance": {"schema_version": "memory_pickup.v1", "wal_sha256": digest,
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

    def _legacy_source_matches(self, role_key: str, horizon: str, source: dict) -> bool:
        # Derive the local path; never follow a path supplied by a saved packet.
        path = self.memory_path(role_key, horizon)
        raw = path.read_bytes() if path.is_file() else b""
        return source.get("sha256") == hashlib.sha256(raw).hexdigest()

    def _pickup_provenance_valid(self, role_key: str, packet: dict) -> bool:
        provenance = packet.get("provenance")
        if (not isinstance(provenance, dict) or provenance.get("schema_version") != "memory_pickup.v1"
                or not _sha256_value(provenance.get("wal_sha256"))
                or type(provenance.get("wal_event_count")) is not int or provenance["wal_event_count"] < 0
                or type(provenance.get("wal_bytes")) is not int or provenance["wal_bytes"] < 0
                or provenance.get("content_sha256") != _pickup_content_digest(packet)):
            return False
        for key in ("legacy_sources", "generated_sources"):
            if not isinstance(provenance.get(key), dict) or set(provenance[key]) != set(MEMORY_HORIZONS):
                return False
        for horizon in MEMORY_HORIZONS:
            legacy = provenance["legacy_sources"][horizon]
            generated = provenance["generated_sources"][horizon]
            if (not isinstance(legacy, dict) or legacy.get("source_kind") != "legacy_mixed"
                    or legacy.get("status") not in ("compatibility_unverified", "withheld_after_correction", "unavailable")
                    or legacy.get("path") != str(self.memory_path(role_key, horizon).relative_to(self.project_root))
                    or (legacy["status"] != "unavailable" and not _sha256_value(legacy.get("sha256")))):
                return False
            if (not isinstance(generated, dict) or generated.get("source_kind") != "wal_generated"
                    or not _sha256_value(generated.get("sha256")) or not isinstance(generated.get("event_ids"), list)
                    or any(not isinstance(event_id, str) or re.fullmatch(r"mem:[a-f0-9]{12}", event_id) is None
                           for event_id in generated["event_ids"])):
                return False
        return True

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

    def rebuild_role_memory(
        self,
        role_key: str,
        role_name: str = "",
        project_name: str = "",
    ) -> dict[str, object]:
        events = self._read_events(role_key)
        events = self._active_events(events)
        root = self.role_root(role_key)
        root.mkdir(parents=True, exist_ok=True)
        for horizon in MEMORY_HORIZONS:
            self.memory_path(role_key, horizon).write_text(
                self._default_memory_content(
                    role_key=role_key,
                    role_name=role_name,
                    project_name=project_name,
                    horizon=horizon,
                ),
                encoding="utf-8",
            )
        for event in events:
            self._append_event_to_memory(role_key, event)
        index = self.refresh_memory_index(role_key)
        return index

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

    def _append_event_to_memory(self, role_key: str, event: dict[str, str]) -> None:
        path = self.memory_path(role_key, str(event["horizon"]))
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                self._default_memory_content(
                    role_key=role_key,
                    role_name=role_key,
                    project_name="",
                    horizon=str(event["horizon"]),
                ),
                encoding="utf-8",
            )
        source = f" Source: `{event['source']}`." if event.get("source") else ""
        entry = f"\n## Memory Events\n\n- {event['text']}{source}\n"
        content = path.read_text(encoding="utf-8")
        if "## Memory Events\n" in content:
            path.write_text(content.rstrip() + f"\n- {event['text']}{source}\n", encoding="utf-8")
        else:
            path.write_text(content.rstrip() + entry, encoding="utf-8")

    def _event_id(self, event: dict[str, object]) -> str:
        identity_payload = {key: event.get(key) for key in IDENTITY_FIELDS}
        payload = json.dumps(identity_payload, sort_keys=True, ensure_ascii=False)
        return "mem:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def _active_events(self, events: list[dict[str, str]]) -> list[dict[str, str]]:
        superseded_ids = {
            str(event.get("supersedes", "")).strip()
            for event in events
            if isinstance(event.get("supersedes"), str) and str(event.get("supersedes", "")).strip()
        }
        return [event for event in events if self._event_id(event) not in superseded_ids]

    def _normal_retrieval_events(self, events: list[dict[str, str]]) -> list[dict[str, str]]:
        return [event for event in events if str(event.get("source", "")).split(":", 1)[0] != "worker_failure"]

    def _normal_pickup_content(self, content: str) -> str:
        lines = [
            line
            for line in content.splitlines()
            if " Source: `worker_failure:" not in line
        ]
        return "\n".join(lines).rstrip()

    def _retrieval_result(self, score: float, event: dict[str, object]) -> dict[str, object]:
        return {
            "id": self._event_id(event),
            "score": float(score),
            "horizon": event.get("horizon", ""),
            "text": event.get("text", ""),
            "source": event.get("source", ""),
            "timestamp": event.get("timestamp", ""),
        }

    def _browse_result(self, event: dict[str, object], *, active_ids: set[str]) -> dict[str, object]:
        event_id = self._event_id(event)
        text = str(event.get("text", ""))
        body = text if len(text) <= 240 else text[:237].rstrip() + "..."
        timestamp = str(event.get("timestamp", ""))
        return {
            "id": event_id,
            "kind": str(event.get("horizon", "")),
            "horizon": str(event.get("horizon", "")),
            "role": str(event.get("role_key", "")),
            "timestamp": timestamp,
            "ts": timestamp,
            "body": body,
            "source": str(event.get("source", "")),
            "action": str(event.get("action", "")),
            "is_superseded": event_id not in active_ids,
        }

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
        if cache_path.is_file():
            for line in cache_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("model") != model_name:
                    continue
                event_id = str(row.get("id", ""))
                vector = row.get("vector", [])
                if not event_id or not isinstance(vector, list):
                    continue
                cached[event_id] = [float(value) for value in vector]

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
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with cache_path.open("a", encoding="utf-8") as handle:
                for event_id, vector in zip(missing_ids, vectors):
                    normalized = [float(value) for value in vector]
                    cached[event_id] = normalized
                    payload = {
                        "id": event_id,
                        "model": model_name,
                        "dim": int(embed_dim) if embed_dim is not None else len(normalized),
                        "vector": normalized,
                    }
                    handle.write(json.dumps(payload) + "\n")

        return [cached[event_id] for event_id in event_ids]

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
        path = self.event_path(role_key)
        if not path.is_file():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            events.append(json.loads(line))
        return events

    def _wal_integrity(
        self,
        role_key: str,
        *,
        events: list[dict[str, str]] | None = None,
    ) -> tuple[int, str]:
        if events is None:
            events = self._read_events(role_key)
        path = self.event_path(role_key)
        if not path.is_file():
            return len(events), hashlib.sha256(b"").hexdigest()
        return len(events), hashlib.sha256(path.read_bytes()).hexdigest()

    def _projection_matches_wal(self, role_key: str, index: dict[str, object]) -> bool:
        integrity = index.get("integrity")
        if not isinstance(integrity, dict):
            return False
        stored_count = integrity.get("event_count")
        if not isinstance(stored_count, int):
            return False
        events = self._read_events(role_key)
        live_count = len(events)
        if stored_count != live_count:
            return False
        stored_sha = integrity.get("events_sha256")
        if not isinstance(stored_sha, str):
            return False
        _, live_sha = self._wal_integrity(role_key, events=events)
        return stored_sha == live_sha

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
