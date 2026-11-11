import asyncio
import json
import os
import subprocess

import httpx
import pytest

from goodteam_os.web_search import JobWebSearch, request_search, search_result


POLICY = {"provider": {"id": "provider-test", "revision": 1}, "model": "search-model", "max_requests": 2, "timeout_seconds": 5}
PAYLOAD = {"id": "resp-1", "status": "completed", "usage": {"input_tokens": 12, "output_tokens": 20}, "output": [
    {"type": "web_search_call", "id": "ws-1", "status": "completed", "action": {"type": "search", "sources": [{"url": "https://example.com/evidence", "title": "Evidence"}]}},
    {"type": "message", "content": [{"type": "output_text", "text": "Supported answer.", "annotations": [
        {"type": "url_citation", "url": "https://example.com/evidence", "title": "Evidence", "start_index": 0, "end_index": 9}]}]},
]}


def test_pi_search_extension_sdk_receipt_replay_and_stop(tmp_path, monkeypatch):
    import goodteam_os.web_search as search
    requests = []
    original_client = httpx.AsyncClient

    def handle(request):
        requests.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer private-test-key"
        return httpx.Response(200, json=PAYLOAD)

    monkeypatch.setattr(search, "_http_client", lambda timeout: original_client(transport=httpx.MockTransport(handle), timeout=timeout))
    class Providers:
        def resolve(self, ref):
            assert ref == POLICY["provider"]
            return {"protocol": "responses", "base_url": "https://provider.example/v1", "api_key": "private-test-key"}
    job = {"id": "job-search", "run_id": "run-1", "project_task_id": "task-1", "web_search": POLICY}
    host = JobWebSearch(tmp_path, job, active=lambda: True, provider_store=Providers())
    env = host.start()
    # Execute the exact installed Pi tool extension in Node, not just a helper.
    script = '''
      import extension from './src/goodteam_os/pi_web_search.mjs';
      let tool; extension({registerTool: value => {tool=value}});
      const result = await tool.execute('call-1', {query:'public research query'}, new AbortController().signal);
      console.log(JSON.stringify(result));
    '''
    try:
        result = subprocess.run(["node", "--input-type=module", "-e", script], env={**os.environ, **env}, capture_output=True, text=True, check=True)
        returned = json.loads(result.stdout)["details"]
        assert returned["status"] == "searched" and returned["job"]["project_task_id"] == "task-1"
        assert returned["citations"][0]["start_index"] == 0
        assert returned["usage"]["input_tokens"] == 12
        assert len(requests) == 1 and requests[0]["tools"] == [{"type": "web_search"}]
        assert requests[0]["input"] == "public research query" and "task-1" not in json.dumps(requests)
        assert requests[0]["include"] == ["web_search_call.action.sources"]
        assert "private-test-key" not in (tmp_path / "web_search.json").read_text()
        assert "https://example.com/evidence" in host.evidence_text()
        # Restart/recovery reads saved result rather than spending again.
        resumed = JobWebSearch(tmp_path, job, active=lambda: True, provider_store=Providers())
        assert resumed.call("public research query") == returned and len(requests) == 1
        host.stop()
        assert host.call("new query")["status"] == "not_authorized_or_stopped"
        assert host.call("public research query") == returned
        assert len(requests) == 1
    finally:
        host.close()


def test_search_unknown_no_retry_budget_and_actual_search_evidence(tmp_path):
    calls = []
    async def fail(*args):
        calls.append(args)
        raise TimeoutError("secret credential")
    class Providers:
        def resolve(self, _ref):
            return {"protocol": "responses"}
    job = {"id": "job-x", "web_search": {**POLICY, "max_requests": 1}}
    host = JobWebSearch(tmp_path, job, active=lambda: True, provider_store=Providers(), requester=fail)
    assert host.call("query")["status"] == "failed_or_unknown"
    assert host.call("query")["status"] == "failed_or_unknown" and len(calls) == 1
    assert host.call("different query")["status"] == "request_budget_exhausted"
    assert "secret" not in (tmp_path / "web_search.json").read_text()
    assert search_result({"output": PAYLOAD["output"][1:]})["status"] == "not_searched"
    assert search_result({"output": [{"type": "web_search_call", "status": "completed"}]})["status"] == "searched_no_sources"
    changed = JobWebSearch(tmp_path, {**job, "web_search": POLICY}, active=lambda: True, provider_store=Providers())
    assert changed.call("query")["status"] == "authorization_changed"
    with pytest.raises(ValueError):
        JobWebSearch(tmp_path, {"id": "x", "web_search": {**POLICY, "max_requests": True}}, active=lambda: True)


def test_sdk_unsupported_tool_is_not_retried(monkeypatch):
    import goodteam_os.web_search as search
    calls = []
    original = httpx.AsyncClient
    def reject(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "unsupported web_search"}})
    monkeypatch.setattr(search, "_http_client", lambda timeout: original(transport=httpx.MockTransport(reject), timeout=timeout))
    with pytest.raises(Exception):
        asyncio.run(request_search({"base_url": "https://provider.example/v1", "api_key": ""}, POLICY, "query"))
    assert len(calls) == 1


def test_pi_runtime_installs_scoped_tool_and_returns_evidence(tmp_path, monkeypatch):
    from pathlib import Path
    from goodteam_os.session_runtimes import PiRuntimeConfig, PiSessionRuntime
    job_root = tmp_path / 'job'
    job_root.mkdir()
    config = PiRuntimeConfig(str((Path(__file__).parent / 'fixtures' / 'fake_pi_rpc.py').resolve()), '', '', '', tmp_path / 'sessions', 2)
    runtime = PiSessionRuntime(project_root=tmp_path, job_root=job_root,
        job={'id': 'ordinary-worker', 'web_search': POLICY}, runtime_cwd=tmp_path,
        config=config, stderr_path=job_root / 'stderr.txt')
    try:
        runtime.start_or_resume()
        assert runtime.web_search is not None
        assert "--extension" in runtime.rpc.command
        assert runtime.rpc.command[-1].endswith("pi_web_search.mjs")
        assert runtime.rpc.env["GOODTEAM_SEARCH_TOKEN"] == runtime.web_search.token
        assert runtime.rpc.env["GOODTEAM_SEARCH_URL"].startswith("http://127.0.0.1:")
        assert runtime.web_search.call('query')['status'] in {'provider_configuration_changed', 'provider_unavailable'}
        runtime.web_search._save({'policy': POLICY, 'job_id': 'ordinary-worker', 'requests': {'x': {'status': 'searched', 'response_id': 'resp-test',
            'sources': [{'url': 'https://example.com/evidence'}]}}})
        assert 'https://example.com/evidence' in runtime.get_last_assistant_text()
        runtime.abort()
        assert runtime.web_search.closed
        assert runtime.web_search.call("after stop")["status"] == "not_authorized_or_stopped"
    finally:
        runtime.close()


def test_job_search_authorization_is_persisted_and_not_reinterpreted(tmp_path):
    from goodteam_os.workers import WorkerJobStore
    jobs = WorkerJobStore(tmp_path)
    spec = {'run_id': 'r', 'role': 'research', 'adapter': 'pi', 'task': 'Find public facts', 'context': '', 'web_search': POLICY}
    job, _ = jobs.create_or_reuse_job(idempotency_key='search-1', **spec)
    assert job['web_search'] == POLICY
    assert jobs.create_or_reuse_job(idempotency_key='search-1', **spec)[0]['id'] == job['id']
    with pytest.raises(ValueError, match='search authorization changed'):
        jobs.create_or_reuse_job(idempotency_key='search-1', **{**spec, 'web_search': None})
    with pytest.raises(ValueError, match='ordinary Pi'):
        jobs.create_job(**{**spec, 'adapter': 'fake'})
    with pytest.raises(ValueError, match='ordinary Pi'):
        jobs.create_job(**{**spec, 'task_skill_context': {}})
