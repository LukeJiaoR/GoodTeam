"""Disposable pgvector + real local HTTP; vectors are protocol fixtures only."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import pytest

from goodteam_os.database import database
from goodteam_os.memory import RoleMemoryStore, runtime_role_memory_store
from goodteam_os.memory_embeddings import PostgresMemoryRetriever
from goodteam_os.providers import ProviderError, ProviderStore
from goodteam_os.workers import WorkerJobStore


@pytest.fixture
def embedding_http_service():
    requests, mode = [], {"invalid": False, "redirect": False, "truncated": False, "slow": False}

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
            if mode["truncated"] or mode["slow"]:
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            payload = json.dumps({"data": list(reversed(data))}).encode()
            if mode["truncated"]:
                self.wfile.write(b"40\r\n{")
            elif mode["slow"]:
                try:
                    self.wfile.write(f"{len(payload):x}\r\n".encode())
                    for byte in payload:
                        self.wfile.write(bytes([byte]))
                        time.sleep(0.03)
                    self.wfile.write(b"\r\n0\r\n\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests, mode
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


@pytest.fixture
def embedding_service(tmp_path, monkeypatch, embedding_http_service):
    url = os.environ.get("GOODTEAM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL with pgvector installed")
    monkeypatch.setenv("GOODTEAM_DATABASE_URL", url)
    monkeypatch.setenv("GOODTEAM_DATABASE_SCHEMA", "gt_rag_" + uuid4().hex)
    with database().connection() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    base_url, requests, mode = embedding_http_service
    store = ProviderStore()
    result = store.update({"action": "save", "expected_revision": 0, "name": "Local embedding fixture",
        "base_url": base_url, "api_key": "fixture-only-secret", "model": "coding-model"})
    provider = result["providers"][0]

    def configure(model="fixture-embedding"):
        return store.update({"action": "configure_embeddings", "expected_revision": store.view()["revision"],
            "provider_id": provider["id"] if model else "", "model": model})

    configure()
    return store, provider, configure, requests, mode


@pytest.mark.parametrize("failure", ["truncated", "slow"])
def test_http_failure_respects_deadline_and_provider_error_contract(tmp_path, embedding_http_service, failure):
    base_url, requests, mode = embedding_http_service
    mode[failure] = True
    connection = {"id": "fixture", "revision": 1, "embedding_revision": 1,
                  "model": "fixture-embedding", "base_url": base_url, "api_key": "fixture-only-secret"}
    retriever = PostgresMemoryRetriever(SimpleNamespace(root=tmp_path, embedding_connection=lambda: connection), connection)
    started = time.monotonic()
    with pytest.raises(ProviderError) as caught:
        retriever._embed(["private fixture memory"], started + 0.2)
    assert time.monotonic() - started < 0.8
    assert "fixture-only-secret" not in str(caught.value) and "private fixture memory" not in str(caught.value)
    assert len(requests) == 1


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


def test_embedding_failure_is_visible_and_never_falls_back_or_follows_redirects(tmp_path, monkeypatch, embedding_service):
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

    mode.update(invalid=False, redirect=False)
    captured = runtime_role_memory_store(memory.project_root).build_pickup_packet("architect", query="Throttle requests")
    assert captured["relevant_events"]
    with database().connection() as conn:
        cached = conn.execute("SELECT * FROM memory_embeddings").fetchall()
    # With ample pickup time left, the single-request cap still applies. A
    # failed query preserves already indexed rows and the original source version.
    monkeypatch.setattr("goodteam_os.memory_embeddings.MAX_REQUEST_SECONDS", 0.2)
    mode["slow"] = True
    count, started = len(requests), time.monotonic()
    packet = runtime_role_memory_store(memory.project_root).build_pickup_packet("architect", query="Throttle requests")
    assert time.monotonic() - started < 0.8
    assert packet["context_status"] == "partial" and "retrieval_unavailable" in packet["context_reasons"]
    assert packet["relevant_events"] == [] and len(requests) == count + 1
    for field in ("wal_sha256", "wal_bytes", "wal_event_count", "retrieval", "generated_sources", "legacy_sources"):
        assert packet["provenance"][field] == captured["provenance"][field]
    with database().connection() as conn:
        assert conn.execute("SELECT * FROM memory_embeddings").fetchall() == cached


@pytest.mark.parametrize("retrieval_available", [True, False])
def test_two_round_discovery_receives_learned_memory_through_pg(tmp_path, monkeypatch, embedding_service, retrieval_available):
    from goodteam_os import project_outcome_discovery
    from test_project_outcome_discovery import test_verified_outcome_creates_once_then_delivers_next_iteration_within_frozen_authority

    remember = project_outcome_discovery._remember
    _, _, _, _, mode = embedding_service
    lesson = "Preserve the accepted revision when documenting subsequent work."

    def remember_with_fixture_lesson(root, outcome):
        remember(root, outcome)
        # Supply one learned fact after verified delivery. The production loop,
        # next Manager input, PG and HTTP retrieval are real; learning text and
        # Manager judgments remain offline fixtures.
        RoleMemoryStore(root).append_memory_event("orchestrator", horizon="long-term", text=lesson,
            source="fixture-learned:" + outcome["delivery"]["receipt_id"])
        mode["truncated"] = not retrieval_available

    monkeypatch.setattr(project_outcome_discovery, "_remember", remember_with_fixture_lesson)
    test_verified_outcome_creates_once_then_delivers_next_iteration_within_frozen_authority(tmp_path, monkeypatch)
    events = [json.loads(line) for line in (tmp_path / "cli-calls.jsonl").read_text().splitlines()]
    packet = next(event["packet"] for event in events if event["kind"] == "manager_discovery")
    if retrieval_available:
        assert packet["memory_pickup"]["context_status"] == "available"
        assert any(item["text"] == lesson for item in packet["memory_pickup"]["relevant_events"])
    else:
        assert packet["memory_pickup"]["context_status"] == "partial"
        assert "retrieval_unavailable" in packet["memory_pickup"]["context_reasons"]
        assert packet["memory_pickup"]["relevant_events"] == []
    assert packet["memory_pickup"]["provenance"]["retrieval"]["model"] == "fixture-embedding"
    assert len([event for event in events if event["kind"] == "worker"]) == 2
