from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from goodteam_os.execution import ExecutionAPI
from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService, MACHINE_SCHEMA
from goodteam_os.execution.candidate_probe import probe_candidate
from goodteam_os.execution.task_snapshot import TaskInputError
from goodteam_os.execution.task_snapshot_store import store_for_project
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.skills.codex_runtime import review_worker_kwargs
from goodteam_os.execution.github import execution_task_from_github_issue
from goodteam_os.execution.orchestrator import ExecutionOrchestrator
from goodteam_os.execution.pr import PullRequestBindingService
from goodteam_os.operator_github import (
    GhCliGitHubClient,
    parse_github_issue_url,
    resolve_known_project,
)
from goodteam_os.operator_queue import (
    DurableOperatorQueue,
    IllegalQueueTransition,
    QueueCorruptionError,
    utc_now,
)
from goodteam_os.operator_candidate_review import (
    configuration_for, freeze_configuration, prepare_candidate, start_candidate_review, check_admission_identity,
)
from goodteam_os.projects import load_project_target
from goodteam_os.telegram import TelegramReviewSessionStore, send_approval_notification
from goodteam_os.workers import worker_backend_doctor


MAX_RETRIES = 3
MAX_FIX_ATTEMPTS = 2
LEGAL_PHASE_TRANSITIONS = {
    "queued": {"hydrating"},
    "hydrating": {"preflight", "paused", "failed"},
    "preflight": {"executing", "paused", "failed"},
    "executing": {"pr_ready", "paused", "failed"},
    "pr_ready": {"observing_ci", "paused", "failed"},
    "observing_ci": {"review_ready", "fixing", "paused", "failed"},
    "review_ready": {"fixing", "waiting_telegram", "paused", "failed"},
    "fixing": {"observing_ci", "paused", "failed"},
    "waiting_telegram": {"approved", "changes_requested", "rejected", "paused"},
    "approved": {"merging", "paused", "failed"},
    "changes_requested": {"fixing", "paused", "failed"},
    "rejected": set(),
    "merging": {"completed", "paused", "failed"},
    "completed": set(),
    "paused": set(),
    "failed": set(),
}


class AmbiguousOperation(RuntimeError):
    pass


class OperatorActions(Protocol):
    def perform(self, kind: str, item: Mapping[str, Any]) -> dict[str, Any]: ...

    def reconcile(
        self,
        kind: str,
        item: Mapping[str, Any],
        operation: Mapping[str, Any],
    ) -> dict[str, Any] | None: ...

    def observe_ci(self, item: Mapping[str, Any]) -> dict[str, Any]: ...


def enqueue_github_issue(
    *,
    workspace: Path,
    state_dir: Path,
    issue_url: str,
    github_client: GhCliGitHubClient | None = None,
) -> tuple[dict[str, Any], bool]:
    parsed = parse_github_issue_url(issue_url)
    project = resolve_known_project(workspace, parsed.repository)
    client = github_client or GhCliGitHubClient(cwd=project.project_root)
    snapshot = client.fetch_issue(project.repository, parsed.issue_number)
    queue = DurableOperatorQueue(state_dir)
    return queue.enqueue(
        idempotency_key=f"github_issue:{project.key}:{parsed.repository.lower()}#{parsed.issue_number}",
        source_url=parsed.url,
        source_revision=snapshot.updated_at,
        project_key=project.key,
        repository_identity=project.repository,
        metadata={
            "issue_number": parsed.issue_number,
            "issue_title": snapshot.title,
            "project_config": str(project.config_path),
            "project_root": str(project.project_root),
            "worker_backend": project.worker_backend,
            "merge_method": project.merge_method,
            "default_branch": project.base_branch or snapshot.default_branch,
            "required_checks": list(project.required_checks),
            "head_sha": "",
            "base_sha": "",
            "pr_number": 0,
            "pr_url": "",
            "decision": {},
            "review": {},
            "ci": {},
        },
    )


def record_operator_decision(
    *,
    queue: DurableOperatorQueue,
    item_id: str,
    decision: str,
    approved_head_sha: str,
    reason: str = "",
    actor_ref: str = "",
) -> dict[str, Any]:
    if decision not in {"approve", "request_changes", "reject"}:
        raise ValueError("decision must be approve, request_changes, or reject")
    if decision in {"request_changes", "reject"} and not reason.strip():
        raise ValueError(f"{decision} requires a reason")
    item = queue.get(item_id)
    if item is None:
        raise ValueError(f"operator task not found: {item_id}")
    if item["phase"] != "waiting_telegram" or item["status"] != "waiting":
        raise IllegalQueueTransition("operator task is not waiting for a Telegram decision")
    current_head = str(item["metadata"].get("head_sha", ""))
    if not approved_head_sha or approved_head_sha != current_head:
        raise IllegalQueueTransition("approval is stale because the PR head changed")
    metadata = dict(item["metadata"])
    existing = dict(metadata.get("decision", {}) or {})
    candidate = {
        "decision": decision,
        "head_sha": approved_head_sha,
        "reason": reason.strip(),
        "actor_ref": actor_ref,
        "decided_at": utc_now(),
    }
    if existing:
        if all(existing.get(key) == value for key, value in candidate.items() if key != "decided_at"):
            return item
        raise IllegalQueueTransition("operator task already has a different decision")
    metadata["decision"] = candidate
    return queue.transition(
        item_id,
        expected_phase="waiting_telegram",
        updates={"metadata": metadata},
        event="telegram_decision_recorded",
        payload={"decision": decision, "head_sha": approved_head_sha, "actor_ref": actor_ref},
    )


class OperatorExecutionStateMachine:
    def __init__(self, queue: DurableOperatorQueue, actions: OperatorActions):
        self.queue = queue
        self.actions = actions

    def tick(self) -> dict[str, Any] | None:
        item = self.queue.active_item()
        if item is None:
            item = self.queue.claim_next()
            return item
        phase = str(item["phase"])
        try:
            if phase == "hydrating":
                result = self._effect(item, "hydrate")
                return self._advance(item, "preflight", updates=self._metadata_updates(item, result))
            if phase == "preflight":
                result = self._effect(item, "preflight")
                if not result.get("ready"):
                    return self._pause(item, "policy_preflight_block", str(result.get("reason", "preflight failed")))
                return self._advance(item, "executing", updates=self._metadata_updates(item, result))
            if phase == "executing":
                result = self._effect(item, "execute")
                if result.get("status") != "succeeded":
                    retained = self.queue.transition(item["id"], expected_phase="executing",
                        updates={"execution_id": str(result.get("execution_id", item.get("execution_id", ""))),
                                 "metadata": self._merged_metadata(item, result)}, event="execution_delivery_blocked")
                    return self._pause(retained, "worker_task_failure", str(result.get("reason", "worker failed")))
                return self._advance(
                    item,
                    "pr_ready",
                    updates={
                        "execution_id": str(result.get("execution_id", item.get("execution_id", ""))),
                        "metadata": self._merged_metadata(item, result),
                    },
                )
            if phase == "pr_ready":
                result = self._effect(item, "ensure_pr")
                return self._advance(item, "observing_ci", updates=self._metadata_updates(item, result))
            if phase == "observing_ci":
                return self._observe_ci(item)
            if phase == "review_ready":
                result = self._effect(item, "review")
                metadata = self._merged_metadata(item, {"review": result})
                if result.get("acceptance_status") == "unknown":
                    return self._pause(item, "plan_review_inconclusive",
                                       str(result.get("summary", "plan evidence is unresolved")), metadata=metadata)
                blockers = list(result.get("blockers", []))
                deterministic = bool(blockers and result.get("deterministic"))
                if deterministic and int(item["attempt"]) <= MAX_FIX_ATTEMPTS:
                    return self._advance(
                        item,
                        "fixing",
                        updates={"metadata": metadata, "attempt": int(item["attempt"]) + 1},
                    )
                if (result.get("recommendation") != "approve" or blockers
                        or result.get("head_sha") != item["metadata"].get("head_sha")):
                    return self._pause(item, "delivery_review_unresolved",
                        str(result.get("reason", "independent review is unresolved")), metadata=metadata)
                return self._advance(
                    item,
                    "waiting_telegram",
                    status="waiting",
                    updates={"metadata": metadata},
                )
            if phase == "fixing":
                hydrated = self._effect(item, "hydrate")
                refreshed = self.queue.transition(
                    item["id"],
                    expected_phase="fixing",
                    updates=self._metadata_updates(item, hydrated),
                    event="fix_source_rehydrated",
                    payload={"source_revision": hydrated.get("source_revision", "")},
                )
                preflight = self._effect(refreshed, "preflight")
                if not preflight.get("ready"):
                    return self._pause(
                        refreshed,
                        "policy_preflight_block",
                        str(preflight.get("reason", "preflight failed")),
                    )
                refreshed = self.queue.transition(
                    item["id"],
                    expected_phase="fixing",
                    updates=self._metadata_updates(refreshed, preflight),
                    event="fix_preflight_passed",
                    payload={"attempt": item["attempt"]},
                )
                result = self._effect(refreshed, "fix")
                if result.get("status") == "blocked":
                    return self._pause(refreshed, "correction_evidence_pending", str(result.get("reason", "correction blocked")),
                        metadata=self._merged_metadata(refreshed, {"correction": {**result, "acceptance_status": "unknown"}}))
                if result.get("status") != "succeeded":
                    return self._pause(refreshed, "bounded_fix_failed", str(result.get("reason", "fix failed")))
                return self._advance(
                    refreshed,
                    "observing_ci",
                    updates={
                        "metadata": self._merged_metadata(
                            refreshed,
                            {**result, "decision": {}, "approval": {}, "review": {}, "ci": {}},
                        ),
                    },
                )
            if phase == "waiting_telegram":
                if not dict(item["metadata"].get("approval", {}) or {}):
                    card = self._effect(item, "send_approval")
                    return self.queue.transition(
                        item["id"],
                        expected_phase="waiting_telegram",
                        updates={"metadata": self._merged_metadata(item, {"approval": card})},
                        event="approval_card_ready",
                        payload={"head_sha": item["metadata"].get("head_sha", "")},
                    )
                return self._apply_decision(item)
            if phase == "approved":
                return self._advance(item, "merging")
            if phase == "changes_requested":
                if int(item["attempt"]) > MAX_FIX_ATTEMPTS:
                    return self._pause(item, "fix_attempts_exhausted", "maximum fix attempts exhausted")
                return self._advance(
                    item,
                    "fixing",
                    status="active",
                    updates={"attempt": int(item["attempt"]) + 1},
                )
            if phase == "merging":
                merge = self._effect(item, "merge")
                merged_item = self.queue.transition(
                    item["id"],
                    expected_phase="merging",
                    updates={"metadata": self._merged_metadata(item, {"merge": merge})},
                    event="merge_confirmed",
                    payload={"merge_commit_sha": merge.get("merge_commit_sha", "")},
                )
                writeback = self._effect(merged_item, "writeback")
                return self._advance(
                    merged_item,
                    "completed",
                    status="completed",
                    updates={"metadata": self._merged_metadata(merged_item, {"merge": merge, "writeback": writeback})},
                )
            return item
        except AmbiguousOperation as exc:
            return self._pause(item, "ambiguous_external_operation", str(exc))
        except (TaskInputError, SkillError) as exc:
            return self._pause(item, "candidate_review_setup_block", exc.code,
                               metadata={**dict(item["metadata"]), "pause_reason": exc.code})
        except QueueCorruptionError:
            raise
        except Exception as exc:
            retries = int(item.get("retry_count", 0)) + 1
            if retries < MAX_RETRIES:
                return self.queue.transition(
                    item["id"],
                    expected_phase=phase,
                    updates={"retry_count": retries, "last_failure_class": exc.__class__.__name__},
                    event="operator_retry_scheduled",
                    payload={"failure_class": exc.__class__.__name__, "retry_count": retries},
                )
            return self._pause(item, "internal_operator_loop_failure", f"{exc.__class__.__name__}: {exc}")

    def _observe_ci(self, item: dict[str, Any]) -> dict[str, Any]:
        result = self.actions.observe_ci(item)
        metadata = self._merged_metadata(item, {"ci": result})
        observed_head = str(result.get("head_sha", ""))
        current_head = str(item["metadata"].get("head_sha", ""))
        if current_head and observed_head and observed_head != current_head:
            metadata["head_sha"] = observed_head
            metadata["review"] = {}
            metadata["decision"] = {}
        status = str(result.get("status", "unknown"))
        if status == "passed" and observed_head == str(metadata.get("head_sha", observed_head)):
            return self._advance(item, "review_ready", updates={"metadata": metadata})
        if status == "failed" and bool(result.get("deterministic")) and int(item["attempt"]) <= MAX_FIX_ATTEMPTS:
            return self._advance(
                item,
                "fixing",
                updates={"metadata": metadata, "attempt": int(item["attempt"]) + 1},
            )
        if status in {"failed", "stale", "unknown"}:
            return self._pause(item, f"ci_{status}", str(result.get("reason", status)), metadata=metadata)
        return self.queue.transition(
            item["id"],
            expected_phase="observing_ci",
            updates={"metadata": metadata},
            event="ci_observed",
            payload={"status": status, "head_sha": observed_head},
        )

    def _apply_decision(self, item: dict[str, Any]) -> dict[str, Any]:
        decision = dict(item["metadata"].get("decision", {}) or {})
        if not decision:
            return item
        if decision.get("head_sha") != item["metadata"].get("head_sha"):
            return self._pause(item, "stale_pr_head", "Telegram decision no longer matches the PR head")
        value = decision.get("decision")
        if value == "approve":
            return self._advance(item, "approved", status="active")
        if value == "request_changes":
            return self._advance(item, "changes_requested", status="active")
        if value == "reject":
            self._effect(item, "reject")
            return self._advance(item, "rejected", status="rejected")
        return self._pause(item, "invalid_decision", f"unknown decision: {value}")

    def _effect(self, item: Mapping[str, Any], kind: str) -> dict[str, Any]:
        head = str(dict(item.get("metadata", {})).get("head_sha", ""))
        operation_id = f"{item['id']}:{item['attempt']}:{kind}:{head or 'none'}"
        if kind == "preflight" and item["metadata"].get("candidate_configuration_resumed") is True:
            operation_id += ":configured"
        operation, created = self.queue.record_operation_intent(
            str(item["id"]), operation_id, kind=kind, payload={"phase": item["phase"], "head_sha": head}
        )
        if operation["state"] == "result":
            return dict(operation["result"])
        if not created:
            reconciled = self.actions.reconcile(kind, item, operation)
            if reconciled is None:
                raise AmbiguousOperation(f"cannot prove outcome for {kind} ({operation_id})")
            result = reconciled
        else:
            result = self.actions.perform(kind, item)
        self.queue.record_operation_result(str(item["id"]), operation_id, result=result)
        return dict(result)

    def _advance(
        self,
        item: Mapping[str, Any],
        phase: str,
        *,
        status: str | None = None,
        updates: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = str(item["phase"])
        if phase not in LEGAL_PHASE_TRANSITIONS.get(current, set()):
            raise IllegalQueueTransition(f"illegal operator phase transition: {current} -> {phase}")
        return self.queue.transition(
            str(item["id"]),
            expected_phase=current,
            phase=phase,
            status=status,
            updates=updates,
            event="operator_phase_changed",
            payload={"previous_phase": current, "phase": phase},
        )

    def _pause(
        self,
        item: Mapping[str, Any],
        failure_class: str,
        reason: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        paused_metadata = dict(item["metadata"] if metadata is None else metadata)
        paused_metadata["pause_reason"] = reason
        return self._advance(
            item,
            "paused",
            status="paused",
            updates={
                "last_failure_class": failure_class,
                "metadata": paused_metadata,
            },
        )

    @staticmethod
    def _merged_metadata(item: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
        metadata = dict(item.get("metadata", {}))
        for key, value in result.items():
            if key not in {"status", "ready", "reason", "execution_id"}:
                metadata[key] = value
        return metadata

    def _metadata_updates(self, item: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {"metadata": self._merged_metadata(item, result)}
        if result.get("source_revision"):
            updates["source_revision"] = str(result["source_revision"])
        return updates


class OperatorService:
    def __init__(
        self,
        *,
        workspace: Path,
        state_dir: Path,
        actions: OperatorActions | None = None,
        candidate_reviews=None,
        codex_review_admission=None,
    ):
        self.workspace = Path(workspace).resolve()
        self.queue = DurableOperatorQueue(state_dir)
        self.actions = actions or LocalOperatorActions(workspace=self.workspace, state_dir=state_dir,
            candidate_reviews=candidate_reviews, codex_review_admission=codex_review_admission)
        self.machine = OperatorExecutionStateMachine(self.queue, self.actions)

    def recover(self) -> dict[str, Any] | None:
        item = self.queue.active_item()
        if item is None:
            return None
        phase = str(item["phase"])
        if (phase == "paused" and item["last_failure_class"] == "policy_preflight_block"
                and item["metadata"].get("pause_reason") == "operator_candidate_configuration_required"
                and not item.get("execution_id")
                and not any(op.get("kind") == "execute" for op in item["operations"].values())
                and isinstance(self.actions, LocalOperatorActions)
                and item["id"] in self.actions.candidate_reviews):
            # Explicit startup now supplies this exact task's missing config.
            # Only the known pre-Builder configuration pause is resumable here.
            configuration_for(self.actions.candidate_reviews, item)
            return self.queue.transition(item["id"], expected_phase="paused", phase="preflight", status="active",
                updates={"retry_count": 0, "last_failure_class": "",
                         "metadata": {**item["metadata"], "pause_reason": "", "candidate_configuration_resumed": True}},
                event="candidate_configuration_resumed", payload={"previous_phase": "paused"})
        if phase not in LEGAL_PHASE_TRANSITIONS:
            return self.queue.transition(
                item["id"],
                status="paused",
                phase="paused",
                updates={"last_failure_class": "ambiguous_recovery_state"},
                event="recovery_paused",
                payload={"previous_phase": phase},
            )
        self.queue.transition(
            item["id"],
            expected_phase=phase,
            event="recovery_classified",
            payload={"classification": "resumable", "phase": phase},
        )
        return self.queue.get(item["id"])

    def tick(self) -> dict[str, Any] | None:
        return self.machine.tick()


class LocalOperatorActions:
    def __init__(self, *, workspace: Path, state_dir: Path, codex_review_admission=None, candidate_reviews=None):
        self.workspace = Path(workspace).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.review_runtime_kwargs = review_worker_kwargs(codex_review_admission)
        self.candidate_reviews = dict(candidate_reviews or {})

    def perform(self, kind: str, item: Mapping[str, Any]) -> dict[str, Any]:
        method = getattr(self, f"_{kind}", None)
        if method is None:
            raise ValueError(f"unsupported operator action: {kind}")
        return dict(method(item))

    def reconcile(
        self,
        kind: str,
        item: Mapping[str, Any],
        operation: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        metadata = dict(item["metadata"])
        client = self._client(item)
        if kind == "execute":
            # Queue result publication can fail before execution_id is saved.
            # Derive only the existing source identity; never repeat intake or
            # dispatch to discover whether an unknown Builder actually ran.
            from goodteam_os.execution.idempotency import execution_id_from_task, task_id_from_github_issue
            from goodteam_os.execution.ledger import ExecutionLedger
            parsed = parse_github_issue_url(str(item["source_url"]))
            execution_id = execution_id_from_task(task_id_from_github_issue(
                str(item["repository_identity"]), parsed.issue_number))
            if item.get("execution_id") and item["execution_id"] != execution_id:
                raise AmbiguousOperation("operator execution identity changed")
            run = ExecutionLedger(self._project(item)).read_run(execution_id)
            if run is not None and run.status.value == "succeeded":
                return self._executed_candidate(item, run)
            return None
        if kind in {"ensure_pr", "send_approval"} and metadata.get("pr_number"):
            if kind == "ensure_pr":
                pr = client.fetch_pr(str(item["repository_identity"]), int(metadata["pr_number"]))
                return _pr_result(pr)
            session_id = str(dict(metadata.get("approval", {}) or {}).get("session_id", ""))
            if session_id and TelegramReviewSessionStore(self.workspace).get(session_id):
                return dict(metadata["approval"])
        if kind == "ensure_pr" and item.get("execution_id"):
            replay = ExecutionAPI(self._project(item), github_client=client).replay(str(item["execution_id"]))
            run = dict(replay.get("run", {}) or {})
            branch = str(run.get("branch_name", ""))
            if branch:
                pr = client.find_open_pr_by_head(str(item["repository_identity"]), branch)
                if pr is not None:
                    return _pr_result(pr)
        if kind == "send_approval":
            session = TelegramReviewSessionStore(self.workspace).find_for_operator_task(
                str(item["id"]), str(metadata.get("head_sha", ""))
            )
            if session is not None and int(session.get("message_id", 0) or 0) > 0:
                return {
                    "status": "reconciled",
                    "session_id": str(session.get("session_id", "")),
                    "message_id": str(session.get("message_id", "")),
                }
            if session is not None:
                return None
        if kind == "merge" and metadata.get("pr_number"):
            pr = client.fetch_pr(str(item["repository_identity"]), int(metadata["pr_number"]))
            if pr.get("merged"):
                return {"merged": True, "merge_commit_sha": pr.get("merge_commit_sha", "")}
        if kind == "writeback" and metadata.get("merge", {}).get("merge_commit_sha"):
            return self._writeback(item)
        if kind == "reject":
            return self._reject(item)
        if kind == "review":
            return self._review(item)
        return None

    def observe_ci(self, item: Mapping[str, Any]) -> dict[str, Any]:
        metadata = dict(item["metadata"])
        repo = str(item["repository_identity"])
        pr = self._client(item).fetch_pr(repo, int(metadata["pr_number"]))
        head_sha = str(pr.get("head_sha", ""))
        checks = self._client(item).fetch_checks(repo, head_sha)
        runs = [run for run in checks.get("check_runs", []) if isinstance(run, dict)]
        required = _required_check_names(metadata.get("required_checks", []))
        if not required:
            return {
                "status": "missing",
                "head_sha": head_sha,
                "checks": [],
                "missing_checks": [],
                "reason": "no required checks are configured or protected",
            }
        selected = _latest_required_runs(runs, required)
        evidence = [
            {
                "id": run.get("id"),
                "name": run.get("name", ""),
                "status": run.get("status", ""),
                "conclusion": run.get("conclusion", ""),
                "head_sha": run.get("head_sha", head_sha),
            }
            for run in selected.values()
        ]
        missing = [name for name in required if name not in selected]
        if missing:
            return {
                "status": "missing",
                "head_sha": head_sha,
                "checks": evidence,
                "required_checks": required,
                "missing_checks": missing,
                "reason": f"required checks have not appeared: {', '.join(missing)}",
            }
        if any(str(run.get("head_sha", head_sha)) != head_sha for run in selected.values()):
            return {"status": "stale", "head_sha": head_sha, "checks": evidence, "reason": "check head mismatch"}
        if any(str(run.get("status", "")).lower() != "completed" for run in selected.values()):
            return {
                "status": "pending",
                "head_sha": head_sha,
                "checks": evidence,
                "required_checks": required,
            }
        failures = [
            run
            for run in selected.values()
            if str(run.get("conclusion", "")).lower() not in {"success", "neutral", "skipped"}
        ]
        if failures:
            conclusions = {str(run.get("conclusion", "")).lower() for run in failures}
            deterministic = conclusions == {"failure"}
            return {
                "status": "failed",
                "head_sha": head_sha,
                "checks": evidence,
                "required_checks": required,
                "deterministic": deterministic,
                "failure_class": "ci_code_failure" if deterministic else "ci_infrastructure_failure",
                "reason": ", ".join(
                    f"{run.get('name', 'check')}={run.get('conclusion', 'unknown')}" for run in failures
                ),
            }
        return {
            "status": "passed",
            "head_sha": head_sha,
            "checks": evidence,
            "required_checks": required,
        }

    def _hydrate(self, item: Mapping[str, Any]) -> dict[str, Any]:
        parsed = parse_github_issue_url(str(item["source_url"]))
        snapshot = self._client(item).fetch_issue(str(item["repository_identity"]), parsed.issue_number)
        task = execution_task_from_github_issue(snapshot, clock=utc_now)
        if item.get("execution_id"):
            frozen = self._frozen_snapshot(item)
            if _contract_requirements(task.task_contract) != _contract_requirements(frozen.task.task_contract):
                raise AmbiguousOperation("source contract changed; explicitly reconcile the plan before fixing")
            source = frozen.task.task_contract.source_intent
            return {"source_revision": frozen.to_dict()["source_revision"],
                    "observed_source_revision": snapshot.updated_at, "issue_title": source["title"],
                    "repository_default_branch": frozen.task.task_contract.metadata.get("default_branch", ""),
                    "task_contract": frozen.task.task_contract.to_dict(),
                    "provenance_references": [source["url"], *source.get("references", [])]}
        return {
            "source_revision": snapshot.updated_at,
            "issue_title": snapshot.title,
            "repository_default_branch": snapshot.default_branch,
            "task_contract": task.task_contract.to_dict() if task.task_contract else {},
            "provenance_references": [snapshot.url, *list(snapshot.references or [])],
        }

    def _frozen_snapshot(self, item):
        store = store_for_project(self._project(item), existing_only=True)
        snapshot = store.load(str(item["execution_id"])) if store else None
        if snapshot is None:
            raise AmbiguousOperation("original task contract snapshot is unavailable")
        metadata = dict(item["metadata"])
        if (metadata.get("task_contract") != snapshot.task.task_contract.to_dict()
                or metadata.get("task_snapshot", snapshot.reference) != snapshot.reference):
            raise AmbiguousOperation("task contract no longer matches the original execution snapshot")
        return snapshot

    def _preflight(self, item: Mapping[str, Any]) -> dict[str, Any]:
        metadata = dict(item["metadata"])
        config = Path(str(metadata["project_config"]))
        target = load_project_target(config)
        backend = str(metadata.get("worker_backend", "") or dict(target.get("worker_backend", {})).get("mode", "codex"))
        report = worker_backend_doctor(backend)
        required_checks = _required_check_names(metadata.get("required_checks", target.get("required_checks", [])))
        source = "project_config"
        if not required_checks:
            required_checks = self._client(item).fetch_required_checks(
                str(item["repository_identity"]),
                str(metadata.get("default_branch", "main")),
            )
            source = "branch_protection"
        checks_ready = bool(required_checks)
        backend_ready = bool(report.get("ready"))
        reason = ""
        if not backend_ready:
            reason = str(report.get("hint", "worker backend is not ready"))
        elif not checks_ready:
            reason = "no required checks configured and the base branch has no protected check contexts"
        elif backend_ready:
            try:
                configuration_for(self.candidate_reviews, item)
            except TaskInputError as exc:
                return {"ready": False, "reason": exc.code}
        return {
            "ready": backend_ready and checks_ready,
            "worker_backend": backend,
            "preflight": _redacted_preflight(report),
            "required_checks": required_checks,
            "required_checks_source": source,
            "reason": reason,
        }

    def _execute(self, item: Mapping[str, Any]) -> dict[str, Any]:
        config = configuration_for(self.candidate_reviews, item)
        parsed = parse_github_issue_url(str(item["source_url"]))
        metadata = dict(item["metadata"])
        orchestrator = ExecutionOrchestrator(self._project(item), self._client(item), sandbox_enabled=True, sandbox_mode="worktree")
        run = orchestrator.intake_github_issue(
            repo=str(item["repository_identity"]),
            issue_number=parsed.issue_number,
            adapter=str(metadata.get("worker_backend", "codex")),
        )
        snapshot = self._frozen_snapshot({**item, "execution_id": run.execution_id})
        check_admission_identity(self.review_runtime_kwargs.get("codex_review_admission"), snapshot,
                                 item, self._project(item), config)
        freeze_configuration(self._automatic_review(item), snapshot, item, config,
                             create=run.status.value == "queued")
        completed = orchestrator.run_execution(run.execution_id)
        if completed.status.value == "succeeded":
            return self._executed_candidate(item, completed)
        return {
            "status": "succeeded" if completed.status.value == "succeeded" else "failed",
            "execution_id": run.execution_id,
            "task_snapshot": snapshot.reference,
            "worker_job_id": run.worker_job_id,
            "branch_name": run.branch_name,
            "reason": "" if completed.status.value == "succeeded" else completed.status.value,
        }

    def _executed_candidate(self, item, run):
        config = configuration_for(self.candidate_reviews, item)
        snapshot = self._frozen_snapshot({**item, "execution_id": run.execution_id})
        check_admission_identity(self.review_runtime_kwargs.get("codex_review_admission"), snapshot,
                                 item, self._project(item), config)
        prepared = prepare_candidate(self._automatic_review(item), snapshot, item, config)
        return {"status": "succeeded", "execution_id": run.execution_id, "task_snapshot": snapshot.reference,
                "worker_job_id": run.worker_job_id, "branch_name": run.branch_name, **prepared}

    def _ensure_pr(self, item: Mapping[str, Any]) -> dict[str, Any]:
        execution_id = str(item["execution_id"])
        head = self._publish_sandbox(item, execution_id)
        binding = PullRequestBindingService(
            self._project(item),
            self._client(item),
            base_ref=str(dict(item["metadata"]).get("default_branch", "main")),
        ).create_for_published_candidate(execution_id, head_sha=head)
        pr = self._client(item).fetch_pr(binding.repo, binding.pr_number or 0)
        return _pr_result(pr)

    def _automatic_review(self, item):
        return AutomaticCandidateReviewService(self._project(item), **self.review_runtime_kwargs)

    def _current_plan_receipt(self, item, service, snapshot, receipt):
        current = service.validation._current(snapshot)
        if current is None or current[1]["candidate"]["head_sha"] != item["metadata"].get("head_sha"):
            raise AmbiguousOperation("current plan check does not match the PR candidate")
        plan, result, _review = current
        probe_candidate(service.validation.source_root(snapshot, plan["request"]), result["candidate"]["head_sha"])
        service.verify_receipt(snapshot, plan, result, receipt)
        return current

    def _review(self, item: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = self._frozen_snapshot(item)
        metadata = dict(item["metadata"])
        head_sha = str(metadata.get("head_sha", ""))
        if metadata.get("pr_number"):
            from goodteam_os.execution.github import GitHubExecutionBindingStore
            binding = GitHubExecutionBindingStore(self._project(item)).read_binding(snapshot.execution_id)
            if (binding is None or binding.pr_number != int(metadata["pr_number"])
                    or binding.repo.casefold() != str(item["repository_identity"]).casefold()):
                return {"head_sha": head_sha, "recommendation": "escalate", "deterministic": False,
                        "blockers": ["delivery_pr_binding_mismatch"], "reason": "delivery_pr_binding_mismatch",
                        "acceptance_status": "unknown", "summary": "delivery_pr_binding_mismatch"}
            pr = self._client(item).fetch_pr(binding.repo, binding.pr_number)
            if str(pr.get("head_sha", "")) != head_sha:
                return {"head_sha": head_sha, "recommendation": "escalate", "deterministic": False,
                        "blockers": ["delivery_pr_head_changed"], "reason": "delivery_pr_head_changed",
                        "acceptance_status": "unknown", "summary": "delivery_pr_head_changed"}
        service = self._automatic_review(item)
        try:
            if str(item.get("id", "")) in self.candidate_reviews:
                start_candidate_review(service, snapshot, item, configuration_for(self.candidate_reviews, item))
            if service.configuration(snapshot.execution_id) is None:
                raise TaskInputError("automatic_review_configuration_required")
            # The existing producer owns registered identity, original criteria,
            # check receipt, exact Git target and the independent Reviewer result.
            current = service.validation._current(snapshot)
            if current is None or current[1]["candidate"]["head_sha"] != head_sha:
                raise TaskInputError("operator_candidate_check_required")
            # Budgets govern new work, not the lifetime of a completed receipt.
            # Revalidate current protected machine evidence before admitting any
            # Reviewer; a human attestation is not a substitute for that result.
            receipt = current[2]
            if not isinstance(receipt, dict) or receipt.get("schema_version") != MACHINE_SCHEMA:
                receipt = service.review_current(snapshot.execution_id)
            self._current_plan_receipt(item, service, snapshot, receipt)
            detail = ReviewResultStore(service.workers, receipt["reviewer"]).detail(
                expected_task_context=receipt["provenance"]["task_skill_context"], include_report=True)
        except (TaskInputError, SkillError) as exc:
            return {"head_sha": head_sha, "snapshot": snapshot.reference, "recommendation": "escalate",
                    "blockers": [exc.code], "summary": exc.code, "deterministic": False,
                    "acceptance_status": "unknown"}
        decision = receipt["decision"]
        return {"head_sha": head_sha, "snapshot": snapshot.reference, "machine_review": receipt,
                "reviewer_identity": receipt["reviewer"], "builder_identity": metadata.get("worker_job_id", ""),
                "recommendation": "escalate" if decision == "inconclusive" else decision,
                "blockers": [] if decision == "approve" else [receipt["reason"]],
                "summary": receipt["reason"], "deterministic": decision == "request_changes",
                "criterion_checks": detail["report"]["checks"], "findings": detail["report"]["findings"],
                "evidence": detail["report"]["evidence"],
                "acceptance_status": "unknown" if decision == "inconclusive" else (
                    "accepted_by_review_policy" if decision == "approve" else "changes_requested"),
                "semantic_verification": "judgment_not_independently_verified"}

    def _send_approval(self, item: Mapping[str, Any]) -> dict[str, Any]:
        metadata = dict(item["metadata"])
        from goodteam_os.execution.candidate_delivery import CandidateDeliveryService
        self._frozen_snapshot(item)
        if CandidateDeliveryService(self._project(item)).review_for_head(
                str(item["execution_id"]), str(metadata.get("head_sha", ""))).get("recommendation") != "approve":
            raise AmbiguousOperation("current candidate lacks passed checks and independent review")
        head_sha = str(metadata.get("head_sha", ""))
        cards = self.state_dir / "operator" / "cards"
        cards.mkdir(parents=True, exist_ok=True)
        artifact = cards / f"{item['id']}-{head_sha}.md"
        artifact.write_text(_approval_artifact(item), encoding="utf-8")
        gate = f"operator-{head_sha[:12]}"
        result = send_approval_notification(
            project_key=str(item["project_key"]),
            run_id=str(item["id"]),
            gate=gate,
            artifact_path=str(artifact),
            summary=(
                f"Issue {item['source_url']} · PR {metadata.get('pr_url', '')} · exact head {head_sha} · "
                f"attempt {item['attempt']}"
            ),
            workspace=self.workspace,
            session_metadata={
                "operator_task_id": item["id"],
                "operator_head_sha": head_sha,
                "operator_state_dir": str(self.state_dir),
                "source_issue": item["source_url"],
                "pr_url": metadata.get("pr_url", ""),
                "attempt": item["attempt"],
            },
        )
        session_id = str(result.get("session_id", ""))
        if session_id:
            store = TelegramReviewSessionStore(self.workspace)
            session = store.get(session_id) or {}
            session.update({"operator_task_id": item["id"], "operator_head_sha": head_sha})
            store.save(session_id, session)
        return result

    def _fix(self, item: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = self._frozen_snapshot(item)
        service = self._automatic_review(item)
        if service.configuration(snapshot.execution_id) is None:
            return {"status": "blocked", "reason": "automatic_review_configuration_required"}
        # Do not convert a human suggestion into a machine request_changes or a
        # new runtime grant. Only the already-bound producer/consumer may fix.
        current = service.validation._current(snapshot)
        if current is None or current[2] is None or current[2].get("decision") != "request_changes":
            return {"status": "blocked", "reason": "bound_plan_findings_required"}
        self._current_plan_receipt(item, service, snapshot, current[2])
        result = service.advance(snapshot.execution_id)
        if result.get("status") != "reviewed":
            return {"status": "blocked", "reason": result.get("reason", "correction_evidence_pending"),
                    "correction": result}
        plan, checked, review = service.validation._current(snapshot)
        head = checked["candidate"]["head_sha"]
        if checked["observation"]["status"] != "passed" or review.get("decision") != "approve":
            raise AmbiguousOperation("correction lacks a passed check and plan review")
        service.verify_receipt(snapshot, plan, checked, review)
        workspace = service.validation.source_root(snapshot, plan["request"])
        probe_candidate(workspace, head)
        self._publish_sandbox(item, snapshot.execution_id, worktree=workspace)
        pr = self._client(item).fetch_pr(str(item["repository_identity"]), int(item["metadata"]["pr_number"]))
        if pr.get("head_sha") != head:
            raise AmbiguousOperation("published correction does not match the checked candidate")
        return {"status": "succeeded", **_pr_result(pr), "correction": result}

    def _merge(self, item: Mapping[str, Any]) -> dict[str, Any]:
        metadata = dict(item["metadata"])
        repo = str(item["repository_identity"])
        pr_number = int(metadata["pr_number"])
        approved_head = str(dict(metadata.get("decision", {}) or {}).get("head_sha", ""))
        pr = self._client(item).fetch_pr(repo, pr_number)
        if str(pr.get("head_sha", "")) != approved_head:
            raise AmbiguousOperation("PR head moved after Telegram approval")
        ci = self.observe_ci(item)
        if ci.get("status") != "passed" or ci.get("head_sha") != approved_head:
            raise AmbiguousOperation("required checks are not successful for the approved head")
        review = dict(metadata.get("review", {}) or {})
        snapshot = self._frozen_snapshot(item)
        service = self._automatic_review(item)
        receipt = review.get("machine_review")
        if not isinstance(receipt, dict):
            raise AmbiguousOperation("independent plan review receipt is missing")
        try:
            self._current_plan_receipt(item, service, snapshot, receipt)
        except (TaskInputError, SkillError) as exc:
            raise AmbiguousOperation("independent plan review is unavailable: " + exc.code) from exc
        if (review.get("head_sha") != approved_head or review.get("snapshot") != snapshot.reference
                or review.get("blockers") or review.get("recommendation") != "approve"
                or receipt.get("decision") != "approve"
                or receipt.get("candidate", {}).get("head_sha") != approved_head):
            raise AmbiguousOperation("independent plan review is stale, incomplete or still has blockers")
        result = self._client(item).merge_pr(
            repo,
            pr_number,
            head_sha=approved_head,
            method=str(metadata.get("merge_method", "squash")),
        )
        if not result.get("merged"):
            raise RuntimeError(str(result.get("message", "GitHub did not confirm the merge")))
        return {"merged": True, "merge_commit_sha": result.get("sha", ""), "head_sha": approved_head}

    def _writeback(self, item: Mapping[str, Any]) -> dict[str, Any]:
        metadata = dict(item["metadata"])
        parsed = parse_github_issue_url(str(item["source_url"]))
        merge = dict(metadata.get("merge", {}) or {})
        marker = f"<!-- goodteam-operator:{item['id']}:completed -->"
        comment = self._client(item).issue_comment_once(
            str(item["repository_identity"]),
            parsed.issue_number,
            marker=marker,
            body=(
                f"GoodTeam completed this task via {metadata.get('pr_url', 'the linked PR')} at exact head "
                f"`{metadata.get('head_sha', '')}`. Merge commit: `{merge.get('merge_commit_sha', '')}`."
            ),
        )
        self._client(item).close_issue(str(item["repository_identity"]), parsed.issue_number)
        return {"comment_id": comment.get("id"), "comment_reused": comment.get("reused", False), "issue_closed": True}

    def _reject(self, item: Mapping[str, Any]) -> dict[str, Any]:
        metadata = dict(item["metadata"])
        decision = dict(metadata.get("decision", {}) or {})
        if metadata.get("pr_number"):
            self._client(item).issue_comment_once(
                str(item["repository_identity"]),
                int(metadata["pr_number"]),
                marker=f"<!-- goodteam-operator:{item['id']}:rejected -->",
                body=f"GoodTeam operator task rejected. Reason: {decision.get('reason', 'not provided')}",
            )
        return {"rejected": True}

    def _publish_sandbox(self, item: Mapping[str, Any], execution_id: str, *, worktree: Path | None = None) -> str:
        metadata = dict(item["metadata"])
        from goodteam_os.execution.candidate_validation import CandidateValidationService
        from goodteam_os.execution.idempotency import branch_name_from_execution_id
        from goodteam_os.memory_storage import role_lock
        validation = CandidateValidationService(self._project(item))
        snapshot = validation._snapshot(execution_id)
        with role_lock(validation._root(execution_id), create=False):
            current = validation._current(snapshot)
            if current is None or current[1]["observation"]["status"] not in {"passed", "failed"}:
                raise AmbiguousOperation("current candidate lacks a completed check receipt")
            selected = validation.source_root(snapshot, current[0]["request"])
            head = current[1]["candidate"]["head_sha"]
            probe_candidate(selected, head)
            if worktree is not None and Path(worktree).resolve() != selected:
                raise AmbiguousOperation("publication workspace differs from the checked candidate")
            worktree = selected
        branch = str(metadata.get("branch_name", ""))
        if (branch != branch_name_from_execution_id(execution_id)
                or str(item["repository_identity"]).casefold() != snapshot.task.repo.casefold()):
            raise AmbiguousOperation("validated candidate publication target is unavailable")
        subprocess.run(
            ["git", "push", "origin", f"{head}:refs/heads/{branch}"],
            cwd=worktree,
            capture_output=True,
            check=True,
            text=True,
        )
        return head

    def _client(self, item: Mapping[str, Any]) -> GhCliGitHubClient:
        return GhCliGitHubClient(
            cwd=self._project(item),
            base_ref=str(dict(item["metadata"]).get("default_branch", "")),
        )

    @staticmethod
    def _project(item: Mapping[str, Any]) -> Path:
        return Path(str(dict(item["metadata"])["project_root"])).resolve()


def _required_check_names(value: Any) -> list[str]:
    values = list(value.values()) if isinstance(value, dict) else ([value] if isinstance(value, str) else value)
    if not isinstance(values, (list, tuple)):
        return []
    return sorted({str(item).strip() for item in values if str(item).strip()})


def _latest_required_runs(runs: list[dict[str, Any]], required: list[str]) -> dict[str, dict[str, Any]]:
    wanted = set(required)
    selected: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, run in enumerate(runs):
        name = str(run.get("name", "")).strip()
        if name not in wanted:
            continue
        try:
            rank = int(run.get("id", index) or index)
        except (TypeError, ValueError):
            rank = index
        if name not in selected or rank >= selected[name][0]:
            selected[name] = (rank, run)
    return {name: selected[name][1] for name in required if name in selected}


def _pr_result(pr: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pr_number": int(pr.get("number", 0) or 0),
        "pr_url": str(pr.get("url", "")),
        "head_sha": str(pr.get("head_sha", "") or pr.get("headRefOid", "")),
        "base_sha": str(pr.get("base_sha", "") or pr.get("baseRefOid", "")),
        "branch_name": str(pr.get("headRefName", "")),
        "base_branch": str(pr.get("baseRefName", "")),
    }


def _redacted_preflight(report: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"mode", "kind", "ready", "available", "auth_present", "hint", "version"}
    return {key: value for key, value in report.items() if key in allowed}


def _review_summary(workbench: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "execution_id": workbench.get("execution_id", ""),
        "status": workbench.get("status", ""),
        "warnings": workbench.get("warnings", []),
    }


def _approval_artifact(item: Mapping[str, Any]) -> str:
    metadata = dict(item["metadata"])
    review = dict(metadata.get("review", {}) or {})
    ci = dict(metadata.get("ci", {}) or {})
    return "\n".join(
        [
            "# GoodTeam exact-head approval",
            "",
            f"- Project: {item['project_key']}",
            f"- Task: {item['id']}",
            f"- Source Issue: {item['source_url']}",
            f"- PR: {metadata.get('pr_url', '')}",
            f"- Exact head: {metadata.get('head_sha', '')}",
            f"- Attempt: {item['attempt']}",
            f"- CI: {ci.get('status', 'unknown')}",
            f"- Review: {review.get('recommendation', 'unknown')}",
            f"- Blockers: {len(review.get('blockers', []))}",
            f"- Recommendation: {review.get('recommendation', 'escalate')}",
            "",
        ]
    )


def _structured_review_result(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("verdict") in {"approve", "request_changes", "escalate"}:
            candidates.append(value)
    return candidates[-1] if candidates else None


def _contract_requirements(contract):
    """Compare task requirements, not issue labels, comments or fetch revisions."""
    value = contract.to_dict()
    source = value.pop("source_intent")
    value.pop("metadata")
    return {**value, "source_identity": {key: source.get(key) for key in ("source_type", "repo", "issue_number", "url")}}
