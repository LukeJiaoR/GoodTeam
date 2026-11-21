"""Full HTTP + SSE integration tests and Phase-1 security/reliability regressions."""
from __future__ import annotations

import base64
import http.client
import http.server
import io
import json
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

import pytest

from goodteam.agents.adapters import AdapterResult, set_mock_handler
from goodteam.agents.roles import save_role
from goodteam.agents.runner import SubagentRunner
from goodteam.api.server import create_server
from goodteam.cli import main as cli_main
from goodteam.providers import ProviderStore
from goodteam.skills.injector import cleanup_staged_skills, stage_and_render
from goodteam.skills.installer import ensure_builtins, preview_zip
from goodteam.skills.models import SkillError
from goodteam.store import Store


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "src").mkdir()
    (path / "src" / "main.py").write_text("print('init')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


def _make_zip(entries: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _http_json(
    base_url: str,
    method: str,
    path: str,
    body: dict | bytes | None = None,
    *,
    content_type: str = "application/json",
) -> tuple[int, dict]:
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if isinstance(body, bytes):
        data = body
        headers["Content-Type"] = content_type
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return exc.code, json.loads(raw) if raw.startswith("{") else {"error": raw}


def test_full_http_sse_lifecycle_and_safe_project_delete(tmp_path):
    repo = tmp_path / "demo_repo"
    _init_repo(repo)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><html><body>GoodTeam v2 SPA</body></html>", encoding="utf-8")
    state_dir = tmp_path / "state"

    # Tiny mock upstream provider for /api/providers/models & /test
    class MockProviderHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            payload = json.dumps({"data": [{"id": "gpt-5.4"}, {"id": "gpt-5.3-codex"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    prov_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockProviderHandler)
    prov_port = prov_srv.server_address[1]
    threading.Thread(target=prov_srv.serve_forever, daemon=True).start()

    wake_done = threading.Event()
    observed_skill_in_subagent = {"ok": False}

    def mock_turn(project_id, text, toolset, on_delta):
        if "Sub-agent execution finished" in text:
            on_delta("Summary: ")
            on_delta("feature ready for review.")
            wake_done.set()
            return "Summary: feature ready for review."
        spawned = toolset.call("spawn_agent", {"role": "feature_coder", "task": "Create src/feature.py"})
        on_delta(f"Dispatched {spawned['run_id']}")
        return f"Dispatched {spawned['run_id']}"

    def mock_subagent(ctx, on_event, stop_ev):
        observed_skill_in_subagent["ok"] = "Zip Skill Rules" in ctx.skill_prompt
        on_event("message", {"text": "Writing src/feature.py"})
        (ctx.worktree_path / "src" / "feature.py").write_text("FEATURE = 'enabled'\n", encoding="utf-8")
        return AdapterResult(
            summary="Created src/feature.py using zip skill.",
            usage={"input_tokens": 120, "cached_input_tokens": 10, "output_tokens": 45},
            exit_code=0,
        )

    set_mock_handler(mock_subagent)
    server, app = create_server(
        "127.0.0.1",
        0,
        state_dir=state_dir,
        frontend_dist=dist,
        mock_turn_fn=mock_turn,
        sse_keepalive_seconds=0.5,
    )
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()

    sse_events: list[tuple[str, dict]] = []
    sse_stop = threading.Event()
    sse_connected = threading.Event()

    def _read_sse(project_id: str) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", f"/api/projects/{project_id}/events")
            resp = conn.getresponse()
            assert resp.status == 200
            sse_connected.set()
            cur_event = ""
            while not sse_stop.is_set():
                line = resp.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text.startswith("event:"):
                    cur_event = text.split(":", 1)[1].strip()
                elif text.startswith("data:") and cur_event:
                    sse_events.append((cur_event, json.loads(text.split(":", 1)[1].strip())))
                    cur_event = ""
        except OSError:
            pass
        finally:
            conn.close()

    try:
        # 1. SPA static + Doctor + FS picker
        with urlopen(f"{base_url}/", timeout=5) as resp:
            assert resp.status == 200
            assert "GoodTeam v2 SPA" in resp.read().decode("utf-8")

        st, doc = _http_json(base_url, "GET", "/api/doctor")
        assert st == 200 and "git" in doc and "codex" in doc

        st, fs_data = _http_json(base_url, "GET", f"/api/fs/list?path={quote(str(tmp_path), safe='')}")
        assert st == 200 and fs_data["path"] == str(tmp_path.resolve())
        assert any(e["name"] == "demo_repo" and e["is_git_repo"] for e in fs_data["entries"])

        st, created_dir = _http_json(base_url, "POST", "/api/fs/mkdir", {"parent": str(tmp_path), "name": "new_sub"})
        assert st == 201 and (tmp_path / "new_sub").is_dir() and created_dir["name"] == "new_sub"

        # 2. Import project
        st, p_res = _http_json(base_url, "POST", "/api/projects", {"path": str(repo), "name": "Demo Repo"})
        assert st == 201
        project_id = p_res["project"]["id"]
        st, plist = _http_json(base_url, "GET", "/api/projects")
        assert st == 200 and len(plist["projects"]) == 1

        # 3. Zip skill preview & install (base64 JSON + raw zip) + URL-encoded skill_id read
        zip_bytes = _make_zip({
            "pkg/SKILL.md": "---\nname: zip-coder\ndescription: Zip helper\n---\n# Zip Skill Rules\nAlways test.\n",
        })
        b64_zip = base64.b64encode(zip_bytes).decode("ascii")
        st, prev = _http_json(base_url, "POST", "/api/skills/zip/preview", {"filename": "z.zip", "content_base64": b64_zip})
        assert st == 200 and prev["skills"][0]["slug"] == "zip-coder"

        st, inst = _http_json(base_url, "POST", "/api/skills/zip/install", {"filename": "z.zip", "content_base64": b64_zip})
        assert st == 201 and inst["skills"][0]["id"] == "zip:zip-coder"

        raw_zip2 = _make_zip({"raw/SKILL.md": "---\nname: raw-skill\ndescription: Raw\n---\nRaw body.\n"})
        st, raw_inst = _http_json(
            base_url, "POST", "/api/skills/zip/install?filename=raw.zip", raw_zip2, content_type="application/zip"
        )
        assert st == 201 and raw_inst["skills"][0]["id"] == "zip:raw-skill"

        enc_id = quote("zip:zip-coder", safe="")
        st, content = _http_json(base_url, "GET", f"/api/skills/{enc_id}/content")
        assert st == 200 and "Zip Skill Rules" in content["content"]

        # 4. Bind zip skill to custom role
        st, role_res = _http_json(base_url, "POST", f"/api/projects/{project_id}/roles", {
            "key": "feature_coder",
            "name": "Feature Coder",
            "description": "Builds features",
            "system_prompt": "Implement cleanly in src/.",
            "execution_policy": "workspace_write",
            "write_domains": ["src"],
            "adapter": "mock",
            "skill_bindings": [{"skill_id": "zip:zip-coder", "mode": "required"}],
        })
        assert st == 201 and role_res["role"]["key"] == "feature_coder"

        # 5. Memory + Manager settings + Providers (never leaks secret!)
        st, mem_res = _http_json(base_url, "PUT", f"/api/projects/{project_id}/memory", {
            "filename": "project.md", "content": "Core architecture notes.", "mode": "replace"
        })
        assert st == 200 and "Core architecture notes." in mem_res["content"]
        st, mem_search = _http_json(base_url, "GET", f"/api/projects/{project_id}/memory?query=architecture")
        assert st == 200 and len(mem_search["hits"]) == 1

        st, ms_res = _http_json(base_url, "PUT", f"/api/projects/{project_id}/manager-settings", {
            "model": "gpt-5.4", "reasoning_effort": "high", "additional_prompt": "Be crisp."
        })
        assert st == 200 and ms_res["settings"]["model"] == "gpt-5.4"

        st, prov_saved = _http_json(base_url, "POST", "/api/providers", {
            "agent": "codex", "name": "MockOAI", "base_url": f"http://127.0.0.1:{prov_port}/v1",
            "api_key": "sk-super-secret-value-999",
        })
        assert st == 201 and prov_saved["provider"]["has_api_key"] is True
        assert "sk-super-secret" not in json.dumps(prov_saved)
        prov_id = prov_saved["provider"]["id"]

        # Update provider with api_key=None must preserve saved key!
        st, prov_upd = _http_json(base_url, "POST", "/api/providers", {
            "id": prov_id, "name": "MockOAI Renamed", "api_key": None,
        })
        assert st == 201 and prov_upd["provider"]["has_api_key"] is True

        st, models_res = _http_json(base_url, "POST", "/api/providers/models", {"provider_id": prov_id})
        assert st == 200 and "gpt-5.4" in models_res["models"]
        st, test_res = _http_json(base_url, "POST", "/api/providers/test", {"provider_id": prov_id})
        assert st == 200 and test_res["ok"] is True

        # 6. Attachment & Task CRUD
        st, att_res = _http_json(base_url, "POST", f"/api/projects/{project_id}/attachments", {
            "filename": "spec.txt", "content_base64": base64.b64encode(b"hello spec").decode(), "mime_type": "text/plain"
        })
        assert st == 201 and att_res["size"] == 10

        st, task_res = _http_json(base_url, "POST", f"/api/projects/{project_id}/tasks", {"title": "Build feature"})
        assert st == 201
        task_id = task_res["task"]["id"]
        st, task_upd = _http_json(base_url, "PATCH", f"/api/projects/{project_id}/tasks/{task_id}", {"status": "in_progress"})
        assert st == 200 and task_upd["task"]["status"] == "in_progress"

        # 7. Connect SSE, send chat message -> spawns agent -> auto-wakeup -> delivery created!
        sse_thread = threading.Thread(target=_read_sse, args=(project_id,), daemon=True)
        sse_thread.start()
        assert sse_connected.wait(timeout=5)

        st, msg_res = _http_json(base_url, "POST", f"/api/projects/{project_id}/messages", {
            "content": "Build the new feature please", "attachments": [att_res],
        })
        assert st == 201 and msg_res["user_message"]["role"] == "user"
        assert wake_done.wait(timeout=10), "Auto-wakeup turn did not complete"
        time.sleep(0.2)

        assert observed_skill_in_subagent["ok"] is True
        event_names = {ev[0] for ev in sse_events}
        assert {"chat_message", "chat_delta", "chat_done", "run_updated", "run_event", "delivery_created"} <= event_names

        # 8. Inspect run, usage, and enriched delivery, then merge to main
        st, runs_res = _http_json(base_url, "GET", f"/api/projects/{project_id}/runs")
        assert st == 200 and len(runs_res["runs"]) == 1
        run_id = runs_res["runs"][0]["id"]

        st, run_detail = _http_json(base_url, "GET", f"/api/runs/{run_id}")
        assert st == 200 and len(run_detail["events"]) >= 1 and "FEATURE = 'enabled'" in run_detail["diff_text"]

        st, usage_res = _http_json(base_url, "GET", f"/api/usage?project_id={project_id}")
        assert st == 200 and usage_res["usage"][0]["input_tokens"] == 120

        st, delivs = _http_json(base_url, "GET", f"/api/projects/{project_id}/deliveries")
        assert st == 200 and len(delivs["deliveries"]) == 1
        d = delivs["deliveries"][0]
        assert d["changed_files"] == ["src/feature.py"]
        assert d["scope_ok"] is True and d["role_key"] == "feature_coder"
        assert "FEATURE = 'enabled'" in d["diff_text"]
        assert not (repo / "src" / "feature.py").exists()  # Main still untouched before human merge!

        st, merged = _http_json(base_url, "POST", f"/api/deliveries/{d['id']}/merge", {})
        assert st == 200 and merged["delivery"]["status"] == "merged" and merged["merge_commit"]
        assert (repo / "src" / "feature.py").read_text(encoding="utf-8") == "FEATURE = 'enabled'\n"

        # 9. Delete project -> DB & GoodTeam worktrees cleaned, local source dir STILL INTACT!
        sse_stop.set()
        st, del_res = _http_json(base_url, "DELETE", f"/api/projects/{project_id}")
        assert st == 200 and del_res["deleted"] is True
        assert repo.is_dir() and (repo / "src" / "feature.py").is_file()
        assert (repo / "src" / "main.py").is_file()

        st, plist_after = _http_json(base_url, "GET", "/api/projects")
        assert st == 200 and plist_after["projects"] == []

        # Uninstall custom zip skill cleanly
        st, uninst = _http_json(base_url, "DELETE", f"/api/skills/{enc_id}")
        assert st == 200 and uninst["deleted"] is True
    finally:
        sse_stop.set()
        set_mock_handler(None)
        server.shutdown()
        server.server_close()
        app.close()
        prov_srv.shutdown()
        prov_srv.server_close()


def test_phase1_security_and_reliability_regressions(tmp_path, capsys):
    # 1. Non-git project deletion must NEVER delete the local project folder
    nongit = tmp_path / "plain_folder"
    nongit.mkdir()
    (nongit / "important.txt").write_text("do not delete\n", encoding="utf-8")
    server, app = create_server(
        "127.0.0.1", 0, state_dir=tmp_path / "state",
        mock_turn_fn=lambda pid, txt, ts, cb: "ok",
    )
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        st, p = _http_json(base_url, "POST", "/api/projects", {"path": str(nongit)})
        assert st == 201
        pid = p["project"]["id"]
        save_role(app.store, pid, {"key": "mock_r", "name": "Mock", "adapter": "mock"})
        run = app.runner.spawn(pid, "mock_r", "hello")
        app.runner.wait(run["id"], timeout=5)
        st, _ = _http_json(base_url, "DELETE", f"/api/projects/{pid}")
        assert st == 200
        assert nongit.is_dir() and (nongit / "important.txt").read_text(encoding="utf-8") == "do not delete\n"
    finally:
        server.shutdown()
        server.server_close()
        app.close()

    # 2. Store.list_messages returns newest N messages in chronological order
    store = Store(state_dir=tmp_path / "store_state")
    try:
        p = store.create_project(name="m", path=str(nongit))
        for i in range(5):
            store.add_message(project_id=p["id"], role="user", content=f"msg-{i}")
        latest = store.list_messages(p["id"], limit=3)
        assert [m["content"] for m in latest] == ["msg-2", "msg-3", "msg-4"]

        # 3. Duplicate skill bindings in injector must not raise FileExistsError
        ensure_builtins(store)
        role = {
            "key": "dup", "name": "Dup",
            "skill_bindings": [
                {"skill_id": "builtin:coding", "mode": "required"},
                {"skill_id": "builtin:coding", "mode": "on_demand"},
            ],
        }
        inj = stage_and_render(store, role, "run-dup", state_dir=tmp_path / "store_state")
        assert inj.required_ids == ("builtin:coding",)
        assert inj.on_demand_ids == ()
        cleanup_staged_skills(inj.staged_dir)
    finally:
        store.close()

    # 4. Zip bomb (highly compressible > 20MB uncompressed payload) rejected during extraction
    bomb = _make_zip({"big/SKILL.md": "---\nname: big\n---\n", "big/zeros.bin": b"\x00" * (21 * 1024 * 1024)})
    with pytest.raises(SkillError) as exc:
        preview_zip(bomb)
    assert exc.value.code == "zip_too_large"

    # 5. Runner does NOT create delivery on scope violation, and stop() does not overwrite completed run
    repo = tmp_path / "repo2"
    _init_repo(repo)
    st2 = Store(state_dir=tmp_path / "r2_state")
    prov2 = ProviderStore(state_dir=tmp_path / "r2_state")
    try:
        proj2 = st2.create_project(name="r2", path=str(repo))
        save_role(st2, proj2["id"], {
            "key": "scoped", "name": "Scoped", "execution_policy": "workspace_write",
            "write_domains": ["src"], "adapter": "mock",
        })
        set_mock_handler(lambda ctx, on_ev, stop_ev: (
            (ctx.worktree_path / "outside.md").write_text("bad\n"),
            AdapterResult(summary="bad", exit_code=0),
        )[1])
        runner = SubagentRunner(st2, prov2, state_dir=tmp_path / "r2_state")
        try:
            r = runner.spawn(proj2["id"], "scoped", "edit outside")
            fin = runner.wait(r["id"], timeout=10)
            assert fin["status"] == "failed" and fin["scope_ok"] is False
            assert st2.list_deliveries(proj2["id"]) == []
            # Calling stop() on already-finished run keeps its terminal status
            after_stop = runner.stop(r["id"])
            assert after_stop["status"] == "failed"
        finally:
            runner.shutdown()
    finally:
        set_mock_handler(None)
        st2.close()

    # 6. CLI doctor --json runs cleanly
    rc = cli_main(["doctor", "--state-dir", str(tmp_path / "cli_state"), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "git" in out and "codex" in out
