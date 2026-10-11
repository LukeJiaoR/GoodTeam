"""The Memory UI consumes this actual public API projection fixture."""
from datetime import UTC, datetime
import json
from pathlib import Path

from goodteam_os import memory
from goodteam_os.web import build_memory_event_detail, build_memory_events


def test_memory_view_fixture_matches_real_api_projection(tmp_path, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    monkeypatch.setattr(memory, "datetime", Clock)
    store = memory.RoleMemoryStore(tmp_path)
    old = store.append_memory_event("architect", horizon="long-term",
                                   text="数据库迁移前使用旧备份流程", source="contract-fixture")
    new = store.append_memory_event("architect", horizon="long-term", text="数据库迁移前必须验证新备份",
                                   source="contract-fixture", supersedes=store._event_id(old))
    store.append_memory_event("qa", horizon="mid-term", text="QA 独立验证数据库回滚", source="contract-fixture")
    actual = {role: build_memory_events(tmp_path, "AUDIT", role) for role in ("architect", "qa")}
    actual["detail"] = build_memory_event_detail(tmp_path, store._event_id(new))
    fixture = Path(__file__).parents[1] / "frontend/src/features/knowledge/fixtures/memory-events.json"
    assert actual == json.loads(fixture.read_text())
