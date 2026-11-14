"""Bind an authorized continuation to an existing, undelivered batch candidate."""
from pathlib import Path


def candidate_for_tasks(*, workspace, project_key, root, session, tasks, continuation_id):
    """Use the project's current candidate when the owner enabled continuation."""
    from goodteam_os.project_batch_feedback import valid_receipt

    value = session.get("project_continuation") or {}
    if (not continuation_id or value.get("id") != continuation_id
            or (value.get("discovery") or {}).get("allow_candidate_handoff") is not True):
        return None
    key = value.get("undelivered_feedback_anchor")
    receipt = (value.get("batch_observations") or {}).get(key)
    if not receipt:
        return None
    if not valid_receipt(value, receipt):
        raise ValueError("The original candidate feedback is invalid.")
    return _source(workspace=workspace, project_key=project_key, root=root, value=value, key=key,
                   allow_no_candidate=True)


def _source(*, workspace, project_key, root, value, key, allow_no_candidate=False):
    from goodteam_os.project_batch_feedback import _read_observation, valid_receipt
    from goodteam_os.project_iteration_quality import _input
    from goodteam_os.project_outcome_discovery import authorization_digest
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore

    receipt = (value.get("batch_observations") or {}).get(key)
    if (value.get("authorization_digest") != authorization_digest(value)
            or not valid_receipt(value, receipt) or _read_observation(
            workspace=workspace, project_key=project_key, root=root, value=value,
            run_id=receipt["batch_run_id"]) != receipt):
        raise ValueError("The candidate feedback no longer matches its original batch.")
    session = TaskAssistantSessionStore(workspace, project_key).read()
    batch = session.get("project_task_batches", {}).get(receipt["batch_run_id"])
    if not batch:
        raise ValueError("The candidate's original batch is unavailable.")
    if allow_no_candidate:
        from goodteam_os.project_iteration_execution import iteration_batch_report

        report = iteration_batch_report(workspace=workspace, project_key=project_key,
                                        root=Path(root), run_id=batch["run_id"])
        frozen = _starting_candidate(root=Path(root), batch=batch, report=report)
        if (frozen is None
                and not any((row.get("captured_result") or {}).get("changed_files")
                            for row in receipt["tasks"])):
            return None
        # Explicit no-integration outcomes must retain the starting candidate,
        # not partial Worker changes or a fallback to the original checkout.
        if (report.get("all_ok") is False and report.get("integration") in (
                {"status": "blocked", "reason": "worker_or_evidence_failed"},
                {"status": "blocked", "reason": "file_collisions"})):
            return verify_candidate_for_batch(workspace=workspace, project_key=project_key,
                                              root=root, batch=batch)
    selected, source = _input(workspace, project_key, Path(root), batch)
    return {"observation_key": key, "receipt_id": receipt["receipt_id"],
            "dispatch_run_id": receipt["dispatch_run_id"], "selected": selected, "source": source}



def _starting_candidate(*, root, batch, report):
    """Follow only recorded, hash-bound recovery baselines to the original batch."""
    from goodteam_os.dispatch_workspace import prepare_dispatch_workspace, read_dispatch_recovery

    original_id = batch["dispatch_run_id"]
    commands = batch.get("recovery_commands") or {}
    seen = set()
    while report["run_id"] != original_id:
        run_id = report["run_id"]
        if run_id in seen or len(seen) >= len(commands):
            raise ValueError("The candidate recovery chain is cyclic or incomplete.")
        seen.add(run_id)
        source = report.get("recovery_source")
        matching = [command for command in commands.values()
                    if command.get("dispatch_run_id") == run_id]
        if (len(matching) != 1 or not isinstance(source, dict)
                or matching[0].get("source") != source):
            raise ValueError("The candidate recovery does not match its confirmed command.")
        current = prepare_dispatch_workspace(root, run_id, None)
        if (current.metadata.get("baseline_from") != source.get("run_id")
                or current.metadata.get("candidate_source") is not None
                or report.get("candidate_source") is not None):
            raise ValueError("The candidate recovery baseline binding changed.")
        parent = read_dispatch_recovery(root, source)
        previous = prepare_dispatch_workspace(root, parent["run_id"], None)
        if current.metadata["baseline_commit"] != previous.metadata["baseline_commit"]:
            raise ValueError("The candidate recovery no longer shares its original baseline.")
        report = parent
    previous = prepare_dispatch_workspace(root, original_id, None)
    frozen = batch.get("candidate_source")
    if (previous.metadata.get("baseline_from") is not None
            or report.get("recovery_source") is not None
            or previous.metadata.get("candidate_source") != frozen
            or report.get("candidate_source") != frozen):
        raise ValueError("The batch's starting candidate binding changed.")
    return frozen

def verify_candidate_for_batch(*, workspace, project_key, root, batch):
    """Recheck the frozen source without granting it delivery or new authority."""
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore

    frozen = batch.get("candidate_source")
    if frozen is None:
        return None
    session = TaskAssistantSessionStore(workspace, project_key).read()
    value = session.get("project_continuation") or {}
    if (value.get("id") != batch.get("expected_continuation_id")
            or value.get("authority") != batch.get("expected_authority")
            or (value.get("discovery") or {}).get("allow_candidate_handoff") is not True):
        raise ValueError("The candidate is outside this continuation's confirmed task scope.")
    if _source(workspace=workspace, project_key=project_key, root=root, value=value,
               key=frozen["observation_key"]) != frozen:
        raise ValueError("The candidate changed after the follow-up was confirmed.")
    return frozen
