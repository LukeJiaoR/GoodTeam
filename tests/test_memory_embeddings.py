"""Disposable pgvector + real local HTTP; vectors are protocol fixtures only."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

from goodteam_os.database import database
from goodteam_os.memory import RoleMemoryStore, runtime_role_memory_store
from goodteam_os.providers import ProviderError, ProviderStore
from goodteam_os.workers import WorkerJobStore


@pytest.fixture
def embedding_service(tmp_path, monkeypatch):
    url = os.environ.get("GOODTEAM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL with pgvector installed")
    monkeypatch.setenv("GOODTEAM_DATABASE_URL", url)
    monkeypatch.setenv("GOODTEAM_DATABASE_SCHEMA", "gt_rag_" + uuid4().hex)
    with database().connection() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    requests, mode = [], {"invalid": False, "redirect": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({**body, "path": self.path, "authorization": self.headers.get("Authorization")})
            if mode["redirect"]:
                self.send_response(307)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/leak")
                self.end_headers()
                return
            # Disjoint phrases intentionally share a fixture vector. This tests
            # retrieval wiring, not the semantic quality of a downloaded model.
            data = [{"index": index, "embedding": ([float("nan"), 0] if mode["invalid"] else
                    [0.0, 1.0] if "colors" in text else [1.0, 0.0])}
                    for index, text in enumerate(body["input"])]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"data": list(reversed(data))}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store = ProviderStore()
    result = store.update({"action": "save", "expected_revision": 0, "name": "Local embedding fixture",
        "base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_key": "fixture-only-secret", "model": "coding-model"})
    provider = result["providers"][0]

    def configure(model="fixture-embedding"):
        return store.update({"action": "configure_embeddings", "expected_revision": store.view()["revision"],
            "provider_id": provider["id"] if model else "", "model": model})

    configure()
    try:
        yield store, provider, configure, requests, mode
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_pg_retrieval_is_incremental_source_bound_and_independent_of_coding_provider(tmp_path, embedding_service):
    settings, provider, configure, requests, _ = embedding_service
    settings.update({"action": "activate", "id": provider["id"], "expected_revision": settings.view()["revision"]})
    coding_reference = settings.capture()
    root = tmp_path / "project"
    memory = RoleMemoryStore(root)
    old = memory.append_memory_event("architect", horizon="long-term", text="Throttle simultaneous requests.", source="lesson")
    memory.append_memory_event("architect", horizon="long-term", text="Use blue colors.", source="lesson")
    query = "解决排队拥堵"
    assert memory.retrieve_relevant_events("architect", query) == []
    workers = WorkerJobStore(root)

    def pickup(run):
        job = workers.create_job(run_id=run, role="architect", adapter="noop", task=query, context="", project_key="DEMO")
        return json.loads((workers.root / job["id"] / "role_pickup_packet.json").read_text())

    captured = pickup("first")
    assert [item["id"] for item in captured["relevant_events"]] == [memory._event_id(old)]
    assert captured["provenance"]["retrieval"]["model"] == "fixture-embedding"
    assert len(requests) == 2 and len(requests[0]["input"]) == 2
    assert all(item["path"] == "/v1/embeddings" and item["authorization"] == "Bearer fixture-only-secret" for item in requests)
    assert "fixture-only-secret" not in json.dumps(captured) + json.dumps(settings.view())
    assert not memory.embeddings_path("architect").exists()
    assert pickup("cached")["relevant_events"] == captured["relevant_events"]
    assert len(requests) == 3 and requests[-1]["input"] == [query]

    other = RoleMemoryStore(tmp_path / "other-project")
    other.append_memory_event("architect", horizon="long-term", text="Unrelated confidential project lesson.")
    runtime_role_memory_store(other.project_root).build_pickup_packet("architect", query=query)
    corrected = memory.append_memory_event("architect", horizon="long-term", text="Limit simultaneous requests to four.",
        source="corrected-lesson", supersedes=memory._event_id(old))
    updated = pickup("corrected")
    assert [item["id"] for item in updated["relevant_events"]] == [memory._event_id(corrected)]
    assert memory._event_id(old) not in {item["id"] for item in memory.validate_pickup_packet(captured)["relevant_events"]}
    count = len(requests)
    memory.list_events("architect", query=query)
    assert len(requests) == count  # Browsing never invokes a provider.

    configure("another-embedding-model")
    changed = pickup("new-model")
    assert changed["provenance"]["retrieval"]["model"] == "another-embedding-model"
    assert len(requests) == count + 2 and len(requests[-2]["input"]) == 2
    assert settings.capture() == coding_reference and settings.active()["model"] == "coding-model"
    with pytest.raises(ProviderError, match="Switch"):
        settings.update({"action": "delete", "id": provider["id"], "expected_revision": settings.view()["revision"]})
    configure("")
    count = len(requests)
    assert runtime_role_memory_store(root).build_pickup_packet("architect", query=query)["relevant_events"] == []
    assert len(requests) == count and settings.capture() == coding_reference


def test_embedding_failure_is_visible_and_never_falls_back_or_follows_redirects(tmp_path, embedding_service):
    _, _, _, requests, mode = embedding_service
    memory = RoleMemoryStore(tmp_path / "project")
    memory.append_memory_event("architect", horizon="long-term", text="Throttle simultaneous requests.")
    for failure in ("invalid", "redirect"):
        mode.update(invalid=failure == "invalid", redirect=failure == "redirect")
        count = len(requests)
        packet = runtime_role_memory_store(memory.project_root).build_pickup_packet("architect", query="Throttle requests")
        assert packet["context_status"] == "partial" and "retrieval_unavailable" in packet["context_reasons"]
        assert packet["relevant_events"] == []
        assert len(requests) == count + 1 and requests[-1]["path"] == "/v1/embeddings"
    with database().connection() as conn:
        assert conn.execute("SELECT count(*) AS count FROM memory_embeddings").fetchone()["count"] == 0


def test_two_round_discovery_receives_learned_memory_through_pg(tmp_path, monkeypatch, embedding_service):
    from goodteam_os import project_outcome_discovery
    from test_project_outcome_discovery import test_verified_outcome_creates_once_then_delivers_next_iteration_within_frozen_authority

    remember = project_outcome_discovery._remember
    lesson = "Preserve the accepted revision when documenting subsequent work."

    def remember_with_fixture_lesson(root, outcome):
        remember(root, outcome)
        # Supply one learned fact after verified delivery. The production loop,
        # next Manager input, PG and HTTP retrieval are real; learning text and
        # Manager judgments remain offline fixtures.
        RoleMemoryStore(root).append_memory_event("orchestrator", horizon="long-term", text=lesson,
            source="fixture-learned:" + outcome["delivery"]["receipt_id"])

    monkeypatch.setattr(project_outcome_discovery, "_remember", remember_with_fixture_lesson)
    test_verified_outcome_creates_once_then_delivers_next_iteration_within_frozen_authority(tmp_path, monkeypatch)
    events = [json.loads(line) for line in (tmp_path / "cli-calls.jsonl").read_text().splitlines()]
    packet = next(event["packet"] for event in events if event["kind"] == "manager_discovery")
    assert packet["memory_pickup"]["context_status"] == "available"
    assert any(item["text"] == lesson for item in packet["memory_pickup"]["relevant_events"])
    assert packet["memory_pickup"]["provenance"]["retrieval"]["model"] == "fixture-embedding"
    assert len([event for event in events if event["kind"] == "worker"]) == 2
