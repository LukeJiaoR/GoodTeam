import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

try:  # BM25 is a declared dependency; degrade gracefully if unavailable.
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover - exercised only without the optional wheel
    BM25Okapi = None


MEMORY_HORIZONS = ("long-term", "mid-term", "short-term")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


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


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _bm25_scores(corpus: list[list[str]], query_tokens: list[str]) -> list[float]:
    """Rank corpus docs against the query. Uses BM25 when available, else a
    deterministic token-overlap score so retrieval still works (and stays
    testable) without the optional wheel."""
    if BM25Okapi is not None:
        return list(BM25Okapi(corpus).get_scores(query_tokens))
    wanted = set(query_tokens)
    return [float(sum(1 for token in doc if token in wanted)) for doc in corpus]


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
    def __init__(self, project_root: Path):
        self.project_root = project_root

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
    ) -> dict[str, str]:
        if horizon not in MEMORY_HORIZONS:
            raise ValueError(f"Unknown memory horizon: {horizon}")
        if action != "add":
            raise ValueError(f"Unsupported memory event action: {action}")
        if not text.strip():
            raise ValueError("Memory event text is required.")

        self.ensure_role_memory(role_key)
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "role_key": role_key,
            "horizon": horizon,
            "action": action,
            "source": source,
            "text": text.strip(),
        }
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
            "horizons": horizons,
        }
        self._write_json(self.index_path(role_key), index)
        return index

    def read_memory_index(self, role_key: str) -> dict[str, object]:
        path = self.index_path(role_key)
        if not path.is_file():
            return self.refresh_memory_index(role_key)
        return json.loads(path.read_text(encoding="utf-8"))

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

    def retrieve_relevant_events(
        self,
        role_key: str,
        query: str,
        *,
        top_k: int = 5,
        horizons: tuple[str, ...] | None = None,
    ) -> list[dict[str, object]]:
        """Return the top-K events most relevant to ``query``, ranked by BM25.

        Each result carries a stable ``mem:<id>`` handle so artifacts can cite
        the exact event a fact came from (grounded citations, M8)."""
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        events = self._read_events(role_key)
        if horizons:
            events = [event for event in events if event.get("horizon") in horizons]
        if not events:
            return []
        corpus = [tokenize(str(event.get("text", ""))) for event in events]
        scores = _bm25_scores(corpus, query_tokens)
        wanted = set(query_tokens)
        # Gate relevance on token overlap (BM25 gives terms that appear in every
        # document a zero/negative IDF, which would drop legitimate matches in a
        # tiny corpus); use the BM25 score only to rank the overlapping events.
        candidates = [
            (score, event)
            for score, event, doc in zip(scores, events, corpus)
            if wanted.intersection(doc)
        ]
        ranked = sorted(candidates, key=lambda pair: pair[0], reverse=True)
        results: list[dict[str, object]] = []
        for score, event in ranked:
            results.append(
                {
                    "id": self._event_id(event),
                    "score": float(score),
                    "horizon": event.get("horizon", ""),
                    "text": event.get("text", ""),
                    "source": event.get("source", ""),
                    "timestamp": event.get("timestamp", ""),
                }
            )
            if len(results) >= top_k:
                break
        return results

    def build_pickup_packet(
        self,
        role_key: str,
        *,
        max_short_chars: int = 1600,
        query: str = "",
        top_k: int = 5,
    ) -> dict[str, object]:
        index = self.read_memory_index(role_key)
        short_path = self.memory_path(role_key, "short-term")
        short_content = short_path.read_text(encoding="utf-8") if short_path.is_file() else ""
        if len(short_content) > max_short_chars:
            short_content = short_content[:max_short_chars] + "\n\n[Short-term memory truncated]"
        horizons = dict(index.get("horizons", {}))
        packet: dict[str, object] = {
            "role_key": role_key,
            "paths": {horizon: str(self.memory_path(role_key, horizon)) for horizon in MEMORY_HORIZONS},
            "event_count": int(index.get("event_count", 0)),
            "short_term": short_content,
            "summaries": {
                "long-term": dict(horizons.get("long-term", {})).get("summary", ""),
                "mid-term": dict(horizons.get("mid-term", {})).get("summary", ""),
            },
        }
        if query.strip():
            packet["relevant_events"] = self.retrieve_relevant_events(role_key, query, top_k=top_k)
        return packet

    def rebuild_role_memory(
        self,
        role_key: str,
        role_name: str = "",
        project_name: str = "",
    ) -> dict[str, object]:
        events = self._read_events(role_key)
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
        payload = json.dumps(event, sort_keys=True, ensure_ascii=False)
        return "mem:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

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

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
