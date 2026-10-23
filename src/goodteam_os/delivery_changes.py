"""Retain a small explicit delivery set from the workflow's immutable Git result."""
from hashlib import sha256
import os
from pathlib import Path
import re

from goodteam_os.repository_access import AnchoredRepository, RepositoryAccessError
from goodteam_os.resources import ResourceCapability, ResourceDescriptor, ResourceKind, ResourceScope
from goodteam_os.source_preview import SENSITIVE_NAME, SENSITIVE_TEXT

MAX_DELIVERY_BYTES = 1_048_576
MAX_DELIVERY_FILES = 32


def clear_delivery_items(state):
    for key in state.delivery_items:
        state.resources.pop(key, None)
    state.delivery_items = {}


def qa_report_status(content):
    """Use the existing explicit Status line; do not infer outcomes from prose."""
    values = [line.strip().partition(":")[2].strip().lower() for line in content.splitlines()
              if line.strip().lower().startswith("status:")]
    return values[0] if len(values) == 1 and values[0] in {"passed", "failed", "blocked", "unknown"} else "unknown"


def _retain(workspace, state, result, key, item):
    data = item.pop("data", None)
    registered = {**item, "project_key": state.project_key, "run_id": state.run_id,
                  "baseline_commit": result["baseline_commit"], "result_commit": result["result_commit"],
                  "version": "", "status": "unavailable"}
    state.resources.pop(key, None)
    state.delivery_items[key] = registered
    if data is None:
        return
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        registered["unavailable_reason"] = "not_utf8"
        return
    if len(data) > MAX_DELIVERY_BYTES or "\0" in text:
        registered["unavailable_reason"] = "preview_limit_or_binary"
        return
    if item["kind"] == "patch" and b"GIT binary patch" in data.splitlines():
        # Both old and new binary payloads are reversibly encoded by Git; text
        # secret scanning cannot inspect either side. Withhold the whole patch.
        registered["unavailable_reason"] = "binary_patch"
        return
    if item["kind"] in {"source", "patch"}:
        paths = result["changed_files"] if item["kind"] == "patch" else [item["relative_path"]]
        if (any(part in {".ssh", ".aws"} or SENSITIVE_NAME.search(part)
                for name in paths for part in Path(name).parts) or SENSITIVE_TEXT.search(text)):
            # Preserve the existing source-preview boundary. Never redact a
            # patch and then present its changed bytes as an exact patch.
            registered["unavailable_reason"] = "sensitive_content"
            return
    digest = sha256(data).hexdigest()
    path = Path(workspace).absolute() / "artifacts" / "runs" / state.run_id / "delivery" / digest / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    registered.update(version="sha256:" + digest, status="available", size_bytes=len(data))
    state.resources[key] = ResourceDescriptor(
        key=key, kind=ResourceKind.TEXT_ARTIFACT, scope=ResourceScope.RUN, path=str(path),
        mime="text/plain", title=str(item["relative_path"]), producer="host_result_collection",
        capabilities=[ResourceCapability.READ, ResourceCapability.PREVIEW, ResourceCapability.DOWNLOAD],
        legacy_artifact=False,
    )


def retain_delivery_changes(workspace, state, repository_result, *, worker_store=None, worker_result=None, worker_job=None):
    from goodteam_os.workflow_workspace import read_workflow_delivery

    source = Path(state.repository_baseline["source_root"])
    items = read_workflow_delivery(source, state.repository_baseline, repository_result,
        project_key=state.project_key, run_id=state.run_id,
        max_bytes=MAX_DELIVERY_BYTES, max_files=MAX_DELIVERY_FILES)
    # Keep previous QA evidence explicitly bound to its own result until QA runs
    # again. Source links always address the latest host-collected task result.
    for key, item in list(state.delivery_items.items()):
        if item["kind"] in {"source", "patch"}:
            state.delivery_items.pop(key)
            state.resources.pop(key, None)
    for item in items:
        key = "delivery-patch" if item["kind"] == "patch" else (
            "delivery-source-" + sha256(os.fsencode(item["relative_path"])).hexdigest()[:24])
        _retain(workspace, state, repository_result, key, item)
    if (worker_store is None or not worker_result or not worker_job or worker_job.get("role") != "qa"
            or worker_job.get("run_id") != state.run_id or worker_job.get("project_key") != state.project_key
            or worker_job.get("workflow_workspace") != state.repository_baseline):
        return
    job_id = str(worker_job.get("id", ""))
    item = {"kind": "qa", "relative_path": "QA result.md", "job_id": job_id,
            "process_status": str(worker_result.get("status", "unknown")), "qa_status": "unknown"}
    if re.fullmatch(r"job-[0-9a-f-]{36}", job_id):
        try:
            with AnchoredRepository(worker_store.root) as repository:
                report = repository.read_bytes(job_id + "/result.md", max_bytes=MAX_DELIVERY_BYTES)
            if not report.truncated:
                item["qa_status"] = qa_report_status(report.data.decode("utf-8"))
                item["data"] = report.data
        except (OSError, UnicodeError, RepositoryAccessError):
            pass
    _retain(workspace, state, repository_result, "delivery-qa", item)
