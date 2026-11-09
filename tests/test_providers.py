"""Provider configuration in disposable PG; local HTTP/CLI fixtures only."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

from goodteam_os.database import database
from goodteam_os.providers import ProviderError, ProviderStore
from goodteam_os.provider_usage import usage_summary
from goodteam_os.workers import WorkerJobStore


@pytest.fixture
def provider_store(monkeypatch):
    url = os.environ.get("GOODTEAM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires a dedicated disposable PostgreSQL database")
    monkeypatch.setenv("GOODTEAM_DATABASE_URL", url)
    monkeypatch.setenv("GOODTEAM_DATABASE_SCHEMA", "gt_provider_" + uuid4().hex)
    return ProviderStore()


def save(store, **values):
    result = store.update({"action": "save", "expected_revision": store.view()["revision"],
        "name": "Test API", "agent": "codex", "protocol": "responses",
        "base_url": "http://127.0.0.1:1/v1", "model": "test-model", **values})
    return result["providers"][-1]


def activate(store, identifier):
    return store.update({"action": "activate", "id": identifier, "expected_revision": store.view()["revision"]})


def test_pg_connections_restart_revision_and_secret_boundary(provider_store, tmp_path, monkeypatch):
    store = provider_store
    saved = save(store, api_key="synthetic-provider-secret")
    activate(store, saved["id"])
    original = store.capture()
    assert saved["has_api_key"] and "api_key" not in saved
    assert "synthetic-provider-secret" not in json.dumps(store.view())
    assert not store.path.exists()  # This path is only the existing PG document identity.
    restarted = subprocess.run([sys.executable, "-c",
        "import json; from goodteam_os.providers import ProviderStore; print(json.dumps(ProviderStore().view()))"],
        check=True, capture_output=True, text=True)
    assert json.loads(restarted.stdout) == store.view()
    with database().connection() as conn:
        row = conn.execute("SELECT payload FROM state_documents WHERE path=%s", (str(store.path),)).fetchone()
        assert row["payload"]["providers"][0]["api_key"] == "synthetic-provider-secret"
    stale = store.view()["revision"]
    save(store, id=saved["id"], name="Updated")
    assert store.active()["api_key"] == "synthetic-provider-secret"
    with pytest.raises(ProviderError, match="changed"):
        store.update({"action": "activate", "id": "", "expected_revision": stale})
    with pytest.raises(ProviderError, match="edited or removed"):
        store.resolve(original)
    with pytest.raises(ProviderError, match="Switch"):
        store.update({"action": "delete", "id": saved["id"], "expected_revision": store.view()["revision"]})
    profile = tmp_path / "models.yaml"
    profile.write_text('profiles:\n  balanced:\n    implementation:\n      primary: "codex:old"\n')
    from goodteam_os.models import resolve_project_model_profile
    assert resolve_project_model_profile(profile, {})[1]["implementation"]["primary"] == "codex:test-model"
    assert resolve_project_model_profile(profile, {"role_model_overrides": {
        "implementation": "codex:explicit"}})[1]["implementation"]["primary"] == "codex:explicit"
    activate(store, "")
    store.update({"action": "delete", "id": saved["id"], "expected_revision": store.view()["revision"]})
    assert not store.view()["providers"] and store.resolve(None) is None
    monkeypatch.delenv("GOODTEAM_DATABASE_URL")
    with pytest.raises(ValueError, match="PostgreSQL"):
        store.view()


def test_model_fetch_uses_saved_key_and_rejects_redirects():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            if len(requests) == 1:
                raw = json.dumps({"data": [{"id": "model-z"}, {"id": "model-a"}, {"id": "model-a"}]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(raw)
            else:
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/leaked")
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        from goodteam_os.providers import fetch_models

        connection = {"base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_key": "fixture-key"}
        assert fetch_models(connection)["models"] == ["model-a", "model-z"]
        with pytest.raises(ProviderError) as error:
            fetch_models(connection)
        assert "fixture-key" not in str(error.value)
        assert requests == [("/v1/models", "Bearer fixture-key")] * 2
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_codex_connection_is_pinned_and_usage_counts_actual_attempts(provider_store, tmp_path, monkeypatch):
    store = provider_store
    first = save(store, name="First", api_key="fixture-secret")
    activate(store, first["id"])
    binary = tmp_path / "bin" / "codex"
    binary.parent.mkdir()
    binary.write_text(f"#!{sys.executable}\n" + r'''import json, os, sys, tomllib
from pathlib import Path
assert not any(k in os.environ for k in ('GOODTEAM_DATABASE_URL', 'GOODTEAM_TEST_DATABASE_URL', 'GOODTEAM_DATABASE_SCHEMA'))
if '--version' in sys.argv:
    print('offline provider fixture')
    raise SystemExit(0)
prompt = sys.stdin.read()
key = os.environ['GOODTEAM_CODEX_PROVIDER_API_KEY']
assert key == 'fixture-secret' and key not in prompt and key not in str(sys.argv)
assert '--json' in sys.argv
config = {}
for i, item in enumerate(sys.argv):
    if item == '-c':
        config.update(tomllib.loads(sys.argv[i+1]))
provider = config['model_providers']['goodteam_api']
assert Path(config['model_catalog_json']).is_file()
assert config['model_provider'] == 'goodteam_api' and provider['name'] == 'First'
assert provider['wire_api'] == 'responses' and provider['requires_openai_auth'] is False
assert provider['base_url'] == 'http://127.0.0.1:1/v1'
assert provider['env_key'] == 'GOODTEAM_CODEX_PROVIDER_API_KEY'
counter = Path(os.environ['PROVIDER_TEST_COUNTER'])
count = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(count))
if count <= 2:
    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':40,'output_tokens':5}}))
print(key, file=sys.stderr)
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('Done. ' + key)
if count == 1:
    print('ERROR: unexpected status 503 Service Unavailable', file=sys.stderr)
    raise SystemExit(1)
''')
    binary.chmod(0o755)
    home = tmp_path / "empty-home"
    home.mkdir()
    (home / "config.toml").write_text('model_catalog_json = "catalog.json"\n')
    (home / "catalog.json").write_text('{"models": []}')
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PROVIDER_TEST_COUNTER", str(tmp_path / "counter"))
    monkeypatch.setenv("GOODTEAM_WORKER_MAX_ATTEMPTS", "2")
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    monkeypatch.setattr("goodteam_os.workers._sleep", lambda _seconds: None)
    workers = WorkerJobStore(tmp_path / "project")

    def new_job():
        return workers.create_job(run_id="provider-test", role="implementation", adapter="codex",
                                  task="Offline provider test", context="Local fixture only", project_key="DEMO")

    job = new_job()
    second = save(store, name="Second", api_key="another-fixture-key", model="other-model")
    activate(store, second["id"])
    status = workers.run_job(job["id"])
    assert status["status"] == "completed" and status["attempts"] == 2
    assert status["model"] == "test-model" and status["provider_connection"]["id"] == first["id"]
    assert status["token_usage"]["total_tokens"] == 105 and status["usage_recorded"]
    for file in (workers.root / job["id"]).rglob("*"):
        if file.is_file() and not file.is_symlink():
            assert b"fixture-secret" not in file.read_bytes()
    activate(store, first["id"])
    unknown = workers.run_job(new_job()["id"])
    assert unknown["token_usage"] is None and unknown["usage_recorded"]
    totals = usage_summary()["totals"]
    assert totals == {"invocations": 3, "reported_invocations": 2, "input_tokens": 200,
                      "cached_input_tokens": 80, "output_tokens": 10, "total_tokens": 210}
    assert usage_summary()["totals"] == totals
    queued = new_job()
    save(store, id=first["id"], api_key="rotated")
    with pytest.raises(ProviderError, match="edited or removed"):
        workers.run_job(queued["id"])
    assert usage_summary()["totals"] == totals


def test_provider_bound_pg_project_can_pick_execute_learn_and_pick_again(provider_store, tmp_path, monkeypatch):
    from test_project_iteration_continuation import (
        test_verified_delivery_automatically_replans_next_scoped_batch_and_survives_replay,
    )
    current = save(provider_store, api_key="iteration-fixture-key")
    activate(provider_store, current["id"])
    observed = []
    original = WorkerJobStore._run_cli_backend

    def run(self, job_root, job, spec):
        assert job["provider_connection"] == {"id": current["id"], "revision": 1}
        result = original(self, job_root, job, spec)
        observed.append(result["provider_connection"]["id"])
        return result

    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", run)
    test_verified_delivery_automatically_replans_next_scoped_batch_and_survives_replay(tmp_path, monkeypatch)
    assert len(observed) == 2 and set(observed) == {current["id"]}
