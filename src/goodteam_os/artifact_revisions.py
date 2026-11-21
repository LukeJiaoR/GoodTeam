"""[INPUT]: 已完成 Worker 的作业目录与结果记录。
[OUTPUT]: 读取当前作业的产物文本。
[POS]: 交付证据读取。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from pathlib import Path


def is_worker_report_text(text):
    """Adapter placeholders and file-change summaries are not authored reports."""
    from goodteam_os.graph import _is_worker_meta_summary

    placeholder = text.startswith("# Worker Result\n") and (
        "See stdout.txt and stderr.txt." in text or "Status: completed-without-assistant-text" in text
    )
    return bool(text.strip() and not _is_worker_meta_summary(text) and not placeholder)


def current_job_artifact(job_root: Path, status, *, artifact_key, filename):
    """Only the current job's result is authoritative, never a shared docs mirror."""
    from goodteam_os.graph import _is_worker_meta_summary

    # result_path is adapter output, but it cannot point at a previous job or a
    # shared repository document. A missing current result is a failure.
    root = job_root.resolve()
    result = Path(str(status.get("result_path") or root / "result.md")).resolve()
    if not result.is_relative_to(root) or not result.is_file():
        return None
    text = result.read_text(encoding="utf-8")
    if is_worker_report_text(text):
        return text
    # File-producing adapters can return a job-local deliverable. Require the
    # exact declared filename and job-local ownership, not arbitrary references.
    artifact = (root / filename).resolve()
    if artifact.is_relative_to(root) and artifact.is_file():
        content = artifact.read_text(encoding="utf-8")
        if content.strip() and not _is_worker_meta_summary(content):
            return content
    return None
