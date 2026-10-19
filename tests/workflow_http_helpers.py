"""Owned subprocesses and real HTTP clients for recovery acceptance."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


REPOSITORY = Path(__file__).resolve().parents[1]
WAIT_SECONDS = 15
REQUEST_TIMEOUT = 25


def wait_until(predicate):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("Recovery fixture condition did not become ready")


class RecoveryServer:
    def __init__(self, root, *, fixture="workflow_recovery_server.py"):
        self.fixture = fixture
        self.root = root
        self.control = root / f"server-{uuid4().hex}"
        self.control.mkdir(parents=True)
        (root / ".recovery-fixture").touch()
        (root / "workspace").mkdir(exist_ok=True)
        self.process = None
        self.log = None
        self.url = ""

    def start(self):
        self.control.joinpath("address.json").unlink(missing_ok=True)
        self.log = (self.control / "server.log").open("ab")
        self.process = subprocess.Popen(
            [sys.executable, str(REPOSITORY / "tests/fixtures" / self.fixture),
             str(self.root), str(self.control)],
            cwd=REPOSITORY, stdout=self.log, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONPATH": str(REPOSITORY / "src"),
                 "GOODTEAM_HOME": str(self.root / "home"),
                 "GOODTEAM_STATE_DIR": str(self.root / "operator-state"),
                 "GOODTEAM_PROJECTS_REGISTRY": str(self.root / "home/projects.json"),
                 "GOODTEAM_DISABLE_TG_NETWORK": "1", "GOODTEAM_TG_BOT_TOKEN": "",
                 "GOODTEAM_TG_CHAT_ID": "", "GOODTEAM_SERVER_ACTIONS": "0"},
        )
        wait_until(self._ready)
        return self

    def _ready(self):
        if self.process.poll() is not None:
            raise AssertionError((self.control / "server.log").read_text())
        address = self.control / "address.json"
        if not address.exists():
            return False
        try:
            self.url = f"http://127.0.0.1:{json.loads(address.read_text())['port']}"
            return self.request("/api/projects")[0] == 200
        except (URLError, json.JSONDecodeError):
            return False

    def stop(self, *, crash=False):
        if self.process and self.process.poll() is None:
            self.process.kill() if crash else self.process.terminate()
            self.process.wait(timeout=5)
        if self.log:
            self.log.close()

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def ok(self, path, body=None):
        status, payload = self.request(path, body)
        assert status in {200, 201, 202}, payload
        return payload

    def state(self):
        return self.ok("/api/state?project=RECOVERY")

    def arm(self, phase):
        for name in ("entered.json", "release"):
            self.control.joinpath(name).unlink(missing_ok=True)
        (self.control / "fault.json").write_text(json.dumps({"phase": phase}))

    def entered(self):
        wait_until(lambda: (self.control / "entered.json").exists())

    def release(self):
        (self.control / "release").touch()


def approval_body(data, gate, *, note="Model-free recovery fixture: continue with simulated review to test recovery only."):
    review = next(item for item in data["approval_reviews"] if item["approval"] == gate)
    return {"project_key": "RECOVERY", "approval": gate, "decision": "approved", "note": note,
            "expected_run_id": data["state"]["run_id"], "expected_target_type": "review",
            "expected_target_id": gate, "expected_artifact_key": review["artifact"],
            "expected_artifact_version": review["artifact_version"]}


def retry_body(data):
    target = data["retry_target"]
    return {"project_key": target["project_key"], "expected_run_id": target["run_id"],
            "expected_retry_version": target["version"]}


def decisions(server):
    return [item for item in server.state()["action_items"] if item["action"] == "approval_decided"]


def wait_for_readiness_input(server):
    def poll():
        home = server.ok("/api/projects/RECOVERY/home")
        readiness = home["readiness"]
        execution = readiness["execution"]
        status = execution["status"]
        if status in {"failed", "cancelled"}:
            step_id = execution.get("current_step_id", "unknown")
            step = next((item for item in readiness["steps"] if item["id"] == step_id), {})
            error = step.get("error") or {}
            raise AssertionError(
                f"Recovery fixture readiness {status} at {step_id}: "
                f"{error.get('code', 'unknown')}: {error.get('message', 'No step error was recorded.')}"
            )
        return home if status == "needs_input" else False

    return wait_until(poll)


def prepare_security_gate(server):
    target = server.root / "target"
    target.mkdir()
    (target / "package.json").write_text('{"name":"recovery","scripts":{"test":"node --test"}}')
    (target / "README.md").write_text("# Recovery fixture\n")
    server.ok("/api/settings", {"worker_backend": "fake", "model_profile": "balanced"})
    server.ok("/api/projects/import", {"root": str(target), "project_key": "RECOVERY"})
    base = "/api/projects/RECOVERY/task-assistant"
    draft = server.ok(base + "/messages", {"message_id": "request-1", "text": "Implement recovery acceptance."})
    proposal = draft["session"]["proposed_task"]
    confirmation = {"action": "confirm_task", "proposal_id": proposal["id"], "expected_revision": proposal["revision"]}
    server.ok(base + "/actions", confirmation)
    home = wait_for_readiness_input(server)
    step = next(item for item in home["readiness"]["steps"] if item["id"] == "command_verification")
    server.ok(base + "/actions", {"action": "continue_preparation", "step_id": "command_verification",
                                  "values": {"confirmed_command_ids": [item["id"] for item in step["input_requirements"]["commands"]]}})
    server.ok(base + "/actions", {"action": "continue_preparation", "step_id": "constraints_confirmation",
                                  "values": {"confirmed": True}})
    for gate in ("takeover", "prd", "rfc"):
        current = server.state()
        assert f"await_{gate}_approval" == current["state"]["next_node"]
        server.ok("/api/approve", approval_body(current, gate))
    current = server.state()
    assert "await_security_approval" == current["state"]["next_node"]
    return approval_body(current, "security")
