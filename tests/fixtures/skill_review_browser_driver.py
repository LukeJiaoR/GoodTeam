"""Model-free browser contract driver, never a product endpoint or Runtime.

Browser interactions create the project, Skill binding, and task. This explicit
owned-test process then calls the REAL Worker create/run path, with a constant
subprocess fixture. It never seeds report/status JSON or changes workflow gates.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from unittest.mock import patch

from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.skills.content import ContentSession
from goodteam_os.skills.models import content_digest
from goodteam_os.skills.review_execution import materialize_review_inputs
from goodteam_os.skills.review_process import ReviewProcess
from goodteam_os.skills.worker_requests import WORKER_METADATA_LOADER
from goodteam_os.skills.task_service import ProjectTaskSkillService
from goodteam_os.state import StateStore
from goodteam_os.workers import WorkerJobStore


def owned_root():
    root = Path(os.environ["GOODTEAM_E2E_ROOT"]).resolve()
    if not root.name.startswith("goodteam-e2e-") or root.parent != Path(tempfile.gettempdir()).resolve():
        raise RuntimeError("Browser driver requires its owned temporary root")
    if not os.environ.get("GOODTEAM_E2E_OWNER") or (root / ".owner").read_text() != os.environ["GOODTEAM_E2E_OWNER"]:
        raise RuntimeError("Browser driver owner mismatch")
    os.environ.update(GOODTEAM_HOME=str(root / "home"), GOODTEAM_STATE_DIR=str(root / "operator-state"),
                      GOODTEAM_PROJECTS_REGISTRY=str(root / "home" / "projects.json"), GOODTEAM_DISABLE_TG_NETWORK="1")
    return root


class BrowserFixtureRuntime:
    """Explicit fixture marker; no public selector can install this class."""
    def __init__(self, root, mode, source):
        self.root, self.mode, self.source, self.calls = root, mode, source, 0

    def authorize(self, snapshot, *, context_id, operation):
        return ContentSession("codex", "browser-contract-v1", "fixture-only", content_digest(b"constant-fixture"),
                              snapshot["job_input"]["request"]["model"], content_digest(b"no-network-no-model"),
                              "fixture-" + context_id, "not-live-conformance", "contract_fixture")

    def check_current(self, provider, ref):
        provider.private_input(ref)

    @contextmanager
    def execution(self, record, provider, ref):
        self.calls += 1
        path = self.root / ("review-process-" + record["job_id"][-16:])
        prompt = materialize_review_inputs(provider, ref, record, path)
        source_name = "head/review-change.js" if record["target"]["kind"] == "git_change" else "candidate"
        exact_source = record["sources"][source_name]
        report = {
            "schema_version": "goodteam.code-review-report.v1",
            "summary": "Fixture review of the exact captured source; no independent test execution.",
            "findings": [],
            "evidence": [{"id": "E1", "path": source_name, "line_start": 1, "line_end": 1,
                          "quote": exact_source.splitlines()[0], "observation": "The captured file exports the fixture constant."}],
            "checks": [{"name": "Export declaration", "status": "checked", "evidence_ids": ["E1"],
                        "detail": "Inspected the exact captured source in this model-free contract fixture."},
                       {"name": "Application tests", "status": "not_run", "evidence_ids": [],
                        "detail": "This Reviewer did not run the application test suite."}],
        }
        if self.mode in {"malformed", "git-malformed"}:
            report["summary"] = ""
        events = [{"type": "thread.started", "thread_id": "owned-browser-fixture"}, {"type": "turn.started"},
                  {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": json.dumps(report)}},
                  {"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}]
        output = "\n".join(json.dumps(item) for item in events) + "\n"
        candidate = "sources/" + source_name if record["target"]["kind"] == "git_change" else "candidate"
        program = ("import sys; from pathlib import Path; assert sys.stdin.buffer.read(); "
                   + "assert Path(" + repr(candidate) + ").read_text() == " + repr(exact_source) + "; "
                   + "sys.stdout.write(" + repr(output) + ")")
        deadline = 5
        if self.mode == "cancel-wait":
            # The browser cancels this actual child through the product API.
            # A fixture-only release file keeps failure teardown bounded; it is
            # never a production cancellation or Runtime admission mechanism.
            program = ("import os,sys,time\nfrom pathlib import Path\nassert sys.stdin.buffer.read()\n"
                       "Path('child-pid').write_text(str(os.getpid()))\n"
                       "while not Path('fixture-release').exists(): time.sleep(0.02)\n"
                       "sys.stdout.write(" + repr(output) + ")\n")
            deadline = 20
        yield ReviewProcess((sys.executable, "-I", "-c", program), path, {}, deadline), prompt


def main():
    root = owned_root()
    key, run_id, mode = sys.argv[1:]
    if mode not in {"valid", "malformed", "save-failure", "revoke", "git-malformed", "git-retry", "cancel-wait", "codex-contract"}:
        raise RuntimeError("Unsupported fixed fixture action")
    workspace = root / "workspace"
    state = StateStore(workspace).read_project_current(key)
    if state is None or state.run_id != run_id or not state.task_skill_context:
        raise RuntimeError("The real browser must first create this exact locked task")
    entry = ProjectRegistry().find_project(key)
    repo = Path(entry["root"]).resolve()
    if not repo.is_relative_to(root / "targets"):
        raise RuntimeError("The browser project is not an owned fixture")
    services = ProjectTaskSkillService(workspace, key, worker_roots=(repo,))
    locked = services.read(state.task_skill_context)
    methods = locked["subjects"]["role:code_review"]["items"]
    if not methods:
        raise RuntimeError("The browser must select a custom Reviewer method")
    if mode == "revoke":
        item = methods[0]
        services.availability.revoke(item["skill_id"], item["version_id"], actor="user", reason="browser-fixture",
                                     command_key="fixture-revoke:" + run_id, expected_revision=services.availability.revision())
        print(json.dumps({"provider_calls": 0, "revoked": True}))
        return
    workers = WorkerJobStore(repo)
    source_path = repo / "src" / "main.js"
    source = source_path.read_text()
    target = None
    if mode == "git-malformed":
        # Only this owned fixture repository; never production or arbitrary commands.
        def git(*args):
            return subprocess.check_output(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", *args], cwd=repo).decode().strip()
        git("init", "-q")
        (repo / "review-change.js").write_text("export const reviewed = 'base';\n")
        (repo / "review-delete.txt").write_text("removed in head\n")
        git("add", "review-change.js", "review-delete.txt")
        git("commit", "-qm", "Bounded fixture base")
        base = git("rev-parse", "HEAD")
        (repo / "review-change.js").write_text("export const reviewed = 'head';\n")
        (repo / "review-delete.txt").unlink()
        (repo / "review-added.txt").write_text("added in head\n")
        git("add", "review-change.js", "review-delete.txt", "review-added.txt")
        git("commit", "-qm", "Bounded fixture head")
        target = {"kind": "git_change", "base_sha": base, "head_sha": git("rev-parse", "HEAD")}
    if mode == "git-retry":
        candidates = [item for item in workers.list_jobs() if item.get("run_id") == run_id
                      and item.get("original_failed_job_id") and item.get("review_target")]
        if len(candidates) != 1:
            raise RuntimeError("The real UI must create exactly one new retry attempt first")
        job = candidates[0]
    else:
        job = workers.create_job(run_id=run_id, project_key=key, role="code_review", adapter="codex", model="fixture-only",
        task="Review the exact fixture source with this task's original methods.", context="Model-free browser contract.",
        execution_policy="read_only", artifact_key="fixture_source", artifact_path=str(source_path),
        artifact_version=content_digest(source.encode()), expected_artifacts=["review-report"],
        idempotency_key="browser-review:" + run_id + ":" + mode, task_skill_context=state.task_skill_context,
        skill_subject="role:code_review", skill_loader_version=WORKER_METADATA_LOADER,
        **({"review_target": target} if target else {}))
    if mode == "codex-contract":
        # Test-only composition: actual Worker chooses the concrete adapter.
        # Only daemon observations/attached executable are scripted; no provider,
        # actual Docker or native Codex acceptance is implied by this fixture.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from test_codex_review_runtime import AdmissionFixture, ScriptedEngine, contract
        from goodteam_os.skills import codex_container
        from goodteam_os.skills.review_results import ReviewResultStore
        selected = contract(model="fixture-only")
        admission = AdmissionFixture(selected=selected)
        fixture_report = {
            "schema_version": "goodteam.code-review-report.v1", "summary": "Fixture review of the exact captured source; no independent test execution.",
            "findings": [], "evidence": [{"id": "E1", "path": "candidate", "line_start": 1, "line_end": 1,
                "quote": source.splitlines()[0], "observation": "Inspected the original source declaration."}],
            "checks": [{"name": "Export declaration", "status": "checked", "evidence_ids": ["E1"],
                        "detail": "Inspected the exact captured source in a model-free contract fixture."},
                       {"name": "Application tests", "status": "not_run", "evidence_ids": [], "detail": "No application tests were run by the Reviewer."}]}
        engine = ScriptedEngine(selected, output_report=fixture_report)
        workers = WorkerJobStore(repo, codex_review_admission=admission)
        with patch.object(codex_container, "DockerEngine", return_value=engine):
            result = workers.run_job(job["id"])
        detail = ReviewResultStore(workers, job["id"]).detail()
        print(json.dumps({"job_id": job["id"], "result": result, "fixture_processes": engine.attaches,
                          "provider_calls": 0, "native_codex": False, "actual_docker": False,
                          "resource_reads": detail.get("resource_reads", [])}))
        return
    runtime = BrowserFixtureRuntime(root, mode, source)
    from goodteam_os.skills import review_results
    original = review_results._replace
    def projection(fd, name, value):
        if mode == "save-failure" and value.get("status") == "received":
            raise OSError("Owned contract fixture: failed status projection")
        return original(fd, name, value)
    with patch("goodteam_os.skills.review_execution.selected_review_runtime", return_value=runtime):
        # A replay with no new process repairs the existing durable outcome. Do
        # not re-inject the simulated failure into recovery.
        results_path = Path(os.environ["GOODTEAM_STATE_DIR"]) / "skills" / "review-results" / job["id"] / "outcome.json"
        if mode == "save-failure" and not results_path.exists():
            with patch.object(review_results, "_replace", projection):
                result = workers.run_job(job["id"])
        else:
            result = workers.run_job(job["id"])
    print(json.dumps({"job_id": job["id"], "result": result, "fixture_processes": runtime.calls, "provider_calls": 0}))


if __name__ == "__main__":
    main()
