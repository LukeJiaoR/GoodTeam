import os
import shlex
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts" / "lib" / "pre-review.sh"


def _q(path: Path | str) -> str:
    return shlex.quote(str(path))


def _run_bash(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
        check=False,
    )


def test_pre_review_auth_failure_detector_matches_failure_class_and_403(tmp_path):
    auth_status = tmp_path / "auth.json"
    auth_status.write_text('{"failure_class":"auth","summary":"Codex failed"}', encoding="utf-8")
    forbidden_status = tmp_path / "forbidden.json"
    forbidden_status.write_text('{"summary":"unexpected status 403 Forbidden"}', encoding="utf-8")
    ok_status = tmp_path / "ok.json"
    ok_status.write_text('{"status":"completed","summary":"No mechanical findings"}', encoding="utf-8")

    result = _run_bash(
        f"""
        set -euo pipefail
        . {_q(HELPER)}
        pre_review_is_auth_failure {_q(auth_status)} && echo auth
        pre_review_is_auth_failure {_q(forbidden_status)} && echo forbidden
        if pre_review_is_auth_failure {_q(ok_status)}; then
          echo bad
        else
          echo ok
        fi
        """
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.splitlines() == ["auth", "forbidden", "ok"]


def test_pre_review_select_report_ignores_empty_or_failed_reports(tmp_path):
    jobs_root = tmp_path / ".goodteam" / "workers" / "jobs"
    empty_job = jobs_root / "job-empty"
    failed_job = jobs_root / "job-failed"
    good_job = jobs_root / "job-good"
    for job in (empty_job, failed_job, good_job):
        job.mkdir(parents=True)
    (empty_job / "status.json").write_text(
        '{"role":"code_review","status":"completed","updated_at":"2026-06-06T00:00:00+00:00"}',
        encoding="utf-8",
    )
    (empty_job / "result.md").write_text("", encoding="utf-8")
    (failed_job / "status.json").write_text(
        '{"role":"code_review","status":"failed","failure_class":"auth","updated_at":"2026-06-06T00:01:00+00:00"}',
        encoding="utf-8",
    )
    (failed_job / "result.md").write_text("auth failure summary\n", encoding="utf-8")
    (good_job / "status.json").write_text(
        '{"role":"code_review","status":"completed","updated_at":"2026-06-06T00:02:00+00:00"}',
        encoding="utf-8",
    )
    (good_job / "result.md").write_text("No mechanical findings\n", encoding="utf-8")

    result = _run_bash(
        f"""
        set -euo pipefail
        . {_q(HELPER)}
        pre_review_select_report {_q(tmp_path)}
        """
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == str(good_job / "result.md")


def test_pre_review_select_latest_status_prefers_current_review_job(tmp_path):
    jobs_root = tmp_path / ".goodteam" / "workers" / "jobs"
    implementation_job = jobs_root / "job-impl"
    review_job = jobs_root / "job-review"
    implementation_job.mkdir(parents=True)
    review_job.mkdir()
    (implementation_job / "status.json").write_text(
        '{"role":"implementation","status":"completed","updated_at":"2026-06-06T00:03:00+00:00"}',
        encoding="utf-8",
    )
    (implementation_job / "result.md").write_text("implementation result\n", encoding="utf-8")
    (review_job / "status.json").write_text(
        '{"role":"code_review","status":"completed","updated_at":"2026-06-06T00:01:00+00:00"}',
        encoding="utf-8",
    )
    (review_job / "result.md").write_text("review result\n", encoding="utf-8")

    result = _run_bash(
        f"""
        set -euo pipefail
        . {_q(HELPER)}
        pre_review_select_report {_q(tmp_path)}
        """
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == str(review_job / "result.md")


def test_pre_review_main_script_uses_fallback_and_report_selector():
    script = (REPO_ROOT / "scripts" / "pre-review.sh").read_text(encoding="utf-8")

    assert '. "$ROOT/scripts/lib/pre-review.sh"' in script
    assert "--adapter" in script
    assert 'for candidate_adapter in $(pre_review_fallback_adapters "$ADAPTER")' in script
    assert 'TRACK="code_review:$candidate_adapter:$TASK_FILE"' in script
    assert 'pre_review_is_auth_failure "$LAST_STATUS"' in script
    assert 'REPORT_SRC="$(pre_review_select_report "$WORKTREE" || true)"' in script
