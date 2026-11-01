"""Bind an existing project batch to the existing candidate quality services.

Only explicit local preparation selects the check command. HTTP actions can
advance that frozen input using separately composed runtime authorities. The
original task cards and dispatch reports remain the execution ledger.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from goodteam_os.workflow_mutations import workflow_guard
from goodteam_os.dispatch_workspace import _git, _git_identity, _read_git, dispatch_artifact_root
from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService
from goodteam_os.execution.candidate_probe import probe_candidate
from goodteam_os.execution.candidate_validation import CandidateValidationService, _bound, _read, _write, validation_contract
from goodteam_os.execution.models import ExecutionTask, TaskContract
from goodteam_os.execution.task_snapshot import TaskExecutionSnapshot, TaskInputError, canonical_bytes, digest, execution_key
from goodteam_os.execution.task_snapshot_store import store_for_project
from goodteam_os.goal_continuation import goal_guard
from goodteam_os.memory_storage import role_lock
from goodteam_os.project_iteration import normalize_write_domains
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.task_start import registered_project_scope
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


_SCHEMA = "project_batch_quality.v1"
_PROJECTION = ("required", "input_digest", "execution_id", "status", "reason", "next_action",
               "check_status", "review_status", "acceptance_status", "candidate_commit", "candidate_path",
               "correction_job_id", "reviewer_task", "snapshot_ref")


def _criteria(batch):
    rows = []
    for binding in sorted(batch["task_bindings"], key=lambda item: item["task_id"]):
        for ordinal, text in enumerate(binding["task_intent"].get("acceptance_criteria") or []):
            rows.append({"task_id": binding["task_id"], "criterion_index": ordinal, "index": len(rows), "text": text})
    return rows


def _batch(workspace, project_key, root, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"task-batch-[a-f0-9]{64}", run_id):
        raise TaskInputError("batch_quality_identity_invalid")
    batch = TaskAssistantSessionStore(workspace, project_key).read().get("project_task_batches", {}).get(run_id)
    if not batch or not batch.get("confirmed") or batch.get("project_root") != str(Path(root).resolve()):
        raise TaskInputError("batch_quality_identity_invalid")
    return batch


def _input(workspace, project_key, root, batch):
    from goodteam_os.project_iteration_execution import TASK_INTENT_FIELDS, iteration_batch_report

    report = iteration_batch_report(workspace=workspace, project_key=project_key, root=root, run_id=batch["run_id"])
    integration, jobs = report.get("integration", {}), report.get("jobs", [])
    if (report.get("all_ok") is not True or integration.get("status") != "succeeded"
            or not jobs or len(jobs) != len(batch["task_ids"])
            or {job.get("project_task_id") for job in jobs} != set(batch["task_ids"])
            or any(job.get("outcome") != "ok" or job.get("attribution_status") != "captured" for job in jobs)):
        raise TaskInputError("batch_quality_candidate_unavailable")
    known = {task["id"]: task for task in LocalTaskStore(root, project_key).list_tasks()}
    for binding in batch["task_bindings"]:
        task = known.get(binding["task_id"], {})
        if (task.get("run_id") != batch["run_id"]
                or {key: task.get(key) for key in TASK_INTENT_FIELDS} != binding["task_intent"]):
            raise TaskInputError("batch_quality_task_changed")
    candidate = Path(str(integration.get("candidate_path", ""))).absolute()
    if (candidate.resolve() != candidate or candidate.name != "candidate"
            or candidate.parent.parent != dispatch_artifact_root(root, report["run_id"])
            or json.loads((candidate.parent / "integration.json").read_text()) != integration):
        raise TaskInputError("batch_quality_source_changed")
    probe_candidate(candidate, integration["candidate_commit"])
    source = {"integration": integration, "git_identity": _git_identity(candidate)}
    selected = {"batch_run_id": batch["run_id"], "project_key": project_key,
                "workspace": str(Path(workspace).resolve()), "project_root": str(Path(root).resolve()),
                "proposal_ids": sorted(batch["proposal_ids"]), "expected_revisions": batch["expected_revisions"],
                "task_bindings": sorted(batch["task_bindings"], key=lambda item: item["task_id"]),
                "results": sorted(({"task_id": job["project_task_id"],
                    "dispatch_run_id": job.get("dispatch_run_id", report["run_id"]), "job_id": job["job_id"],
                    "result_commit": job["result_commit"]} for job in jobs), key=lambda item: item["task_id"]),
                "baseline_commit": integration["baseline_commit"], "candidate_commit": integration["candidate_commit"]}
    return selected, source


def _requirement(store, run_id):
    if store is None:
        return None
    from goodteam_os.skills.filesystem import directory

    parent = store.root / execution_key(run_id)
    entered = False
    try:
        with directory(parent):
            entered = True
            value = _read(parent / "batch-quality-required.json")
    except SkillError as exc:
        if not entered and isinstance(exc.__cause__, FileNotFoundError):
            # A project store can exist before this batch has any quality
            # configuration. Missing parent is distinct from corrupt evidence.
            return None
        raise TaskInputError("batch_quality_binding_invalid") from exc
    if value is not None and (value != _bound(value) or value.get("schema_version") != _SCHEMA
                              or value.get("batch_run_id") != run_id):
        raise TaskInputError("batch_quality_binding_invalid")
    return value


def batch_quality_binding(root, snapshot):
    """Read the protected source/configuration; public session fields grant nothing."""
    store = store_for_project(Path(root), existing_only=True)
    if store is None or store.accept(snapshot).task.source_type != "project_task_batch":
        raise TaskInputError("batch_quality_input_missing")
    value = _read(store.root / execution_key(snapshot.execution_id) / "batch-quality-source.json")
    source = snapshot.task.task_contract.source_intent
    required = _requirement(store, source["input"]["batch_run_id"])
    if (value is None or value != _bound(value) or value.get("schema_version") != _SCHEMA
            or value.get("snapshot") != snapshot.reference or value.get("input_digest") != source["input_digest"]
            or required is None or required.get("snapshot") != snapshot.reference
            or required.get("configuration_digest") != digest(canonical_bytes(value.get("configuration")))):
        raise TaskInputError("batch_quality_binding_invalid")
    return value


def batch_quality_runtime(root, snapshot):
    binding = batch_quality_binding(root, snapshot)
    runtime = binding["configuration"]["correction_runtime"]
    if (runtime.get("adapter") != snapshot.to_dict()["adapter"] or runtime.get("role") != snapshot.to_dict()["role"]
            or runtime.get("execution_policy") != "standard"):
        raise TaskInputError("batch_quality_runtime_changed")
    return runtime


def _current_source(root, snapshot):
    binding = batch_quality_binding(root, snapshot)
    selected = snapshot.task.task_contract.source_intent["input"]
    batch = _batch(selected["workspace"], selected["project_key"], root, selected["batch_run_id"])
    current, source = _input(selected["workspace"], selected["project_key"], root, batch)
    if current != selected or source != binding["source"]:
        raise TaskInputError("batch_quality_source_changed")
    return binding, Path(source["integration"]["candidate_path"])


def batch_candidate_source(root, snapshot, request):
    binding, candidate = _current_source(root, snapshot)
    configuration = binding["configuration"]
    if (request.get("batch_source") != binding["receipt_id"] or request.get("builder_worker")
            or any(request.get(key) != value for key, value in configuration["validation_contract"].items())):
        raise TaskInputError("batch_quality_check_changed")
    initial = binding["source"]["integration"]["candidate_commit"]
    if not request.get("correction_worker"):
        if request["head_sha"] != initial:
            raise TaskInputError("batch_quality_source_changed")
        return candidate
    from goodteam_os.execution.correction_store import CorrectionAttemptStore

    authority = CorrectionAttemptStore(root, request["correction_worker"])
    value = authority.load()
    if value.original.data != snapshot.data:
        raise TaskInputError("correction_identity_mismatch")
    sealed = authority.sealed_candidate(value)
    if sealed is None or sealed["head_sha"] != request["head_sha"]:
        raise TaskInputError("correction_sealed_candidate_mismatch")
    candidate = authority.workspace(value)
    if _git(candidate, ["merge-base", "--is-ancestor", initial, request["head_sha"]], check=False).returncode:
        raise TaskInputError("batch_quality_correction_parent_changed")
    paths = _read_git(candidate, ["diff", "--name-only", "--no-renames", "-z", initial, request["head_sha"]])
    allowed = snapshot.task.task_contract.path_scope["allowed_paths"]
    if any(not any(path == domain or path.startswith(domain + "/") for domain in allowed)
           for path in paths.decode().split("\0") if path):
        raise TaskInputError("batch_quality_correction_outside_scope")
    return candidate


def prepare_project_batch_quality(*, workspace, project_key, root, run_id, argv, criteria,
                                  reviewer_model, fix_model, seconds=30, wall_seconds=900, fix_seconds=120):
    """Explicit local command/configuration only; freeze input without running a model."""
    from goodteam_os.project_iteration_execution import _require_execution_slot

    workspace, root = Path(workspace).resolve(), Path(root).resolve()
    if registered_project_scope(root) != (workspace, project_key):
        raise TaskInputError("batch_quality_project_unregistered")
    if (any(not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model)
            for model in (reviewer_model, fix_model))
            or type(wall_seconds) is not int or not 1 <= wall_seconds <= 3600
            or type(fix_seconds) is not int or not 1 <= fix_seconds <= 600):
        raise TaskInputError("batch_quality_configuration_invalid")
    with goal_guard(workspace, project_key), workflow_guard(workspace, project_key):
        _require_execution_slot(workspace, project_key, run_id)
        batch = _batch(workspace, project_key, root, run_id)
        if batch.get("status") == "abandoned":
            raise TaskInputError("batch_quality_abandoned")
        if batch.get("status") == "delivered":
            raise TaskInputError("batch_quality_already_delivered")
        selected, source = _input(workspace, project_key, root, batch)
        identifier = digest(canonical_bytes(selected))
        execution_id = run_id + "-quality-" + identifier
        items = [item["task_intent"] for item in selected["task_bindings"]]
        domains, criterion_map = [], _criteria(batch)
        acceptance = [f"{row['task_id']}: {row['text']}" for row in criterion_map]
        for task in items:
            allowed = normalize_write_domains(task.get("write_domains"))
            if (not task.get("acceptance_criteria") or allowed is None
                    or task.get("execution_policy") == "read_only"):
                raise TaskInputError("batch_quality_explicit_scope_required")
            domains.extend(allowed)
        contract = TaskContract(
            source_intent={"source_type": "project_task_batch", "source_revision": identifier,
                           "input_digest": identifier, "input": selected},
            task_identity={"task_id": execution_id, "batch_run_id": run_id,
                           "project_key": project_key, "input_digest": identifier},
            summary="Verify and independently review the confirmed project batch.",
            instructions=canonical_bytes(items).decode(), acceptance_criteria=acceptance,
            constraints=["Preserve the original project tasks and dependencies; do not merge, publish or deploy.",
                         "Correct only the declared project paths: " + ", ".join(sorted(set(domains)))],
            path_scope={"allowed_paths": sorted(set(domains)), "excluded_paths": []},
            metadata={"criterion_map": criterion_map})
        task = ExecutionTask(task_id=execution_id, source_type="project_task_batch", repo=project_key, issue_number=0,
            title="Project batch quality", body=contract.instructions, goal=contract.summary, status="queued",
            created_at=batch["created_at"], updated_at=batch["created_at"], task_contract=contract)
        store = store_for_project(root)
        snapshot = TaskExecutionSnapshot.capture_batch(task, scope=store.scope, role="implementation", adapter="codex")
        configuration = {"validation_contract": validation_contract(snapshot, argv=argv, criteria=criteria, seconds=seconds),
            "reviewer_adapter": "codex", "reviewer_model": reviewer_model, "max_rounds": 1,
            "wall_seconds": wall_seconds, "fix_seconds": fix_seconds,
            "correction_runtime": {"role": "implementation", "adapter": "codex", "model": fix_model,
                                   "execution_policy": "standard"}}
        required = _bound({"schema_version": _SCHEMA, "batch_run_id": run_id, "snapshot": snapshot.reference,
                          "input_digest": identifier, "configuration_digest": digest(canonical_bytes(configuration))})
        with role_lock(store.root / execution_key(run_id)):
            previous = _requirement(store, run_id)
            if previous is not None and previous != required:
                raise TaskInputError("batch_quality_configuration_changed")
            # The independent required marker precedes publication. A lost or
            # incomplete configuration cannot silently revert to human-only delivery.
            if previous is None:
                _write(store.root / execution_key(run_id) / "batch-quality-required.json", required)
            store.commit(snapshot)
            path = store.root / execution_key(execution_id) / "batch-quality-source.json"
            binding = _bound({"schema_version": _SCHEMA, "snapshot": snapshot.reference,
                              "input_digest": identifier, "source": source, "configuration": configuration})
            previous = _read(path)
            if previous is not None and previous != binding:
                raise TaskInputError("batch_quality_binding_invalid")
            if previous is None:
                _write(path, binding)
        quality = project_batch_quality_evidence(workspace=workspace, project_key=project_key, root=root, run_id=run_id)
        _publish(workspace, project_key, run_id, quality)
        return quality


def _publish(workspace, project_key, run_id, quality):
    def update(session):
        session["project_task_batches"][run_id]["quality"] = {key: quality[key] for key in _PROJECTION if key in quality}
        return session
    TaskAssistantSessionStore(workspace, project_key)._mutate(update)


def _next_action(reason):
    if reason in {"batch_quality_reviewer_admission_required", "local_codex_task_mismatch", "trusted_codex_task_mismatch"}:
        return "Configure the existing Reviewer policy for the displayed reviewer_task and original project root, then advance this same input."
    if reason in {"correction_verified_runtime_unavailable", "correction_native_authorization_required",
                  "correction_runtime_authorization_required"}:
        return "Use the displayed correction input and worktree to configure an existing exact-attempt native correction policy; resume this batch with that policy."
    if reason:
        return "Inspect the retained check and independent review evidence. Resolve this exact input's blocker before continuing; do not rerun an uncertain process."
    return "Explicitly advance the frozen checks and independent review with the configured runtime authorities."


def project_batch_quality_evidence(*, workspace, project_key, root, run_id):
    """Read receipts only. Never execute, enroll, prepare, import Git objects or advance clocks."""
    batch = _batch(workspace, project_key, root, run_id)
    result = {"required": bool(batch.get("quality", {}).get("required")), "status": "not_configured",
              "check_status": "unknown", "review_status": "missing", "acceptance_status": "unknown",
              "criterion_map": _criteria(batch)}
    try:
        store = store_for_project(Path(root), existing_only=True)
        required = _requirement(store, run_id)
        if required is None:
            if result["required"]:
                raise TaskInputError("batch_quality_input_missing")
            return result
        result.update(required=True, input_digest=required["input_digest"],
                      execution_id=required["snapshot"]["execution_id"], snapshot_ref=required["snapshot"])
        snapshot = store.load(result["execution_id"])
        if snapshot is None or snapshot.reference != required["snapshot"]:
            raise TaskInputError("batch_quality_input_missing")
        binding, candidate = _current_source(Path(root), snapshot)
        validation = CandidateValidationService(root)
        checked = validation.evidence(snapshot.execution_id)
        automatic = AutomaticCandidateReviewService(root).projection(snapshot.execution_id)
        last = _read(validation._root(snapshot.execution_id) / "batch-quality-last-result.json") or {}
        if last and last.get("input_digest") != required["input_digest"]:
            raise TaskInputError("batch_quality_binding_invalid")
        result.update(status="prepared", reason="", candidate_commit=binding["source"]["integration"]["candidate_commit"],
                      candidate_path=str(candidate), criterion_map=snapshot.task.task_contract.metadata["criterion_map"],
                      reviewer_task={"kind": "direct_task", "project_key": project_key,
                                     "logical_id": "candidate-review:" + snapshot.payload_digest},
                      validation=checked, automatic_review=automatic,
                      **{key: checked[key] for key in ("check_status", "review_status", "acceptance_status")})
        if automatic:
            result.update(status=automatic["status"], reason=automatic.get("reason", ""),
                          correction_job_id=automatic.get("worker_job_id", ""))
        if last.get("status") == "blocked" and result["acceptance_status"] != "accepted_by_review_policy":
            result.update(status="blocked", reason=last.get("reason", ""))
        if result.get("correction_job_id"):
            from goodteam_os.execution.correction_store import CorrectionAttemptStore
            authority = CorrectionAttemptStore(root, result["correction_job_id"])
            value = authority.load()
            result["correction_input"] = value.reference
            result["correction_workspace"] = str(authority.workspace(value))
        current = validation._current(snapshot)
        if current:
            plan, receipt, review = current
            candidate = validation.source_root(snapshot, plan["request"])
            result.update(candidate_commit=receipt["candidate"]["head_sha"], candidate_path=str(candidate),
                          validation_receipt_id=receipt["receipt_id"], review_receipt_id=(review or {}).get("receipt_id", ""))
        if (result["check_status"] == "passed" and result["review_status"] == "machine_recorded"
                and result["acceptance_status"] == "accepted_by_review_policy"):
            result.update(status="reviewed", reason="candidate_review_policy_satisfied")
        elif result["status"] == "reviewed":
            result.update(status="blocked", reason="batch_quality_independent_review_required")
        result["next_action"] = ("Review the retained evidence and deliver this exact candidate through the existing authorized Git workflow."
                                 if result["status"] == "reviewed" else _next_action(result.get("reason", "")))
        return result
    except (ValueError, OSError, KeyError, TypeError, SkillError) as exc:
        reason = getattr(exc, "code", "batch_quality_evidence_unavailable")
        return {**result, "required": True, "status": "blocked", "acceptance_status": "unknown",
                "reason": reason, "next_action": _next_action(reason)}


def advance_project_batch_quality(*, workspace, project_key, root, run_id, expected_input_digest,
                                  confirmed=False, billing_acknowledged=False,
                                  codex_review_admission=None, codex_correction_admission=None):
    from goodteam_os.execution.correction_native import correction_worker_kwargs
    from goodteam_os.project_iteration_execution import _require_execution_slot, _require_authority
    from goodteam_os.skills.codex_runtime import review_worker_kwargs

    if confirmed is not True or billing_acknowledged is not True:
        raise TaskInputError("batch_quality_confirmation_required")
    if not isinstance(expected_input_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_input_digest):
        raise TaskInputError("batch_quality_input_changed")
    runtime = {**review_worker_kwargs(codex_review_admission), **correction_worker_kwargs(codex_correction_admission)}
    workspace, root = Path(workspace).resolve(), Path(root).resolve()
    with goal_guard(workspace, project_key), workflow_guard(workspace, project_key):
        batch = _batch(workspace, project_key, root, run_id)
        if batch.get("status") == "abandoned":
            raise TaskInputError("batch_quality_abandoned")
        quality = project_batch_quality_evidence(workspace=workspace, project_key=project_key, root=root, run_id=run_id)
        if not quality.get("required") or quality.get("input_digest") != expected_input_digest:
            raise TaskInputError("batch_quality_input_changed")
        if quality.get("status") != "reviewed" and batch.get("status") != "delivered":
            _require_execution_slot(workspace, project_key, run_id)
            _require_authority(workspace, project_key, root, batch["project_config"], batch.get("expected_authority"))
            service = AutomaticCandidateReviewService(root, **runtime)
            try:
                snapshot = service.validation._snapshot(quality["execution_id"])
                binding, candidate = _current_source(root, snapshot)
                if codex_review_admission is None:
                    raise TaskInputError("batch_quality_reviewer_admission_required")
                configuration = binding["configuration"]
                # Import only retained, verified local objects for the existing
                # Reviewer/correction worktree services. Never move source HEAD/index.
                head = binding["source"]["integration"]["candidate_commit"]
                ref = "refs/goodteam/batch-quality/" + digest(snapshot.execution_id.encode())
                retained = _git(root, ["rev-parse", "--verify", ref], check=False)
                if retained.returncode == 0 and retained.stdout.decode().strip() != head:
                    raise TaskInputError("batch_quality_source_changed")
                if retained.returncode:
                    _git(root, ["fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--", str(candidate), head + ":" + ref])
                saved = service.configuration(snapshot.execution_id)
                if saved is None:
                    checked = service.validation.validate(snapshot.execution_id,
                        operation_id="batch-check-" + expected_input_digest,
                        head_sha=head, **configuration["validation_contract"])
                    service.start(snapshot.execution_id, base_sha=binding["source"]["integration"]["baseline_commit"],
                        receipt_id=checked["receipt_id"], head_sha=head,
                        **{key: configuration[key] for key in ("reviewer_adapter", "reviewer_model", "max_rounds", "wall_seconds", "fix_seconds")})
                else:
                    service.start(snapshot.execution_id, receipt_id=saved["initial_receipt"], head_sha=saved["initial_head"],
                        **{key: saved[key] for key in ("base_sha", "reviewer_adapter", "reviewer_model", "max_rounds", "wall_seconds", "fix_seconds")})
                outcome = service.advance(snapshot.execution_id, acknowledge_external_cost=billing_acknowledged)
            except (ValueError, OSError, SkillError) as exc:
                outcome = {"status": "blocked", "reason": getattr(exc, "code", "batch_quality_execution_unavailable")}
            _write(service.validation._root(quality["execution_id"]) / "batch-quality-last-result.json",
                   {"input_digest": expected_input_digest, "status": outcome["status"], "reason": outcome.get("reason", "")})
            quality = project_batch_quality_evidence(workspace=workspace, project_key=project_key, root=root, run_id=run_id)
        _publish(workspace, project_key, run_id, quality)
        return {"quality": quality, "task_assistant_session": TaskAssistantSessionStore(workspace, project_key).read()}


def accepted_batch_quality(*, workspace, project_key, root, run_id):
    """Delivery gate. Legacy batches retain their separately confirmed human path."""
    quality = project_batch_quality_evidence(workspace=workspace, project_key=project_key, root=root, run_id=run_id)
    if not quality["required"]:
        return None
    if (quality["status"] != "reviewed" or quality["check_status"] != "passed"
            or quality["review_status"] != "machine_recorded" or quality["acceptance_status"] != "accepted_by_review_policy"):
        raise TaskInputError("batch_quality_acceptance_required")
    return quality
