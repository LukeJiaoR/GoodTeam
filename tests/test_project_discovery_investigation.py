"""One contract: explicit investigation authority maps to a read-only Worker.

This crosses the production task ledger; Manager input and delivery are fixtures,
not a real model or dispatch acceptance test.
"""
from copy import deepcopy

import pytest

from goodteam_os.project_iteration_planner import (
    _MANAGER_DISCOVERY_TASK, _MANAGER_INVESTIGATION_DISCOVERY_TASK, _discovery_prompt,
)
from goodteam_os.project_outcome_discovery import (
    _creation_ref, _current, _discovered_task_intent, authorization_digest,
    normalize_discovery, validate_discovered_task,
)
from goodteam_os.tasks import LocalTaskStore


def test_investigation_opt_in_keeps_legacy_authority_and_host_execution_profile(tmp_path):
    legacy = {"objective": "Check the delivered import flow before choosing its next improvement.",
              "allowed_write_domains": ["src/import", "tests/import"],
              "max_new_tasks": 2, "max_planning_calls": 3}
    assert normalize_discovery(legacy) == legacy
    assert "allow_investigation" not in normalize_discovery(legacy)
    grant = {"id": "grant-a", "seed_task_ids": ["APP-1"], "seed_task_intents": {"APP-1": {}},
             "approved_backlog_version": "sha256:" + "b" * 64, "discovery": legacy,
             "authority": {"project": "APP"}, "max_batches": 3, "max_manager_calls": 3,
             "status": "active"}
    grant["authorization_digest"] = authorization_digest(grant)
    packet = {"discovery": legacy, "packet_digest": "sha256:" + "a" * 64,
              "authorized_task_ids": ["APP-1"], "authorized_done_task_ids": ["APP-1"],
              "allowed_source_refs": ["receipt-1"],
              "verified_outcome": {"delivery": {"receipt_id": "receipt-1"}}}
    task = {"title": "Check the delivered import assumptions",
            "description": "Determine which documented import assumptions still need evidence.",
            "acceptance_criteria": ["Return source-backed findings and any unresolved facts."],
            "write_domains": [], "dependencies": ["APP-1"], "source_refs": ["receipt-1"],
            "task_type": "investigation"}
    proposed = {"new_task": task, "rationale": "The delivered flow needs a factual check before selecting another change."}
    assert _discovery_prompt(packet) == _MANAGER_DISCOVERY_TASK
    with pytest.raises(ValueError):
        validate_discovered_task(packet, proposed)

    enabled = normalize_discovery({**legacy, "allow_investigation": True})
    assert authorization_digest({**grant, "discovery": enabled}) != grant["authorization_digest"]
    with pytest.raises(ValueError):
        _current({"project_continuation": {**grant, "discovery": enabled}}, grant)
    packet = {**packet, "discovery": enabled,
              "investigation_capability": {"role": "research", "execution_policy": "read_only", "dispatchable": True}}
    assert _discovery_prompt(packet) == _MANAGER_INVESTIGATION_DISCOVERY_TASK
    selected = validate_discovered_task(packet, proposed)
    role_packet = {**packet, "role_capabilities": [{"role": "research", "dispatchable": True}]}
    assert validate_discovered_task(role_packet, proposed) == selected
    assert "Role dispatch:" in _discovery_prompt(role_packet)
    named = {**proposed, "new_task": {**task, "worker_id": "research-a"}}
    with pytest.raises(ValueError, match="bounded task"):
        validate_discovered_task(role_packet, named)
    frozen_member_packet = {**packet, "worker_capabilities": [
        {"role": "research", "worker_id": "research-a", "dispatchable": True}]}
    assert "Worker selection extension:" in _discovery_prompt(frozen_member_packet)
    assert validate_discovered_task(frozen_member_packet, named)["new_task"]["worker_id"] == "research-a"
    creation_ref = _creation_ref(grant, packet)
    intent = _discovered_task_intent(packet, selected, creation_ref)
    assert intent["role"] == "research" and intent["execution_policy"] == "read_only"
    assert intent["write_domains"] == [] and "task_type" not in intent
    assert intent["external_refs"] == [creation_ref, "receipt-1"]
    assert _discovered_task_intent(packet, deepcopy(selected), creation_ref) == intent
    assert grant["discovery"] == legacy  # No implicit migration of old grants.

    ledger = LocalTaskStore(tmp_path, "APP")
    seed = ledger.create_task(title="Delivered import seed", description="The prior delivery is a fixture.",
                             role="implementation", write_domains=["src/import"])
    assert seed["id"] == "APP-1"
    ledger.update_task(seed["id"], status="done", delivery_verified=True)
    research = ledger.create_task_once(creation_ref=creation_ref, intent=intent)
    assert research["role"] == "research" and research["execution_policy"] == "read_only"
    assert research["write_domains"] == []
    ledger.update_task(research["id"], status="review")
    before_replay = ledger.path.read_bytes()
    assert ledger.create_task_once(creation_ref=creation_ref, intent=deepcopy(intent))["status"] == "review"
    assert ledger.path.read_bytes() == before_replay and len(ledger.list_tasks()) == 2
    with pytest.raises(ValueError, match="fixed project discovery"):
        ledger.create_task_once(creation_ref=creation_ref,
            intent={**intent, "execution_policy": "workspace_write", "write_domains": ["src/import"]})
    with pytest.raises(ValueError, match="fixed project discovery"):
        ledger.create_task_once(creation_ref=creation_ref, intent={**intent, "write_domains": ["src/import"]})
    assert ledger.path.read_bytes() == before_replay

    invalid = [
        {**proposed, "new_task": {**task, "write_domains": ["src/import"]}},
        {**proposed, "new_task": {**task, "role": "implementation"}},
        {**proposed, "new_task": {**task, "execution_policy": "workspace_write"}},
        {**proposed, "new_task": {**task, "task_type": "deploy"}},
        {**proposed, "new_task": {**task, "source_refs": ["invented-receipt"]}},
        {**proposed, "new_task": {**task, "dependencies": ["unapproved-task"]}},
    ]
    for response in invalid:
        with pytest.raises(ValueError):
            _discovered_task_intent(packet, response, creation_ref)
    for value in ("true", 1, None, []):
        with pytest.raises(ValueError, match="boolean"):
            normalize_discovery({**legacy, "allow_investigation": value})
    for capability in ({}, {"dispatchable": False}, {"dispatchable": "true"}):
        with pytest.raises(ValueError, match="available Researcher"):
            validate_discovered_task({**packet, "investigation_capability": capability}, proposed)
    with pytest.raises(ValueError):
        validate_discovered_task({**packet, "discovery": {**legacy, "allow_investigation": False}}, proposed)
    assert _discovery_prompt({"discovery": {**legacy, "allow_investigation": False}}) == _MANAGER_DISCOVERY_TASK

    writing = {key: value for key, value in task.items() if key != "task_type"}
    writing["write_domains"] = ["src/import"]
    for context in (packet, {**packet, "discovery": legacy}):
        accepted = _discovered_task_intent(context, {**proposed, "new_task": writing}, creation_ref)
        assert accepted["role"] == "implementation" and accepted["execution_policy"] == "workspace_write"
    writing_ref = creation_ref + ":implementation"
    writing_intent = _discovered_task_intent(packet, {**proposed, "new_task": writing}, writing_ref)
    assert ledger.create_task_once(creation_ref=writing_ref, intent=writing_intent)["role"] == "implementation"
    stop = {"new_task": None, "rationale": "No further authorized work is justified by the supplied evidence."}
    assert validate_discovered_task(packet, stop) == stop
    with pytest.raises(ValueError, match="No follow-up"):
        _discovered_task_intent(packet, stop, creation_ref)
    with pytest.raises(ValueError, match="stopped"):
        _current({"project_continuation": {**grant, "status": "stopped"}}, grant)
