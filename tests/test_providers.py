"""[INPUT]: 隔离 PostgreSQL、Git 工作区与本地进程替身。
[OUTPUT]: Provider 和 Role Skill 组合回归。
[POS]: 覆盖原派工、运行启动与失败重试。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
import json
import os
from pathlib import Path
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
    keyless = save(store, name="Local Codex")
    with pytest.raises(ProviderError, match="Pi requires an API key"):
        store.update({"action": "activate", "id": keyless["id"], "agent": "pi",
                      "expected_revision": store.view()["revision"]})
    store.update({"action": "delete", "id": keyless["id"], "expected_revision": store.view()["revision"]})
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
        connection["api_key"] = ""
        with pytest.raises(ProviderError) as error:
            fetch_models(connection)
        assert "fixture-key" not in str(error.value)
        assert requests == [("/v1/models", "Bearer fixture-key"), ("/v1/models", None)]
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
assert key == os.environ.get('GOODTEAM_TEST_EXPECTED_KEY', 'fixture-secret')
assert key not in prompt and key not in str(sys.argv)
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
    save(store, id=first["id"], name="First", api_key="rotated")
    monkeypatch.setenv("GOODTEAM_TEST_EXPECTED_KEY", "rotated")
    status = workers.run_job(queued["id"])
    assert status["status"] == "completed"
    assert status["provider_connection"]["revision"] == queued["provider_connection"]["revision"] + 1
    assert usage_summary()["totals"]["invocations"] == totals["invocations"] + 1


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


def test_readonly_qa_dispatch_uses_one_provider_and_binding_across_codex_and_pi(provider_store, tmp_path, monkeypatch):
    from hashlib import sha256
    from threading import Lock
    from goodteam_os import dispatch
    from goodteam_os.dispatch import dispatch_tracks
    from goodteam_os.dispatch_workspace import dispatch_artifact_root
    from goodteam_os.service import service_state_dir
    from goodteam_os.skills.library import SkillLibraryService
    from goodteam_os.skills.pi_resources import PiResourceChannel
    from goodteam_os.skills.worker_requests import public_skill_job_fields
    from goodteam_os.team_control_plane import TeamControlPlaneStore
    from test_pi_method_dispatch import configured

    source, workspace = configured(tmp_path)
    team = TeamControlPlaneStore(workspace, "DEMO")
    current = team.read()
    items = [{key: item[key] for key in ("skill_id", "version_id", "activation", "enabled")}
             for item in current["skill_bindings"]["role:implementation"]["items"]]
    library = SkillLibraryService(service_state_dir())
    for name in ("optional", "disabled"):
        folder = tmp_path / name
        (folder / "references").mkdir(parents=True)
        (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: QA fixture\n---\n{name} body\n")
        (folder / "references" / "guide.txt").write_text(f"{name} reference")
        installed = library.install(library.preview_local(str(folder), command_key=name)["proposal_id"])["installed"]
        items.append({key: installed[key] for key in ("skill_id", "version_id")} |
                     {"activation": "on_demand", "enabled": name == "optional"})
    team.execute([{"kind": "set_role_skills", "role": "qa", "mode": "custom",
                   "expected_binding_revision": current["skill_bindings"]["role:qa"]["binding_revision"],
                   "bindings": items}],
                 actor="user", surface="team_ui", expected_revision=current["revision"], command_key="bind-qa")
    connection = save(provider_store, api_key="fixture-shared-qa-key")
    for agent in ("codex", "pi"):
        provider_store.update({"action": "activate", "id": connection["id"], "agent": agent,
                               "expected_revision": provider_store.view()["revision"]})
    reference = {"id": connection["id"], "revision": connection["revision"]}
    assert provider_store.capture("codex") == provider_store.capture("pi") == reference

    bin_root = tmp_path / "bin"
    bin_root.mkdir()
    codex = bin_root / "codex"
    codex.write_text(f"#!{sys.executable}\n" + r'''import json, os, sys
from pathlib import Path
if '--version' in sys.argv:
    print('codex 0.171.0')
    raise SystemExit(0)
assert sys.argv[sys.argv.index('--sandbox') + 1] == 'read-only'
assert '--ignore-user-config' in sys.argv and '--ignore-rules' in sys.argv
assert 'wire_api = "responses"' in ' '.join(sys.argv)
assert os.environ['GOODTEAM_CODEX_PROVIDER_API_KEY'] == 'updated-qa-key'
payload = json.loads(sys.stdin.read().split('\n\n', 1)[0])
assert 'ORIGINAL ROLE METHOD' in payload['required_methods'][0]['text']
catalog = {item['name']: Path(item['resource_base']) for item in payload['method_catalog']}
assert set(catalog) == {'task-method', 'optional'}
assert 'optional body' not in json.dumps(payload)
assert 'ORIGINAL ROLE METHOD' in (catalog['task-method'] / 'SKILL.md').read_text()
assert (catalog['optional'] / 'references' / 'guide.txt').read_text() == 'optional reference'
print(catalog['optional'])
if os.environ.get('GOODTEAM_438_FAIL_QA'):
    raise SystemExit(1)
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('Codex QA returned.')
''')
    codex.chmod(0o755)
    pi = bin_root / "pi"
    script = (Path(__file__).parent / "fixtures/fake_pi_rpc.py").read_text()
    script = script.replace("thinking = ", r'''private_dir = Path(os.environ['PI_CODING_AGENT_DIR'])
provider_config = json.loads((private_dir / 'models.json').read_text())['providers'][provider]
assert provider_config == {'baseUrl': 'http://127.0.0.1:1/v1', 'api': 'openai-responses',
    'apiKey': '$GOODTEAM_PI_PROVIDER_API_KEY', 'models': [{'id': 'test-model'}]}
assert model == 'test-model' and '--no-extensions' in args
assert os.environ['GOODTEAM_PI_PROVIDER_API_KEY'] == 'updated-qa-key'
print(os.environ['GOODTEAM_PI_PROVIDER_API_KEY'], file=sys.stderr)
thinking = ''', 1)
    script = script.replace("    global streaming\n",
        "    global streaming\n    assert 'ORIGINAL ROLE METHOD' in message and 'optional body' not in message\n", 1)
    pi.write_text(script)
    pi.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_root) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GOODTEAM_PI_PATH", str(pi))
    monkeypatch.setenv("GOODTEAM_PI_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)

    def witness(channel):
        policy = channel.startup_policy
        assert channel.call({"operation": "tool_policy_witness", "version": policy.version, "nonce": policy.nonce,
                             "active": list(policy.tools), "available": list(policy.tools)})["status"] == "policy_witness_received"
        assert channel.call({"skill_id": items[1]["skill_id"], "relative": "references/guide.txt",
                             "call_id": "optional-example"})["text"] == "optional reference"
        assert channel.call({"skill_id": items[2]["skill_id"], "relative": "SKILL.md",
                             "call_id": "disabled-example"})["status"] == "resource_unavailable"
        return {}

    monkeypatch.setattr(PiResourceChannel, "start", witness)
    tracks = [{"role": "qa", "adapter": agent, "execution_policy": "read_only", "task": "Read the baseline.",
               "track_id": f"qa-{agent}", "project_task_id": f"qa-{agent}"} for agent in ("codex", "pi")]
    run_job = dispatch._run_dispatched_job
    pending_edit = Lock()
    updated = None

    def edit_before_start(project_root, job, verify_commands, **kwargs):
        nonlocal updated
        with pending_edit:
            if updated is None:
                updated = save(provider_store, id=connection["id"], api_key="updated-qa-key")
        return run_job(project_root, job, verify_commands, **kwargs)

    monkeypatch.setattr(dispatch, "_run_dispatched_job", edit_before_start)
    report = dispatch_tracks(project_root=source, logical_task_id="qa-cross-agent", require_isolation=True, tracks=tracks)
    assert report["all_ok"], [(row["adapter"], row["summary"]) for row in report["jobs"]]
    workers = WorkerJobStore(source)
    details = {job["id"]: public_skill_job_fields(job) for job in workers.list_jobs()}
    for row in report["jobs"]:
        root = workers.root / row["job_id"]
        job = json.loads((root / "job.json").read_text())
        assert job["provider_connection"] == reference
        assert job["status"] == "queued" and row["status"] == "completed"
        used = row["invocation_evidence"]["provider_connection"]
        assert (used["id"], used["revision"]) == (connection["id"], updated["revision"])
        assert details[row["job_id"]]["provider_connection"] == {"id": used["id"], "revision": used["revision"]}
        assert {item["version_id"] for item in row["method_items"]} == {item["version_id"] for item in items[:2]}
        assert row["method_input_status"] in {"transport_attempted", "prompt_acknowledged"}
        if row["adapter"] == "codex":
            assert not Path((root / "stdout.txt").read_text().strip()).exists()
        for name in ("job.json", "status.json", "stdout.txt", "stderr.txt", "result.md"):
            captured = (root / name).read_text()
            assert "fixture-shared-qa-key" not in captured and "updated-qa-key" not in captured

    monkeypatch.setenv("GOODTEAM_438_FAIL_QA", "1")
    failed = dispatch_tracks(project_root=source, logical_task_id="qa-failed", require_isolation=True, tracks=tracks[:1])
    failed_job = failed["jobs"][0]
    assert failed_job["status"] == "failed"
    old = json.loads((workers.root / failed_job["job_id"] / "job.json").read_text())
    monkeypatch.delenv("GOODTEAM_438_FAIL_QA")
    activate(provider_store, save(provider_store, name="New default", api_key="new-default")["id"])
    package = tmp_path / "method"
    skill = package / "SKILL.md"
    skill.write_text(skill.read_text().replace("ORIGINAL ROLE METHOD", "UPDATED ROLE METHOD"))
    replacement = library.install(library.preview_local(str(package), command_key="edit-qa-method")["proposal_id"])["installed"]
    assert replacement["version_id"] != items[0]["version_id"]
    current = team.read()
    team.execute([{"kind": "set_role_skills", "role": "qa", "mode": "custom",
                   "bindings": [{**items[0], "skill_id": replacement["skill_id"], "version_id": replacement["version_id"]}],
                   "expected_binding_revision": current["skill_bindings"]["role:qa"]["binding_revision"]}],
                 actor="user", surface="team_ui", expected_revision=current["revision"], command_key="empty-qa")
    receipt = dispatch_artifact_root(source, failed["run_id"]) / "report.json"
    retry = dispatch_tracks(project_root=source, logical_task_id="qa-retry", require_isolation=True,
        tracks=[{**tracks[0], "original_failed_job_id": old["id"], "retry_of_idempotency_key": old["idempotency_key"],
                 "retry_attempt": 1, "retry_method_mode": old["method_loading_mode"],
                 "provider_connection": old["provider_connection"]}],
        recovery_source={"run_id": failed["run_id"], "report_digest": sha256(receipt.read_bytes()).hexdigest(),
                         "retry_task_ids": ["qa-codex"]})
    assert retry["all_ok"], [(row["adapter"], row["summary"]) for row in retry["jobs"]]
    assert retry["jobs"][0]["task_skill_context"] == failed_job["task_skill_context"]
    assert retry["jobs"][0]["invocation_evidence"]["provider_connection"]["id"] == connection["id"]
