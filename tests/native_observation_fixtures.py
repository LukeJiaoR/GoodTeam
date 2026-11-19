"""A native receipt fixture at the Manager transport seam; no provider execution."""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

from goodteam_os import graph, manager_app_server, manager_execution, projects
from goodteam_os.project_observation_runtime import judge_with_production_runtime
from goodteam_os.project_outcome_discovery import authorization_digest
from goodteam_os.project_running_observation import _binding, human_observation_context
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


class NativeObservationFixture:
    def __init__(self, root, monkeypatch, *, human_questions=False):
        self.root = root
        self.config = root / "project.yaml"
        self.store = TaskAssistantSessionStore(root, "DEMO")
        self.calls = []
        self.lose_reply = False
        self.on_run = None
        self.output = {"assessment": "wait", "rationale": "The Worker is still carrying out its original task.",
                       "observed_job_ids": ["worker"]}
        value = {"id": "grant", "status": "active", "authority": "fixed", "manager_calls": 0,
            "max_manager_calls": 4, "max_batches": 3, "approved_backlog_version": "version",
            "seed_task_ids": ["task"], "seed_task_intents": {"task": "intent"},
            "discovery": {"allow_running_observation": True},
            "pending_batch": {"run_id": "batch", "task_ids": ["task"]}}
        if human_questions:
            value["discovery"]["allow_running_human_question"] = True
        value["authorization_digest"] = authorization_digest(value)
        self.batch = {"run_id": "batch", "confirmed": True, "expected_continuation_id": "grant",
            "project_root": str(root.resolve()), "status": "running", "dispatch_run_id": "attempt"}
        self.store._mutate(lambda session: {**session, "project_continuation": value,
                                          "project_task_batches": {"batch": deepcopy(self.batch)}})
        monkeypatch.setattr(projects, "load_project_target", lambda _: {})
        monkeypatch.setattr(graph, "resolve_project_runtime_root", lambda _: root)
        monkeypatch.setattr(manager_execution, "run_manager_analysis", self.analysis)
        monkeypatch.setattr(manager_app_server, "runtime_for", lambda *args: SimpleNamespace(
            root=root, reconcile=lambda identity: self.store.read().get("manager_app_server", {})
                .get("requests", {}).get(identity, {})))

    def candidate(self, key="one"):
        session = self.store.read()
        packet = {"binding": _binding(session, self.root),
                  "workers": [{"job_id": "worker", "status": "running"}],
                  "human_context": human_observation_context(session["project_continuation"])}
        self.store._mutate(lambda current: {**current, "project_continuation": {**current["project_continuation"],
            "running_observation": {"digest": key, "packet": packet}}})

    def analysis(self, **kwargs):
        value = self.store.read()["project_continuation"]
        key = value["running_observation"]["digest"]
        record = value["running_judgments"][key]
        identity = "running-observation:" + sha256((value["id"] + ":" + key).encode()).hexdigest()
        assert record["status"] == "reserved" and value["manager_calls"] == len(self.calls) + 1
        assert record["manager_invocation"]["logical_id"] == kwargs["logical_id"] == identity
        assert record["manager_invocation"]["input_digest"] == sha256(kwargs["context"].encode()).hexdigest()
        assert kwargs["phase"] == "project_running_observation" and kwargs["authority_check"]()
        assert json.loads(kwargs["context"])["observation"] == record["packet"]
        self.calls.append(kwargs)
        result = {"text": json.dumps(self.output), "manager_execution": {"adapter": "codex-app-server",
            "logical_id": identity, "thread_id": "thread-" + key, "turn_id": "turn-" + key,
            "status": "completed"}}
        native = {"status": "completed", "phase": kwargs["phase"], "run_id": kwargs["run_id"],
                  "context": kwargs["context"], "thread_id": "thread-" + key, "turn_id": "turn-" + key,
                  "result": result}
        def complete(session):
            session.setdefault("manager_app_server", {}).setdefault("requests", {})[identity] = native
            return session
        self.store._mutate(complete)
        if self.on_run is not None:
            self.on_run(kwargs)
        if self.lose_reply:
            raise TimeoutError("Original native turn completed, but its caller lost the reply.")
        return result

    def judge(self):
        return judge_with_production_runtime(store=self.store, root=self.root, project_config=self.config,
            project_key="DEMO", value=self.store.read()["project_continuation"], authority_check=lambda: True)
