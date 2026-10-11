from __future__ import annotations

from typing import Any

from goodteam_os.execution.candidate_validation import candidate_evidence
from goodteam_os.execution.evidence_manifest_extractors import (
    ci_observations,
    ci_summary,
    json_ready,
    merge_summary,
    review_observations,
    review_summary,
)
from goodteam_os.execution.execution_replay_graph import execution_replay_graph_manifest
from goodteam_os.execution.github_broker_status import github_broker_status_manifest
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.memory_provenance import build_memory_provenance_projection
from goodteam_os.execution.models import ExecutionRunStatus, LedgerEvent
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
    events = replay["events"]
    source = _source_manifest(task, events)
    pr_number = _pr_number_from_replay(run, events)
    pr_repo = _repo_from_events(events) or source["repo"]
    final_status = _manifest_final_status(run, pr_number, events)
    incomplete_reason = _incomplete_reason(run, pr_number, events)
    memory_provenance = build_memory_provenance_projection(
        memory_store,
        events,
        execution_id,
        final_status=final_status,
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
        "task_id": _task_id_from_replay(execution_id, task, run, events),
        "source": source,
        "source_issue": source["issue_ref"],
        "github_ref": source["github_ref"],
        "task_contract": contract_manifest,
        "task_input": input_evidence,
        "candidate_validation": validation,
        "runtime_policy": runtime_policy_manifest(events),
        "github_broker_status": github_broker_status_manifest(run, events),
        "pr": _pr_manifest(pr_repo, pr_number, run, events),
        "ledger_events": [json_ready(event) for event in events],
        "ci_observations": ci_observations(events),
        "ci_summary": ci_summary(events),
        "review_observations": review_observations(events),
        "review_summary": review_summary(events),
        "merge_summary": merge_summary(events),
        "memory_provenance_refs": memory_provenance["refs"],
        "memory_provenance_integrity": memory_provenance["integrity"],
        "final_status": final_status,
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


def _source_manifest(task: Any, events: list[LedgerEvent]) -> dict[str, Any]:
    repo = task.repo if task is not None else _repo_from_events(events)
    issue_number = task.issue_number if task is not None else _issue_number_from_events(events)
    issue_ref = f"{repo}#{issue_number}" if repo and issue_number is not None else ""
    source_type = task.source_type if task is not None else ("github_issue" if issue_ref else "")
    return {
        "type": source_type,
        "repo": repo,
        "issue_number": issue_number,
        "issue_ref": issue_ref,
        "github_ref": f"github:{issue_ref}" if issue_ref else "",
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
        "ref": _pr_ref(repo, pr_number),
        "branch_name": _branch_name(run, events),
        "commit_sha": _pr_commit_sha(events),
    }


def _pr_ref(repo: str, pr_number: int | None) -> str:
    return f"{repo}#{pr_number}" if repo and pr_number is not None else ""


def _repo_from_events(events: list[LedgerEvent]) -> str:
    for event in events:
        repo = event.payload.get("repo")
        if isinstance(repo, str) and repo.strip():
            return repo.strip()
    return ""


def _issue_number_from_events(events: list[LedgerEvent]) -> int | None:
    return _int_or_none(_first_payload_value(events, "issue_number"))


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _pr_number_from_replay(run: Any, events: list[LedgerEvent]) -> int | None:
    if run is not None and run.pr_number is not None:
        return int(run.pr_number)
    for event in reversed(events):
        value = _int_or_none(event.payload.get("pr_number"))
        if value is not None:
            return value
    return None


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


def _first_payload_value(events: list[LedgerEvent], key: str) -> object | None:
    for event in events:
        if key in event.payload:
            return event.payload[key]
    return None


def _manifest_final_status(run: Any, pr_number: int | None, events: list[LedgerEvent]) -> str:
    if run is None:
        return "incomplete"
    return _final_status(run, pr_number, events)


def _final_status(run: Any, pr_number: int | None, events: list[LedgerEvent]) -> str:
    if run.status is ExecutionRunStatus.FAILED:
        return "failed"
    if pr_number is None:
        return "pending"
    ci = ci_summary(events)
    if ci.get("state") == "failure":
        return "failed"
    if run.status is ExecutionRunStatus.SUCCEEDED and merge_summary(events)["merged"]:
        return "completed"
    return "pending"


def _incomplete_reason(run: Any, pr_number: int | None, events: list[LedgerEvent]) -> str:
    if run is None:
        return "execution_run_missing"
    if run.status is ExecutionRunStatus.FAILED:
        return _failure_reason(events)
    ci = ci_summary(events)
    if ci.get("state") == "failure":
        return "ci_failed"
    if pr_number is None:
        return "pr_missing"
    if ci.get("state") == "pending":
        return "ci_pending"
    if ci.get("state") == "unavailable":
        return "ci_unavailable"
    if not merge_summary(events)["merged"]:
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
