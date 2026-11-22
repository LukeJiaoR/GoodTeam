"""[INPUT]: 执行账本、角色记忆和共享证据规则。
[OUTPUT]: build_evidence_manifest 生成执行证据清单。
[POS]: 汇总执行回放、候选检查和记忆归档链接供只读查询。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""

from __future__ import annotations

from typing import Any

from goodteam_os.execution.candidate_validation import candidate_evidence
from goodteam_os.execution.correction_loop import correction_evidence
from goodteam_os.execution.evidence_manifest_extractors import (
    ci_observations,
    ci_summary,
    final_status,
    json_ready,
    merge_summary,
    pr_identity,
    pr_ref,
    review_observations,
    review_summary,
    source_events,
    source_manifest,
)
from goodteam_os.execution.execution_replay_graph import execution_replay_graph_manifest
from goodteam_os.execution.github_broker_status import github_broker_status_manifest
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.memory_provenance import build_memory_provenance_projection
from goodteam_os.execution.models import ExecutionRun, ExecutionRunStatus, LedgerEvent
from goodteam_os.execution.runtime_policy import runtime_policy_manifest
from goodteam_os.execution.task_input_evidence import task_input_evidence
from goodteam_os.memory import RoleMemoryStore


def build_evidence_manifest(
    ledger: ExecutionLedger,
    memory_store: RoleMemoryStore,
    execution_id: str,
) -> dict[str, Any]:
    replay = ledger.replay(execution_id)
    task = replay["task"]
    run = replay["run"]
    ledger_events = replay["events"]
    events = source_events(ledger_events)
    source = source_manifest(task, events)
    pr_repo, pr_number = pr_identity(run, events, fallback_repo=source["repo"])
    ci = ci_summary(events)
    merge = merge_summary(events)
    ci_state = ci.get("state")
    merged = merge["merged"]
    status = final_status(run, pr_number, ci_state=ci_state, merged=merged)
    incomplete_reason = _incomplete_reason(run, pr_number, events, ci_state=ci_state, merged=merged)
    memory_provenance = build_memory_provenance_projection(
        memory_store,
        ledger_events,
        execution_id,
        final_status=status,
        incomplete_reason=incomplete_reason,
    )
    input_evidence = task_input_evidence(ledger.project_root, execution_id,
                                        worker_job_id=str(run.worker_job_id) if run else "")
    validation = candidate_evidence(ledger.project_root, execution_id)
    checks = {item["index"]: item for item in validation.get("criteria", [])}
    for index, item in enumerate(input_evidence.get("acceptance_coverage", [])):
        item["check_status"] = checks.get(index, {}).get("check_status", "unknown")
        item["review_status"] = validation.get("review_status", "missing") if index in checks else "missing"
        if index in checks:
            item["evidence"] = [validation["receipt"]["receipt_id"]]
            item["reason"] = "semantic_acceptance_not_verified"
    contract_manifest = _task_contract_manifest(task, captured=input_evidence.get("contract"))
    if input_evidence["status"] == "unavailable":
        contract_manifest = {**_task_contract_manifest(None), "fallback_warning": {
            "code": input_evidence["reason"], "message": "The original captured task input is unavailable.",
        }}
    manifest = {
        "schema_version": "execution_evidence_manifest.v1",
        "execution_id": execution_id,
        "task_id": _task_id_from_replay(execution_id, task, run, ledger_events),
        "source": source,
        "source_issue": source["issue_ref"],
        "github_ref": source["github_ref"],
        "task_contract": contract_manifest,
        "task_input": input_evidence,
        "candidate_validation": validation,
        "correction_loop": correction_evidence(ledger.project_root, execution_id),
        "runtime_policy": runtime_policy_manifest(ledger_events),
        "github_broker_status": github_broker_status_manifest(run, ledger_events),
        "pr": _pr_manifest(pr_repo, pr_number, run, events),
        "ledger_events": [json_ready(event) for event in ledger_events],
        "ci_observations": ci_observations(events),
        "ci_summary": ci,
        "review_observations": review_observations(events),
        "review_summary": review_summary(events),
        "merge_summary": merge,
        "memory_provenance_refs": memory_provenance["refs"],
        "memory_provenance_integrity": memory_provenance["integrity"],
        "final_status": status,
        "incomplete_reason": incomplete_reason,
    }
    manifest["execution_replay_graph"] = execution_replay_graph_manifest(manifest)
    return json_ready(manifest)


def _task_contract_manifest(task: Any, *, captured: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = getattr(task, "task_contract", None)
    if contract is None and captured is None:
        return {
            "available": False,
            "schema_version": "",
            "summary": "",
            "expected_artifacts": [],
            "path_scope": {},
            "acceptance_criteria": [],
            "verification_expectations": [],
            "review_expectations": [],
            "fallback_warning": {
                "code": "task_contract_missing",
                "message": "Legacy execution has no persisted task_contract.v1 data.",
            },
        }
    payload = captured if captured is not None else json_ready(contract)
    return {
        "available": True,
        "schema_version": str(payload.get("schema_version", "")),
        "summary": str(payload.get("summary", "")),
        "expected_artifacts": list(payload.get("expected_artifacts", [])),
        "path_scope": dict(payload.get("path_scope", {})),
        "acceptance_criteria": list(payload.get("acceptance_criteria", [])),
        "verification_expectations": list(payload.get("verification_expectations", [])),
        "review_expectations": list(payload.get("review_expectations", [])),
        "fallback_warning": {},
    }


def _task_id_from_replay(
    execution_id: str,
    task: Any,
    run: Any,
    events: list[LedgerEvent],
) -> str:
    if run is not None:
        return str(run.task_id)
    if task is not None:
        return str(task.task_id)
    for event in events:
        if event.task_id:
            return str(event.task_id)
    return execution_id


def _pr_manifest(
    repo: str,
    pr_number: int | None,
    run: Any,
    events: list[LedgerEvent],
) -> dict[str, Any]:
    return {
        "repo": repo,
        "number": pr_number,
        "ref": pr_ref(repo, pr_number),
        "branch_name": _branch_name(run, events),
        "commit_sha": _pr_commit_sha(events),
    }


def _branch_name(run: Any, events: list[LedgerEvent]) -> str:
    if run is not None and str(run.branch_name).strip():
        return str(run.branch_name).strip()
    for event in reversed(events):
        value = event.payload.get("branch_name")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _pr_commit_sha(events: list[LedgerEvent]) -> str:
    for event in reversed(events):
        if event.payload.get("transition") != "pr_opened":
            continue
        value = event.payload.get("commit_sha")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _incomplete_reason(
    run: ExecutionRun | None,
    pr_number: int | None,
    events: list[LedgerEvent],
    *,
    ci_state: object,
    merged: bool,
) -> str:
    if run is None:
        return "execution_run_missing"
    if run.status is ExecutionRunStatus.FAILED:
        return _failure_reason(events)
    if ci_state == "failure":
        return "ci_failed"
    if pr_number is None:
        return "pr_missing"
    if ci_state == "pending":
        return "ci_pending"
    if ci_state == "unavailable":
        return "ci_unavailable"
    if not merged:
        return "merge_missing"
    return ""


def _failure_reason(events: list[LedgerEvent]) -> str:
    for event in reversed(events):
        if event.payload.get("transition") != "execution_failed":
            continue
        failure_class = str(event.payload.get("failure_class", "")).strip()
        if failure_class:
            return f"execution_failed:{failure_class}"
    return "execution_failed"
