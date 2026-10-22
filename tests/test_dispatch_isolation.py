"""Public direct dispatch uses retained Git copies and never the source checkout."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from goodteam_os.dispatch import dispatch_tracks
from goodteam_os.workers import WorkerJobStore


def git(root, *arguments):
    return subprocess.run(["git", "-C", str(root), *arguments], check=True,
                          capture_output=True, text=True).stdout.strip()


def repository(root):
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Dispatch fixture")
    git(root, "config", "user.email", "dispatch@example.invalid")
    git(root, "config", "commit.gpgSign", "false")
    (root / ".gitignore").write_text(".goodteam/\n")
    (root / "original.txt").write_text("human source\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Initial fixture")
    return root


@pytest.fixture
def cli_fixture(tmp_path, monkeypatch):
    """A real subprocess at the supported CLI seam, with no provider credentials."""
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    binary = binary_dir / "codex"
    binary.write_text(f"#!{sys.executable}\n" + """import json, os, sys
from pathlib import Path
if '--version' in sys.argv:
    print('offline dispatch fixture 1')
    raise SystemExit(0)
prompt = sys.stdin.read()
assert prompt
cwd = Path.cwd()
assert Path(sys.argv[sys.argv.index('--cd') + 1]) == cwd
assert (cwd / '.git').is_dir()
with Path(os.environ['DISPATCH_FIXTURE_CALLS']).open('a') as calls:
    calls.write(json.dumps({'cwd': str(cwd), 'argv': sys.argv[1:]}) + '\\n')
Path('worker.txt').write_text('worker output\\n')
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('Offline worker returned.\\n')
if os.environ.get('DISPATCH_FIXTURE_FAIL') == '1':
    print('Deliberate fixture failure', file=sys.stderr)
    raise SystemExit(2)
""")
    binary.chmod(0o755)
    calls = tmp_path / "worker-calls.jsonl"
    empty_home = tmp_path / "empty-codex-home"
    empty_home.mkdir()
    monkeypatch.setenv("PATH", str(binary_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CODEX_HOME", str(empty_home))
    monkeypatch.setenv("DISPATCH_FIXTURE_CALLS", str(calls))
    monkeypatch.delenv("DISPATCH_FIXTURE_FAIL", raising=False)
    return calls


def track():
    return {"role": "implementation", "adapter": "codex", "task": "Write the assigned worker output."}


def test_public_dispatch_replays_exact_result_without_worker_or_verify_reexecution(tmp_path, cli_fixture):
    source = repository(tmp_path / "source")
    verify_calls = tmp_path / "verify-calls"
    script = ("from pathlib import Path; "
              "assert Path('worker.txt').read_text() == 'worker output\\n'; "
              f"Path({str(verify_calls)!r}).open('a').write('verify\\n')")
    verify = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    request = dict(project_root=source, tracks=[track()], logical_task_id="retained-public-task",
                   verify_commands={"implementation": verify})

    first = dispatch_tracks(**request)

    assert first["all_ok"] is True, first
    job = first["jobs"][0]
    calls = [json.loads(line) for line in cli_fixture.read_text().splitlines()]
    assert len(calls) == 1
    assert calls[0]["cwd"] == job["workspace_path"] == job["verify"]["cwd"]
    assert job["invocation_evidence"]["runtime_cwd"] == job["workspace_path"]
    assert job["invocation_evidence"]["workspace_cwd_fallback"] is False
    assert Path(job["workspace_path"]) != source
    assert job["changed_files"] == ["worker.txt"]
    assert not (source / "worker.txt").exists()
    # Restart returns the recorded result even if a human subsequently edits the source.
    (source / "original.txt").write_text("later human edit\n")
    again = dispatch_tracks(**request)
    assert again == first
    assert cli_fixture.read_text().splitlines() == [json.dumps(calls[0])]
    assert verify_calls.read_text() == "verify\n"
    assert (source / "original.txt").read_text() == "later human edit\n"
    assert len(WorkerJobStore(source).list_jobs()) == 1
    # A crash after worker/verification settlement but before outer publication
    # must recover those receipts, rather than repeat either side effect.
    report = Path(job["workspace_path"]).parent.parent / "report.json"
    report.unlink()
    recovered = dispatch_tracks(**request)
    assert recovered["all_ok"] is True, recovered
    assert recovered["jobs"][0]["job_id"] == job["job_id"]
    assert recovered["jobs"][0]["workspace_path"] == job["workspace_path"]
    assert recovered["baseline"] == first["baseline"]
    assert len(cli_fixture.read_text().splitlines()) == 1
    assert verify_calls.read_text() == "verify\n"


@pytest.mark.parametrize("change", ["task", "verify"])
def test_public_dispatch_same_identity_rejects_changed_request(tmp_path, cli_fixture, change):
    source = repository(tmp_path / "source")
    request = dict(project_root=source, tracks=[track()], logical_task_id="fixed-input")
    result = dispatch_tracks(**request)
    original_calls = cli_fixture.read_bytes()
    if change == "task":
        request["tracks"] = [{**track(), "task": "Different task input"}]
    else:
        request["verify_commands"] = {"implementation": "echo different verification"}

    with pytest.raises(ValueError, match="dispatch_request_changed"):
        dispatch_tracks(**request)

    assert cli_fixture.read_bytes() == original_calls
    assert len(WorkerJobStore(source).list_jobs()) == 1
    assert Path(result["jobs"][0]["workspace_path"]).is_dir()


@pytest.mark.parametrize("git_initialized", [False, True])
def test_public_dispatch_without_committed_git_baseline_blocks_before_worker(tmp_path, monkeypatch, git_initialized):
    source = tmp_path / "source"
    source.mkdir()
    (source / "original.txt").write_text("uncommitted source\n")
    if git_initialized:
        git(source, "init", "-q")

    def forbidden_worker(*args, **kwargs):
        pytest.fail("A worker ran without a complete committed Git baseline.")

    monkeypatch.setattr(WorkerJobStore, "run_job", forbidden_worker)
    with pytest.raises(ValueError):
        dispatch_tracks(project_root=source, tracks=[track()])
    assert (source / "original.txt").read_text() == "uncommitted source\n"
    assert not (source / "worker.txt").exists()


def test_public_dispatch_missing_assigned_workspace_never_falls_back_to_source(tmp_path, monkeypatch, cli_fixture):
    source = repository(tmp_path / "source")
    run_job = WorkerJobStore.run_job
    removed = []

    def remove_before_launch(store, job_id, **kwargs):
        assigned = Path(kwargs["sandbox_context"]["workspace_path"])
        removed.append(assigned)
        shutil.rmtree(assigned)
        return run_job(store, job_id, **kwargs)

    monkeypatch.setattr(WorkerJobStore, "run_job", remove_before_launch)
    result = dispatch_tracks(project_root=source, tracks=[track()])

    assert result["all_ok"] is False
    assert result["execution_ok"] is False
    assert result["integration"]["status"] == "blocked"
    assert result["jobs"][0]["attribution_status"] == "unknown"
    assert result["jobs"][0]["changed_files"] is None
    assert removed and all(path != source for path in removed)
    assert not cli_fixture.exists()
    assert (source / "original.txt").read_text() == "human source\n"
    assert not (source / "worker.txt").exists()


def test_public_dispatch_failed_worker_retains_output_and_isolated_workspace(tmp_path, monkeypatch, cli_fixture):
    source = repository(tmp_path / "source")
    monkeypatch.setenv("DISPATCH_FIXTURE_FAIL", "1")
    result = dispatch_tracks(project_root=source, tracks=[track()],
                             verify_commands={"implementation": "echo verification-must-not-run"})

    assert result["all_ok"] is False
    assert result["execution_ok"] is False
    assert result["integration"]["status"] == "blocked"
    job = result["jobs"][0]
    assert job["status"] == "failed"
    assert job["verify"]["reason"] == "worker_failed"
    assert job["changed_files"] == ["worker.txt"]
    assert (Path(job["workspace_path"]) / "worker.txt").read_text() == "worker output\n"
    assert Path(job["status_path"]).is_file()
    assert Path(job["result_path"]).read_text() == "Offline worker returned.\n"
    status = json.loads(Path(job["status_path"]).read_text())
    assert Path(status["stderr_path"]).read_text() == "Deliberate fixture failure\n"
    assert len(status["attempt_history"]) == 1
    assert len(cli_fixture.read_text().splitlines()) == 1
    assert (source / "original.txt").read_text() == "human source\n"
    assert not (source / "worker.txt").exists()
