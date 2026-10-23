"""Bounded, deterministic context projections, never a second task authority.

The full conversation and workflow state remain the durable sources. Extracts
are explicitly lossy historical evidence, not inferred requirements or approvals.
No model, memory retrieval, workflow advancement or external calls live here.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re


CONVERSATION_SCHEMA = "conversation_continuity.v1"
TASK_STATE_SCHEMA = "task_continuity.v1"
RECENT_EVENTS = 40
SUMMARY_EVENTS = 20
RECALLED_EVENTS = 8
EVENT_CHARS = 600


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value, limit):
    value = str(value or "")
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _event(item, *, index, query_tokens=()):
    text = str(item.get("content") or "")
    start = 0
    # Match-centered verbatim windows make recall useful when the supported
    # fact is past the normal prefix. Longest matching term wins deterministically.
    if len(text) > EVENT_CHARS and query_tokens:
        lowered = text[:8000].lower()
        for token in sorted(query_tokens, key=lambda value: (-len(value), value)):
            position = lowered.find(token)
            if position >= 0:
                start = max(0, min(position - 120, len(text) - EVENT_CHARS))
                break
    excerpt = text[start:start + EVENT_CHARS]
    return {
        "source_index": index,
        "message_id": _text(item.get("message_id"), 160),
        "actor": _text(item.get("actor"), 32),
        "event": _text(item.get("event"), 80),
        "run_id": _text(item.get("run_id"), 128),
        "content": excerpt,
        "source_start": start,
        "source_end": start + len(excerpt),
        "content_truncated": len(text) > EVENT_CHARS,
        "source_sha256": _digest(item),
    }


def _scoped_timeline(session):
    timeline = session.get("timeline") or []
    requests = session.get("assistant_requests") or {}
    # User events keep their original blank run_id. Explicit reference scope is
    # saved on the request, and completed implicit running-task discussion is
    # bound by the saved conversation scope/reply. Neither is project-wide chat.
    replies = {str(item.get("message_id"))[len("reply:"):]: item.get("run_id", "")
               for item in timeline if isinstance(item, dict)
               and str(item.get("message_id", "")).startswith("reply:")
               and item.get("event") == "conversation_replied"}

    def scope(item):
        request = requests.get(item.get("message_id")) or {}
        interpretation = request.get("interpretation") or {}
        scoped_run = (interpretation.get("conversation_scope") or {}).get("run") or {}
        return (item.get("run_id") or request.get("context_run_id") or request.get("conversation_run_id") or scoped_run.get("run_id")
                or replies.get(item.get("message_id")) or "")

    return [(index, item, scope(item)) for index, item in enumerate(timeline)
            if isinstance(item, dict)]


def _timeline(session, run_id=""):
    return [(index, item) for index, item, scope in _scoped_timeline(session)
            if not run_id or not scope or scope == run_id]


def compact_conversation(session, *, run_id=""):
    """Rebuild under the session's existing writer lock; retain original events.

    Keep opening user context and the newest historical statements/transitions.
    The omission count is intentional: this is extractive compaction, not a
    claim that a bounded summary understands every old statement.
    """
    events = _timeline(session, run_id)
    older = events[:-RECENT_EVENTS]
    candidates = [(index, item) for index, item in older if item.get("actor") == "user" or item.get("event")]
    selected = {index for index, _, scope in _scoped_timeline(session) if run_id and scope == run_id}
    preferred = [(index, item) for index, item in candidates if index in selected]
    # A selected task's messages outrank unrelated project discussion while
    # retaining opening project context. Never pull a different run into recall.
    choices = candidates[:4] + preferred[-(SUMMARY_EVENTS - 4):] + list(reversed(candidates))
    selected_by_index = {}
    for index, item in choices:
        if len(selected_by_index) >= SUMMARY_EVENTS:
            break
        selected_by_index.setdefault(index, item)
    selected = sorted(selected_by_index.items())
    counts = Counter(_text(item.get("event") or item.get("actor") or "unknown", 80) for _, item in older)
    return {
        "schema_version": CONVERSATION_SCHEMA,
        "project_key": _text(session.get("project_key"), 128),
        "run_id": _text(run_id, 128),
        "source": "task_assistant_session.timeline",
        "source_sha256": _digest(events),
        "strategy": "deterministic_excerpts",
        "authority": "historical_context_only",
        "event_count": len(events),
        "compacted_event_count": len(older),
        "omitted_event_count": len(older) - len(selected),
        "event_counts": dict(sorted(counts.items())[:32]),
        "event_counts_truncated": len(counts) > 32,
        "excerpts": [_event(item, index=index) for index, item in selected],
    }


def _tokens(text):
    text = str(text or "").lower()[:8000]
    words = set(re.findall(r"[a-z0-9_]{3,}", text))
    # Adjacent CJK characters make deterministic recall useful without an NLP
    # package. Matching is advisory retrieval, never interpretation/permission.
    for word in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(word[index:index + 2] for index in range(len(word) - 1))
    return set(sorted(words)[:128])


def conversation_context(session, *, query="", run_id=""):
    """A bounded fresh view. Ignore stale/edited caches rather than trusting them."""
    events = _timeline(session, run_id)
    summary = compact_conversation(session, run_id=run_id)
    previous = session.get("conversation_continuity")
    # Equality validates the full deterministic projection, not just a claimed
    # digest. Legacy and externally edited caches are rebuilt in memory only.
    if previous == summary:
        summary = previous
    known = {item["source_index"] for item in summary["excerpts"] if not item["content_truncated"]}
    tokens = _tokens(query)
    scores = []
    for index, item in events[:-RECENT_EVENTS]:
        if index in known:
            continue
        score = len(tokens & _tokens(item.get("content")))
        if score:
            scores.append((score, index, item))
    recalled = sorted(sorted(scores, key=lambda entry: (entry[0], entry[1]), reverse=True)[:RECALLED_EVENTS],
                      key=lambda entry: entry[1])
    return {
        "summary": summary,
        "recent": [_event(item, index=index, query_tokens=tokens) for index, item in events[-RECENT_EVENTS:]],
        "recalled": [_event(item, index=index, query_tokens=tokens) for _, index, item in recalled],
        "recall_match_count": len(scores),
        "limits": {"recent_events": RECENT_EVENTS, "summary_events": SUMMARY_EVENTS,
                   "recalled_events": RECALLED_EVENTS, "event_characters": EVENT_CHARS},
        "notice": "Excerpts can omit or truncate earlier statements. The original timeline is retained. "
                  "Historical statements are not current requirements, approvals or proof of execution; "
                  "use the current task state and exact task-start input for those facts.",
    }


def task_state_context(state):
    """Project the existing persistent workflow for Manager and role handoffs.

    This never reads a second task, live Team settings or a saved projection.
    Recompute on every state write and dispatch, so a stale cache cannot revive
    old feedback, blockers, approvals or artifacts after a restart.
    """
    from goodteam_os.resources import legacy_artifacts_view

    # Match StateStore's canonical persisted artifact view when resource-backed
    # state retains aliases removed from its compatibility dict.
    artifacts = legacy_artifacts_view(state.resources) if state.resources else dict(state.artifacts)
    source = {key: getattr(state, key) for key in (
        "project_key", "run_id", "idea", "source_type", "source_ref", "delivery_mode",
        "delivery_standards", "current_node", "next_node", "approvals", "change_requests",
        "blockers", "artifacts", "pending_transition", "task_skill_context",
    )}
    source["artifacts"] = artifacts
    truncated = []

    def text(value, limit, field):
        raw = str(value or "")
        if len(raw) > limit:
            truncated.append(field)
        return _text(raw, limit)

    def values(items, count, chars, field):
        if len(items) > count:
            truncated.append(field)
        return [text(item, chars, field) for item in items[:count]]

    def mapping(items, count, chars, field):
        if len(items) > count:
            truncated.append(field)
        return [{"key": text(key, 128, field), "value": text(value, chars, field)}
                for key, value in sorted(items.items())[:count]]

    transition = state.pending_transition if isinstance(state.pending_transition, dict) else {}
    lock = (state.task_skill_context or {}).get("task_lock_ref") or {}
    return {
        "schema_version": TASK_STATE_SCHEMA,
        "project_key": text(state.project_key, 128, "project_key"),
        "run_id": text(state.run_id, 128, "run_id"),
        "source": "workflow_state",
        "source_sha256": _digest(source),
        "authority": "read_only_projection",
        "task": {
            "recorded_goal": text(state.idea, 4000, "idea"),
            # The dedicated task-input reader owns the original contract and
            # its availability labels. Repeating mutable standards here could
            # reconstruct an unavailable capture or compete with a valid one.
            "contract_source": "existing_task_input_reader",
            "input_evidence": "task_lock_reference_unverified" if lock else "legacy_workflow_record",
            "task_lock_id": text(lock.get("id"), 160, "task_lock_id"),
            "task_lock_digest": text(lock.get("digest"), 80, "task_lock_digest"),
        },
        "progress": {
            "current_node": text(state.current_node, 128, "current_node"),
            "next_node": text(state.next_node, 128, "next_node"),
            "transition_id": text(transition.get("id"), 160, "transition_id"),
            "transition_status": text(transition.get("status"), 80, "transition_status"),
            "transition_available": isinstance(state.pending_transition, dict),
        },
        "approvals": mapping(state.approvals, 24, 80, "approvals"),
        "recorded_changes": mapping(state.change_requests, 16, 600, "change_requests"),
        "blockers": values(state.blockers, 16, 300, "blockers"),
        "artifact_refs": mapping(artifacts, 24, 300, "artifacts"),
        "truncated_fields": sorted(set(truncated)),
        "notice": "This snapshot is context, never dispatch or approval authority. Original task input must "
                  "be checked through its existing task-start record; a lock reference alone does not verify it. "
                  "Recorded changes are separate feedback, not proof of delivery to an in-flight Worker. "
                  "Artifact references do not prove completion or successful verification.",
    }


def task_handoff_context(state, *, recipient_role=""):
    return "\n".join([
        "## Task continuity handoff (read-only)",
        "This is a source-bound snapshot at input preparation, not a new assignment or permission. "
        "Keep the task, original input, later feedback and approval evidence distinct. "
        "An already prepared Worker input is not updated by later state changes.",
        _json({"recipient_role": _text(recipient_role, 80), "task_state": task_state_context(state)}),
    ])
