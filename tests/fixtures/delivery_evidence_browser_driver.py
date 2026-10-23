"""Synthetic delivery evidence in an owned browser workspace; never real QA."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from goodteam_os.delivery_changes import retain_delivery_changes
from goodteam_os.resources import register_markdown_artifact
from goodteam_os.reviewer_dispatch import artifact_version_for_path
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workers import WorkerJobStore
from goodteam_os.workflow_workspace import collect_workflow_workspace, prepare_workflow_workspace, workspace_for_workflow


def main():
    root = Path(os.environ["GOODTEAM_E2E_ROOT"]).resolve()
    owner = os.environ.get("GOODTEAM_E2E_OWNER")
    if (not root.name.startswith("goodteam-e2e-") or root.parent != Path(tempfile.gettempdir()).resolve()
            or not owner or (root / ".owner").read_text() != owner):
        raise ValueError("Delivery fixture requires its owned temporary root")
    source = Path(sys.argv[1]).resolve()
    if not source.is_relative_to(root / "targets"):
        raise ValueError("Source must belong to this browser fixture")
    os.environ.update(GOODTEAM_HOME=str(root / "home"), GOODTEAM_STATE_DIR=str(root / "operator-state"),
                      GOODTEAM_PROJECTS_REGISTRY=str(root / "home/projects.json"))
    workspace = root / "workspace"
    states = StateStore(workspace)
    if states.read_project_current(sys.argv[2]) is not None:
        raise ValueError("Refusing to replace an existing run")
    git = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
           "-c", "user.name=Browser Fixture", "-c", "user.email=fixture@goodteam.invalid", "-c", "commit.gpgSign=false"]
    for arguments in (["init", "-q"], ["add", "."], ["commit", "-qm", "Browser fixture baseline"]):
        subprocess.run([*git, *arguments], cwd=source, check=True)
    state = create_initial_state("delivery-browser-fixture", sys.argv[2], "Synthetic delivery inspection")
    state.current_node, state.next_node = "release_review_gate", "await_release_approval"
    state.repository_baseline = prepare_workflow_workspace(source, project_key=state.project_key, run_id=state.run_id)
    retained = workspace_for_workflow(source, state.repository_baseline, project_key=state.project_key, run_id=state.run_id)
    content = "export const retained = '<img src=x>';\n"
    (retained / "src/main.js").write_text(content)
    result = collect_workflow_workspace(source, state.repository_baseline, project_key=state.project_key, run_id=state.run_id)
    workers = WorkerJobStore(source)
    qa = workers.create_job(run_id=state.run_id, project_key=state.project_key, role="qa", adapter="fake",
                            task="Synthetic retained QA report; do not execute", context="",
                            workflow_workspace=state.repository_baseline)
    (workers.root / qa["id"] / "result.md").write_text(
        "Status: blocked\nSynthetic QA fixture: npm run test:e2e; exit: 1; tests: 0\n")
    retain_delivery_changes(workspace, state, result, worker_store=workers, worker_job=qa,
                            worker_result={"role": "qa", "status": "completed"})
    release = workspace / "artifacts" / "runs" / state.run_id / "release.md"
    release.write_text(f"# Synthetic release\n\n[Retained source]({retained}/src/main.js:1)\n\n[Exact patch](changes.patch)\n")
    register_markdown_artifact(state, "release_notes", release)
    reviewer = workers.create_job(run_id=state.run_id, project_key=state.project_key, role="release", adapter="fake",
        task="Model-free legacy Reviewer browser fixture", context="Synthetic fixture, not acceptance",
        profile_id="correctness-security", approval="release", gate="release", phase="release",
        artifact_key="release_notes", artifact_path=str(release), artifact_version=artifact_version_for_path(str(release)),
        dispatch_group_id="browser-delivery", idempotency_key="browser-delivery-review")
    states.write_project_current(state)
    print(json.dumps({"run_id": state.run_id, "job_id": reviewer["id"], "content": content,
                      "baseline_commit": result["baseline_commit"], "result_commit": result["result_commit"]}))


if __name__ == "__main__":
    main()
