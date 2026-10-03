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
import yaml


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Dedicated runner lifecycle uses Linux /proc")
HELPER = Path(__file__).resolve().parents[1] / "ops/ci-runner/runner_cleanup.py"
spec = importlib.util.spec_from_file_location("ci_runner_cleanup", HELPER)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)
PROC_REFERENCES = cleanup.references
NATIVE_CHILDREN = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").exists()
CHILD_PROCESSES = cleanup.child_processes


def sandbox_children(parent):
    # Test-only compatibility for the sandbox's omitted /proc children files.
    # Kernel subreaper adoption, process identities, signals and reaping stay real.
    children = set()
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            if cleanup.process_identity(int(path.name))[1] == parent:
                children.add(int(path.name))
        except FileNotFoundError:
            pass
    return children


def command_cli(lifecycle):
    bootstrap = (
        "import importlib.util, sys; "
        f"spec = importlib.util.spec_from_file_location('cleanup', {str(HELPER)!r}); "
        "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
    )
    if not NATIVE_CHILDREN:
        bootstrap += (
            f"test_spec = importlib.util.spec_from_file_location('fixture', {str(Path(__file__).resolve())!r}); "
            "fixture = importlib.util.module_from_spec(test_spec); test_spec.loader.exec_module(fixture); "
            "module.child_processes = fixture.sandbox_children; "
        )
    return [sys.executable, "-c", bootstrap + "sys.exit(module.main())", "--root", str(lifecycle.root)]


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
    if not NATIVE_CHILDREN:
        monkeypatch.setattr(cleanup, "child_processes", sandbox_children)
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
    ready = temporary / "ready"
    payload = [sys.executable, "-c", (
        "from pathlib import Path; import os, subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True); "
        f"Path({str(ready)!r}).write_text(str(os.getppid()) + ' ' + str(os.getpid()) + ' ' + str(child.pid)); "
        f"raise SystemExit({result})"
    )]
    command = [*command_cli(lifecycle), "run", "--"]
    if result in (signal.SIGINT, signal.SIGTERM):
        policy = lifecycle.root / "hooks/cleanup-policy.json"
        policy.write_text(json.dumps({"min_free_bytes": 1, "min_free_percent": 1, "disk_check_seconds": 5}))
        payload = [sys.executable, "-c", (
            "from pathlib import Path; import os, subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True); "
            f"Path({str(ready)!r}).write_text(str(os.getppid()) + ' ' + str(os.getpid()) + ' ' + str(child.pid)); "
            "time.sleep(60)"
        )]
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        script = lifecycle.root / "step.sh"
        # Execute the actual multi-command workflow step through the same Bash
        # options as Actions. A fixture replaces only the venv tool and runner root.
        workflow = yaml.load(Path(".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
        step = next(s for s in workflow["jobs"]["browser"]["steps"]
                    if s.get("name") == "Create isolated browser backend environment")
        fixture_bin = lifecycle.root / "bin"
        fixture_bin.mkdir()
        tool = fixture_bin / "python3.12"
        tool.write_text(f"#!{sys.executable}\n" + payload[2] + "\n")
        tool.chmod(0o700)
        script.write_text(step["run"].replace("python3 ops/ci-runner/runner_cleanup.py run --",
                                             shlex.join(command), 1))
        process = subprocess.Popen(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", str(script)],
                                   env={**os.environ, **env, "PATH": f"{fixture_bin}:{os.environ['PATH']}",
                                        "GITHUB_PATH": str(lifecycle.root / "github-path")})
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists()
            assert int(ready.read_text().split()[0]) == process.pid  # exec preserves the runner's target PID.
            started = time.monotonic()
            process.send_signal(result)
            assert process.wait(timeout=2.5) == 128 + result
            assert time.monotonic() - started < 2.5
            for pid in ready.read_text().split():
                status = Path(f"/proc/{pid}/stat")
                assert not status.exists() or status.read_text().rsplit(")", 1)[1].split()[0] == "Z"
            assert unrelated.poll() is None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if ready.exists():
                for pid in ready.read_text().split():
                    try:
                        os.kill(int(pid), signal.SIGKILL)  # Only this fixture's recorded processes.
                    except ProcessLookupError:
                        pass
            unrelated.terminate()
            unrelated.wait(timeout=5)
    else:
        assert subprocess.run([*command, *payload], timeout=5).returncode == result
        assert ready.exists()
        assert all(not Path(f"/proc/{pid}").exists() for pid in ready.read_text().split())
    assert lifecycle.finish() == 0
    assert not temporary.exists()
    assert evidence.read_text() == "test evidence"
    assert unknown.is_dir()


def test_cancel_while_event_lock_is_held_does_not_deadlock(lifecycle):
    ready = lifecycle.root / "command-pids"
    cancelled = lifecycle.root / "signal-delivered"
    command = command_cli(lifecycle)
    # The old monitor reaches Event.clear with its own non-reentrant lock held.
    # The replacement monitor receives the same signal/held lock at its sleep boundary.
    injection = f"""
import faulthandler, threading
event = threading.Event()
original_sleep = module.time.sleep
def cancel_under_lock():
    with event._cond:
        module.Path({str(cancelled)!r}).write_text(str(module.time.monotonic()))
        module.os.kill(module.os.getpid(), module.signal.SIGTERM)
def wait(timeout=None):
    while not module.Path({str(ready)!r}).exists():
        original_sleep(0.01)
def pause(duration):
    if module.Path({str(ready)!r}).exists() and not module.Path({str(cancelled)!r}).exists():
        cancel_under_lock()
    original_sleep(duration)
event.wait = wait
event.clear = cancel_under_lock
threading.Event = lambda: event
module.time.sleep = pause
faulthandler.dump_traceback_later(1.5)
"""
    command[2] = command[2].replace("sys.exit(module.main())", injection + "\nsys.exit(module.main())")
    payload = [sys.executable, "-c", (
        "from pathlib import Path; import json, os, signal, subprocess, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True); "
        f"Path({str(ready)!r}).write_text(json.dumps([os.getpid(), child.pid])); time.sleep(60)"
    )]
    process = subprocess.Popen([*command, "run", "--", *payload], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    identities = []
    try:
        deadline = time.monotonic() + 3
        while not cancelled.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert cancelled.exists()
        identities = [cleanup.process_identity(pid)[0] for pid in json.loads(ready.read_text())]
        try:
            _, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired as error:
            trace = (error.stderr or b"").decode()
            pytest.fail(f"Cancellation deadlocked while Event lock was held:\n{trace}")
        assert process.returncode == 143, stderr
        assert time.monotonic() - float(cancelled.read_text()) < 2.5
        assert all(not Path(f"/proc/{identity['pid']}").exists() for identity in identities)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for identity in identities:
            if cleanup.alive(identity):
                os.kill(identity["pid"], signal.SIGKILL)  # Only recorded fixture descendants.


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


def test_low_disk_refuses_launch_and_stops_only_new_command_tree(lifecycle, monkeypatch):
    launched = lifecycle.root / "launched"
    command = [sys.executable, "-c", (
        "from pathlib import Path; import json, os, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'], start_new_session=True); "
        f"Path({str(launched)!r}).write_text(json.dumps([os.getpid(), child.pid])); time.sleep(60)"
    )]
    monkeypatch.setattr(lifecycle, "disk_ok", lambda: False)
    assert lifecycle.run(command) == 75
    assert not launched.exists()
    observations = iter([True, True, False])
    monkeypatch.setattr(lifecycle, "disk_ok", lambda: next(observations))
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        with pytest.raises(ValueError, match="dedicated helper process"):
            lifecycle.run(command)
        assert unrelated.poll() is None  # No adoption or signaling of an existing job's children.
        assert not launched.exists()
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
    observations = iter([True, True, False])
    monkeypatch.setattr(lifecycle, "disk_ok", lambda: next(observations))
    assert lifecycle.run(command) == 75
    assert launched.exists()
    assert all(not Path(f"/proc/{pid}").exists() for pid in json.loads(launched.read_text()))
    monkeypatch.setattr(cleanup.shutil, "disk_usage", lambda root: type("Disk", (), {"total": 100, "free": 0})())
    assert not cleanup.Lifecycle(lifecycle.root).disk_ok()
    assert "Low disk" in Path(os.environ["GITHUB_STEP_SUMMARY"]).read_text()


def test_missing_native_children_interface_refuses_command_before_launch(lifecycle, monkeypatch):
    if NATIVE_CHILDREN:
        pytest.skip("This sandbox preflight regression requires the omitted native children interface")
    monkeypatch.setattr(cleanup, "child_processes", CHILD_PROCESSES)
    launched = lifecycle.root / "must-not-launch"
    with pytest.raises(ValueError, match="children interface"):
        lifecycle.run([sys.executable, "-c", f"from pathlib import Path; Path({str(launched)!r}).touch()"])
    assert not launched.exists()


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
