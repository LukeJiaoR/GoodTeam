"""Read-only, attributed reports for an exact artifact's advisory reviewers."""

import json
import re
from pathlib import Path

from goodteam_os.repository_access import AnchoredRepository, RepositoryAccessError


MAX_REPORT_BYTES = 128 * 1024


def project_gate_reports(statuses, *, roots, run_id, project_key):
    # Statuses must already be selected for the exact gate/artifact generation.
    # No report or completed process changes approval or command-test status.
    from goodteam_os.graph import sanitize_artifact_excerpt

    reports = []
    for status in statuses:
        job_id = str(status.get("id") or status.get("job_id") or "")
        if (not re.fullmatch(r"job-[A-Za-z0-9-]+", job_id)
                or job_id.startswith(("job-skill-", "job-input-", "job-correction-"))
                or status.get("run_id") != run_id
                or status.get("project_key") != project_key):
            continue
        item = {
            "job_id": job_id,
            "profile_id": str(status.get("profile_id") or ""),
            "status": str(status.get("status") or "unknown"),
            "artifact_version": str(status.get("artifact_version") or ""),
            "report_status": "unavailable", "content": "", "report_sha256": "",
            "advisory_only": True,
        }
        matches = []
        for root in roots:
            root = Path(root)
            relative = f".goodteam/workers/jobs/{job_id}"
            expected = root / relative / "result.md"
            if str(expected.absolute()) != str(status.get("result_path") or ""):
                continue
            try:
                with AnchoredRepository(root) as repository:
                    metadata = repository.read_text(f"{relative}/job.json", max_bytes=MAX_REPORT_BYTES)
                    if metadata.truncated:
                        continue
                    job = json.loads(metadata.text)
                    if not isinstance(job, dict) or job.get("id") != job_id or any(job.get(key) != status.get(key) for key in (
                        "run_id", "project_key", "artifact_key", "artifact_path", "artifact_version", "profile_id", "role",
                    )):
                        continue
                    report = repository.read_text(f"{relative}/result.md", max_bytes=MAX_REPORT_BYTES)
                    if report.truncated:
                        continue
                    matches.append(report)
            except (OSError, ValueError, RepositoryAccessError):
                continue
        if len(matches) == 1:
            report = matches[0]
            item.update(report_status="available", content=sanitize_artifact_excerpt(report.text, max_chars=None),
                        report_sha256=report.content_hash)
        reports.append(item)
    return reports
