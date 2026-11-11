"""Planner uses existing discovery/ledger contracts without widening old grants."""
from copy import deepcopy
import pytest
from goodteam_os.project_outcome_discovery import normalize_discovery, validate_discovered_task, _discovered_task_intent, authorization_digest, _current
from goodteam_os.project_iteration_planner import _discovery_prompt
from goodteam_os.tasks import LocalTaskStore


def test_explicit_planning_opt_in_preserves_old_grants_and_real_ledger_identity(tmp_path):
    legacy = dict(objective="Break down the confirmed architecture into bounded implementation steps.", allowed_write_domains=["src"], max_new_tasks=2, max_planning_calls=3)
    assert normalize_discovery(legacy) == legacy
    enabled = normalize_discovery({**legacy, "allow_planning": True})
    grant = dict(id="grant", seed_task_ids=[], seed_task_intents={}, approved_backlog_version="v1", discovery=legacy, authority={}, max_batches=3,max_manager_calls=3,status="active")
    grant["authorization_digest"] = authorization_digest(grant)
    with pytest.raises(ValueError): _current({"project_continuation": {**grant,"discovery":enabled}},grant)
    packet=dict(discovery=enabled,planning_capability={"dispatchable":True},role_capabilities=[{"role":"planner","dispatchable":True}], authorized_done_task_ids=[],authorized_task_ids=[],allowed_source_refs=["seed"],verified_outcome={"delivery":{"receipt_id":"seed"}})
    proposed=dict(new_task=dict(title="Plan the agreed feature",description="Decompose the agreed feature; preserve constraints and unresolved questions.",acceptance_criteria=["Return bounded steps, dependencies and uncertainty."],write_domains=[],dependencies=[],source_refs=["seed"],task_type="planning"),rationale="A bounded decomposition is needed before choosing the implementation increment.")
    selected=validate_discovered_task(packet,proposed)
    intent=_discovered_task_intent(packet,selected,"project-discovery:planner")
    assert intent["role"]=="planner" and intent["execution_policy"]=="read_only" and intent["write_domains"]==[]
    ledger=LocalTaskStore(tmp_path,"APP")
    task=ledger.create_task_once(creation_ref="project-discovery:planner",intent=intent)
    ledger.update_task(task["id"],status="review")
    assert ledger.create_task_once(creation_ref="project-discovery:planner",intent=deepcopy(intent))["status"]=="review"
    assert len(ledger.list_tasks())==1
    assert "Planning extension" in _discovery_prompt(packet)
    assert "Planning extension" not in _discovery_prompt({"discovery":legacy})
    for denied in (legacy,{**legacy,"allow_investigation":True},{**legacy,"allow_planning":False}):
        with pytest.raises(ValueError): validate_discovered_task({**packet,"discovery":denied},proposed)
    for wrong in ({"dispatchable":False},{}, {"dispatchable":"true"}):
        with pytest.raises(ValueError): validate_discovered_task({**packet,"planning_capability":wrong},proposed)
    for key,val in (("write_domains",["src"]),("task_type","investigation"),("role","planner")):
        with pytest.raises(ValueError): validate_discovered_task(packet,{**proposed,"new_task":{**proposed["new_task"],key:val}})
    for bad in (1,"true",None):
        with pytest.raises(ValueError):normalize_discovery({**legacy,"allow_planning":bad})


def test_existing_authorization_api_persists_explicit_planner_scope_without_starting_runtime(tmp_path):
    from goodteam_os import web
    from goodteam_os.project_iteration import project_iteration_backlog
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore
    from test_dispatch_isolation import repository
    source=repository(tmp_path/"source")
    config=tmp_path/"projects/demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nrepos:\n  root: {source}\nworker_backend:\n  mode: fake\n")
    ledger=LocalTaskStore(source,"DEMO")
    seed=ledger.create_task(title="Deliver the approved seed",description="Bounded original implementation",role="implementation",write_domains=["src"])
    request=dict(action="start_project_continuation",task_ids=[seed["id"]],max_batches=3,confirmed=True,billing_acknowledged=True,idempotency_key="planner-opt-in",expected_backlog_version=project_iteration_backlog(source,"DEMO")["version"],discovery=dict(objective="Plan the follow-up against the accepted seed and latest Context.",allowed_write_domains=["src"],max_new_tasks=2,max_planning_calls=3,allow_planning=True))
    code,result=web.handle_project_api_post(tmp_path,"/api/projects/DEMO/task-assistant/actions",request)
    assert code==200,result
    saved=TaskAssistantSessionStore(tmp_path,"DEMO").read()["project_continuation"]
    assert saved["discovery"]["allow_planning"] is True and "allow_investigation" not in saved["discovery"]
    assert saved["manager_calls"]==0 and saved["batches_started"]==0 and saved["pending_batch"] is None
    code,replayed=web.handle_project_api_post(tmp_path,"/api/projects/DEMO/task-assistant/actions",request)
    assert code==200 and replayed["project_continuation"]==saved
    with pytest.raises(web.ProjectApiError, match="different scope"):
        web.handle_project_api_post(tmp_path,"/api/projects/DEMO/task-assistant/actions",{**request,"discovery":{**request["discovery"],"allow_investigation":True}})
