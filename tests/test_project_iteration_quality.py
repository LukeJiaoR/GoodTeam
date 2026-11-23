"""One offline batch exercises real checks, protected review and correction.

Only Manager decisions and native CLI outputs are protocol fixtures. The
compiled launchers never contact a provider; they do not prove model judgment,
native sandbox confinement or live credentials. Admission, subprocess capture,
immutable snapshots, correction sealing and delivery use production code.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from goodteam_os import web
from goodteam_os.assistant_queue import queue_manager_iteration_choice
from goodteam_os.dispatch_workspace import dispatch_artifact_root
from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService
from goodteam_os.execution.correction_native import TrustedCodexCorrectionAdmission
from goodteam_os.execution.correction_store import CorrectionAttemptStore
from goodteam_os.execution.task_snapshot import canonical_bytes, execution_key
from goodteam_os.execution.task_snapshot_store import store_for_project
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_iteration_continuation import (
    accept_project_batch_delivery, configure_project_continuation, tick_project_continuation,
)
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.project_iteration_execution import run_queued_iteration_batch
from goodteam_os.project_iteration_planner import plan_next_iteration
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.skills.codex_trusted import TrustedCodexReviewAdmission
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.team_control_plane import ensure_team_state
from goodteam_os.workers import WorkerJobStore
from test_dispatch_isolation import git, repository


def _private_json(path, value):
    path.write_bytes(canonical_bytes(value))
    path.chmod(0o600)
    return path


def _offline_native_cli(tmp_path, monkeypatch):
    """Compiled test transports satisfy unchanged native executable admission.

    A fixed local Python fixture supplies Codex JSONL; no service or authority
    is monkeypatched. The private fixture home contains no provider credential.
    """
    compiler = shutil.which("cc")
    assert compiler, "The native transport regression requires a local C compiler."
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    calls = tmp_path / "native-calls.jsonl"
    fixture = tmp_path / "offline_native_transport.py"
    fixture.write_text("CALLS = " + repr(str(calls)) + "\n" + r'''
import hashlib, json, re, sys
from pathlib import Path
from uuid import uuid4

mode = sys.argv.pop(1)
if '--version' in sys.argv:
    print('codex-cli ' + '.'.join(map(str, sys.version_info[:3])))
    raise SystemExit(0)
prompt = sys.stdin.read()
assert prompt

def event(kind, **details):
    with Path(CALLS).open('a') as stream:
        stream.write(json.dumps({'kind': kind, 'cwd': str(Path.cwd()), **details}) + '\n')

def final_turn(report):
    for item in [
        {'type': 'thread.started', 'thread_id': 'offline-' + uuid4().hex},
        *([{'type': 'item.completed', 'item': {'id': 'startup-warning', 'type': 'error',
                                            'message': 'Offline native configuration warning.'}}]
          if mode == 'review' else []),
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(report)}},
        {'type': 'turn.completed'},
    ]:
        print(json.dumps(item))

if mode == 'review':
    target = json.loads(prompt.split('# Exact review target\n', 1)[1].split('\n# Captured sources', 1)[0])
    manifest = json.loads(prompt.split('# Captured sources (file manifest; read these exact files as needed)\n', 1)[1]
                         .split('\n# Result channel contract', 1)[0])
    sources = {}
    for name, item in manifest.items():
        data = Path(item['path']).read_bytes()
        assert len(data) == item['bytes']
        assert 'sha256:' + hashlib.sha256(data).hexdigest() == item['digest']
        sources[name] = data.decode('utf-8')
    assert {entry[side]['source_name'] for entry in target['files'] for side in ('base', 'head')
            if entry[side] is not None} == set(sources)
    assert '"head/unit-a.txt":"broken\\n"' not in prompt
    evidence = []
    for index, name in enumerate(('unit-a.txt', 'unit-b.txt')):
        text = sources['head/' + name]
        evidence.append({'id': 'E' + str(index), 'path': 'head/' + name,
                         'line_start': 1, 'line_end': 1, 'quote': text,
                         'observation': 'Offline protocol fixture observed the exact candidate file content.'})
    broken = sources['head/unit-a.txt'] != 'fixed\n'
    support = []
    supplied, _ = json.JSONDecoder().raw_decode(prompt.split('## Original task inputs\n\n', 1)[1])
    context = json.loads(supplied['context'])
    if 'host_check_output' in context:
        logs = context['host_check_output']
        assert logs['stdout'] == 'Checked both tasks against the retained regression\n'
        assert ('AssertionError' in logs['stderr']) == broken
        assert sources['head/regression.txt'] == 'Retained regression evidence\n'
        assert next(row for row in target['files'] if row['path'] == 'regression.txt')['change'] == 'E'
        evidence.append({'id': 'E2', 'path': 'head/regression.txt', 'line_start': 1, 'line_end': 1,
                         'quote': sources['head/regression.txt'], 'observation': 'Reviewed the unchanged regression alongside the captured host output.'})
        support.append('E2')
    criteria = sorted(set(re.findall(r'criterion:[0-9a-f]{64}:\d+', prompt)))
    assert len(criteria) == 2
    findings = [{'id': 'F1', 'severity': 'high', 'title': 'Assigned output remains broken',
                 'detail': 'unit-a.txt contains broken rather than the required fixed text.',
                 'evidence_ids': ['E0'], 'recommendation': 'Write fixed followed by a newline to unit-a.txt.'}] if broken else []
    event('review', target=target, broken=broken, supplemental_evidence=bool(support))
    final_turn({'schema_version': 'goodteam.code-review-report.v1',
                'summary': 'Offline source-backed protocol fixture; no real model judgment.',
                'findings': findings, 'evidence': evidence,
                'checks': [{'name': criterion, 'status': 'checked', 'evidence_ids': ['E0', 'E1', *support],
                            'detail': 'Compared both exact candidate files to the two assigned requirements.'}
                           for criterion in criteria] + [{
                               'name': 'Full host command independently executed', 'status': 'not_run',
                               'evidence_ids': [], 'detail': 'Relied on the exact-candidate host check receipt.'}]})
elif '--output-last-message' in sys.argv:
    name = re.search(r'TASK_FILE=(unit-[ab]\.txt)', prompt).group(1)
    assert Path(sys.argv[sys.argv.index('--cd') + 1]) == Path.cwd()
    Path(name).write_text('broken\n' if name == 'unit-a.txt' else 'ready\n')
    Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('Offline Worker candidate; review required.\n')
    event('worker', task=name)
else:
    assert Path(sys.argv[sys.argv.index('--cd') + 1]) == Path.cwd()
    assert Path('unit-a.txt').read_text() == 'broken\n'
    assert Path('unit-b.txt').read_text() == 'ready\n'
    result = json.loads(prompt.rsplit('\n', 1)[1])
    assert result['schema_version'] == 'goodteam.codex-correction-output.v1'
    Path('unit-a.txt').write_text('fixed\n')
    result['summary'] = 'Offline correction transport changed only the assigned file; host must verify.'
    event('correction', input=result['input'], base_sha=result['base_sha'])
    final_turn(result)
''')
    binaries = {}
    for mode, name in (("worker", "codex"), ("review", "codex-review")):
        launcher = tmp_path / (name + ".c")
        launcher.write_text(
            '#include <stdlib.h>\n#include <unistd.h>\n'
            'int main(int argc, char **argv) {\n'
            '  char **args = calloc((size_t)argc + 3, sizeof(char *));\n'
            f'  args[0] = {json.dumps(sys.executable)};\n'
            f'  args[1] = {json.dumps(str(fixture))};\n'
            f'  args[2] = {json.dumps(mode)};\n'
            '  for (int i = 1; i < argc; i++) args[i + 2] = argv[i];\n'
            '  execv(args[0], args); return 111;\n}\n')
        binary = binary_dir / name
        subprocess.run([compiler, str(launcher), "-o", str(binary)], check=True, capture_output=True)
        binaries[mode] = binary
    native_home = tmp_path / "offline-native-home"
    native_home.mkdir(mode=0o700)
    _private_json(native_home / "auth.json", {})
    (native_home / "config.toml").write_text("# Empty offline fixture; no provider authority.\n")
    (native_home / "config.toml").chmod(0o600)
    monkeypatch.setenv("PATH", str(binary_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CODEX_HOME", str(native_home))
    return binaries, native_home, calls


def _review_admission(tmp_path, source, snapshot, binary, native_home):
    policy = dict(schema_version="goodteam.codex-trusted-review-policy.v1",
        authorization_id="offline-batch-quality", worker_root=str(source),
        task={"kind": "direct_task", "project_key": "DEMO", "logical_id": "candidate-review:" + snapshot.payload_digest},
        codex=str(binary), codex_home=str(native_home), model="offline-fixture", max_contexts=2,
        deadline_seconds=30, expires_at=int(time.time()) + 600, trusted_task_inputs=True,
        accept_native_managed_configuration=True, accept_unknown_cost=True,
        provider_request_limit=None, usd_limit=None)
    return TrustedCodexReviewAdmission(_private_json(tmp_path / "review-policy.json", policy))


def _correction_admission(tmp_path, source, job_id, binary, native_home):
    attempt = CorrectionAttemptStore(source, job_id)
    captured = attempt.load()
    policy = dict(schema_version="goodteam.trusted-codex-correction-policy.v1",
        project_root=str(source), input=captured.reference, workspace=str(attempt.workspace(captured)),
        codex=str(binary), codex_home=str(native_home),
        version=subprocess.run([str(binary), "--version"], check=True, capture_output=True,
                               text=True).stdout.strip().removeprefix("codex-cli "),
        binary_digest="sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest(), model="offline-fixture",
        expires_at=int(time.time()) + 600, max_seconds=30, max_invocations=1, trusted_task_inputs=True,
        accept_native_managed_configuration=True, accept_unknown_cost=True,
        provider_request_limit=None, usd_limit=None)
    return TrustedCodexCorrectionAdmission(_private_json(tmp_path / "correction-policy.json", policy))


def test_batch_quality_checks_reviews_corrects_and_delivers_without_reexecution(tmp_path, monkeypatch, delivery_handoff=None):
    from goodteam_os.project_iteration_quality import (
        advance_project_batch_quality,
        prepare_project_batch_quality,
        project_batch_quality_evidence,
    )

    binaries, native_home, calls = _offline_native_cli(tmp_path, monkeypatch)
    source = repository(tmp_path / "source")
    (source / 'regression.txt').write_text('Retained regression evidence\n')
    git(source, 'add', 'regression.txt')
    git(source, 'commit', '-qm', 'Add unchanged regression input')
    original_head = git(source, "rev-parse", "HEAD")
    config = tmp_path / "projects/demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nsource_type: existing_project\nrepos:\n  root: {source}\nworker_backend:\n  mode: codex\n")
    branch = "qa/delivery"
    git(source, "checkout", "-qb", branch)
    with config.open("a") as file:
        file.write(f"local_delivery_branch: {branch}\n")
    ProjectRegistry().register_project(config, root=source, key="DEMO")
    ensure_team_state(tmp_path, "DEMO", config)
    ledger = LocalTaskStore(source, "DEMO")
    tasks = [ledger.create_task(title=f"TASK_FILE=unit-{letter}.txt", description="Write only the assigned file.",
        role="implementation", write_domains=[f"unit-{letter}.txt"],
        acceptance_criteria=[f"unit-{letter}.txt contains {expected} followed by a newline."])
        for letter, expected in (("a", "fixed"), ("b", "ready"))]
    task_ids = [item["id"] for item in tasks]
    backlog = project_iteration_backlog(source, "DEMO")
    scope = dict(workspace=tmp_path, project_key="DEMO", root=source)
    run_scope = {}
    if delivery_handoff is None:
        value = configure_project_continuation(**scope, project_config=config, request={
            "task_ids": task_ids, "confirmed": True, "billing_acknowledged": True,
            "idempotency_key": "qa-local-delivery", "expected_backlog_version": backlog["version"],
            "discovery": {"objective": "Validate and deliver the files.", "allowed_write_domains": ["."],
                          "use_project_backlog": True},
        })
        run_scope = {"expected_authority": value["authority"], "expected_continuation_id": value["id"]}
    plan_next_iteration(**scope, project_config=config, expected_backlog_version=backlog["version"], confirmed=True,
        manager_runner=lambda **_: {"selection": {"selected_task_ids": task_ids, "compared_task_ids": task_ids,
            "rationale": "Both independent tasks have exact, disjoint authorized file scopes."}})
    queued = queue_manager_iteration_choice(**scope, expected_backlog_version=backlog["version"])
    batch = run_queued_iteration_batch(**scope, project_config=config, proposal_ids=queued["queued_proposal_ids"],
        expected_revisions={key: 1 for key in queued["queued_proposal_ids"]}, confirmed=True, **run_scope)
    assert batch["status"] == "review", batch
    run = dict(**scope, run_id=batch["run_id"])
    report_path = dispatch_artifact_root(source, batch["dispatch_run_id"]) / "report.json"
    original_report = report_path.read_bytes()
    initial_head = batch["report"]["integration"]["candidate_commit"]
    assert all(task["status"] == "review" and not task["delivery_verified"]
               and "observed_workflow_status" not in task for task in ledger.list_tasks())

    checks = tmp_path / "check-calls.jsonl"
    check = ("import json; from pathlib import Path; "
             "observed = {p: Path(p).read_text() for p in ('unit-a.txt', 'unit-b.txt')}; "
             f"Path({str(checks)!r}).open('a').write(json.dumps(observed) + '\\n'); "
             "print('Checked both tasks against the retained regression'); "
             "assert observed == {'unit-a.txt': 'fixed\\n', 'unit-b.txt': 'ready\\n'}")
    prepared = prepare_project_batch_quality(**run, argv=[sys.executable, "-I", "-B", "-c", check], criteria=[0, 1],
        reviewer_model="offline-fixture", fix_model="offline-fixture", seconds=30, wall_seconds=300,
        fix_seconds=30, evidence_paths=["regression.txt"])
    snapshot = store_for_project(source, existing_only=True).load(prepared["execution_id"])
    assert snapshot.task.source_type == "project_task_batch"
    assert not calls.read_text().count('"kind": "review"')
    review_admission = _review_admission(tmp_path, source, snapshot, binaries["review"], native_home)
    advance = dict(**run, expected_input_digest=prepared["input_digest"], confirmed=True,
                   billing_acknowledged=True, codex_review_admission=review_admission)
    with pytest.raises(ValueError, match="batch_quality_input_changed"):
        advance_project_batch_quality(**{**advance, "expected_input_digest": "0" * 64})
    assert not checks.exists()
    first = advance_project_batch_quality(**advance)
    assert first["quality"]["status"] == "blocked"
    assert first["quality"]["check_status"] == "failed"
    assert first["quality"]["validation"]["review"], first["quality"]["reason"]
    assert first["quality"]["validation"]["review"]["decision"] == "request_changes"
    assert first["quality"]["reason"] == "correction_verified_runtime_unavailable"
    assert "exact-attempt native correction policy" in first["quality"]["next_action"]
    assert first["quality"]["correction_input"] and Path(first["quality"]["correction_workspace"]).is_dir()
    automatic = AutomaticCandidateReviewService(source, codex_review_admission=review_admission)
    awaiting = automatic.projection(snapshot.execution_id)
    assert awaiting["status"] == "awaiting_worker", (first, awaiting)
    assert awaiting["worker_job_id"]
    events = [json.loads(line) for line in calls.read_text().splitlines()]
    assert [item["kind"] for item in events].count("worker") == 2
    assert [item["kind"] for item in events].count("review") == 1
    assert not any(item["kind"] == "correction" for item in events)
    assert json.loads(checks.read_text()) == {"unit-a.txt": "broken\n", "unit-b.txt": "ready\n"}
    assert git(source, "rev-parse", "HEAD") == original_head
    assert not (source / "unit-a.txt").exists()
    with pytest.raises(ValueError, match="batch_quality_acceptance_required"):
        accept_project_batch_delivery(**scope, request={"run_id": batch["run_id"],
            "candidate_commit": initial_head, "landed_commit": original_head, "reviewed": True, "confirmed": True})

    quality_root = store_for_project(source, existing_only=True).root / execution_key(snapshot.execution_id)
    def retained_files():
        return {str(path.relative_to(quality_root)): path.read_bytes()
                for path in quality_root.rglob("*") if path.is_file()}
    before_reads, before_calls = retained_files(), calls.read_bytes()
    project_batch_quality_evidence(**run)
    code, report = web.handle_project_api_get(tmp_path, f"/api/projects/DEMO/iteration-batches/{batch['run_id']}")
    assert code == 200 and report["quality"]["correction_input"] == first["quality"]["correction_input"]
    assert retained_files() == before_reads and calls.read_bytes() == before_calls

    correction_admission = _correction_admission(tmp_path, source, awaiting["worker_job_id"], binaries["worker"], native_home)
    endpoint = "/api/projects/DEMO/task-assistant/actions"
    action = {"action": "advance_project_batch_quality", "run_id": batch["run_id"],
              "expected_input_digest": prepared["input_digest"], "confirmed": True, "billing_acknowledged": True}
    with pytest.raises(web.ProjectApiError, match="frozen input identity"):
        web.handle_project_api_post(tmp_path, endpoint, {**action, "argv": ["unapproved-command"]})
    code, completed = web.handle_project_api_post(tmp_path, endpoint, action,
        codex_review_admission=review_admission, codex_correction_admission=correction_admission)
    assert code == 200
    evidence = project_batch_quality_evidence(**run)
    assert completed["quality"]["status"] == "reviewed", (completed, evidence)
    final_head = evidence["candidate_commit"]
    assert final_head != initial_head
    assert (Path(evidence["candidate_path"]) / "unit-a.txt").read_text() == "fixed\n"
    review = evidence["validation"]["review"]
    assert evidence["check_status"] == "passed" and evidence["acceptance_status"] == "accepted_by_review_policy"
    assert review["independence"] == "registered_read_only_new_session"
    assert review["snapshot"] == snapshot.reference
    assert review["candidate"]["head_sha"] == review["provenance"]["target"]["head_sha"] == final_head
    observations = [json.loads(line) for line in checks.read_text().splitlines()]
    assert observations == [{"unit-a.txt": "broken\n", "unit-b.txt": "ready\n"},
                            {"unit-a.txt": "fixed\n", "unit-b.txt": "ready\n"}]
    events = [json.loads(line) for line in calls.read_text().splitlines()]
    reviews = [item for item in events if item["kind"] == "review"]
    assert [item["target"]["head_sha"] for item in reviews] == [initial_head, final_head]
    assert all(item["supplemental_evidence"] for item in reviews)
    assert "review_output" not in evidence["validation"]["receipt"]["observation"]
    assert [item["broken"] for item in reviews] == [True, False]
    assert [item["kind"] for item in events].count("correction") == 1
    assert len({item["cwd"] for item in events}) == 5
    assert report_path.read_bytes() == original_report
    assert git(source, "rev-parse", "HEAD") == original_head
    assert all(task["status"] == "review" and not task["delivery_verified"] for task in ledger.list_tasks())

    if delivery_handoff is None:
        git(source, "checkout", "-qb", "qa/other")
        tick_project_continuation(**scope, project_config=config)
        assert git(source, "rev-parse", "HEAD") == original_head
        assert all(task["status"] == "review" for task in ledger.list_tasks())
        git(source, "checkout", "-q", branch)
        waiting = source / "untracked-user-work.txt"
        waiting.write_text("Do not replace local user work.\n")
        tick_project_continuation(**scope, project_config=config)
        assert git(source, "rev-parse", "HEAD") == original_head and waiting.is_file()
        waiting.unlink()
        tick_project_continuation(**scope, project_config=config)
        saved = TaskAssistantSessionStore(tmp_path, "DEMO").read()["project_task_batches"][batch["run_id"]]
        assert saved["delivery_receipt"]["producer"] == "independently_reviewed_candidate_delivery"
        assert git(source, "rev-parse", "HEAD") == final_head
        assert git(source, "rev-parse", "qa/other") == original_head
    else:
        git(source, "merge", "--quiet", "--no-edit", "--no-ff", final_head)
    delivery = dict(run_id=batch["run_id"], candidate_commit=final_head, landed_commit=git(source, "rev-parse", "HEAD"),
                    reviewed=True, confirmed=True)
    if delivery_handoff is not None:
        delivery_handoff(tmp_path, source, config, delivery)
    assert all(task["status"] == "done" and task["delivery_verified"]
               and task["delivery_receipt"]["candidate_commit"] == final_head for task in ledger.list_tasks())
    saved_calls, saved_checks = calls.read_bytes(), checks.read_bytes()
    saved_jobs = WorkerJobStore(source).list_jobs()
    assert len(saved_jobs) == 5 and snapshot.job_id not in {job["id"] for job in saved_jobs}
    advance_project_batch_quality(**advance, codex_correction_admission=correction_admission)
    accept_project_batch_delivery(**scope, request=delivery)
    assert calls.read_bytes() == saved_calls and checks.read_bytes() == saved_checks
    assert WorkerJobStore(source).list_jobs() == saved_jobs
    assert report_path.read_bytes() == original_report


@pytest.mark.parametrize("needs_qa", [False, True])
def test_manager_delivers_with_chosen_evidence_without_batch_setup(tmp_path, monkeypatch, needs_qa):
    source = repository(tmp_path / "source")
    branch = "qa/delivery"
    git(source, "checkout", "-qb", branch)
    config = tmp_path / "projects/demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nrepos:\n  root: {source}\nworker_backend:\n  mode: codex\nlocal_delivery_branch: {branch}\n")
    ProjectRegistry().register_project(config, root=source, key="DEMO")
    binary = tmp_path / "bin/codex"
    binary.parent.mkdir()
    calls = tmp_path / "calls.jsonl"
    binary.write_text(f"#!{sys.executable}\n" + r'''
import json, os, subprocess, sys
from pathlib import Path
if "--version" in sys.argv:
    print("offline Team reviewer")
    raise SystemExit(0)
task = sys.stdin.read()
product = Path("product.txt")
if "REVIEW_PRODUCT" in task:
    action, result = "review", f"Checked the candidate content: {product.read_text().strip()}."
elif "CORRECT_PRODUCT" in task:
    assert product.read_text() == "broken\n"
    action, result = "correct", "Corrected the reported defect."
    product.write_text("ready\n")
else:
    action, result = "implement", "Created the candidate."
    product.write_text(os.environ["TEAM_FIXTURE_INITIAL_PRODUCT"] + "\n")
with Path(os.environ["TEAM_REVIEW_FIXTURE_CALLS"]).open("a") as stream:
    stream.write(json.dumps({"action": action, "cwd": str(Path.cwd()),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}) + "\n")
Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(result)
''')
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("TEAM_REVIEW_FIXTURE_CALLS", str(calls))
    monkeypatch.setenv("TEAM_FIXTURE_INITIAL_PRODUCT", "broken" if needs_qa else "ready")
    ledger = LocalTaskStore(source, "DEMO")
    original = ledger.create_task(title="Initial product implementation", description="Create the product output.",
        role="implementation", write_domains=["product.txt"], acceptance_criteria=["product.txt contains ready."])
    scope = dict(workspace=tmp_path, project_key="DEMO", root=source, project_config=config)
    configure_project_continuation(**scope, request={
        "task_ids": [original["id"]], "idempotency_key": "team-quality", "confirmed": True,
        "billing_acknowledged": True, "expected_backlog_version": project_iteration_backlog(source, "DEMO")["version"],
        "discovery": {"objective": "Deliver the product.", "allowed_write_domains": ["."],
                      "allow_team_roles": True, "allow_backlog_judgment": True, "allow_undelivered_feedback": True,
                      "allow_candidate_handoff": True, "use_project_backlog": True},
    })
    decisions = []

    def manager(*, context, **_):
        if context.get("verified_outcome") and not context.get("batch_feedback"):
            return {"selection": {"new_task": None, "rationale": "The scoped work has been delivered."}}
        feedback = context.get("batch_feedback")
        if not feedback:
            return {"selection": {"selected_task_ids": [original["id"]],
                                  "rationale": "Implement the scoped product result."}}
        result = feedback["tasks"][0]
        source_refs = [feedback["receipt_id"]]
        if not needs_qa or result["role"] == "qa" and "ready" in result["report"]:
            decisions.append(context["delivery_candidate"]["candidate_commit"])
            return {"selection": {"new_task": None, "accept_candidate": True,
                                  "rationale": "The available evidence supports the original criterion."}}
        if result["role"] == "qa":
            task = {"role": "implementation", "title": "CORRECT_PRODUCT", "write_domains": ["product.txt"],
                    "description": "Correct the content defect found by the independent review."}
        else:
            task = {"role": "qa", "title": "REVIEW_PRODUCT", "write_domains": [],
                    "description": "Verify the exact candidate against the original criterion."}
        return {"selection": {"new_task": {**task, "acceptance_criteria": ["Report the evidence for the product content."],
                                           "dependencies": [], "source_refs": source_refs},
                              "rationale": "Use the current candidate and the evidence from this batch."}}

    deadline = time.monotonic() + 180
    while not ledger.get_task(original["id"]).get("delivery_verified"):
        state = tick_project_continuation(**scope, manager_runner=manager)
        assert state["status"] == "active", state.get("reason")
        assert time.monotonic() < deadline, state
        time.sleep(.02)
    state = tick_project_continuation(**scope, manager_runner=manager)
    assert state["status"] != "blocked", state.get("reason")
    assert decisions == [git(source, "rev-parse", "HEAD")]
    assert (source / "product.txt").read_text() == "ready\n"
    accepted_tasks = [ledger.get_task(original["id"])]
    if needs_qa:
        accepted_tasks.append(next(task for task in ledger.list_tasks() if task["title"] == "CORRECT_PRODUCT"))
    for task in accepted_tasks:
        assert task["status"] == "done" and task["delivery_receipt"]["quality"]["candidate_commit"] == decisions[0]
        code, report = web.handle_project_api_get(tmp_path, f"/api/projects/DEMO/iteration-batches/{task['run_id']}")
        assert code == 200 and report["quality"]["acceptance_status"] == "accepted_by_manager"
        assert report["quality"]["decision_receipt_id"] == task["delivery_receipt"]["quality"]["decision_receipt_id"]
    invocations = [json.loads(line) for line in calls.read_text().splitlines()]
    expected = ["implement", "review", "correct", "review"] if needs_qa else ["implement"]
    assert [item["action"] for item in invocations] == expected
    if needs_qa:
        assert invocations[-1]["head"] == decisions[0]
    assert len({item["cwd"] for item in invocations}) == len(expected)
