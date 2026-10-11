"""Local lexical and injected semantic retrieval; no downloaded model or provider."""
import json

import pytest

from goodteam_os import memory
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.web import build_memory_events
from goodteam_os.workers import WorkerJobStore


def append(store, text):
    return store.append_memory_event("architect", horizon="long-term", text=text, source="local-test")


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("text,query", [
    ("发布前必须验证数据库迁移并保留回滚方案", "数据库迁移"),
    ("PostgreSQL数据库迁移完成后检查复制延迟", "PostgreSQL 数据库迁移"),
    ("Vérifier la réplication après déploiement", "réplication déploiement"),
])
def test_multilingual_query_reaches_browse_and_worker_retrieval(tmp_path, monkeypatch, fallback, text, query):
    if fallback:
        monkeypatch.setattr(memory, "BM25Okapi", None)
    store = RoleMemoryStore(tmp_path)
    expected = append(store, text)
    append(store, "界面布局采用两列卡片")
    expected_id = store._event_id(expected)

    relevant = store.retrieve_relevant_events("architect", query)
    assert relevant and relevant[0]["id"] == expected_id
    assert relevant[0]["text"] == text
    browsed = build_memory_events(tmp_path, "DEMO", "architect", query=query)["events"]
    assert browsed and browsed[0]["id"] == expected_id
    assert browsed[0]["body"] == text


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("query", ["数据库", "database", "！？..."])
def test_zero_term_corpus_and_punctuation_queries_do_not_crash(tmp_path, monkeypatch, fallback, query):
    if fallback:
        monkeypatch.setattr(memory, "BM25Okapi", None)
    store = RoleMemoryStore(tmp_path)
    append(store, "！？...🧠")
    assert store.retrieve_relevant_events("architect", query) == []
    assert store.list_events("architect", query=query) == []
    assert memory._bm25_scores([], memory.tokenize(query)) == []


def test_query_without_lexical_terms_keeps_explicit_semantic_retrieval(tmp_path):
    calls = []

    def embed(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    store = RoleMemoryStore(tmp_path, embedder=embed)
    expected = append(store, "数据库迁移前必须留存备份")
    assert memory.tokenize("🧠") == []
    assert store.retrieve_relevant_events("architect", "🧠")[0]["id"] == store._event_id(expected)
    assert calls[-1] == ["🧠"]
    calls.clear()
    assert store.retrieve_relevant_events("architect", " \n\t") == []
    assert calls == []


def test_chinese_memory_is_in_next_worker_pickup_packet(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    store = RoleMemoryStore(tmp_path)
    expected = append(store, "数据库迁移前必须验证备份")
    workers = WorkerJobStore(tmp_path)
    job = workers.create_job(run_id="chinese-pickup", role="architect", adapter="noop",
                             task="实现数据库迁移", context="", project_key="DEMO")
    job_root = workers.root / job["id"]
    packet = json.loads((job_root / "role_pickup_packet.json").read_text())
    assert packet["relevant_events"][0]["id"] == store._event_id(expected)
    assert "数据库迁移前必须验证备份" in workers._render_worker_pickup_context(job_root)


@pytest.mark.parametrize("fallback", [False, True])
def test_han_phrase_excludes_single_character_noise_before_top_k(tmp_path, monkeypatch, fallback):
    if fallback:
        monkeypatch.setattr(memory, "BM25Okapi", None)
    store = RoleMemoryStore(tmp_path)
    append(store, "库房保管钥匙请联系张三经理并先做好登记核对资料记录")
    expected = append(store, "数据库迁移")
    expected_id = store._event_id(expected)
    assert [event["id"] for event in store.retrieve_relevant_events("architect", "数据库迁移", top_k=1)] == [expected_id]
    assert [event["id"] for event in store.list_events("architect", query="数据库迁移")] == [expected_id]


@pytest.mark.parametrize("fallback", [False, True])
def test_han_phrase_coverage_precedes_negative_bm25_length_bias(tmp_path, monkeypatch, fallback):
    if fallback:
        monkeypatch.setattr(memory, "BM25Okapi", None)
    store = RoleMemoryStore(tmp_path)
    append(store, "数据归档操作请联系张三经理并先做好登记核对资料记录")
    expected = append(store, "数据库迁移")
    result = store.retrieve_relevant_events("architect", "数据库迁移", top_k=1)
    assert result[0]["id"] == store._event_id(expected)


def test_single_han_character_query_remains_searchable(tmp_path):
    store = RoleMemoryStore(tmp_path)
    expected = append(store, "数据库迁移")
    assert store.retrieve_relevant_events("architect", "库")[0]["id"] == store._event_id(expected)
