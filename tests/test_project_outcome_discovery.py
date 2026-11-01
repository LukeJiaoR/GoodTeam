"""One bounded project loop learns from delivery and creates its next task.

Only CLI responses are offline protocol fixtures, not real model judgments or
proof of native confinement. Manager planning, Worker jobs, checks, protected
review, Git delivery, memory provenance and create-once recovery are production
paths. No provider or orchestration function is replaced.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from goodteam_os import project_iteration_continuation as continuation
from goodteam_os import web
from goodteam_os.execution.task_snapshot_store import store_for_project
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_iteration_quality import prepare_project_batch_quality
from goodteam_os.project_outcome_discovery import validate_discovered_task
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.skills.codex_trusted import TrustedCodexReviewAdmission
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.team_control_plane import ensure_team_state
from goodteam_os.workers import WorkerJobStore
from test_dispatch_isolation import git, repository
from test_project_iteration_quality import _private_json


def _offline_cli(tmp_path, monkeypatch):
    """Use a pinned native executable with an empty, private credential home."""
    compiler = shutil.which("cc")
    assert compiler, "The native review regression requires a local C compiler."
    calls = tmp_path / "cli-calls.jsonl"
    fixture = tmp_path / "offline_discovery_transport.py"
    fixture.write_text("CALLS = " + repr(str(calls)) + "\n" + r'''
import json, re, sys
from pathlib import Path
from uuid import uuid4

if '--version' in sys.argv:
    print('codex-cli 0.160.0')
    raise SystemExit(0)
prompt = sys.stdin.read()
assert prompt

def event(kind, **details):
    with Path(CALLS).open('a') as stream:
        stream.write(json.dumps({'kind': kind, 'cwd': str(Path.cwd()), **details}) + '\n')

if '# Exact review target\n' in prompt:
    target = json.loads(prompt.split('# Exact review target\n', 1)[1].split('\n# Captured sources', 1)[0])
    sources = json.loads(prompt.split('# Captured sources (logical names; files under sources/ for Git targets)\n', 1)[1]
                        .split('\n# Result channel contract', 1)[0])
    evidence = [{'id': 'E' + str(index), 'path': name, 'line_start': 1, 'line_end': 1,
                 'quote': text.splitlines(keepends=True)[0],
                 'observation': 'Offline fixture inspected the exact candidate source.'}
                for index, (name, text) in enumerate(sorted(sources.items()))
                if name.startswith('head/') and text]
    assert evidence
    criteria = sorted(set(re.findall(r'criterion:[0-9a-f]{64}:\d+', prompt)))
    assert len(criteria) == 1
    report = {'schema_version': 'goodteam.code-review-report.v1',
              'summary': 'Offline source-backed review protocol fixture; no real model judgment.',
              'findings': [], 'evidence': evidence,
              'checks': [{'name': criteria[0], 'status': 'checked',
                          'evidence_ids': [item['id'] for item in evidence],
                          'detail': 'Observed the assigned file and the independently passed host check.'}]}
    event('review', target=target)
    for item in [{'type': 'thread.started', 'thread_id': 'offline-' + uuid4().hex},
                 {'type': 'turn.started'},
                 {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(report)}},
                 {'type': 'turn.completed'}]:
        print(json.dumps(item))
else:
    assert Path(sys.argv[sys.argv.index('--cd') + 1]) == Path.cwd()
    output = Path(sys.argv[sys.argv.index('--output-last-message') + 1])
    if 'Role: orchestrator\n' in prompt:
        packet = json.loads(prompt.split('## Context\n\n', 1)[1].split('\n\n## Sandbox Context', 1)[0])
        if packet['schema_version'] == 'project_iteration_choice_packet.v1':
            eligible = [item['task_id'] for item in packet['backlog']['candidates'] if item['eligible_to_plan']]
            assert len(eligible) == 1
            response = {'selected_task_ids': eligible, 'selected_task_id': eligible[0],
                        'compared_task_ids': eligible,
                        'rationale': 'Start the only authorized seed task before observing its reviewed delivery.'}
            kind = 'manager_choice'
        else:
            assert packet['schema_version'] == 'project_outcome_discovery_packet.v1'
            outcome = packet['verified_outcome']
            assert outcome['quality']['check_status'] == 'passed'
            assert outcome['quality']['acceptance_status'] == 'accepted_by_review_policy'
            assert all(task['status'] == 'done' and task['delivery_verified'] for task in outcome['tasks'])
            candidate = outcome['delivery']['candidate_commit']
            name = 'docs/followup-' + candidate[:8] + '.txt'
            response = {'new_task': {
                'title': 'TASK_FILE=' + name,
                'description': 'Document a follow-up from the verified seed delivery. EVIDENCE_CANDIDATE=' + candidate,
                'acceptance_criteria': [name + ' records the exact delivered candidate ' + candidate + '.'],
                'write_domains': [name], 'dependencies': [task['id'] for task in outcome['tasks']],
                'source_refs': packet['allowed_source_refs']},
                'rationale': 'The delivered seed now permits a distinct follow-up recording its verified candidate identity.'}
            kind = 'manager_discovery'
        event(kind, packet=packet, response=response)
        output.write_text(json.dumps(response))
    else:
        assert 'Role: implementation\n' in prompt
        name = re.search(r'TASK_FILE=(docs/[a-z0-9-]+\.txt)', prompt).group(1)
        if name == 'docs/seed.txt':
            content = 'ready for a project follow-up\n'
        else:
            candidate = re.search(r'EVIDENCE_CANDIDATE=([0-9a-f]{40})', prompt).group(1)
            assert name == 'docs/followup-' + candidate[:8] + '.txt'
            assert Path('docs/seed.txt').read_text() == 'ready for a project follow-up\n'
            content = 'follow-up uses delivered candidate: ' + candidate + '\n'
        Path(name).parent.mkdir(exist_ok=True)
        Path(name).write_text(content)
        output.write_text('Offline Worker produced a candidate; independent review and delivery remain required.\n')
        event('worker', task=name)
''')
    launcher = tmp_path / "offline_codex.c"
    launcher.write_text(
        '#include <stdlib.h>\n#include <unistd.h>\n'
        'int main(int argc, char **argv) {\n'
        '  char **args = calloc((size_t)argc + 2, sizeof(char *));\n'
        f'  args[0] = {json.dumps(sys.executable)};\n'
        f'  args[1] = {json.dumps(str(fixture))};\n'
        '  for (int i = 1; i < argc; i++) args[i + 1] = argv[i];\n'
        '  execv(args[0], args); return 111;\n}\n')
    binary = tmp_path / "bin/codex"
    binary.parent.mkdir()
    subprocess.run([compiler, str(launcher), "-o", str(binary)], check=True, capture_output=True)
    native_home = tmp_path / "offline-native-home"
    native_home.mkdir(mode=0o700)
    _private_json(native_home / "auth.json", {})
    (native_home / "config.toml").write_text("# Offline fixture: no provider credential.\n")
    (native_home / "config.toml").chmod(0o600)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CODEX_HOME", str(native_home))
    monkeypatch.setenv("GOODTEAM_WORKER_MAX_ATTEMPTS", "1")
    return binary, native_home, calls


def test_verified_outcome_creates_once_then_delivers_next_iteration_within_frozen_authority(tmp_path, monkeypatch):
    binary, native_home, calls = _offline_cli(tmp_path, monkeypatch)
    source = repository(tmp_path / "source")
    config = tmp_path / "projects/demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nsource_type: existing_project\nrepos:\n  root: {source}\nworker_backend:\n  mode: codex\n")
    ProjectRegistry().register_project(config, root=source, key="DEMO")
    ensure_team_state(tmp_path, "DEMO", config)
    ledger = LocalTaskStore(source, "DEMO")
    seed = ledger.create_task(title="TASK_FILE=docs/seed.txt", description="Write the bounded seed documentation file.",
        role="implementation", write_domains=["docs/seed.txt"],
        acceptance_criteria=["docs/seed.txt contains ready for a project follow-up followed by a newline."])
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    endpoint = "/api/projects/DEMO/task-assistant/actions"

    def action(request, **authority):
        code, result = web.handle_project_api_post(tmp_path, endpoint, request, **authority)
        assert code == 200
        return result

    def tick():
        continuation.tick_authorized_project_continuations(tmp_path)
        return store.read()["project_continuation"]

    def events():
        return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

    def settled(task_id):
        deadline = time.monotonic() + 20
        while ledger.get_task(task_id)["status"] not in {"review", "blocked"}:
            assert time.monotonic() < deadline, store.read()["project_continuation"]
            time.sleep(.01)
        assert ledger.get_task(task_id)["status"] == "review", ledger.get_task(task_id)
        return ledger.get_task(task_id)["run_id"]

    request = dict(action="start_project_continuation", task_ids=[seed["id"]], max_batches=2,
        confirmed=True, billing_acknowledged=True, idempotency_key="legacy-stopped-before-work",
        expected_backlog_version=project_iteration_backlog(source, "DEMO")["version"])
    legacy = action(request)["project_continuation"]
    action({"action": "stop_project_continuation", "continuation_id": legacy["id"]})
    assert action(request)["project_continuation"]["status"] == tick()["status"] == "stopped"
    assert not events() and ledger.list_tasks() == [seed]

    request.update(idempotency_key="two-deliveries-with-outcome-discovery", discovery={
        "objective": "Use delivered documentation evidence to create one bounded, valuable follow-up.",
        "allowed_write_domains": ["docs"], "max_new_tasks": 1, "max_planning_calls": 2})
    with pytest.raises(web.ProjectApiError, match="different scope or limits"):
        action({**request, "idempotency_key": "legacy-stopped-before-work"})
    assert tick()["status"] == "stopped" and not events()
    authorized = action(request)["project_continuation"]
    assert authorized["seed_task_ids"] == authorized["task_ids"] == [seed["id"]]
    assert not events()
    tick()
    first_run = settled(seed["id"])
    tick()  # Awaiting review/delivery cannot spend the discovery budget.
    assert [event["kind"] for event in events()] == ["manager_choice", "worker"]
    assert len(ledger.list_tasks()) == 1 and not (source / "docs/seed.txt").exists()

    checks = tmp_path / "check-calls.jsonl"

    def quality_and_deliver(run_id, task_id, path, content):
        check = ("import json; from pathlib import Path; "
                 f"observed = Path({path!r}).read_text(); "
                 f"Path({str(checks)!r}).open('a').write(json.dumps({{'path': {path!r}, 'content': observed}}) + '\\n'); "
                 f"assert observed == {content!r}")
        prepared = prepare_project_batch_quality(workspace=tmp_path, project_key="DEMO", root=source,
            run_id=run_id, argv=[sys.executable, "-I", "-B", "-c", check], criteria=[0],
            reviewer_model="offline-fixture", fix_model="offline-fixture", seconds=30, wall_seconds=300, fix_seconds=30)
        snapshot = store_for_project(source, existing_only=True).load(prepared["execution_id"])
        policy = dict(schema_version="goodteam.codex-trusted-review-policy.v1",
            authorization_id="discovery-" + snapshot.payload_digest[:24], worker_root=str(source),
            task={"kind": "direct_task", "project_key": "DEMO", "logical_id": "candidate-review:" + snapshot.payload_digest},
            codex=str(binary), codex_home=str(native_home), model="offline-fixture", max_contexts=1,
            deadline_seconds=30, expires_at=int(time.time()) + 600, trusted_task_inputs=True,
            accept_native_managed_configuration=True, accept_unknown_cost=True,
            provider_request_limit=None, usd_limit=None)
        admission = TrustedCodexReviewAdmission(_private_json(tmp_path / (run_id + "-review.json"), policy))
        advance = dict(action="advance_project_batch_quality", run_id=run_id,
            expected_input_digest=prepared["input_digest"], confirmed=True, billing_acknowledged=True)
        head = git(source, "rev-parse", "HEAD")
        quality = action(advance, codex_review_admission=admission)["quality"]
        assert quality["status"] == "reviewed", quality
        assert quality["check_status"] == "passed" and quality["acceptance_status"] == "accepted_by_review_policy"
        review = quality["validation"]["review"]
        assert review["independence"] == "registered_read_only_new_session"
        assert review["candidate"]["head_sha"] == quality["candidate_commit"]
        assert git(source, "rev-parse", "HEAD") == head and ledger.get_task(task_id)["status"] == "review"
        git(source, "merge", "--quiet", "--no-edit", "--no-ff", quality["candidate_commit"])
        delivery = dict(action="accept_project_batch_delivery", run_id=run_id,
            candidate_commit=quality["candidate_commit"], landed_commit=git(source, "rev-parse", "HEAD"),
            reviewed=True, confirmed=True)
        action(delivery)
        task = ledger.get_task(task_id)
        assert task["status"] == "done" and task["delivery_verified"]
        before_calls, before_checks = calls.read_bytes(), checks.read_bytes()
        action(advance, codex_review_admission=admission)
        action(delivery)
        assert calls.read_bytes() == before_calls and checks.read_bytes() == before_checks
        return task["delivery_receipt"]

    first_receipt = quality_and_deliver(first_run, seed["id"], "docs/seed.txt", "ready for a project follow-up\n")
    assert len(ledger.list_tasks()) == 1
    original_write = TaskAssistantSessionStore._write
    interrupted = []

    def lose_publication(self, payload):
        value = payload.get("project_continuation") or {}
        discovered = [task for task in ledger.list_tasks() if task["id"] != seed["id"]]
        if (self.path == store.path and discovered and not interrupted
                and discovered[0]["id"] in value.get("discovered_task_ids", [])):
            interrupted.append(discovered[0]["id"])
            raise OSError("Fixture interruption after durable create-once, before session publication")
        return original_write(self, payload)

    with monkeypatch.context() as patch:
        patch.setattr(TaskAssistantSessionStore, "_write", lose_publication)
        pending = tick()
    assert len(interrupted) == 1
    next_id = interrupted[0]
    assert pending["status"] == "active" and pending["reason"] == "discovery_publication_pending"
    assert pending["discovery_pending"]["selection"]["new_task"]
    assert pending["manager_calls"] == 2 and pending["task_ids"] == [seed["id"]]
    assert len(ledger.list_tasks()) == 2 and ledger.get_task(next_id)["status"] == "todo"
    assert not ledger.get_task(next_id).get("run_id")
    assert [event["kind"] for event in events()] == ["manager_choice", "worker", "review", "manager_discovery"]
    assert action(request)["project_continuation"] == pending

    tail_interrupted = []
    before_tail = calls.read_bytes()

    def lose_queue_acknowledgment(self, payload):
        original_write(self, payload)  # The queue reservation really reached disk.
        value = payload.get("project_continuation") or {}
        reservation = value.get("pending_batch") or {}
        if (self.path == store.path and not tail_interrupted and value.get("discovery_pending") is None
                and reservation.get("task_ids") == [next_id] and not reservation.get("run_id")):
            tail_interrupted.append(deepcopy(reservation))
            raise OSError("Fixture lost acknowledgment after the final queue reservation was persisted")

    with monkeypatch.context() as patch:
        patch.setattr(TaskAssistantSessionStore, "_write", lose_queue_acknowledgment)
        queued = tick()
    assert len(tail_interrupted) == 1 and queued["status"] == "active"
    assert queued["discovery_pending"] is None and queued["pending_batch"] == tail_interrupted[0]
    assert queued["new_tasks_created"] == 1 and queued["manager_calls"] == queued["batches_started"] == 2
    assert len(ledger.list_tasks()) == 2 and ledger.get_task(next_id)["status"] == "todo"
    assert not ledger.get_task(next_id).get("run_id") and calls.read_bytes() == before_tail
    current = tick()  # Resume the original queue reservation without another Manager call.
    second_run = settled(next_id)
    assert first_run != second_run
    assert current["seed_task_ids"] == [seed["id"]] and current["discovered_task_ids"] == [next_id]
    assert current["task_ids"] == [seed["id"], next_id]
    assert current["new_tasks_created"] == 1 and current["manager_calls"] == current["batches_started"] == 2
    assert len(ledger.list_tasks()) == 2
    discovery = next(event for event in events() if event["kind"] == "manager_discovery")
    packet = discovery["packet"]
    outcome = packet["verified_outcome"]
    assert outcome["delivery"] == first_receipt
    assert outcome["tasks"][0]["id"] == seed["id"] and outcome["tasks"][0]["delivery_verified"]
    assert set(packet["allowed_source_refs"]) == {first_receipt["receipt_id"],
        first_receipt["quality"]["validation_receipt_id"], first_receipt["quality"]["review_receipt_id"]}
    pickup = packet["memory_pickup"]
    assert pickup["provenance"]["schema_version"] == "memory_pickup.v1"
    assert pickup["provenance"]["wal_event_count"] > 0 and pickup["provenance"]["content_sha256"]
    memory = RoleMemoryStore(source)
    assert memory.validate_pickup_packet(pickup)["provenance"] == pickup["provenance"]
    assert first_receipt["receipt_id"] not in pickup["short_term"]  # Delivery is separately verified provenance.
    outside = deepcopy(discovery["response"])
    outside["new_task"]["write_domains"] = ["outside-approved-scope.txt"]
    with pytest.raises(ValueError, match="approved discovery scope"):
        validate_discovered_task(packet, outside)
    with pytest.raises(web.ProjectApiError, match="different scope or limits"):
        action({**request, "discovery": {**request["discovery"], "allowed_write_domains": ["docs", "src"]}})
    assert action(request)["project_continuation"]["seed_task_ids"] == [seed["id"]]

    candidate = first_receipt["candidate_commit"]
    next_path = "docs/followup-" + candidate[:8] + ".txt"
    next_task = ledger.get_task(next_id)
    assert next_task["title"] == "TASK_FILE=" + next_path and next_task["dependencies"] == [seed["id"]]
    assert next_task["write_domains"] == [next_path] and next_task["role"] == "implementation"
    assert not (source / next_path).exists()
    second_receipt = quality_and_deliver(second_run, next_id, next_path,
        "follow-up uses delivered candidate: " + candidate + "\n")
    terminal = tick()
    assert terminal["status"] == "exhausted" and terminal["reason"] == "approved_iteration_budget_used"
    assert terminal["seed_task_ids"] == [seed["id"]] and terminal["discovered_task_ids"] == [next_id]
    assert terminal["manager_calls"] == terminal["max_manager_calls"] == 2
    assert terminal["batches_started"] == terminal["max_batches"] == 2
    assert terminal["new_tasks_created"] == 1
    saved_tasks, saved_calls = ledger.list_tasks(), calls.read_bytes()
    delivered = ledger.get_task(next_id)
    creation_ref = next(ref for ref in delivered["external_refs"] if ref.startswith("project-discovery:"))
    intent = {key: delivered[key] for key in ("title", "description", "role", "priority", "dependencies",
        "acceptance_criteria", "write_domains", "execution_policy", "external_refs", "artifact_refs")}
    assert ledger.create_task_once(creation_ref=creation_ref, intent=intent) == delivered
    duplicate_ref = "project-discovery:another-outcome"
    with pytest.raises(ValueError, match="equivalent project task"):
        ledger.create_task_once(creation_ref=duplicate_ref, intent={**intent,
            "external_refs": [duplicate_ref, *[ref for ref in intent["external_refs"] if ref != creation_ref]]})
    assert action(request)["project_continuation"] == tick() == terminal
    assert ledger.list_tasks() == saved_tasks and calls.read_bytes() == saved_calls
    assert all(task["status"] == "done" and task["delivery_verified"] for task in saved_tasks)
    facts = [event for event in memory.list_events("orchestrator", include_execution=True)
             if event["source"].startswith("execution:project-delivery:")]
    assert {json.loads(event["body"])["verified_delivery"] for event in facts} == {
        first_receipt["receipt_id"], second_receipt["receipt_id"]}
    assert len(facts) == 2
    assert [event["kind"] for event in events()] == [
        "manager_choice", "worker", "review", "manager_discovery", "worker", "review"]
    assert len(checks.read_text().splitlines()) == 2
    job_store = WorkerJobStore(source)
    jobs = job_store.list_jobs()
    managers = [job for job in jobs if job["role"] == "orchestrator"]
    assert len(jobs) == 6 and len(managers) == 2
    assert all(job["status"] == "completed" and job["execution_policy"] == "read_only" for job in managers)
    discovery_job = next(job for job in managers if job["phase"] == "project_outcome_discovery")
    assert json.loads((job_store.root / discovery_job["id"] / "context.md").read_text()) == packet
    captured_digest = sha256(json.dumps(packet, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert discovery_job["idempotency_key"].endswith(":project-outcome:" + captured_digest)
