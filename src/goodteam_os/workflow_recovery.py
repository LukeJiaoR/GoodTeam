"""Exact failed-Workflow evidence, reused by the existing review and batch paths.

This is an admission adapter, not another task ledger or dispatcher. A recovery
review cannot approve the parent, and a continuation still owns its finite scope.
"""
from pathlib import Path
import json

from goodteam_os.reviewer_dispatch import artifact_version_for_path
from goodteam_os.state import StateStore
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.workflow_workspace import collect_workflow_workspace


ARTIFACT = "workflow_recovery"


def _inputs(state):
    return {"idea": state.idea, "baseline": state.repository_baseline,
            "artifacts": {key: artifact_version_for_path(state.artifacts.get(key, ""))
                          for key in ("prd", "rfc")}}


def _idle(workspace, state):
    from goodteam_os.web import _workflow_retry_context
    from goodteam_os.workflow_retry import build_retry_target

    if (state.task_skill_context or state.current_node != "implementation"
            or state.next_node != "failure_triage" or not state.blockers
            or not state.repository_baseline):
        raise ValueError("Recovery requires the original failed ordinary Workflow.")
    config, jobs, context = _workflow_retry_context(workspace, state)
    # This operation's queued read-only reviewers do not own a writing attempt.
    ordinary = [job for job in jobs if job.get("artifact_key") != ARTIFACT]
    target = build_retry_target(state, jobs=ordinary, execution_context=context)
    if not target["available"] or state.pending_transition.get("status") == "interrupted":
        raise ValueError("Resolve active or unknown Workflow attempts before recovery.")
    return config, jobs


def candidate(workspace, state, *, reviewed=False):
    """Revalidate the parent and its unchanged task bytes, never recapture source."""
    _config, jobs = _idle(workspace, state)
    record = state.release_candidate.get(ARTIFACT) or {}
    if (record.get("run_id") != state.run_id or record.get("inputs") != _inputs(state)
            or record.get("version") != artifact_version_for_path(state.artifacts.get(ARTIFACT, ""))):
        raise ValueError("The failed Workflow recovery candidate changed; prepare it again.")
    root = Path(state.repository_baseline["source_root"])
    current = collect_workflow_workspace(root, state.repository_baseline,
                                        project_key=state.project_key, run_id=state.run_id)
    if current["result_commit"] != record["repository_result"]["result_commit"]:
        raise ValueError("The failed Workflow source changed after recovery review was prepared.")
    if reviewed:
        from goodteam_os.review_policy import evaluate_review_evidence_policy
        from goodteam_os.review_provenance import build_reviewer_provenance

        reports = [job for job in jobs if job.get("dispatch_group_id") == record.get("reviewer_group_id")
                   and job.get("run_id") == state.run_id and job.get("artifact_key") == ARTIFACT
                   and job.get("artifact_path") == state.artifacts[ARTIFACT]
                   and job.get("artifact_version") == record["version"]
                   and job.get("execution_policy") == "read_only" and job.get("status") == "completed"
                   and evaluate_review_evidence_policy(build_reviewer_provenance(job), gate="release")["state"] == "ok"
                   and Path(str(job.get("result_path", ""))).is_file()
                   and Path(str(job.get("result_path", ""))).stat().st_size]
        latest = {job["profile_id"]: job for job in sorted(reports, key=lambda item: str(item.get("updated_at", "")))}
        if not record["reviewer_job_ids"] or set(latest) != set(record["reviewer_profile_ids"]):
            raise ValueError("Complete the exact-candidate independent read-only recovery review first.")
        return {**record, "review_reports": {job["id"]: artifact_version_for_path(job["result_path"]) for job in latest.values()}}
    return record


def prepare(workspace, project_key, request):
    from goodteam_os.artifacts import ArtifactStore
    from goodteam_os.artifact_revisions import invalidate_artifact
    from goodteam_os.graph import dispatch_gate_reviewer_jobs
    from goodteam_os.resources import register_markdown_artifact
    from goodteam_os.web import append_action, workflow_retry_target
    from goodteam_os.workflow_mutations import workflow_guard

    with workflow_guard(workspace, project_key):
        store = StateStore(workspace)
        state = store.read_project_current(project_key)
        if state is None or request.get("expected_run_id") != state.run_id:
            raise ValueError("Select the current failed Workflow run.")
        target = workflow_retry_target(workspace, state)
        if request.get("expected_retry_version") != target["version"] or not target["available"]:
            raise ValueError("Refresh the exact idle failed Workflow attempt before preparing recovery.")
        config, _jobs = _idle(workspace, state)
        root = Path(state.repository_baseline["source_root"])
        result = collect_workflow_workspace(root, state.repository_baseline,
                                           project_key=project_key, run_id=state.run_id, candidate=True)
        if result["integration"]["status"] != "succeeded":
            raise ValueError("The retained failed Workflow cannot form a reviewable candidate.")
        record = {"run_id": state.run_id, "inputs": _inputs(state), "repository_result": result}
        if ARTIFACT in state.artifacts:
            invalidate_artifact(state, ARTIFACT)
        path = ArtifactStore(Path(workspace), revision_tokens=state.artifact_generations).write_markdown(state.run_id, ARTIFACT, "candidate.md",
            "# Failed Workflow candidate — advisory review only\n\n"
            "The parent task and failed QA remain unresolved. Review the exact retained source, "
            "findings and delivery provenance; do not approve, merge, deploy or modify files.\n\n"
            + json.dumps(record, ensure_ascii=False, indent=2))
        register_markdown_artifact(state, ARTIFACT, path, reviewable=False, editable=False)
        record["version"] = artifact_version_for_path(str(path))
        jobs = dispatch_gate_reviewer_jobs(state=state, workspace=workspace, project_config=config,
            approval="release", artifact_key=ARTIFACT, artifact_path=path)
        record["reviewer_job_ids"] = [job["id"] for job in jobs]
        record["reviewer_profile_ids"] = [job["profile_id"] for job in jobs]
        record["reviewer_group_id"] = jobs[0]["dispatch_group_id"] if jobs else ""
        state.release_candidate[ARTIFACT] = record
        store.write_project_current(state, project_key)
        store.write_project_checkpoint(state, project_key)
        append_action(workspace, run_id=state.run_id, project_key=project_key, actor="human",
                      action="workflow_recovery_candidate_prepared", artifact=ARTIFACT,
                      artifact_path=str(path), artifact_version=record["version"])
        return {"workflow_recovery": record, "reviewer_jobs": jobs,
                "advisory_only": True, "approval_unchanged": True}


def confirm_parent(workspace, project_key, root, reference):
    """Owner selects a reviewed candidate already landed through normal Git."""
    from goodteam_os.dispatch_workspace import _git, _read_git
    from goodteam_os.project_iteration_continuation import _require_clean_checkout, _tree

    if not isinstance(reference, dict) or set(reference) != {"run_id", "version"}:
        raise ValueError("Select the exact reviewed failed-Workflow candidate.")
    state = StateStore(workspace).read_project_current(project_key)
    if state is None or state.run_id != reference["run_id"]:
        raise ValueError("Recovery belongs to a different parent Workflow.")
    record = candidate(workspace, state, reviewed=True)
    if reference["version"] != record["version"] or Path(root).resolve() != Path(record["inputs"]["baseline"]["source_root"]):
        raise ValueError("Recovery source or candidate identity changed.")
    integration = record["repository_result"]["integration"]
    head = _read_git(root, ["rev-parse", "HEAD"]).strip().decode()
    commit = integration["candidate_commit"]
    if (_git(root, ["merge-base", "--is-ancestor", commit, head], check=False).returncode
            or _tree(root, head) != _tree(Path(integration["candidate_path"]), commit)):
        raise ValueError("Land the exact reviewed candidate in the original registered repository first.")
    _require_clean_checkout(root, "recovery project")
    return {**reference, "review_reports": record["review_reports"]}


def linked_parent(workspace, project_key, *, continuation_id=None, admitted=None):
    """Only the confirmed continuation/batch can share its idle failed parent."""
    value = TaskAssistantSessionStore(workspace, project_key).read().get("project_continuation") or {}
    reference = admitted or value.get(ARTIFACT)
    if admitted is not None and (continuation_id != value.get("id") or admitted != value.get(ARTIFACT)):
        raise ValueError("The admitted recovery batch belongs to a different continuation.")
    if (not reference or continuation_id != value.get("id")
            or admitted is None and value.get("status") != "active"):
        return None
    state = StateStore(workspace).read_project_current(project_key)
    if state is None:
        raise ValueError("The linked failed Workflow recovery identity changed.")
    record = candidate(workspace, state, reviewed=True)
    if reference != {"run_id": state.run_id, "version": record["version"], "review_reports": record["review_reports"]}:
        raise ValueError("The linked failed Workflow recovery identity changed.")
    from goodteam_os.dispatch_workspace import _git
    from goodteam_os.project_iteration_continuation import _require_clean_checkout

    root = Path(state.repository_baseline["source_root"])
    if _git(root, ["merge-base", "--is-ancestor", record["repository_result"]["integration"]["candidate_commit"], "HEAD"], check=False).returncode:
        raise ValueError("The registered source no longer contains the reviewed recovery candidate.")
    _require_clean_checkout(root, "recovery project")
    proposal = TaskAssistantSessionStore(workspace, project_key).read().get("proposed_task") or {}
    if proposal and state.run_id not in {proposal.get("run_id"), proposal.get("start_run_id")}:
        raise ValueError("Another task proposal owns this project.")
    return state
