import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from goodteam_os import model_discovery, workers


def test_parallel_provider_and_singleflight(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def probe(provider):
        calls.append(provider)
        if provider == "agy":
            entered.set()
            assert release.wait(3)
        return [{"backend": provider}]

    monkeypatch.setattr(model_discovery, "_probe", probe)
    with ThreadPoolExecutor(max_workers=3) as clients:
        slow = clients.submit(model_discovery.discover, "agy")
        assert entered.wait(1)
        duplicate = clients.submit(model_discovery.discover, "antigravity")
        assert clients.submit(model_discovery.discover, "codex").result(timeout=1) == [{"backend": "codex"}]
        release.set()
        assert duplicate.result(timeout=1) == slow.result(timeout=1)
    assert calls.count("agy") == 1


def test_completed_probe_is_refreshed_after_failure(monkeypatch):
    observations = iter([
        [{"backend": "codex", "models_source": "unavailable"}],
        [{"backend": "codex", "models_source": "live"}],
    ])
    monkeypatch.setattr(model_discovery, "_pending", {})
    monkeypatch.setattr(model_discovery, "_probe", lambda _provider: next(observations))

    assert model_discovery.discover("codex")[0]["models_source"] == "unavailable"
    assert model_discovery.discover("codex")[0]["models_source"] == "live"


def test_fake_cli_timeout_is_not_cached_or_live(tmp_path, monkeypatch):
    cli = tmp_path / "codex"
    cli.write_text(f"#!{sys.executable}\nprint('{{\"models\":[]}}')\n")
    cli.chmod(0o755)
    monkeypatch.setattr(workers, "worker_backend_doctor", lambda _: {"available": True, "command_path": str(cli)})
    real_run = workers.subprocess.run
    calls = 0

    def bounded_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired(args[0], 0.05)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(workers.subprocess, "run", bounded_run)
    failed = model_discovery.discover("codex")[0]
    assert failed["models_source"] == "unavailable"
    assert failed["models"] == []
    payload = {"models": [{"slug": "real-model", "visibility": "list", "supported_reasoning_levels": [],
                           "default_reasoning_level": "invented"}]}
    cli.write_text(f"#!{sys.executable}\nprint({json.dumps(payload)!r})\n")
    recovered = model_discovery.discover("codex")[0]
    assert calls == 2
    assert recovered["models_source"] == "live"
    assert recovered["model_capabilities"] == {"real-model": {"reasoning_depths": []}}
    assert recovered["observed_at"]


def test_fast_fake_cli_finishes_while_slow_cli_is_running(tmp_path, monkeypatch):
    entered, release = tmp_path / "entered", tmp_path / "release"
    slow, fast = tmp_path / "agy", tmp_path / "codex"
    slow.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport time\n"
                    f"Path({str(entered)!r}).touch()\n"
                    f"while not Path({str(release)!r}).exists(): time.sleep(.01)\nprint('slow-model')\n")
    fast.write_text(f"#!{sys.executable}\nprint('{{\"models\":[{{\"slug\":\"fast-model\",\"visibility\":\"list\"}}]}}')\n")
    for cli in (slow, fast):
        cli.chmod(0o755)
    monkeypatch.setattr(workers, "worker_backend_doctor", lambda provider: {
        "available": True, "command_path": str(tmp_path / provider),
    })
    with ThreadPoolExecutor(max_workers=2) as clients:
        pending = clients.submit(model_discovery.discover, "agy")
        try:
            deadline = time.monotonic() + 2
            while not entered.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert entered.exists()
            assert clients.submit(model_discovery.discover, "codex").result(timeout=2)[0]["models"] == ["fast-model"]
            assert not pending.done()
        finally:
            release.touch()
        assert pending.result(timeout=2)[0]["models"] == ["slow-model"]


@pytest.mark.parametrize("depths,depth,accepted", [([], "high", False), (["low"], "high", False),
                                                (["high"], "high", True), (["ultra"], "mystery", False)])
def test_changed_selection_requires_model_evidence(monkeypatch, depths, depth, accepted):
    monkeypatch.setattr(model_discovery, "discover", lambda _: [{
        "model_capabilities": {"m": {"reasoning_depths": depths}}, "reasoning_depths": ["high"],
    }])
    runtime = {"provider": "codex", "model": "m", "reasoning_depth": depth}
    if accepted:
        model_discovery.validate_runtime_choice(runtime)
    else:
        with pytest.raises(ValueError, match="not confirmed"):
            model_discovery.validate_runtime_choice(runtime)


def test_automatic_and_unchanged_history_do_not_probe(monkeypatch):
    def fail(_):
        pytest.fail("offline read/unchanged choice must not probe")

    monkeypatch.setattr(model_discovery, "discover", fail)
    runtime = {"provider": "codex", "model": "old", "reasoning_depth": "high"}
    model_discovery.validate_runtime_choice(runtime, dict(runtime))
    model_discovery.validate_runtime_choice({**runtime, "additional_prompt": "changed"}, runtime)
    model_discovery.validate_runtime_choice({**runtime, "reasoning_depth": "default"}, runtime)


def test_unknown_provider_rejected_without_starting_work():
    with pytest.raises(ValueError, match="Unknown"):
        model_discovery.discover("unknown")


@pytest.mark.parametrize("canonical,alias", [("claude", "anthropic"), ("agy", "antigravity"), ("pi", "pi-rpc")])
@pytest.mark.parametrize("alias_first", [False, True])
def test_batch_alias_choices_share_one_canonical_observation(monkeypatch, canonical, alias, alias_first):
    calls = []

    def discover(provider):
        calls.append(provider)
        # A second observation changes support; the same transaction must not
        # split validation across two different capability snapshots.
        return [{"model_capabilities": {"m": {"reasoning_depths": ["high"] if len(calls) == 1 else []}}}]

    monkeypatch.setattr(model_discovery, "discover", discover)
    catalogs = {}
    for provider in ([alias, canonical] if alias_first else [canonical, alias]):
        model_discovery.validate_runtime_choice({"provider": provider, "model": "m", "reasoning_depth": "high"},
                                               catalogs=catalogs)
    assert calls == [canonical]
    assert list(catalogs) == [canonical]


def test_batch_reuses_catalog_and_rejects_without_persisting(tmp_path, monkeypatch):
    from goodteam_os.projects import write_project_config
    from goodteam_os.team_control_plane import TeamControlPlaneStore, ensure_team_state

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = write_project_config(workspace / "projects", "demo", "Demo", repo_path=str(tmp_path), worker_mode="fake")
    ensure_team_state(workspace, "demo", config)
    store = TeamControlPlaneStore(workspace, "demo")
    before = store.read()
    calls = []

    def discover(provider):
        calls.append(provider)
        return [{"model_default": "m", "model_capabilities": {"m": {"reasoning_depths": ["high"]}}}]

    monkeypatch.setattr(model_discovery, "discover", discover)
    commands = [{"kind": "change_worker_runtime", "worker_id": w["id"], "provider": "codex", "model": "m",
                 "reasoning_depth": "high"} for w in before["workers"][:2]]
    store.execute(commands, actor="user", surface="team_ui")
    assert calls == ["codex"]
    snapshot = store.read()
    calls.clear()
    # The capability has changed since selection; reject before persisting any
    # part of the next atomic command batch.
    with pytest.raises(ValueError, match="not confirmed"):
        store.execute([{**commands[0], "reasoning_depth": "ultra"},
                       {"kind": "rename_worker", "worker_id": commands[1]["worker_id"], "name": "Changed"}],
                      actor="user", surface="team_ui")
    assert store.read() == snapshot


def test_http_provider_query_isolated_from_slow_provider(tmp_path, monkeypatch):
    import urllib.request
    from goodteam_os import web

    entered = threading.Event()
    release = threading.Event()

    def probe(provider):
        if provider == "agy":
            entered.set()
            assert release.wait(5)
        return [{"backend": provider, "models": ["fake-model"], "models_source": "live"}]

    monkeypatch.setattr(model_discovery, "_probe", probe)
    import queue
    created = queue.Queue()
    server_type = web.ThreadingHTTPServer

    class CapturingServer(server_type):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.put(self)

    monkeypatch.setattr(web, "ThreadingHTTPServer", CapturingServer)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    thread = threading.Thread(target=web.serve, kwargs={"workspace": tmp_path / "workspace", "port": 0,
                                                      "frontend_dist": dist}, daemon=True)
    thread.start()
    server = created.get(timeout=5)
    base = f"http://127.0.0.1:{server.server_port}/api/agents?provider="

    def get(provider):
        with urllib.request.urlopen(base + provider, timeout=3) as response:
            return json.load(response)

    try:
        with ThreadPoolExecutor(max_workers=2) as clients:
            slow = clients.submit(get, "agy")
            assert entered.wait(1)
            assert clients.submit(get, "codex").result(timeout=2)["agents"][0]["backend"] == "codex"
            release.set()
            assert slow.result()["agents"][0]["backend"] == "agy"
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
