"""Explicit one-attempt Worker lifecycle, reusing the existing worktree executor."""
from __future__ import annotations

from pathlib import Path

from goodteam_os.memory_storage import role_lock
from goodteam_os.workers import CLI_BACKENDS, WorkerJobStore

from .candidate_probe import probe_candidate
from .candidate_validation import _bound, _read, _write
from .correction_input import CorrectionAttemptInput
from .correction_loop import CorrectionLoopService
from .correction_store import CorrectionAttemptStore
from .correction_workspace import CorrectionWorktreeGit, worktree_identity
from .task_context import task_worker_fields
from .task_snapshot import TaskInputError, digest


class CorrectionWorkerService:
    def __init__(self, project_root, *, codex_correction_admission=None):
        self.project_root = Path(project_root).resolve()
        self.loop = CorrectionLoopService(self.project_root)
        self.workers = WorkerJobStore(self.project_root, codex_correction_admission=codex_correction_admission)

    def prepare(self, execution_id, *, loop_id, receipt_id, head_sha, operation_id, max_seconds=120):
        snapshot = self.loop.validation._snapshot(execution_id)
        original = snapshot.to_dict()
        if (original["adapter"] not in {*CLI_BACKENDS, "fake", "noop"}
                or (original["adapter"] in CLI_BACKENDS and CLI_BACKENDS[original["adapter"]].needs_pty)):
            raise TaskInputError("correction_adapter_unsupported")
        root = self.loop.validation._root(execution_id)
        with role_lock(root, create=False):
            state = self.loop._load(snapshot)
            if state is None or state["loop_id"] != loop_id:
                raise TaskInputError("correction_identity_mismatch")
            existing = next((row for row in state["rounds"] if "worker_job_id" in row
                and self.loop._worker_input(snapshot, row)[1].to_dict()["operation_id"] == operation_id), None)
            if existing:
                _authority, value = self.loop._worker_input(snapshot, existing)
                captured = value.to_dict()
                if (captured["review"]["validation_receipt_id"] != receipt_id or captured["candidate"]["head_sha"] != head_sha
                        or captured["max_seconds"] != max_seconds):
                    raise TaskInputError("correction_operation_conflict")
            else:
                if digest(operation_id.encode()) in {state["initial"]["operation_key"],
                        *(row["operation_key"] for row in state["rounds"])}:
                    raise TaskInputError("correction_operation_conflict")
                history = self.loop._history(snapshot, state)
                phase, reason = self.loop._phase(snapshot, state, history)
                if phase != "awaiting_fix":
                    raise TaskInputError(reason)
                plan, _started, checked, review = history[-1]
                if checked["receipt_id"] != receipt_id or checked["candidate"]["head_sha"] != head_sha:
                    raise TaskInputError("correction_feedback_mismatch")
                source = self.loop.validation.source_root(snapshot, plan["request"])
                probe_candidate(source, head_sha)
                rows = self.loop.validation.store.receipts(snapshot)
                if any(row["stage"] == "transport_attempt" for row in rows) and self.loop.validation.store.host_return(snapshot) is None:
                    raise TaskInputError("correction_original_transport_unresolved")
                value = CorrectionAttemptInput.capture(snapshot, loop_id=loop_id, ordinal=len(state["rounds"]) + 1,
                    operation_id=operation_id, review=review, candidate=checked["candidate"], max_seconds=max_seconds)
                from .correction_feedback import render_correction_input
                # Fail before reserving a round or creating a worktree when the
                # complete retained feedback cannot fit. Never trim findings.
                render_correction_input(self.project_root, value)
                authority = CorrectionAttemptStore(self.project_root, value.job_id)
                self.loop.reserve_worker(value, publish=False)
                authority.commit(value)
                self.loop.reserve_worker(value)
        self._materialize(value)
        return self.evidence(execution_id, value.job_id)

    def _materialize(self, value):
        authority = self.workers._input_store(value)
        with authority.lock():
            authority.assert_preparable(value)
            expected = {"input": value.reference, "candidate": value.to_dict()["candidate"]}
            ready = _read(authority.root / "workspace-ready.json")
            if ready is not None and any(ready.get(key) != item for key, item in expected.items()):
                raise TaskInputError("correction_workspace_mismatch")
            if ready is None:
                if _read(authority.root / "workspace-started.json") is not None:
                    raise TaskInputError("correction_workspace_preparation_unknown")
                _write(authority.root / "workspace-started.json", expected)
                workspace = self.project_root / value.workspace_relative
                try:
                    CorrectionWorktreeGit().create_worktree(repo_root=self.project_root, worktree_path=workspace,
                        branch_name="sandbox/" + value.job_id, base_ref=value.to_dict()["candidate"]["head_sha"])
                except (OSError, ValueError) as exc:
                    raise TaskInputError("correction_workspace_preparation_unknown") from exc
                probe_candidate(authority.workspace(value, require_ready=False), value.to_dict()["candidate"]["head_sha"])
                ownership = worktree_identity(self.project_root, workspace, value.job_id)
                _write(authority.root / "workspace-ready.json", _bound({**expected, "ownership": ownership}))
            workspace = authority.workspace(value)
            original_ready = _read(authority.execution_root / "job-ready.json")
            if original_ready is None or original_ready.get("snapshot") != value.original.reference:
                raise TaskInputError("task_input_job_not_ready")
            original_request = original_ready["request"]
        authority.check_job_projection(value, create=True)
        job, _created = self.workers.create_or_reuse_job(run_id=value.execution_id, execution_id=value.execution_id,
            role=value.original.to_dict()["role"], adapter=value.original.to_dict()["adapter"],
            model=original_request.get("model") or "", execution_policy=original_request.get("execution_policy") or "standard",
            idempotency_key=value.to_dict()["operation_id"], task_input=value, **task_worker_fields(value))
        self.workers._attach_sandbox_context(self.workers.root / value.job_id, job,
            {"available": True, "mode": "worktree", "workspace_path": str(workspace), "worktree_path": str(workspace),
             "status": "active", "execution_id": value.job_id, "branch_name": "sandbox/" + value.job_id})

    def run(self, execution_id, job_id, *, acknowledge_external_cost=False):
        authority = self.workers._input_store(job_id=job_id)
        with authority.lock():
            value = authority.load()
            if value.execution_id != execution_id:
                raise TaskInputError("correction_identity_mismatch")
            started, previous = authority.outcome(value)
            if previous is not None:
                return self.finalize(execution_id, job_id)
            if started is not None:
                raise TaskInputError("correction_worker_outcome_unknown")
            adapter = value.original.to_dict()["adapter"]
            capability = authority.runtime_capability(value)
            if not capability["available"]:
                raise TaskInputError(capability["reason"])
            if adapter not in {"fake", "noop"} and acknowledge_external_cost is not True:
                raise TaskInputError("correction_runtime_authorization_required")
            # Only the explicit CLI/trusted caller may create this authorization;
            # there is no HTTP run/command endpoint in this slice.
            auth = authority.authorization(value)
            previous_auth = _read(authority.root / "authorization.json")
            if previous_auth is not None and previous_auth != auth:
                raise TaskInputError("correction_runtime_authorization_required")
            if "native_runtime" in auth:
                required = _bound({"input": value.reference, "native_runtime": auth["native_runtime"]})
                previous_required = _read(authority.root / "native-runtime-required.json")
                if previous_required is not None and previous_required != required:
                    raise TaskInputError("correction_native_binding_missing")
                if previous_required is None:
                    _write(authority.root / "native-runtime-required.json", required)
            _write(authority.root / "authorization.json", auth)
            authority.assert_dispatchable(value)
            job = self.workers._read_json(self.workers.root / job_id / "job.json")
            authority.admit(job_id, job)
            workspace = authority.check_runtime_context(value, job)
            probe_candidate(workspace, value.to_dict()["candidate"]["head_sha"])
            authority.start(value)
        try:
            result = self.workers.run_job(job_id)
        except (OSError, ValueError) as exc:
            raise TaskInputError("correction_worker_outcome_unknown") from exc
        finally:
            authority.release_start()
        with authority.lock():
            returned = authority.host_return(value)
            if returned is None or returned["status"] != result.get("status"):
                raise TaskInputError("correction_worker_outcome_unknown")
            _write(authority.root / "run-result.json", _bound({"input": value.reference,
                "status": result["status"], "host_return": returned}))
        return self.finalize(execution_id, job_id)

    def finalize(self, execution_id, job_id):
        """Finish/replay a returned native artifact without authorizing dispatch."""
        authority = self.workers._input_store(job_id=job_id)
        with authority.lock():
            value = authority.load()
            if value.execution_id != execution_id:
                raise TaskInputError("correction_identity_mismatch")
            native = authority.native_binding(value)
            _started, result = authority.outcome(value)
            if native is not None and result is not None and result["status"] == "completed":
                from .correction_seal import seal_stopped_candidate
                seal_stopped_candidate(authority)
        return self.evidence(execution_id, job_id)

    def evidence(self, execution_id, job_id):
        from .correction_input import correction_runtime_capability
        authority = CorrectionAttemptStore(self.project_root, job_id)
        with authority.lock():
            value = authority.load()
            if value.execution_id != execution_id:
                raise TaskInputError("correction_identity_mismatch")
            started, result = authority.outcome(value)
            native = authority.native_binding(value)
            sealed = authority.sealed_candidate(value) if native and _read(authority.root / "seal-result.json") else None
            return {"job_id": job_id, "input": value.reference, "workspace": value.workspace_relative,
                    "status": result["status"] if result else "outcome_unknown" if started else "prepared",
                    "max_invocations": 1, "max_seconds": value.to_dict()["max_seconds"],
                    "provider_receipt": "unknown", "acceptance_status": "unknown", "verification": "unverified",
                    "runtime_admission": correction_runtime_capability(value.original.to_dict()["adapter"]),
                    **({"native_runtime": native, "candidate": sealed} if native else {})}
