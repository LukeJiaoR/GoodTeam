"""Owned HTTP acceptance host; local synthetic Workers, never paid models."""
import json
import subprocess
import sys

from goodteam_os.workers import WorkerJobStore
from workflow_recovery_server import main


complete = WorkerJobStore._complete_fake


def synthetic_result(store, job_root, job):
    if job["adapter"] != "fake":
        raise AssertionError("Goal acceptance cannot invoke a real model")
    context = (job_root / "context.md").read_text()
    if job.get("phase") == "goal_assessment":
        packet = json.loads(context)
        ref, artifact = next((ref, text) for ref, text in packet["evidence"].items() if ":artifact:qa_report:" in ref)
        marker = "REGRESSION_MISSING" if "REGRESSION_MISSING" in artifact else "REGRESSION_PASSED"
        checks = [{"criterion": index, "status": "missing" if index == 1 and marker == "REGRESSION_MISSING" else "met",
                   "evidence_ref": ref, "quote": marker} for index in range(len(packet["acceptance_criteria"]))]
        result = complete(store, job_root, job)
        (job_root / "result.md").write_text(json.dumps({"requires_new_scope": False, "checks": checks}))
        return result
    root = store.project_root
    if job["role"] == "implementation":
        (root / "feature.py").write_text("def add(a, b): return a + b\n")
        if "Prior evidence references" in context:
            (root / "test_feature.py").write_text("from feature import add\nassert add(2, 3) == 5\n")
    result = complete(store, job_root, job)
    if job["role"] == "qa":
        check = root / "test_feature.py"
        if check.exists():
            subprocess.run([sys.executable, str(check)], cwd=root, check=True, timeout=5)
        marker = "REGRESSION_PASSED" if check.exists() else "REGRESSION_MISSING"
        # This summary is incorporated into the real graph's QA artifact.
        result = store._write_status(job_root, job, "completed", "Local fixture observation: " + marker)
    return result


if __name__ == "__main__":
    WorkerJobStore._complete_fake = synthetic_result
    main()
