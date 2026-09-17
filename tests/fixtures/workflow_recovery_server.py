"""Test-only process host: real HTTP/state, fake Workers and bounded fault barriers."""

import json
import sys
import time
from pathlib import Path

from goodteam_os import web, workflow_mutations
from goodteam_os.workers import WorkerJobStore


BARRIER_TIMEOUT = 20
WORKER_TIMEOUT = 1


def wait_for_release(control, phase):
    fault_path = control / "fault.json"
    if not fault_path.exists():
        return
    fault = json.loads(fault_path.read_text())
    if fault["phase"] != phase:
        return
    fault_path.unlink()
    (control / "entered.json").write_text(json.dumps({"phase": phase}))
    deadline = time.monotonic() + BARRIER_TIMEOUT
    while not (control / "release").exists():
        if time.monotonic() > deadline:
            raise TimeoutError("Recovery fixture barrier expired")
        time.sleep(0.02)


def install_transition_faults(control):
    publish = workflow_mutations.publish_decision
    advance = web.advance_approved_workflow

    def publish_with_barrier(workspace, state):
        if state.pending_transition["action"]["gate"] == "security":
            wait_for_release(control, "before_publish")
        return publish(workspace, state)

    def advance_with_barrier(**kwargs):
        action = kwargs["state"].pending_transition.get("action", {})
        if action.get("gate") == "security":
            wait_for_release(control, "before_execute")
        return advance(**kwargs)

    workflow_mutations.publish_decision = publish_with_barrier
    web.advance_approved_workflow = advance_with_barrier


def install_fake_worker_fault(control):
    complete = WorkerJobStore._complete_fake
    run_job = WorkerJobStore.run_job

    def fake_only(store, job_id, **kwargs):
        job = json.loads((store.root / job_id / "job.json").read_text())
        if job["adapter"] != "fake":
            raise AssertionError("Recovery acceptance must never invoke a real Worker")
        return run_job(store, job_id, **kwargs)

    def timeout_once(store, job_root, job):
        fault_path = control / "worker-timeout"
        if job["role"] != "implementation" or not fault_path.exists():
            return complete(store, job_root, job)
        fault_path.unlink()
        return store._run_cli_worker(
            job_root, job, command=[sys.executable, "-c", "import time; time.sleep(30)"],
            model="fake", preflight={}, label="Acceptance sleeper", capture="stdout",
            summarize=lambda code, stderr: f"Sleeper exited: {code}", timeout_seconds=WORKER_TIMEOUT,
        )

    WorkerJobStore.run_job = fake_only
    WorkerJobStore._complete_fake = timeout_once


def main():
    root, control = (Path(value).resolve() for value in sys.argv[1:])
    if not (root / ".recovery-fixture").is_file() or control.parent != root:
        raise ValueError("Recovery host requires an owned test fixture")
    install_transition_faults(control)
    install_fake_worker_fault(control)
    http_server = web.ThreadingHTTPServer

    class BoundServer(http_server):
        def server_activate(self):
            super().server_activate()
            (control / "address.json").write_text(json.dumps({"port": self.server_port}))

    web.ThreadingHTTPServer = BoundServer
    web.serve(workspace=root / "workspace", host="127.0.0.1", port=0)


if __name__ == "__main__":
    main()
