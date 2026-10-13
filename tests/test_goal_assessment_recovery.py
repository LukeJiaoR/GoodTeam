"""Known-completed protocol failures may retry; execution ambiguity never may."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from goodteam_os.goal_continuation import authority_snapshot, handle_goal_action, tick_goal
from goodteam_os.state import StateStore
from goodteam_os.workers import WorkerJobStore
from test_goal_continuation import setup as fixture_setup, finished_run  # noqa: F401
from test_task_skill_entrypoints import ctx as skill_fixture, method, bind  # noqa: F401


@pytest.fixture
def setup(fixture_setup):  # noqa: F811
    return fixture_setup


def recovery_goal(setup, *, attempts=2):
    workspace, root, config, store, _ = setup
    return store.create_goal(objective="Make bounded fixture complete", acceptance_criteria=["Feature works", "Regression check exists"],
        allowed_scope=["Only the fixture"], max_work_units=2, max_assessment_attempts=attempts,
        confirmed=True, idempotency_key="recovery", authority_snapshot=authority_snapshot(workspace, "DEMO", config, root))


def protocol_worker(monkeypatch, *, invalid_count=1, invalid_kind="json"):
    calls = []
    original = WorkerJobStore._complete_fake
    def complete(store, job_root, job):
        result = original(store, job_root, job)
        if job.get("phase") != "goal_assessment":
            return result
        assert job["adapter"] == "fake"
        calls.append(job["id"])
        packet = json.loads((job_root / "context.md").read_text())
        ref, text = next(iter(packet["evidence"].items()))
        checks = [{"criterion": i, "status": "missing" if i == 1 and "missing" in text else "met",
                   "evidence_ref": ref, "quote": text} for i in range(len(packet["acceptance_criteria"]))]
        output = {"requires_new_scope": False, "checks": checks}
        if len(calls) <= invalid_count:
            if invalid_kind == "json":
                (job_root / "result.md").write_text("{unfinished")
                return result
            if invalid_kind == "non_object":
                (job_root / "result.md").write_text("[]")
                return result
            if invalid_kind == "scope":
                output["requires_new_scope"] = True
            if invalid_kind == "reference":
                output["checks"][0]["evidence_ref"] = "unrelated-run"
        (job_root / "result.md").write_text(json.dumps(output))
        return result
    monkeypatch.setattr(WorkerJobStore, "_complete_fake", complete)
    return calls


def run_tick(setup, goal, **kwargs):
    return tick_goal(setup[0], "DEMO", goal["id"], starter=finished_run, **kwargs)


@pytest.mark.parametrize("invalid_kind", ["json", "non_object"])
def test_completed_protocol_failure_reassesses_same_goal_without_repeating_work(setup, monkeypatch, invalid_kind):
    goal = recovery_goal(setup)
    calls = protocol_worker(monkeypatch, invalid_kind=invalid_kind)
    first = run_tick(setup, goal)
    run_id = first["active_work"]["run_id"]
    failed = run_tick(setup, goal)
    assert failed["status"] == "active"
    assert len(failed["active_work"]["assessment_failures"]) == 1
    assert failed["active_work"]["run_id"] == run_id
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: run_tick(setup, goal), range(3)))
    # Recreated store/ticks simulate process re-entry; no successful workflow is replayed.
    final = run_tick(setup, goal)
    assert final["status"] == "complete"
    assert len(final["work_units"]) == 2
    assert len(calls) == len(set(calls)) == 3
    assert final["work_units"][0]["run_id"] == run_id
    assert all(result["id"] == goal["id"] for result in results)


def retry_request(goal):
    work = goal["active_work"]
    failure = work["assessment_failures"][-1]
    return dict(action="retry_goal_assessment", goal_id=goal["id"], expected_revision=goal["revision"],
        work_id=work["id"], run_id=work["run_id"], evidence_digest=failure["evidence_digest"],
        failed_job_id=failure["job_id"], confirmed=True, idempotency_key="retry-explicit")


def test_old_goal_gets_no_automatic_retry_but_exact_confirmation_resumes(setup, monkeypatch):
    workspace, root, _, store, create = setup
    goal = create()  # historical creation contract has no retry allowance
    calls = protocol_worker(monkeypatch)
    run_tick(setup, goal)
    blocked = run_tick(setup, goal)
    assert blocked["status"] == "blocked"
    assert run_tick(setup, goal)["status"] == "blocked" and len(calls) == 1
    request = retry_request(blocked)
    resumed = handle_goal_action(workspace, "DEMO", root, request)["goal"]
    assert resumed["id"] == goal["id"] and resumed["status"] == "active"
    assert handle_goal_action(workspace, "DEMO", root, request)["goal"] == resumed
    reviewed = run_tick(setup, goal)
    assert reviewed["work_units"][0]["status"] == "done" and len(calls) == 2
    assert len(store.get_goal(goal["id"])["work_units"]) == 2


@pytest.mark.parametrize("change", ["stop", "config", "evidence", "stale", "run"])
def test_retry_rejects_changed_authority_or_stopped_goal(setup, monkeypatch, change):
    workspace, root, config, store, create = setup
    goal = create()
    protocol_worker(monkeypatch)
    run_tick(setup, goal)
    blocked = run_tick(setup, goal)
    request = retry_request(blocked)
    if change == "stop":
        handle_goal_action(workspace, "DEMO", root, dict(action="stop_goal", goal_id=goal["id"],
            expected_revision=blocked["revision"], idempotency_key="stop"))
    elif change == "config":
        config.write_text(config.read_text() + "# changed\n")
    elif change == "evidence":
        state = StateStore(workspace).read_project_current("DEMO")
        from pathlib import Path
        Path(state.artifacts["qa"]).write_text("Changed evidence, do not reuse assessment.")
    elif change == "stale":
        request["expected_revision"] -= 1
    else:
        request["run_id"] = "another-run"
    with pytest.raises(ValueError):
        handle_goal_action(workspace, "DEMO", root, request)
    assert store.get_goal(goal["id"])["status"] != "active"


@pytest.mark.parametrize("kind", ["scope", "reference"])
def test_semantic_scope_or_citation_failure_is_never_retryable(setup, monkeypatch, kind):
    goal = recovery_goal(setup)
    calls = protocol_worker(monkeypatch, invalid_kind=kind)
    run_tick(setup, goal)
    blocked = run_tick(setup, goal)
    assert blocked["status"] == "blocked"
    assert not blocked["active_work"].get("assessment_failures")
    assert run_tick(setup, goal)["status"] == "blocked" and len(calls) == 1


def test_old_protocol_failure_cannot_reopen_later_safety_block(setup, monkeypatch):
    workspace, root, _, _, _ = setup
    goal = recovery_goal(setup)
    protocol_worker(monkeypatch)
    run_tick(setup, goal)
    run_tick(setup, goal)  # first malformed response, automatic retry still allowed
    protocol_worker(monkeypatch, invalid_kind='scope')
    blocked = run_tick(setup, goal)
    assert blocked['status'] == 'blocked'
    with pytest.raises(ValueError, match='recoverable'):
        handle_goal_action(workspace, 'DEMO', root, retry_request(blocked))


def test_all_attempts_are_bounded_and_hard_cap_cannot_be_approved(setup, monkeypatch):
    workspace, root, _, _, _ = setup
    goal = recovery_goal(setup, attempts=3)
    calls = protocol_worker(monkeypatch, invalid_count=99)
    run_tick(setup, goal)
    for _ in range(5):
        blocked = run_tick(setup, goal)
    assert blocked['status'] == 'blocked' and len(calls) == 3
    assert not blocked.get('assessment_retry_target')
    with pytest.raises(ValueError):
        handle_goal_action(workspace, 'DEMO', root, retry_request(blocked))


@pytest.mark.parametrize('outcome', ['unknown', 'forged_completed'])
def test_unknown_or_unproven_assessment_never_retries(setup, monkeypatch, outcome):
    from goodteam_os.goal_assessment import CompletedAssessmentProtocolError
    from goodteam_os.manager_execution import ManagerExecutionError
    goal = recovery_goal(setup, attempts=3)
    run_tick(setup, goal)
    def assessor(**_kwargs):
        if outcome == 'forged_completed':
            raise CompletedAssessmentProtocolError('nonexistent-job', 'invalid_json')
        raise ManagerExecutionError('Unknown worker execution outcome')
    blocked = run_tick(setup, goal, assessor=assessor)
    assert blocked['status'] == 'blocked'
    assert not blocked.get('assessment_retry_target')
    assert not blocked['active_work'].get('assessment_failures')
    assert run_tick(setup, goal)['status'] == 'blocked'


def test_restart_after_completed_response_before_failure_record_reuses_job(setup, monkeypatch):
    from goodteam_os.goals import GoalStore
    goal = recovery_goal(setup)
    calls = protocol_worker(monkeypatch)
    run_tick(setup, goal)
    record = GoalStore.record_assessment_failure
    def crash(*_args, **_kwargs):
        raise KeyboardInterrupt('simulated process loss before ledger receipt')
    monkeypatch.setattr(GoalStore, 'record_assessment_failure', crash)
    with pytest.raises(KeyboardInterrupt):
        run_tick(setup, goal)
    assert len(calls) == 1
    monkeypatch.setattr(GoalStore, 'record_assessment_failure', record)
    recovered = run_tick(setup, goal)
    assert recovered['status'] == 'active' and len(calls) == 1
    assert len(recovered['active_work']['assessment_failures']) == 1
    reviewed = run_tick(setup, goal)
    assert reviewed['work_units'][0]['status'] == 'done' and len(calls) == 2


def test_catalog_only_change_refuses_reassessment(setup, monkeypatch, tmp_path):
    workspace, root, _, _, create = setup
    catalog = tmp_path / 'models-custom.yaml'
    catalog.write_text('default: old-model\n')
    monkeypatch.setenv('GOODTEAM_MODEL_CONFIG', str(catalog))
    goal = create()
    calls = protocol_worker(monkeypatch)
    run_tick(setup, goal)
    blocked = run_tick(setup, goal)
    catalog.write_text('default: changed-model\n')
    with pytest.raises(ValueError, match='configuration or model catalog'):
        handle_goal_action(workspace, 'DEMO', root, retry_request(blocked))
    assert len(calls) == 1


def test_existing_attempt_with_unknown_runtime_status_is_not_replayed(setup, monkeypatch):
    from pathlib import Path
    from goodteam_os.goal_assessment import assessment_identity
    from goodteam_os.goals import GoalStore
    goal = recovery_goal(setup)
    calls = protocol_worker(monkeypatch)
    running = run_tick(setup, goal)
    record = GoalStore.record_assessment_failure
    monkeypatch.setattr(GoalStore, 'record_assessment_failure', lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        run_tick(setup, goal)
    monkeypatch.setattr(GoalStore, 'record_assessment_failure', record)
    worker_store = WorkerJobStore(setup[1])
    job = worker_store.find_job_by_idempotency_key(assessment_identity(goal, running['active_work'], 1))
    path = Path(worker_store.root) / job['id'] / 'job.json'
    job['status'] = 'running'  # owned fixture models an interrupted runtime claim
    path.write_text(json.dumps(job))
    status_path = path.with_name('status.json')
    status = json.loads(status_path.read_text())
    status['status'] = 'running'
    status_path.write_text(json.dumps(status))
    blocked = run_tick(setup, goal)
    assert blocked['status'] == 'blocked'
    assert not blocked.get('assessment_retry_target')
    assert not blocked['active_work'].get('assessment_failures')
    assert len(calls) == 1


def test_skill_bound_assessment_uses_canonical_identity_and_preserves_runtime_gate(skill_fixture):  # noqa: F811
    from goodteam_os.goal_assessment import bound_assessment_job, run_evidence, verify_completed_assessment
    from goodteam_os.goals import GoalStore
    ctx = skill_fixture
    bind(ctx, method(ctx), subject='manager:orchestrator')
    store = GoalStore(ctx.workspace, 'DEMO')
    goal = store.create_goal(objective='Assess isolated fixture', acceptance_criteria=['Feature works'],
        allowed_scope=['Fixture only'], max_work_units=1, max_assessment_attempts=3, confirmed=True,
        idempotency_key='skill-goal', authority_snapshot=authority_snapshot(ctx.workspace, 'DEMO', ctx.config, ctx.repo))
    tick_goal(ctx.workspace, 'DEMO', goal['id'], starter=finished_run)
    blocked = tick_goal(ctx.workspace, 'DEMO', goal['id'])
    work = blocked['active_work']
    evidence = run_evidence(ctx.workspace, StateStore(ctx.workspace).read_project_current('DEMO'))
    job = bound_assessment_job(ctx.repo, blocked, work, 1, evidence)
    assert job['id'].startswith('job-skill-')
    assert job['idempotency_key'].startswith('manager-job:DEMO:goal-assessment:')
    assert job['skill_error_code'] == 'skill_runtime_not_connected'
    assert blocked['status'] == 'blocked' and not blocked.get('assessment_retry_target')
    with pytest.raises(ValueError, match='completion and read-only'):
        verify_completed_assessment(ctx.repo, blocked, work, 1, job['id'], evidence)
    with pytest.raises(ValueError, match='immutable request'):
        bound_assessment_job(ctx.repo, blocked, work, 1, {**evidence, 'different-run': 'Invented evidence'})
