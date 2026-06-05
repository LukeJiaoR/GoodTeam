import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts" / "lib" / "land-provenance.sh"


def _make_fake_main(root: Path) -> Path:
    root.mkdir()
    (root / ".venv" / "bin").mkdir(parents=True)
    python_shim = root / ".venv" / "bin" / "python"
    python_shim.write_text(f"#!/usr/bin/env bash\nexec {sys.executable!r} \"$@\"\n", encoding="utf-8")
    python_shim.chmod(0o755)
    os.symlink(REPO_ROOT / "src", root / "src")
    return python_shim


def _run_bash(script: str, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _q(path: Path | str) -> str:
    return shlex.quote(str(path))


def test_land_provenance_extract_archives_jobs_and_appends_land_event(tmp_path):
    root = tmp_path / "main"
    worktree = tmp_path / "worktree"
    python_shim = _make_fake_main(root)
    job_dir = worktree / ".goodteam" / "workers" / "jobs" / "job-123"
    job_dir.mkdir(parents=True)
    (job_dir / "result.md").write_text("recognizable provenance payload\n", encoding="utf-8")
    (job_dir / "status.json").write_text(
        json.dumps({"run_id": "run-abc", "job_id": "job-123", "role": "implementation"}),
        encoding="utf-8",
    )
    pr = "98701"

    result = _run_bash(
        f"""
        set -euo pipefail
        . {_q(HELPER)}
        export PY={_q(python_shim)}
        land_provenance_extract {pr} {_q(worktree)}
        rm -rf {_q(worktree)}
        land_provenance_airdrop {pr} {_q(root)} abcdef123456
        """,
        env={**os.environ, "PATH": os.environ["PATH"]},
    )

    archive_file = root / ".goodteam" / "provenance" / f"pr-{pr}" / "job-123" / "result.md"
    event_path = root / ".goodteam" / "memory" / "roles" / "release" / "events.jsonl"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]

    assert result.returncode == 0, result.stderr + result.stdout
    assert archive_file.read_text(encoding="utf-8") == "recognizable provenance payload\n"
    assert not worktree.exists()
    assert len(events) == 1
    assert events[0]["horizon"] == "mid-term"
    assert events[0]["source"] == f"land/pr-{pr}"
    assert "PR #98701 landed" in events[0]["text"]
    assert "run_id=run-abc" in events[0]["text"]
    assert "job_id=job-123" in events[0]["text"]
    assert "dispatch_role=implementation" in events[0]["text"]
    assert f"archive=.goodteam/provenance/pr-{pr}/" in events[0]["text"]


def test_land_provenance_missing_worktree_still_returns_success_and_writes_event(tmp_path):
    root = tmp_path / "main"
    python_shim = _make_fake_main(root)
    missing_worktree = tmp_path / "missing-worktree"
    pr = "98702"

    result = _run_bash(
        f"""
        set -euo pipefail
        . {_q(HELPER)}
        export PY={_q(python_shim)}
        land_provenance_extract {pr} {_q(missing_worktree)}
        land_provenance_airdrop {pr} {_q(root)} 123456abcdef
        """,
        env={**os.environ, "PATH": os.environ["PATH"]},
    )

    event_path = root / ".goodteam" / "memory" / "roles" / "release" / "events.jsonl"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]

    assert result.returncode == 0, result.stderr + result.stdout
    assert not missing_worktree.exists()
    assert not (root / ".goodteam" / "provenance" / f"pr-{pr}").exists()
    assert len(events) == 1
    assert events[0]["source"] == f"land/pr-{pr}"
    assert "PR #98702 landed" in events[0]["text"]
