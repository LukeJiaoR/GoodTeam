"""Existing Worker → locked content → parent capture → durable advisory result.

Production admission remains closed pending the selected Linux Codex envelope's
live conformance. There is no configuration/JSON switch or test runtime registry.
The internal adapter seam is exercised by subprocess fixtures only in tests.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from uuid import uuid4

from . import worker_requests as requests
from .content import SkillContextProvider
from .filesystem import directory, subdirectory, write_new
from .models import SkillError, canonical_json, content_digest
from .review_process import run_review_process
from .review_report import report_output_instructions
from .review_results import ReviewResultStore, _read_optional


class ClosedReviewRuntime:
    def authorize(self, snapshot, *, context_id, operation):
        raise SkillError("skill_runtime_not_connected", "The selected Reviewer Runtime has not passed independent admission.")


def selected_review_runtime(workers, snapshot):
    """Server composition only; never derived from job flags or user packages."""
    return ClosedReviewRuntime()


def _input(provider, context_ref, record):
    private = provider.private_input(context_ref)
    original = private["original_job_input"]
    base = original["files"].get("review_prompt_base.md")
    if not base:
        raise SkillError("review_protocol_unsupported", "The locked Reviewer has no supported protocol base.")
    # Explicit user/task inputs remain exact. Do not serialize original_job_input
    # (which also contains private Operator paths and deployment metadata).
    required = "\n".join("## Required method " + item["skill_id"] + "\n" + item["text"] for item in private["required"])
    catalog = [{key: deepcopy(item[key]) for key in
                ("skill_id", "version_id", "package_digest", "name", "description", "activation", "resources")}
               for item in private["catalog"]]
    text = (base + "\n# Locked methods\n" + required + "\n# Optional method catalog\n"
            + canonical_json(catalog) + "\nResources: methods/<skill_id>/<relative path>. Read only when relevant.\n"
            + "# Captured review source: candidate\n" + record["sources"]["candidate"]
            + "\n# Result channel contract\n" + report_output_instructions(["candidate"]))
    encoded = text.encode("utf-8")
    if len(encoded) > provider.limits.max_input_bytes:
        raise SkillError("content_budget_exceeded", "The complete Reviewer prompt exceeds its limit; no input was truncated.")
    return encoded, private


def materialize_review_inputs(provider, context_ref, record, root: Path):
    """Populate an adapter-owned *new* staging tree; not proof of model reads.

    The adapter must project this tree read-only into its separately verified
    isolation envelope. The Worker never receives a mount of Operator storage.
    """
    prompt, private = _input(provider, context_ref, record)
    root = Path(root)
    with directory(root.parent) as parent:
        os.mkdir(root.name, mode=0o700, dir_fd=parent)
        with subdirectory(parent, root.name) as fd:
            write_new(fd, "candidate", record["sources"]["candidate"].encode(), mode=0o400)
            os.fsync(fd)
        os.fsync(parent)
    for item in private["catalog"]:
        for resource in item["resources"]:
            relative = resource["path"]
            data = provider.read_resource(context_ref, skill_id=item["skill_id"], relative=relative,
                                          call_id="materialize:" + content_digest((item["skill_id"] + ":" + relative).encode())[7:])
            target = root / "methods" / item["skill_id"] / relative
            with directory(target.parent, create=True) as fd:
                write_new(fd, target.name, data, mode=0o400)
    provider.private_input(context_ref)  # Final current revocation/session check.
    ReviewResultStore(provider.workers, provider.job_id).observe_delivery("materialized", content_digest(prompt))
    return prompt


def _mirror(workers, job_id, snapshot, ref, *, status, error_code="", runtime_status="not_connected"):
    """Append protected execution evidence before replacing the Worker view.

    No result.md, Memory write-back, approval, gate, merge or deploy transition.
    A received advisory report is deliberately NOT legacy 'completed' evidence.
    """
    root, operator, _locks, _availability = requests._services(workers)
    expected = requests._metadata(job_id, ref, snapshot)
    result = {"job_id": job_id, "run_id": expected["run_id"], "project_key": expected["project_key"],
              "role": expected["role"], "adapter": expected["adapter"], "status": status,
              "summary": "Advisory review report received; no approval decision." if status == "review_received" else "Reviewer execution requires attention.",
              "updated_at": requests.datetime.now(requests.UTC).isoformat(), "artifacts": [], "changed_files": [],
              "skill_preflight_status": "verified", "skill_error_code": error_code,
              "skill_runtime_status": runtime_status, "task_skill_context": expected["task_skill_context"],
              "skill_execution_snapshot_ref": ref, "skill_subject": snapshot["subject"],
              "skill_loader_version": snapshot["loader_version"],
              "invocation_evidence": {"kind": "advisory_review", "adapter": expected["adapter"],
                                      "provider": "none" if runtime_status == "contract_fixture" else "unconfirmed",
                                      **({"external_invocation": False} if runtime_status == "contract_fixture" else {}),
                                      "reason": "See protected review execution detail; this view grants no authority."}}
    with requests._request_lock(operator, job_id) as fd:
        attempt_id = "preflight-" + uuid4().hex
        result["skill_preflight_attempt_id"] = attempt_id
        write_new(fd, job_id + "." + attempt_id + ".json", canonical_json(result).encode())
        requests.os.fsync(fd)
        with directory(root / ".goodteam" / "workers" / "jobs" / job_id) as job_fd:
            requests._write_status(job_fd, result)
    return result


def continue_review_worker(workers, job_id, snapshot, ref):
    """Called after existing Worker preflight, outside its request-file lock.

    Return None only when production admission is closed *before an attempt*.
    Any saved attempt prevents fallback or duplicate launch, including crashes.
    """
    if snapshot["subject"] != "role:code_review":
        return None
    results = ReviewResultStore(workers, job_id)
    operator = requests._services(workers)[1]
    try:
        with results._store(operator) as fd:
            existing = _read_optional(fd, "attempt.json")
    except (SkillError, FileNotFoundError) as exc:
        if isinstance(exc, SkillError) and not isinstance(exc.__cause__, FileNotFoundError):
            raise
        existing = None
    if existing is not None:
        # Never silently reconnect or spend again after restart.
        try:
            detail = results.recover()
        except SkillError as exc:
            return _mirror(workers, job_id, snapshot, ref, status="failed", error_code=exc.code, runtime_status="recovery_required")
        return _mirror(workers, job_id, snapshot, ref, status="review_received" if detail["report_available"] else "failed",
                       error_code=detail.get("error_code", ""), runtime_status=detail["runtime_verification"])
    runtime = selected_review_runtime(workers, snapshot)
    if isinstance(runtime, ClosedReviewRuntime):
        return None
    context_id = "review-" + uuid4().hex
    provider = SkillContextProvider(workers, job_id, authority=runtime)
    begun = False
    try:
        context_ref = provider.prepare(context_id)
        results.begin(provider, context_ref)
        begun = True
        record = results.private_attempt()
        # Admission and physical readonly projection are adapter responsibilities,
        # not a prompt request or a public metadata capability flag.
        with runtime.execution(record, provider, context_ref) as (spec, prompt):
            expected_prompt, _private = _input(provider, context_ref, record)
            if prompt != expected_prompt:
                raise SkillError("review_input_mismatch", "The adapter did not deliver the complete original method/input contract.")
            def delivered():
                results.observe_delivery("content_delivered", content_digest(prompt))
                results.observe_delivery("catalog_presented", content_digest(prompt))
            results.mark_running()  # Durable before subprocess creation; uncertain crash cannot rerun.
            capture = run_review_process(spec, prompt, check_current=lambda: runtime.check_current(provider, context_ref),
                                         on_input_delivered=delivered)
        # The adapter context must confirm outer-envelope cleanup before receive.
        results.receive(capture)
        return _mirror(workers, job_id, snapshot, ref, status="review_received",
                       runtime_status=record["runtime_session"]["verification_kind"])
    except (SkillError, OSError) as exc:
        code = exc.code if isinstance(exc, SkillError) else "review_io_failed"
        if begun:
            # Never replace a durably received result with a projection/save error.
            try:
                with results._store(operator) as fd:
                    outcome = _read_optional(fd, "outcome.json")
                if outcome is None:
                    if code == "review_termination_unknown":
                        results.mark_stopping()
                    else:
                        results.fail(code, status="invalid" if code.startswith(("review_report", "review_capture")) else "failed")
            except (SkillError, OSError):
                code = "review_result_save_failed"
        return _mirror(workers, job_id, snapshot, ref, status="failed", error_code=code, runtime_status="blocked")
