"""Exercise runner resource boundaries using synthetic registration and local subprocesses."""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Dedicated runner lifecycle uses Linux /proc")
HELPER = Path("ops/ci-runner/runner_cleanup.py").resolve()
spec = importlib.util.spec_from_file_location("ci_runner_cleanup", HELPER)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)
PROC_REFERENCES = cleanup.references


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    root = tmp_path / "goodteam-ci"
    (root / "actions-runner/_diag").mkdir(parents=True)
    (root / "actions-runner/.runner").write_text(json.dumps({
        "agentName": cleanup.RUNNER, "gitHubUrl": f"https://github.com/{cleanup.REPOSITORY}",
    }))
    (root / "hooks").mkdir()
    (root / "hooks/cleanup-policy.json").write_text(json.dumps({
        "min_free_bytes": 1, "min_free_percent": 1, "disk_check_seconds": 1,
    }))
    monkeypatch.setenv("GITHUB_REPOSITORY", cleanup.REPOSITORY)
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "github-env"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    monkeypatch.setattr(cleanup, "worker_identity", lambda root: cleanup.process_identity(os.getpid())[0])
    # The execution sandbox hides unrelated same-UID platform processes. Exercise
    # real fd/cwd/maps reads for our fixture process; production scans all PIDs.
    read_references = cleanup.references
    monkeypatch.setattr(cleanup, "references", lambda: read_references([Path(f"/proc/{os.getpid()}")]))
    instance = cleanup.Lifecycle(root)
    instance.initialize()
    return instance


def job_environment(lifecycle):
    assert lifecycle.start() == 0
    env = dict(line.split("=", 1) for line in Path(os.environ["GITHUB_ENV"]).read_text().splitlines())
    return env, Path(env["GOODTEAM_CI_JOB_TEMP"])


@pytest.mark.parametrize("result", [0, 7, signal.SIGINT, signal.SIGTERM])
def test_success_failure_and_cancel_keep_evidence_and_remove_owned_temp(lifecycle, result):
    env, temporary = job_environment(lifecycle)
    (temporary / "venv").mkdir()
    (temporary / "venv/python").symlink_to(sys.executable)
    evidence = Path(env["GOODTEAM_CI_EVIDENCE"]) / "results.xml"
    evidence.write_text("test evidence")
    unknown = lifecycle.root / "actions-runner/_work/_temp/unknown"
    unknown.mkdir(parents=True)
    payload = [sys.executable, "-c", f"raise SystemExit({result})"]
    command = [sys.executable, str(HELPER), "--root", str(lifecycle.root), "run", "--"]
    if result in (signal.SIGINT, signal.SIGTERM):
        ready = temporary / "ready"
        payload = [sys.executable, "-c", (
            "from pathlib import Path; import os, subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"Path({str(ready)!r}).write_text(str(os.getpid()) + ' ' + str(child.pid)); time.sleep(60)"
        )]
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        process = subprocess.Popen([*command, *payload])
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists()
            process.send_signal(result)
            assert process.wait(timeout=5) == 128 + result
            for pid in ready.read_text().split():
                status = Path(f"/proc/{pid}/stat")
                assert not status.exists() or status.read_text().rsplit(")", 1)[1].split()[0] == "Z"
            assert unrelated.poll() is None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            unrelated.terminate()
            unrelated.wait(timeout=5)
    else:
        assert subprocess.run([*command, *payload], timeout=5).returncode == result
    assert lifecycle.finish() == 0
    assert not temporary.exists()
    assert evidence.read_text() == "test evidence"
    assert unknown.is_dir()


def test_recovery_requires_expiry_dead_worker_and_no_open_handles(lifecycle, monkeypatch):
    env, temporary = job_environment(lifecycle)
    job = temporary.parent
    marker_file = job / cleanup.MARKER
    marker = json.loads(marker_file.read_text())
    marker["created"] -= 40 * 86400
    marker_file.write_text(json.dumps(marker))
    lifecycle.sweep()  # A live worker protects even ancient directories.
    assert temporary.exists()
    marker["worker"]["start"] = "different-process-start"
    marker_file.write_text(json.dumps(marker))  # PID reuse must not make a dead lease live.
    evidence = Path(env["GOODTEAM_CI_EVIDENCE"]) / "old.xml"
    evidence.write_text("old evidence")
    with (temporary / "open-file").open("w"):
        lifecycle.sweep()
        assert temporary.exists()
    # A live child with only inherited TMPDIR is still a consumer of the job.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             env={**os.environ, "TMPDIR": str(temporary)})
    with monkeypatch.context() as context:
        context.setattr(cleanup, "references", lambda: PROC_REFERENCES([
            Path(f"/proc/{os.getpid()}"), Path(f"/proc/{child.pid}"),
        ]))
        try:
            lifecycle.sweep()
            assert temporary.exists()
        finally:
            child.terminate()
            child.wait(timeout=5)
    lifecycle.sweep()
    assert not job.exists()
    _, fresh = job_environment(lifecycle)
    marker_file = fresh.parent / cleanup.MARKER
    marker = json.loads(marker_file.read_text())
    marker["worker"]["start"] = "dead"
    marker_file.write_text(json.dumps(marker))
    lifecycle.sweep()
    assert fresh.exists()  # Expiry is required even with a dead worker.


def test_unknown_markers_symlink_escape_and_unreadable_process_fail_closed(lifecycle, monkeypatch):
    unknown = lifecycle.state / "jobs" / ("a" * 32)
    unknown.mkdir()
    outside = lifecycle.root.parent / "business-data"
    outside.mkdir()
    (outside / "db").write_text("keep")
    (lifecycle.state / "jobs" / ("b" * 32)).symlink_to(outside, target_is_directory=True)
    lifecycle.sweep()
    assert unknown.exists()
    assert (outside / "db").read_text() == "keep"
    _, temporary = job_environment(lifecycle)
    monkeypatch.setattr(cleanup, "references", lambda: (_ for _ in ()).throw(PermissionError("unreadable process")))
    with pytest.raises(PermissionError):
        lifecycle.finish()
    assert temporary.exists()


def test_cache_cap_and_retention_never_expire_current_or_unknown_logs(lifecycle):
    env, _ = job_environment(lifecycle)
    cache = Path(env["PIP_CACHE_DIR"]) / "wheel"
    cache.write_bytes(b"x" * 1024)
    lifecycle.policy["cache_max_bytes"] = 1
    with cache.open("rb"):
        with pytest.raises(ValueError, match="cache cap"):
            lifecycle.finish()
        assert cache.exists()
    diag = lifecycle.root / "actions-runner/_diag"
    old = diag / "Worker_20250101-000000-utc.log"
    opened = diag / "Runner_20250101-000000-utc.log"
    recent = diag / "Worker_20261003-000000-utc.log"
    unknown = diag / "other-service.log"
    for log in (old, opened, recent, unknown):
        log.write_text("evidence")
    aged = time.time() - 40 * 86400
    for log in (old, opened, unknown):
        os.utime(log, (aged, aged))
    with opened.open("rb"):
        lifecycle.finish()
        assert opened.exists()
    assert not cache.exists()
    assert not old.exists()
    assert recent.exists() and unknown.exists()


def test_low_disk_refuses_launch_and_stops_only_new_command_group(lifecycle, monkeypatch):
    launched = lifecycle.root / "launched"
    command = [sys.executable, "-c", (
        f"from pathlib import Path; import os, time; Path({str(launched)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )]
    monkeypatch.setattr(lifecycle, "disk_ok", lambda: False)
    assert lifecycle.run(command) == 75
    assert not launched.exists()
    observations = iter([True, True, False])
    monkeypatch.setattr(lifecycle, "disk_ok", lambda: next(observations))
    assert lifecycle.run(command) == 75
    assert launched.exists()
    assert not Path(f"/proc/{launched.read_text()}").exists()
    monkeypatch.setattr(cleanup.shutil, "disk_usage", lambda root: type("Disk", (), {"total": 100, "free": 0})())
    assert not cleanup.Lifecycle(lifecycle.root).disk_ok()
    assert "Low disk" in Path(os.environ["GITHUB_STEP_SUMMARY"]).read_text()


def test_wrong_repository_registration_cannot_clean(lifecycle):
    registration = lifecycle.root / "actions-runner/.runner"
    registration.write_text(json.dumps({"agentName": "yuva-ci-01", "gitHubUrl": "https://github.com/LukeJiaoR/ziwei"}))
    with pytest.raises(ValueError, match="GoodTeam"):
        cleanup.Lifecycle(lifecycle.root)


def test_hook_cli_binds_to_its_registered_worker_ancestor(lifecycle):
    # A local bash executable stands in for the worker. No server or real
    # runner config is involved. Only /proc visibility is narrowed for sandboxing.
    worker = lifecycle.root / "actions-runner/bin/Runner.Worker"
    worker.parent.mkdir()
    shutil.copy2("/bin/bash", worker)
    bootstrap = (
        "import importlib.util, os, sys; from pathlib import Path; "
        f"spec = importlib.util.spec_from_file_location('cleanup', {str(HELPER)!r}); "
        "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "inspect = module.references; module.references = lambda: inspect([Path(f'/proc/{os.getpid()}')]); "
        "sys.exit(module.main())"
    )
    commands = [
        shlex.join([sys.executable, "-c", bootstrap, "--root", str(lifecycle.root), phase])
        for phase in ("started", "completed")
    ]
    result = subprocess.run([str(worker), "-c", "set -e\n" + "\n".join(commands) + "\n:"], timeout=10)
    assert result.returncode == 0
    env = dict(line.split("=", 1) for line in Path(os.environ["GITHUB_ENV"]).read_text().splitlines())
    assert not Path(env["GOODTEAM_CI_JOB_TEMP"]).exists()
    refused = subprocess.run(
        [sys.executable, str(HELPER), "--root", str(lifecycle.root), "started"], capture_output=True, text=True,
    )
    assert refused.returncode == 1 and "refused unsafe" in refused.stdout
    assert list((lifecycle.state / "jobs").iterdir()) == [lifecycle.state / "jobs" / cleanup.MARKER]
