"""Model-free continuation contracts using real run facts and isolated workflow.

The synthetic assessor is deliberately separate from real model validation.
"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from goodteam_os.goal_assessment import run_evidence, validate_assessment
from goodteam_os.goal_continuation import authority_snapshot, handle_goal_action, tick_goal
from goodteam_os.goals import GoalStore
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.team_control_plane import ensure_team_state
from goodteam_os import web


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    (root / "README.md").write_text("# Isolated Goal fixture\n")
    projects = tmp_path / "projects"
    projects.mkdir()
    config = projects / "demo.local.yaml"
    config.write_text(f"key: DEMO\nname: Goal fixture\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\nstaging:\n  enabled: false\n")
    ensure_team_state(tmp_path, "DEMO", config)
    store = GoalStore(tmp_path, "DEMO")
    def create(limit=2):
        return store.create_goal(objective="Make the bounded fixture useful", acceptance_criteria=["Feature works", "Regression check exists"],
            allowed_scope=["Only the isolated fixture; no deployment or new dependencies"], max_work_units=limit,
            confirmed=True, idempotency_key="create", authority_snapshot=authority_snapshot(tmp_path, "DEMO", config, root))
    return tmp_path, root, config, store, create


def finished_run(workspace, **kwargs):
    """Offline executor writes actual output evidence for assigned work."""
    state = create_initial_state(kwargs["run_id"], "DEMO", kwargs["idea"])
    state.current_node = state.next_node = "complete"
    path = workspace / "artifacts" / "runs" / state.run_id / "qa" / "result.md"
    path.parent.mkdir(parents=True)
    # The second result depends on the task derived from first evidence. It is
    # not a second prequeued task or a fabricated Goal completion event.
    content = "Feature works. Regression check missing."
    if "Prior evidence references" in kwargs["idea"]:
        assert "2. Regression check exists" in kwargs["idea"]
        content = "Regression check exists and passes."
    path.write_text(content)
    state.artifacts = {"qa": str(path)}
    StateStore(workspace).write_project_current(state)
    StateStore(workspace).write_project_checkpoint(state)


def assessment(**kwargs):
    ref, text = next(iter(kwargs["evidence"].items()))
    checks = [{"criterion": index, "status": "missing" if index == 1 and "missing" in text else "met",
               "evidence_ref": ref, "quote": text} for index in range(len(kwargs["goal"]["acceptance_criteria"]))]
    return validate_assessment({"requires_new_scope": False, "checks": checks}, kwargs["goal"], kwargs["evidence"])


def test_result_omission_automatically_derives_next_run_and_cards(setup):
    workspace, root, _, store, create = setup
    goal = create()
    started = []
    def executor(**kwargs):
        started.append(kwargs["run_id"])
        finished_run(**kwargs)
    def tick():
        return tick_goal(workspace, "DEMO", goal["id"], starter=executor, assessor=assessment)
    tick()  # execute first bounded unit
    first = tick()  # observe actual result -> derive only omitted criterion
    assert len(first["work_units"]) == 2
    assert first["work_units"][1]["criterion_indices"] == [1]
    assert first["work_units"][1]["dependencies"] == ["w1"]
    assert first["work_units"][1]["source_evidence_refs"] == first["work_units"][0]["evidence_refs"]
    assert "missing" in first["work_units"][0]["gaps"][0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: tick(), range(4)))
    assert store.get_goal(goal["id"])["status"] == "complete"
    assert len(set(started)) == len(started) == 2
    assert tick()["status"] == "complete"
    cards = LocalTaskStore(root, "DEMO").list_tasks()
    assert len(cards) == 2 and all(card["status"] == "done" and card["artifact_refs"] for card in cards)


def test_budget_gate_approval_continues_same_goal_without_start_command(setup):
    workspace, root, _, store, create = setup
    goal = create(1)
    def tick():
        return tick_goal(workspace, "DEMO", goal["id"], starter=finished_run, assessor=assessment)
    tick()
    waiting = tick()
    assert waiting["status"] == "awaiting_approval"
    assert len(waiting["work_units"]) == 1
    assert tick()["revision"] == waiting["revision"]
    request = dict(action="decide_goal_gate", goal_id=goal["id"], gate_id=waiting["gate"]["id"],
                   expected_revision=waiting["revision"], decision="approve", idempotency_key="approve")
    resumed = handle_goal_action(workspace, "DEMO", root, request)["goal"]
    assert resumed["id"] == goal["id"]
    assert handle_goal_action(workspace, "DEMO", root, request)["goal"] == resumed
    tick()
    assert tick()["status"] == "complete"


def test_unknown_claim_after_restart_blocks_without_reexecution(setup):
    workspace, _, _, store, create = setup
    goal = create()
    store.next_work(goal["id"], expected_revision=goal["revision"], idempotency_key="precrash")
    result = tick_goal(workspace, "DEMO", goal["id"], starter=lambda **kw: pytest.fail("duplicate dispatch"))
    assert result["status"] == "blocked" and "ownership" in result["stop_reason"]


def test_lost_dispatch_response_reconciles_exact_run_after_restart(setup):
    workspace, _, _, store, create = setup
    goal = create()
    def interrupted(**kwargs):
        finished_run(**kwargs)
        raise RuntimeError("lost response")
    result = tick_goal(workspace, "DEMO", goal["id"], starter=interrupted)
    assert result["active_work"]["status"] == "dispatching"
    result = tick_goal(workspace, "DEMO", goal["id"], starter=lambda **kw: pytest.fail("duplicate"), assessor=assessment)
    assert result["work_units"][0]["status"] == "done"
    assert len(result["work_units"]) == 2


def test_changed_runtime_scope_or_stop_prevents_dispatch(setup):
    workspace, root, config, store, create = setup
    goal = create()
    config.write_text(config.read_text() + "# changed\n")
    result = tick_goal(workspace, "DEMO", goal["id"], starter=lambda **kw: pytest.fail("changed authority"))
    assert result["status"] == "blocked"
    stopped = handle_goal_action(workspace, "DEMO", root, dict(action="stop_goal", goal_id=goal["id"],
        expected_revision=result["revision"], idempotency_key="stop"))["goal"]
    assert stopped["status"] == "stopped"
    assert tick_goal(workspace, "DEMO", goal["id"], starter=lambda **kw: pytest.fail("stopped"))["status"] == "stopped"


def test_current_run_refs_and_literal_quotes_required(tmp_path):
    state = create_initial_state("bound", "DEMO", "task")
    state.current_node = state.next_node = "complete"
    foreign = tmp_path / "artifacts/runs/other/result.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("Forged evidence")
    state.artifacts = {"qa": str(foreign)}
    with pytest.raises(ValueError, match="no readable"):
        run_evidence(tmp_path, state)
    goal = {"acceptance_criteria": ["criterion"]}
    result = {"requires_new_scope": False, "checks": [{"criterion": 0, "status": "met", "evidence_ref": "current", "quote": "Not the result"}]}
    with pytest.raises(ValueError, match="citation"):
        validate_assessment(result, goal, {"current": "Actual evidence"})
    result["requires_new_scope"] = True
    with pytest.raises(ValueError, match="beyond"):
        validate_assessment(result, goal, {})


def test_goal_uses_real_workflow_and_retains_existing_gates(setup):
    workspace, _, _, store, create = setup
    goal = create()
    result = tick_goal(workspace, "DEMO", goal["id"])
    assert result["status"] == "active", result
    state = StateStore(workspace).read_project_current("DEMO")
    assert state.next_node == "await_takeover_approval"
    # Repeated service ticks cannot approve a Gate or create another run.
    for _ in range(2):
        tick_goal(workspace, "DEMO", goal["id"], assessor=lambda **kw: pytest.fail("premature assessment"))
    assert StateStore(workspace).read_project_current("DEMO").next_node == "await_takeover_approval"
    seen = []
    for _ in range(10):
        state = StateStore(workspace).read_project_current("DEMO")
        if state.current_node == "complete":
            break
        gate = state.next_node.removeprefix("await_").removesuffix("_approval")
        review = next(item for item in web.build_approval_reviews(state, workspace=workspace) if item["approval"] == gate)
        seen.append(gate)
        web.decide_approval(workspace=workspace, project_key="DEMO", approval=gate, decision="approved",
            expected_run_id=state.run_id, expected_target_type="review", expected_target_id=gate,
            expected_artifact_key=review["artifact"], expected_artifact_version=review["artifact_version"],
            note="Offline synthetic workflow verification only.")
    state = StateStore(workspace).read_project_current("DEMO")
    assert state.current_node == "complete", state
    assert {"takeover", "prd", "rfc", "security", "release"} <= set(seen)
    assert run_evidence(workspace, state)
    assert store.get_goal(goal["id"])["work_units"][0]["run_id"] == state.run_id


def test_stopped_goal_observes_existing_run_completion_without_new_work(setup):
    workspace, root, _, store, create = setup
    goal = create()
    running = tick_goal(workspace, "DEMO", goal["id"])
    handle_goal_action(workspace, "DEMO", root, dict(action="stop_goal", goal_id=goal["id"],
        expected_revision=running["revision"], idempotency_key="stop-existing"))
    work = running["active_work"]
    # A synthetic executor finishes its already dispatched run independently.
    finished_run(workspace, run_id=work["run_id"], idea=work["task"])
    result = tick_goal(workspace, "DEMO", goal["id"], starter=lambda **kw: pytest.fail("stopped goal dispatched"))
    assert result["status"] == "stopped"
    assert result["active_work"]["status"] == "running"  # durable submission receipt
    assert result["active_work"]["observed_execution_status"] == "done"  # StateStore fact
    cards = [card for card in LocalTaskStore(root, "DEMO").list_tasks() if card.get("goal_id")]
    assert len(cards) == 1 and cards[0]["status"] == "done"


def test_ordinary_and_goal_starts_share_existing_workflow_mutex(setup, monkeypatch):
    from threading import Event
    from goodteam_os.workflow_mutations import WorkflowBusyError
    workspace, _, config, store, create = setup
    goal = create()
    entered, release = Event(), Event()
    original = web.run_adoption_until_next_gate
    calls = []
    def held(**kwargs):
        calls.append(kwargs["state"].run_id)
        entered.set()
        assert release.wait(5)
        return original(**kwargs)
    monkeypatch.setattr(web, "run_adoption_until_next_gate", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        automatic = pool.submit(tick_goal, workspace, "DEMO", goal["id"])
        assert entered.wait(5)
        try:
            with pytest.raises(WorkflowBusyError):
                web.start_workflow(workspace=workspace, project_key="DEMO", idea="Ordinary concurrent task",
                    model_config=config, model_profile="balanced", project_config=config, source_type="existing_project")
        finally:
            release.set()
        assert automatic.result(timeout=5)["status"] == "active"
    assert len(calls) == 1
    assert StateStore(workspace).read_project_current("DEMO").run_id == store.get_goal(goal["id"])["active_work"]["run_id"]


def test_actual_worker_result_drives_manager_and_two_real_workflow_runs(setup, monkeypatch):
    """All production orchestration; only executable model output is synthetic."""
    import importlib.util
    import sys
    from pathlib import Path
    from goodteam_os.workers import WorkerJobStore
    fixture = Path(__file__).parent / "fixtures" / "goal_continuation_server.py"
    monkeypatch.syspath_prepend(str(fixture.parent))
    original = WorkerJobStore._complete_fake
    spec = importlib.util.spec_from_file_location("goal_local_fixture", fixture)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        # Fixture installation is test-only; restore via monkeypatch finalizer.
        WorkerJobStore._complete_fake = original
        monkeypatch.setattr(WorkerJobStore, "_complete_fake", module.synthetic_result)
        workspace, root, _, store, create = setup
        goal = create()
        run_ids = []
        for round_number in range(2):
            started = tick_goal(workspace, "DEMO", goal["id"])
            assert started["status"] == "active", started
            run_ids.append(started["active_work"]["run_id"])
            for _ in range(10):
                state = StateStore(workspace).read_project_current("DEMO")
                if state.current_node == "complete":
                    break
                gate = state.next_node.removeprefix("await_").removesuffix("_approval")
                review = next(item for item in web.build_approval_reviews(state, workspace=workspace) if item["approval"] == gate)
                web.decide_approval(workspace=workspace, project_key="DEMO", approval=gate, decision="approved",
                    expected_run_id=state.run_id, expected_target_type="review", expected_target_id=gate,
                    expected_artifact_key=review["artifact"], expected_artifact_version=review["artifact_version"],
                    note="Offline synthetic workflow verification only.")
            reviewed = tick_goal(workspace, "DEMO", goal["id"])
            if round_number == 0:
                assert reviewed["status"] == "active", reviewed
                assert reviewed["work_units"][1]["criterion_indices"] == [1]
                assert "REGRESSION_MISSING" in reviewed["work_units"][0]["gaps"][0]
                assert not (root / "test_feature.py").exists()
            else:
                assert reviewed["status"] == "complete", reviewed
        assert len(set(run_ids)) == 2
        assert (root / "test_feature.py").exists()
        jobs = WorkerJobStore(root).list_jobs()
        assert all(job["adapter"] == "fake" for job in jobs)
        assert len([job for job in jobs if job.get("phase") == "goal_assessment"]) == 2
        assert all(work["assessment_checks"] for work in store.get_goal(goal["id"])["work_units"])
    finally:
        sys.modules.pop("goal_local_fixture", None)


@pytest.mark.parametrize("source", ["workflow", "manager"])
def test_changed_effective_model_catalog_cannot_expand_confirmed_routing(setup, monkeypatch, source):
    from pathlib import Path
    workspace, _, _, _, create = setup
    catalog_text = (Path(__file__).resolve().parents[1] / "models.yaml").read_text()
    if source == "workflow":
        # CWD models.yaml wins in the workflow resolver even with no project
        # or Team model change. Manager still uses its repository fallback.
        monkeypatch.chdir(workspace)
        catalog = workspace / "models.yaml"
    else:
        catalog = workspace / "manager-models.yaml"
        monkeypatch.setenv("GOODTEAM_MODEL_CONFIG", str(catalog))
    catalog.write_text(catalog_text)
    goal = create()
    catalog.write_text(catalog_text + "\n# new effective provider catalog\n")
    result = tick_goal(workspace, "DEMO", goal["id"], starter=lambda **kw: pytest.fail("changed model routing"))
    assert result["status"] == "blocked"
    assert "configuration changed" in result["stop_reason"]
