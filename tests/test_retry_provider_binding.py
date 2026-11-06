"""Legacy retry routes through real offline CLI processes in disposable roots."""
from copy import deepcopy
import hashlib
import json
import os
import sys

import pytest

from goodteam_os.assistant_requests import worker_attempt_id
from goodteam_os.manager_execution import ManagerExecutionError, run_manager_chat_turn
from goodteam_os.providers import ProviderError, ProviderStore
from goodteam_os.reviewer_retry_inputs import create_reviewer_retry
from goodteam_os.workers import WorkerJobStore


@pytest.mark.parametrize("kind", ["manager", "reviewer"])
@pytest.mark.parametrize("binding", ["managed", "local", "historical"])
def test_legacy_retry_keeps_original_connection_after_provider_switch(tmp_path, monkeypatch, kind, binding):
    providers = [{"id": "provider-" + digit * 32, "revision": 1, "name": name,
                  "agent": "codex", "protocol": "responses", "base_url": "http://127.0.0.1:1/v1",
                  "model": "fixture-model", "api_key": name + "-fixture-key"}
                 for digit, name in (("a", "Original"), ("b", "New"))]
    state = {"providers": providers, "active": {"codex": providers[0]["id"]} if binding == "managed" else {}}
    monkeypatch.setattr(ProviderStore, "read", lambda _self: deepcopy(state))
    monkeypatch.delenv("GOODTEAM_DATABASE_URL", raising=False)
    monkeypatch.delenv("GOODTEAM_CODEX_PROVIDER_API_KEY", raising=False)
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    monkeypatch.setenv("GOODTEAM_WORKER_MAX_ATTEMPTS", "1")
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("RETRY_FIXTURE_CALLS", str(calls))
    monkeypatch.setenv("RETRY_FIXTURE_FAIL", "1")
    binary = tmp_path / "bin" / "codex"
    binary.parent.mkdir()
    binary.write_text(f"#!{sys.executable}\n" + r'''import json, os, sys, tomllib
from pathlib import Path
if '--version' in sys.argv:
    print('offline retry fixture')
    raise SystemExit(0)
config = {}
for i, arg in enumerate(sys.argv):
    if arg == '-c':
        config.update(tomllib.loads(sys.argv[i+1]))
provider = config.get('model_providers', {}).get('goodteam_api')
key = os.environ.get('GOODTEAM_CODEX_PROVIDER_API_KEY')
assert key == (provider['name'] + '-fixture-key' if provider else None)
prompt = sys.stdin.read()
with Path(os.environ['RETRY_FIXTURE_CALLS']).open('a') as stream:
    stream.write(json.dumps({'provider': provider['name'] if provider else 'local', 'prompt': prompt}) + '\n')
if os.environ['RETRY_FIXTURE_FAIL'] == '1':
    print('ERROR: offline fixture failure', file=sys.stderr)
    raise SystemExit(1)
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(
    json.dumps({'intent': 'ambiguous', 'response': 'Offline retry completed.'}))
''')
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "projects" / "demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nrepos:\n  root: {project}\nworker_backend:\n  mode: codex\n"
                      "role_model_overrides:\n  orchestrator: codex:fixture-model\n")
    workers = WorkerJobStore(project)
    context = "Original captured context for " + kind

    def manager_turn(attempt, captured_context):
        return run_manager_chat_turn(project_config=config, project_key="DEMO", text="Inspect this project",
            context=captured_context, logical_message_id="original-message",
            message_id=worker_attempt_id("DEMO", "original-message", attempt))

    if kind == "manager":
        with pytest.raises(ManagerExecutionError):
            manager_turn(1, context)
        original = workers.list_jobs()[0]
    else:
        original = workers.create_job(run_id="review-run", project_key="DEMO", role="code_review",
            adapter="codex", model="fixture-model", task="Review captured input", context=context,
            execution_policy="read_only", idempotency_key="original-review")
        assert workers.run_job(original["id"])["status"] == "failed"
    original_path = workers.root / original["id"] / "job.json"
    if binding == "historical":
        saved = json.loads(original_path.read_text())
        saved.pop("provider_connection")
        original_path.write_text(json.dumps(saved))
    original_bytes = original_path.read_bytes()
    reference = json.loads(original_bytes).get("provider_connection")
    state["active"]["codex"] = providers[1]["id"]
    monkeypatch.setenv("RETRY_FIXTURE_FAIL", "0")

    def retry():
        if kind == "manager":
            result = manager_turn(2, "Newly rendered context must not replace the captured input")
            return workers.root / result["manager_execution"]["job_id"]
        job, created = create_reviewer_retry(workers, original, retry_attempt=2,
            idempotency_key=hashlib.sha256(b"review-retry").hexdigest())
        if created:
            assert workers.run_job(job["id"])["status"] == "completed"
        return workers.root / job["id"]

    retry_root = retry()
    observed = [json.loads(line) for line in calls.read_text().splitlines()]
    expected_provider = "Original" if binding == "managed" else "local"
    assert [call["provider"] for call in observed] == [expected_provider, expected_provider]
    assert all(context in call["prompt"] for call in observed)
    assert json.loads((retry_root / "job.json").read_text())["provider_connection"] == reference
    assert retry() == retry_root and len(calls.read_text().splitlines()) == 2
    assert original_path.read_bytes() == original_bytes
    fresh = workers.create_job(run_id="fresh", role="implementation", adapter="codex", task="New task", context="")
    assert fresh["provider_connection"] == {"id": providers[1]["id"], "revision": 1}
    if binding == "managed":
        providers[0]["revision"] += 1
        with pytest.raises(ProviderError) as error:
            workers.run_job(retry_root.name)
        assert error.value.code == "provider_connection_changed"
        state["providers"] = [providers[1]]
        with pytest.raises(ProviderError):
            workers.run_job(retry_root.name)
        assert len(calls.read_text().splitlines()) == 2
