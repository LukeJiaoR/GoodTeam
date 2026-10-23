"""Explicit legacy-run browser fixture with real fake-Worker memory receipts.

Modern workflows use the separate Skill protocol. Never downgrade such a run to
obtain ordinary-memory receipts. These synthetic legacy states are UI fixtures,
not evidence that a real workflow completed; all receipts come from run_job.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workers import WorkerJobStore


def create_legacy_run(workspace, target, project_key, *, archive=False):
    states = StateStore(workspace)
    previous = states.read_project_current(project_key)
    if archive:
        if previous is None or previous.task_skill_context or previous.current_node != "complete":
            raise ValueError("Only the completed legacy fixture may be archived")
        states.archive_project_current(project_key, reason="completed")
    elif previous is not None:
        raise ValueError("Refusing to replace an existing run")
    run_id = f"legacy-memory-{uuid4()}"
    state = create_initial_state(run_id, project_key, "Explicit legacy memory inspection fixture")
    state.current_node = state.next_node = "complete"
    workers = WorkerJobStore(target)
    job = workers.create_job(run_id=run_id, project_key=project_key, role="architect", adapter="fake",
                             task="Record a model-free legacy memory observation", context="")
    result = workers.run_job(job["id"])
    if result["status"] != "completed":
        raise ValueError("Legacy fake Worker did not complete")
    states.write_project_current(state)
    return {"run_id": run_id, "job_id": job["id"]}


def main():
    root = Path(os.environ["GOODTEAM_E2E_ROOT"]).resolve()
    owner = os.environ.get("GOODTEAM_E2E_OWNER")
    if (not root.name.startswith("goodteam-e2e-") or root.parent != Path(tempfile.gettempdir()).resolve()
            or not owner or (root / ".owner").read_text() != owner):
        raise ValueError("Browser fixture requires its owned temporary root")
    target = Path(sys.argv[1]).resolve()
    if not target.is_relative_to(root / "targets"):
        raise ValueError("Target must belong to this browser fixture")
    os.environ.update(GOODTEAM_HOME=str(root / "home"), GOODTEAM_STATE_DIR=str(root / "operator-state"),
                      GOODTEAM_PROJECTS_REGISTRY=str(root / "home/projects.json"))
    print(json.dumps(create_legacy_run(root / "workspace", target, sys.argv[2], archive=sys.argv[3] == "archive")))


if __name__ == "__main__":
    main()
