"""Byte-compatible pure extraction against a frozen pre-refactor baseline."""
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest

import goodteam_os.memory as facade
from goodteam_os.memory_core import context, projections


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "memory" / "pickup-v1.json").read_text())


def normalize(value, root):
    if isinstance(value, str):
        return value.replace(str(root), "<PROJECT>")
    if isinstance(value, list):
        return [normalize(item, root) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item, root) for key, item in value.items()}
    return value


def wire_bytes(value):
    # Preserve field order as well as Unicode text and values, not just dict ==.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_capture_and_frozen_validation_match_pre_refactor_bytes(tmp_path, monkeypatch, case):
    class FixedClock:
        @staticmethod
        def now(tz):
            return datetime.fromisoformat(FIXTURE["fixed_time"])
    monkeypatch.setattr(facade, "datetime", FixedClock)
    store = facade.RoleMemoryStore(tmp_path)
    if "legacy" in case:
        paths = store.ensure_role_memory("architect")
        for horizon, text in case["legacy"].items():
            paths[horizon].write_text(text)
    created = []
    def apply(operations):
        for operation in operations:
            fields = dict(operation)
            index = fields.pop("supersedes_index", None)
            fields.setdefault("source", "synthetic-job/result.md")
            if index is not None:
                fields["supersedes"] = store._event_id(created[index])
            created.append(store.append_memory_event("architect", **fields))
    apply(case["operations"])
    packet = store.build_pickup_packet("architect", query=case["query"], max_short_chars=case.get("max_short_chars", 1600))
    captured = wire_bytes(normalize(packet, tmp_path))
    assert captured == wire_bytes(case["expected_capture"])
    original_packet = copy.deepcopy(packet)
    apply(case.get("after_capture", []))
    validated = store.validate_pickup_packet(packet)
    assert wire_bytes(normalize(validated, tmp_path)) == wire_bytes(case["expected_validation"])
    assert wire_bytes(store._read_events("architect")) == wire_bytes(case["expected_events"])
    assert packet == original_packet


def test_legacy_imports_reexport_original_pure_contracts():
    assert facade.summarize_markdown is context.summarize_markdown
    assert facade._bounded_context is context._bounded_context
    assert facade._pickup_content_digest is context._pickup_content_digest
    assert facade._sha256_value is context._sha256_value
    assert facade.MEMORY_HORIZONS is projections.MEMORY_HORIZONS
    assert facade.IDENTITY_FIELDS is projections.IDENTITY_FIELDS


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_pure_context_uses_in_memory_values_without_mutating_inputs(case):
    events = copy.deepcopy(case["expected_events"])
    packet = copy.deepcopy(case["expected_capture"])
    before = wire_bytes({"events": events, "packet": packet})
    active = projections.active_events(events)
    normal = projections.normal_retrieval_events(active)
    active_ids = {projections.event_id(event) for event in active}
    for event in events:
        identifier = projections.event_id(event)
        projections.retrieval_result(1.25, event, identifier=identifier)
        projections.browse_result(event, active_ids=active_ids, identifier=identifier)
    for horizon in facade.MEMORY_HORIZONS:
        selected = [event for event in normal if event["horizon"] == horizon]
        text, source = context.generated_horizon_context(horizon, selected, short_limit=137, identify=projections.event_id)
        assert hashlib.sha256(text.encode()).hexdigest() == source["sha256"]
        assert source["source_kind"] == "wal_generated"
    paths = {horizon: f".goodteam/memory/roles/architect/{horizon}.md" for horizon in facade.MEMORY_HORIZONS}
    assert context.pickup_provenance_valid(packet, paths)
    assert before == wire_bytes({"events": events, "packet": packet})


def test_facade_projection_preserves_overridden_identity_call_seam(tmp_path):
    class SpecializedStore(facade.RoleMemoryStore):
        def _event_id(self, event):
            return "custom:" + event["text"]
    store = SpecializedStore(tmp_path)
    events = [
        {"text": "OLD"},
        {"text": "NEW", "supersedes": "custom:OLD"},
    ]
    assert store._active_events(events) == [events[1]]
    assert store._retrieval_result(1.0, events[1])["id"] == "custom:NEW"
    assert store._browse_result(events[1], active_ids={"custom:NEW"})["id"] == "custom:NEW"
