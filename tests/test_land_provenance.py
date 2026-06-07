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


def _events_by_source(event_path: Path) -> dict[str, list[dict[str, object]]]:
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    grouped: dict[str, list[dict[str, object]]] = {}
    for event in events:
        grouped.setdefault(str(event.get("source", "")), []).append(event)
    return grouped


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
        land_provenance_airdrop {pr} {_q(root)} abcdef123456 pass
        """,
        env={**os.environ, "PATH": os.environ["PATH"]},
    )

    archive_file = root / ".goodteam" / "provenance" / f"pr-{pr}" / "job-123" / "result.md"
    event_path = root / ".goodteam" / "memory" / "roles" / "release" / "events.jsonl"
    events_by_source = _events_by_source(event_path)
    land_events = events_by_source[f"land/pr-{pr}"]
    review_events = events_by_source[f"review/pr-{pr}"]

    assert result.returncode == 0, result.stderr + result.stdout
    assert archive_file.read_text(encoding="utf-8") == "recognizable provenance payload\n"
    assert not worktree.exists()
    assert len(land_events) == 1
    assert len(review_events) == 1
    assert land_events[0]["horizon"] == "mid-term"
    assert "PR #98701 landed" in land_events[0]["text"]
    assert "CI=pass" in land_events[0]["text"]
    assert "run_id=run-abc" in land_events[0]["text"]
    assert "job_id=job-123" in land_events[0]["text"]
    assert "dispatch_role=implementation" in land_events[0]["text"]
    assert f"archive=.goodteam/provenance/pr-{pr}/" in land_events[0]["text"]
    assert "land_review_decision=approved_to_land" in review_events[0]["text"]


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
    events_by_source = _events_by_source(event_path)
    land_events = events_by_source[f"land/pr-{pr}"]
    review_events = events_by_source[f"review/pr-{pr}"]

    assert result.returncode == 0, result.stderr + result.stdout
    assert not missing_worktree.exists()
    assert not (root / ".goodteam" / "provenance" / f"pr-{pr}").exists()
    assert len(land_events) == 1
    assert len(review_events) == 1
    assert "PR #98702 landed" in land_events[0]["text"]


def test_land_provenance_missing_worktree_clears_stale_staging_before_airdrop(tmp_path):
    root = tmp_path / "main"
    python_shim = _make_fake_main(root)
    missing_worktree = tmp_path / "missing-worktree"
    pr = "98703"
    stale_staging = Path(f"/tmp/land-provenance-{pr}")
    stale_file = stale_staging / "stale-job" / "result.md"

    try:
        stale_file.parent.mkdir(parents=True)
        stale_file.write_text("STALE provenance should not leak\n", encoding="utf-8")

        result = _run_bash(
            f"""
            set -euo pipefail
            . {_q(HELPER)}
            export PY={_q(python_shim)}
            land_provenance_extract {pr} {_q(missing_worktree)}
            land_provenance_airdrop {pr} {_q(root)} fedcba654321
            """,
            env={**os.environ, "PATH": os.environ["PATH"]},
        )

        archive = root / ".goodteam" / "provenance" / f"pr-{pr}"
        event_path = root / ".goodteam" / "memory" / "roles" / "release" / "events.jsonl"
        events_by_source = _events_by_source(event_path)
        land_events = events_by_source[f"land/pr-{pr}"]
        review_events = events_by_source[f"review/pr-{pr}"]

        assert result.returncode == 0, result.stderr + result.stdout
        assert not stale_staging.exists()
        assert not (archive / "stale-job").exists()
        assert not archive.exists()
        assert len(land_events) == 1
        assert len(review_events) == 1
        assert "PR #98703 landed" in land_events[0]["text"]
    finally:
        subprocess.run(["rm", "-rf", str(stale_staging)], check=False)


def test_land_provenance_airdrop_records_forced_unverified_ci_status(tmp_path):
    root = tmp_path / "main"
    python_shim = _make_fake_main(root)
    pr = "98704"

    result = _run_bash(
        f"""
        set -euo pipefail
        . {_q(HELPER)}
        export PY={_q(python_shim)}
        land_provenance_airdrop {pr} {_q(root)} 999999abcdef forced-unverified
        """,
        env={**os.environ, "PATH": os.environ["PATH"]},
    )

    event_path = root / ".goodteam" / "memory" / "roles" / "release" / "events.jsonl"
    events_by_source = _events_by_source(event_path)
    land_events = events_by_source[f"land/pr-{pr}"]
    review_events = events_by_source[f"review/pr-{pr}"]

    assert result.returncode == 0, result.stderr + result.stdout
    assert len(land_events) == 1
    assert len(review_events) == 1
    assert "PR #98704 landed" in land_events[0]["text"]
    assert "CI=forced-unverified" in land_events[0]["text"]
    assert "CI=pass" not in land_events[0]["text"]
    assert "CI=forced-unverified" in review_events[0]["text"]


def test_land_provenance_extract_prefers_non_review_job_over_newer_code_review(tmp_path):
    root = tmp_path / "main"
    worktree = tmp_path / "worktree"
    python_shim = _make_fake_main(root)
    jobs_root = worktree / ".goodteam" / "workers" / "jobs"
    implementation = jobs_root / "job-impl"
    code_review = jobs_root / "job-review"
    implementation.mkdir(parents=True)
    code_review.mkdir()
    (implementation / "result.md").write_text("implementation payload\n", encoding="utf-8")
    (implementation / "status.json").write_text(
        json.dumps({"run_id": "run-impl", "job_id": "job-impl", "role": "implementation"}),
        encoding="utf-8",
    )
    (code_review / "result.md").write_text("review payload\n", encoding="utf-8")
    (code_review / "status.json").write_text(
        json.dumps({"run_id": "run-review", "job_id": "job-review", "role": "code_review"}),
        encoding="utf-8",
    )
    os.utime(implementation / "status.json", (100, 100))
    os.utime(code_review / "status.json", (200, 200))
    pr = "98705"

    result = _run_bash(
        f"""
        set -euo pipefail
        . {_q(HELPER)}
        export PY={_q(python_shim)}
        land_provenance_extract {pr} {_q(worktree)}
        land_provenance_airdrop {pr} {_q(root)} aaaaabbbbb pass
        """,
        env={**os.environ, "PATH": os.environ["PATH"]},
    )

    event_path = root / ".goodteam" / "memory" / "roles" / "release" / "events.jsonl"
    events_by_source = _events_by_source(event_path)
    land_events = events_by_source[f"land/pr-{pr}"]

    assert result.returncode == 0, result.stderr + result.stdout
    assert "run_id=run-impl" in land_events[0]["text"]
    assert "job_id=job-impl" in land_events[0]["text"]
    assert "dispatch_role=implementation" in land_events[0]["text"]
    assert "job-review" not in land_events[0]["text"]


def test_land_provenance_airdrop_archives_pre_review_report_and_links_review_event(tmp_path):
    root = tmp_path / "main"
    python_shim = _make_fake_main(root)
    pr = "98706"
    pre_review_report = Path(f"/tmp/pre-review-{pr}-report.md")

    try:
        pre_review_report.write_text("No mechanical findings\nResidual risk: browser smoke not run.\n", encoding="utf-8")

        result = _run_bash(
            f"""
            set -euo pipefail
            . {_q(HELPER)}
            export PY={_q(python_shim)}
            land_provenance_airdrop {pr} {_q(root)} cccccdddddd pass
            """,
            env={**os.environ, "PATH": os.environ["PATH"]},
        )

        archived_report = root / ".goodteam" / "provenance" / f"pr-{pr}" / "pre-review-report.md"
        event_path = root / ".goodteam" / "memory" / "roles" / "release" / "events.jsonl"
        events_by_source = _events_by_source(event_path)
        land_events = events_by_source[f"land/pr-{pr}"]
        review_events = events_by_source[f"review/pr-{pr}"]

        assert result.returncode == 0, result.stderr + result.stdout
        assert archived_report.read_text(encoding="utf-8") == pre_review_report.read_text(encoding="utf-8")
        assert "pre_review_report=y" in land_events[0]["text"]
        assert f"pre_review_report_path=.goodteam/provenance/pr-{pr}/pre-review-report.md" in land_events[0]["text"]
        assert len(review_events) == 1
        assert "land_review_decision=approved_to_land" in review_events[0]["text"]
        assert f"pre_review_report=.goodteam/provenance/pr-{pr}/pre-review-report.md" in review_events[0]["text"]
    finally:
        pre_review_report.unlink(missing_ok=True)


def test_land_provenance_airdrop_writes_pr_manifest_linking_job_and_review_artifacts(tmp_path):
    root = tmp_path / "main"
    worktree = tmp_path / "worktree"
    python_shim = _make_fake_main(root)
    pr = "98707"
    pre_review_report = Path(f"/tmp/pre-review-{pr}-report.md")
    job_dir = worktree / ".goodteam" / "workers" / "jobs" / "job-manifest"
    job_dir.mkdir(parents=True)
    (job_dir / "task.md").write_text("Implement manifest-backed provenance.\n", encoding="utf-8")
    (job_dir / "research-report.md").write_text("Research basis for this implementation.\n", encoding="utf-8")
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "id": "job-manifest",
                "run_id": "run-manifest",
                "role": "implementation",
                "adapter": "codex",
                "model": "gpt-5-codex",
                "task_source": "/tmp/contract-task.md",
                "constraints_source": "/tmp/contract-constraints.md",
                "research_report_source": "/tmp/research-report.md",
                "expected_artifacts": ["scripts/lib/land-provenance.sh"],
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "status.json").write_text(
        json.dumps({"run_id": "run-manifest", "job_id": "job-manifest", "role": "implementation"}),
        encoding="utf-8",
    )

    try:
        pre_review_report.write_text("Approved mechanically.\n", encoding="utf-8")

        result = _run_bash(
            f"""
            set -euo pipefail
            . {_q(HELPER)}
            export PY={_q(python_shim)}
            land_provenance_extract {pr} {_q(worktree)}
            land_provenance_airdrop {pr} {_q(root)} fff111222333 pass
            """,
            env={**os.environ, "PATH": os.environ["PATH"]},
        )

        manifest_path = root / ".goodteam" / "provenance" / f"pr-{pr}" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert result.returncode == 0, result.stderr + result.stdout
        assert manifest["pr"] == 98707
        assert manifest["ci_status"] == "pass"
        assert manifest["squash_merge_sha"] == "fff111222333"
        assert manifest["pre_review_report"] == ".goodteam/provenance/pr-98707/pre-review-report.md"
        assert manifest["memory_sources"] == ["land/pr-98707", "review/pr-98707"]
        assert manifest["jobs"] == [
            {
                "job_id": "job-manifest",
                "run_id": "run-manifest",
                "role": "implementation",
                "adapter": "codex",
                "model": "gpt-5-codex",
                "task_artifact": ".goodteam/provenance/pr-98707/job-manifest/task.md",
                "task_source": "/tmp/contract-task.md",
                "constraints_source": "/tmp/contract-constraints.md",
                "research_report_artifact": ".goodteam/provenance/pr-98707/job-manifest/research-report.md",
                "research_report_source": "/tmp/research-report.md",
                "expected_artifacts": ["scripts/lib/land-provenance.sh"],
            }
        ]
    finally:
        pre_review_report.unlink(missing_ok=True)


def test_land_provenance_airdrop_archives_review_rationale_and_links_manifest(tmp_path):
    root = tmp_path / "main"
    worktree = tmp_path / "worktree"
    python_shim = _make_fake_main(root)
    pr = "98708"
    rationale = tmp_path / "review-rationale.md"
    job_dir = worktree / ".goodteam" / "workers" / "jobs" / "job-rationale"
    job_dir.mkdir(parents=True)
    (job_dir / "task.md").write_text("Implement review-rationale provenance.\n", encoding="utf-8")
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "id": "job-rationale",
                "run_id": "run-rationale",
                "role": "implementation",
                "adapter": "codex",
                "model": "gpt-5-codex",
                "task_source": "/tmp/rationale-task.md",
                "constraints_source": "/tmp/rationale-constraints.md",
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "status.json").write_text(
        json.dumps({"run_id": "run-rationale", "job_id": "job-rationale", "role": "implementation"}),
        encoding="utf-8",
    )
    rationale.write_text(
        "Approved to land because the regression test covers the user-visible bug.\n",
        encoding="utf-8",
    )

    result = _run_bash(
        f"""
        set -euo pipefail
        . {_q(HELPER)}
        export PY={_q(python_shim)}
        export LAND_PROVENANCE_REVIEW_RATIONALE={_q(rationale)}
        land_provenance_extract {pr} {_q(worktree)}
        land_provenance_airdrop {pr} {_q(root)} abc123def456 pass
        """,
        env={**os.environ, "PATH": os.environ["PATH"]},
    )

    archive = root / ".goodteam" / "provenance" / f"pr-{pr}"
    archived_rationale = archive / "review-rationale.md"
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    event_path = root / ".goodteam" / "memory" / "roles" / "release" / "events.jsonl"
    events_by_source = _events_by_source(event_path)
    review_events = events_by_source[f"review/pr-{pr}"]

    assert result.returncode == 0, result.stderr + result.stdout
    assert archived_rationale.read_text(encoding="utf-8") == rationale.read_text(encoding="utf-8")
    assert manifest["review_rationale"] == ".goodteam/provenance/pr-98708/review-rationale.md"
    assert "review_rationale=y" in review_events[0]["text"]
    assert "review_rationale_path=.goodteam/provenance/pr-98708/review-rationale.md" in review_events[0]["text"]


def test_land_pr_script_accepts_review_rationale_option_and_exports_for_airdrop():
    script = (REPO_ROOT / "scripts" / "land-pr.sh").read_text(encoding="utf-8")

    assert "--review-rationale) REVIEW_RATIONALE=\"$2\"; shift 2 ;;" in script
    assert "--review-rationale=*) REVIEW_RATIONALE=\"${1#*=}\"; shift ;;" in script
    assert "export LAND_PROVENANCE_REVIEW_RATIONALE=\"$REVIEW_RATIONALE\"" in script
