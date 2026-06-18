import json
import hmac
import os
import re
import shutil
import subprocess
import threading
from dataclasses import asdict
from datetime import datetime, UTC
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen
from uuid import uuid4

from goodteam_os import context as project_context
from goodteam_os.debate import default_debate_dispatch, run_gate_debate
from goodteam_os.dispatch import (
    dispatch_tracks,
    ensure_dispatch_project_root_is_safe,
    parse_dispatch_track_spec,
    parse_dispatch_verify_spec,
)
from goodteam_os.graph import (
    accept_refinement,
    advance_approved_workflow,
    artifact_mirror_root,
    discard_refinement,
    extract_open_questions,
    generate_rfc,
    generate_prd,
    refine_artifact,
    render_downstream_artifact_markdown,
    resolve_project_runtime_root,
    run_architecture_until_gate,
    run_adoption_until_next_gate,
    run_fix_until_next_gate,
    run_until_next_gate,
    should_generate_artifact_with_worker,
    sync_project_stage_tasks,
)
from goodteam_os.artifacts import ArtifactStore
from goodteam_os.models import list_model_profiles, resolve_project_model_profile
from goodteam_os.nodes.review_gate import gate_next_node
from goodteam_os.projects import DELIVERY_MODES, check_project_target, load_project_target, update_worker_backend_config
from goodteam_os.resources import (
    ResourceCapability,
    ResourceKind,
    ResourceScope,
    register_markdown_artifact,
    resource_to_dict,
)
from goodteam_os.roles import approval_artifact_map, list_role_definitions, workflow_steps_for_source
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.messages import read_messages
from goodteam_os.staging import (
    deployment_backend_config,
    delivery_contract_report,
    run_staging_backend,
)
from goodteam_os.state import AgileDevState, ApprovalDecision, StateStore, create_initial_state, normalize_project_key
from goodteam_os.tasks import LocalTaskStore, build_task_board, serialize_task_board
from goodteam_os.telegram import send_approval_notification
from goodteam_os.workflow_table import (
    GATE_TRANSITIONS,
    WORKFLOW_NODES,
    advisors_for_gate,
    approved_next_for_gate,
    resolve_profile,
    workflow_execution_plan,
    workflow_execution_plan_v2,
)
from goodteam_os.workers import BUILTIN_WORKER_MODES, CLI_BACKENDS, WorkerJobStore, discover_installed_agents
from goodteam_os.workers import DEFAULT_CODEX_MODEL
from goodteam_os.cross_project_memory import retrieve as retrieve_cross_project_memory

PRD_REVIEW_ROLES = [
    "Product / Requirement",
    "Architect",
    "Review / QA",
]

_PREFERRED_WORKER_BACKEND_ORDER = ("codex", "claude", "gemini", "agy", "opencode", "fake", "noop", "human")
WORKER_BACKEND_OPTIONS = [
    mode
    for mode in _PREFERRED_WORKER_BACKEND_ORDER
    if mode in {*BUILTIN_WORKER_MODES, *CLI_BACKENDS}
]
WORKER_BACKEND_ACCEPTED_OPTIONS = sorted({*WORKER_BACKEND_OPTIONS, *CLI_BACKENDS, "manual"})
WORKER_ROLES = [
    "orchestrator",
    "research",
    "product_requirement",
    "architect",
    "review",
    "planner",
    "ui",
    "implementation",
    "frontend",
    "backend",
    "qa",
    "code_review",
    "release",
    "retro_memory",
]
PRE_REVIEW_REPORT_DIR = Path("/tmp")
DEV_LOOP_SUBPROCESS_TIMEOUT_SECONDS = 10
SERVER_ACTION_TIMEOUT_SECONDS = 600
FS_LIST_MAX_ENTRIES = 500
FS_LIST_IGNORED_NAMES = {".git", ".github", ".venv", "__pycache__", "node_modules", ".next", "dist", "build"}
DISPATCH_TRACK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def append_action(
    workspace: Path,
    *,
    run_id: str,
    actor: str,
    action: str,
    project_key: str = "",
    gate: str = "",
    decision: str = "",
    before_node: str = "",
    after_node: str = "",
    artifact: str = "",
    artifact_path: str = "",
    debate_ref: str = "",
    note: str = "",
    decision_origin: str = "",
    actor_ref: str = "",
    review_mode: str = "",
    review_session_id: str = "",
    client_context: dict[str, object] | None = None,
    evidence_snapshot: dict[str, object] | None = None,
    review_items: list[dict[str, object]] | None = None,
) -> None:
    action_log = _action_log_path(workspace, project_key)
    action_log.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "actor": actor,
        "action": action,
        "gate": gate,
        "decision": decision,
        "before_node": before_node,
        "after_node": after_node,
        "artifact": artifact,
        "artifact_path": artifact_path,
    }
    if debate_ref:
        event["debate_ref"] = debate_ref
    if note:
        event["note"] = note
    if decision_origin:
        event["decision_origin"] = decision_origin
    if actor_ref:
        event["actor_ref"] = actor_ref
    if review_mode:
        event["review_mode"] = review_mode
    if review_session_id:
        event["review_session_id"] = review_session_id
    if client_context:
        event["client_context"] = client_context
    if evidence_snapshot:
        event["evidence_snapshot"] = evidence_snapshot
    if review_items:
        event["review_items"] = review_items
    with action_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


_DEFAULT_TG_NOTIFY_GATES: frozenset[str] = frozenset(
    {"prd", "rfc", "security", "staging", "release", "fix_plan", "code_review", "qa"}
)

# Phase 1 `fix` profile gate -> artifact-key overlay. The role-config
# approval_artifacts map only knows greenfield gates; the fix gates write the
# same artifact files greenfield does, so map them onto those keys additively.
_FIX_APPROVAL_ARTIFACTS: dict[str, str] = {
    "fix_plan": "fix_plan",
    "code_review": "implementation_handoff",
    "qa": "qa_report",
}


def _approval_artifact_for_gate(gate: str) -> str:
    """Resolve the artifact key for a gate, overlaying the fix-profile gates."""
    base = approval_artifact_map()
    if gate in base:
        return base[gate]
    return _FIX_APPROVAL_ARTIFACTS.get(gate, gate)


def _tg_notify_gate_set() -> frozenset[str]:
    """Return the set of gates that trigger a Telegram notification card."""
    env = os.getenv("GOODTEAM_TG_NOTIFY_GATES", "")
    if env.strip():
        return frozenset(g.strip() for g in env.split(",") if g.strip())
    return _DEFAULT_TG_NOTIFY_GATES


def notify_current_approval_gate(state: AgileDevState | None, workspace: Path = Path(".")) -> dict[str, object]:
    gate = current_approval_gate(state)
    if state is None or not gate:
        return {"status": "skipped", "reason": "no approval gate"}
    if gate not in _tg_notify_gate_set():
        return {"status": "skipped", "reason": "gate_not_in_notify_set"}
    artifact_key = _approval_artifact_for_gate(gate)
    try:
        return send_approval_notification(
            project_key=state.project_key,
            run_id=state.run_id,
            gate=gate,
            artifact_path=state.artifacts.get(artifact_key, ""),
            summary=f"GoodTeam is waiting for {gate.upper()} approval on {state.project_key}.",
            workspace=workspace,
        )
    except Exception as exc:
        return {"status": "failed", "error": exc.__class__.__name__}


def current_approval_gate(state: AgileDevState | None) -> str:
    if state is None:
        return ""
    match = re.fullmatch(r"await_(.+)_approval", state.next_node)
    if not match:
        return ""
    gate = match.group(1)
    if str(state.approvals.get(gate, "")) != ApprovalDecision.PENDING:
        return ""
    return gate


def _mask_advisory_text(text: object) -> str:
    try:
        from goodteam_os.telegram import mask_sensitive_data

        return mask_sensitive_data(str(text))
    except Exception:
        return str(text)


def _project_runtime_roots_for_state(*, state: AgileDevState | object | None, workspace: Path, project_key: str) -> list[Path]:
    project_roots: list[Path] = []
    source_ref = str(getattr(state, "source_ref", "") or "") if state is not None else ""
    if source_ref and source_ref != "manual":
        source_path = Path(source_ref)
        if source_path.is_file():
            try:
                target = load_project_target(source_path)
                repos = target.get("repos", {})
                if isinstance(repos, dict) and repos:
                    project_roots.append(resolve_project_runtime_root({str(key): Path(str(value)) for key, value in repos.items()}))
                else:
                    project_roots.append(source_path.parent)
            except Exception:
                project_roots.append(source_path.parent)
    try:
        config_path = _project_config_for_key(workspace, project_key)
        target = load_project_target(config_path)
        repos = target.get("repos", {})
        if isinstance(repos, dict) and repos:
            project_roots.append(resolve_project_runtime_root({str(key): Path(str(value)) for key, value in repos.items()}))
        else:
            project_roots.append(config_path.parent)
    except Exception:
        project_roots.append(workspace)

    roots: list[Path] = []
    seen_roots: set[Path] = set()
    for root in project_roots:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        if resolved in seen_roots:
            continue
        seen_roots.add(resolved)
        roots.append(resolved)
    return roots


def build_provenance_refs(*, state: AgileDevState | object | None, workspace: Path, project_key: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    try:
        roots = _project_runtime_roots_for_state(state=state, workspace=workspace, project_key=project_key)
        seen_manifests: set[Path] = set()
        for root in roots:
            provenance_root = root / ".goodteam" / "provenance"
            if not provenance_root.is_dir():
                continue
            for manifest_path in sorted(provenance_root.glob("pr-*/manifest.json")):
                try:
                    resolved_manifest = manifest_path.resolve()
                except Exception:
                    resolved_manifest = manifest_path
                if resolved_manifest in seen_manifests:
                    continue
                seen_manifests.add(resolved_manifest)
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(manifest, dict):
                    continue
                archive_paths = manifest.get("archive_paths", {})
                archive_paths = archive_paths if isinstance(archive_paths, dict) else {}
                pr_ref = manifest.get("pr", manifest.get("pr_number", manifest.get("number", "")))
                if not pr_ref:
                    pr_ref = manifest_path.parent.name.removeprefix("pr-")
                refs.append(
                    {
                        "pr": _mask_advisory_text(pr_ref),
                        "branch": _mask_advisory_text(manifest.get("branch", "")),
                        "ci_status": _mask_advisory_text(manifest.get("ci_status", "")),
                        "merge_sha": _mask_advisory_text(
                            manifest.get("merge_sha", manifest.get("squash_merge_sha", manifest.get("squash_sha", "")))
                        ),
                        "job_ref": _mask_advisory_text(manifest.get("job_ref", archive_paths.get("job", ""))),
                        "contract_ref": _mask_advisory_text(
                            manifest.get("contract_ref", archive_paths.get("contract", archive_paths.get("task_contract", "")))
                        ),
                        "pre_review_ref": _mask_advisory_text(
                            manifest.get(
                                "pre_review_ref",
                                manifest.get("pre-review_ref", archive_paths.get("pre_review", archive_paths.get("pre-review", ""))),
                            )
                        ),
                    }
                )
    except Exception:
        return []
    return refs


def build_approval_recommendation(
    *,
    state: AgileDevState | object | None,
    review: dict[str, object] | object,
    evidence_snapshot: dict[str, object] | object,
    workspace: Path,
) -> dict[str, object]:
    del workspace
    recommendation: dict[str, object] = {
        "verdict": "caution",
        "confidence": "low",
        "blocking_count": 0,
        "reasons": [],
    }
    try:
        review_dict = review if isinstance(review, dict) else {}
        evidence = evidence_snapshot if isinstance(evidence_snapshot, dict) else {}
        reasons: list[dict[str, str]] = []
        blockers = getattr(state, "blockers", []) if state is not None else []
        blockers = blockers if isinstance(blockers, list) else []
        for blocker in blockers:
            reasons.append({"kind": "blocker", "text": _mask_advisory_text(blocker)})

        evidence_blocker_count = 0
        try:
            evidence_blocker_count = int(evidence.get("blocker_count", 0) or 0)
        except Exception:
            evidence_blocker_count = 0
        approval = str(review_dict.get("approval", "") or "")
        status_values = [
            str(evidence.get("ci_status", "unknown") or "unknown"),
            str(evidence.get("smoke_status", "unknown") or "unknown"),
            str(evidence.get("qa_status", "unknown") or "unknown"),
        ]
        unknown_count = sum(1 for status in status_values if status == "unknown")
        release_stage = approval in {"staging", "release"}
        if evidence_blocker_count > 0 and not blockers:
            reasons.append({"kind": "missing_evidence", "text": "Evidence snapshot reports blockers without blocker details."})
        if release_stage and unknown_count:
            reasons.append({"kind": "missing_evidence", "text": "Release-stage gate has unknown CI, smoke, or QA evidence."})

        messages = {}
        raw_messages = {}
        committee = review_dict.get("committee", {})
        committee = committee if isinstance(committee, dict) else {}
        message_list = committee.get("messages", [])
        message_list = message_list if isinstance(message_list, list) else []
        for index, raw_message in enumerate(message_list):
            if not isinstance(raw_message, dict):
                continue
            message_id = str(raw_message.get("id", "") or "")
            if not message_id:
                continue
            messages[message_id] = index
            raw_messages[message_id] = raw_message
        for message_id, raw_message in raw_messages.items():
            if str(raw_message.get("kind", "")) not in {"ask", "reject"}:
                continue
            message_index = messages.get(message_id, -1)
            has_owner_reply = False
            for later in message_list[message_index + 1 :]:
                if not isinstance(later, dict):
                    continue
                if str(later.get("in_reply_to", "")) == message_id and str(later.get("kind", "")) in {"assert", "propose"}:
                    has_owner_reply = True
                    break
            if not has_owner_reply:
                body = str(raw_message.get("body", "") or "Committee objection is still unresolved.")
                reasons.append({"kind": "open_objection", "text": _mask_advisory_text(body)})

        content = str(review_dict.get("content", "") or "")
        blocking_count = len(blockers)
        if blockers:
            verdict = "request_changes"
        elif any(reason["kind"] in {"missing_evidence", "open_objection"} for reason in reasons):
            verdict = "caution"
        elif content.strip():
            verdict = "approve"
            reasons.append({"kind": "clean", "text": "Artifact content is present and no blocking signals were found."})
        else:
            verdict = "caution"
            reasons.append({"kind": "missing_evidence", "text": "No artifact content is available for review."})

        if blockers:
            confidence = "low"
        elif len(reasons) > 1 and any(reason["kind"] != "clean" for reason in reasons):
            confidence = "low"
        elif any(reason["kind"] in {"missing_evidence", "open_objection"} for reason in reasons):
            confidence = "medium"
        elif unknown_count:
            confidence = "medium"
        else:
            confidence = "high"

        recommendation = {
            "verdict": verdict,
            "confidence": confidence,
            "blocking_count": blocking_count,
            "reasons": reasons,
        }
    except Exception:
        return recommendation
    return recommendation


def _derive_smoke_status(workspace: Path) -> str:
    """Aggregate recent smoke report verdicts; failure-safe, defaults to "unknown"."""
    try:
        items = build_smoke_run_items(workspace)
        if not items:
            return "unknown"
        statuses = [str(item.get("report_status", "") or "").lower() for item in items]
        if any(status == "failed" for status in statuses):
            return "failed"
        if any(status == "passed" for status in statuses):
            return "passed"
        return "unknown"
    except Exception:
        return "unknown"


def _derive_qa_status(state: AgileDevState | object | None) -> str:
    """Map the run-scoped QA gate decision to a verdict; defaults to "unknown"."""
    try:
        approvals = getattr(state, "approvals", {}) if state is not None else {}
        if not isinstance(approvals, dict):
            return "unknown"
        decision = str(approvals.get("qa", "") or "").lower()
        if decision == "approved":
            return "passed"
        if decision == "rejected":
            return "failed"
        return "unknown"
    except Exception:
        return "unknown"


def _map_manifest_ci_status(raw_status: object) -> str:
    """Map a provenance manifest's ci_status string to passed/failed/unknown."""
    status = str(raw_status or "").strip().lower()
    if status in {"success", "passed", "pass", "succeeded"}:
        return "passed"
    if status in {"failure", "failed", "fail", "error"}:
        return "failed"
    return "unknown"


def _derive_ci_status(
    *,
    state: AgileDevState | object | None,
    workspace: Path,
    project_key: str,
) -> str:
    """Read the run's provenance manifest ci_status from disk; defaults to "unknown"."""
    try:
        run_id = str(getattr(state, "run_id", "") or "") if state is not None else ""
        roots = _project_runtime_roots_for_state(state=state, workspace=workspace, project_key=project_key)
        seen_manifests: set[Path] = set()
        candidates: list[tuple[float, str]] = []
        run_match_status: str | None = None
        for root in roots:
            provenance_root = root / ".goodteam" / "provenance"
            if not provenance_root.is_dir():
                continue
            for manifest_path in provenance_root.glob("pr-*/manifest.json"):
                try:
                    resolved_manifest = manifest_path.resolve()
                except Exception:
                    resolved_manifest = manifest_path
                if resolved_manifest in seen_manifests:
                    continue
                seen_manifests.add(resolved_manifest)
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(manifest, dict):
                    continue
                ci_status = _map_manifest_ci_status(manifest.get("ci_status", ""))
                manifest_run_id = str(manifest.get("run_id", "") or "")
                if run_id and manifest_run_id and manifest_run_id == run_id:
                    if ci_status != "unknown":
                        return ci_status
                    run_match_status = run_match_status or ci_status
                try:
                    mtime = manifest_path.stat().st_mtime
                except Exception:
                    mtime = 0.0
                candidates.append((mtime, ci_status))
        if run_match_status is not None:
            return run_match_status
        if not candidates:
            return "unknown"
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    except Exception:
        return "unknown"


def build_approval_evidence_snapshot(
    *,
    state: AgileDevState | object | None,
    workspace: Path,
    project_key: str,
    debate_ref: str = "",
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "artifact_refs": [],
        "worker_job_refs": [],
        "ci_status": "unknown",
        "smoke_status": "unknown",
        "qa_status": "unknown",
        "screenshot_refs": [],
        "debate_refs": [debate_ref] if debate_ref else [],
        "blocker_count": 0,
        "provenance_refs": [],
    }
    try:
        artifacts = getattr(state, "artifacts", {}) if state is not None else {}
        approvals = getattr(state, "approvals", {}) if state is not None else {}
        artifact_refs: set[str] = set()
        if isinstance(artifacts, dict):
            artifact_refs.update(str(key) for key, value in artifacts.items() if str(key).strip() and str(value).strip())
        if isinstance(approvals, dict):
            approval_map = approval_artifact_map()
            artifact_refs.update(
                str(approval_map.get(str(gate), str(gate)))
                for gate in approvals
                if isinstance(artifacts, dict) and str(approval_map.get(str(gate), str(gate))) in artifacts
            )
        snapshot["artifact_refs"] = sorted(artifact_refs)
    except Exception:
        snapshot["artifact_refs"] = []

    try:
        blockers = getattr(state, "blockers", []) if state is not None else []
        snapshot["blocker_count"] = len(blockers) if isinstance(blockers, list) else 0
    except Exception:
        snapshot["blocker_count"] = 0

    try:
        snapshot["provenance_refs"] = build_provenance_refs(state=state, workspace=workspace, project_key=project_key)
    except Exception:
        snapshot["provenance_refs"] = []

    try:
        snapshot["smoke_status"] = _derive_smoke_status(workspace)
    except Exception:
        snapshot["smoke_status"] = "unknown"

    try:
        snapshot["qa_status"] = _derive_qa_status(state)
    except Exception:
        snapshot["qa_status"] = "unknown"

    try:
        snapshot["ci_status"] = _derive_ci_status(state=state, workspace=workspace, project_key=project_key)
    except Exception:
        snapshot["ci_status"] = "unknown"

    try:
        run_id = str(getattr(state, "run_id", "") or "")
        if not run_id:
            return snapshot
        project_roots = _project_runtime_roots_for_state(state=state, workspace=workspace, project_key=project_key)
        worker_refs: set[str] = set()
        for root in project_roots:
            for job in WorkerJobStore(root).list_jobs():
                if str(job.get("run_id", "")) != run_id:
                    continue
                job_id = str(job.get("id", "") or job.get("job_id", ""))
                if job_id:
                    worker_refs.add(job_id)
        snapshot["worker_job_refs"] = sorted(worker_refs)
    except Exception:
        snapshot["worker_job_refs"] = []

    return snapshot


def _action_log_path(workspace: Path, project_key: str = "") -> Path:
    if project_key:
        return StateStore(workspace).project_state_dir(project_key) / "action-log.jsonl"
    return workspace / "state" / "action-log.jsonl"


def build_action_title(action: dict[str, str]) -> str:
    actor = action.get("actor", "").title() or "System"
    action_type = action.get("action", "")
    artifact = action.get("artifact", "")
    gate = action.get("gate", "")
    decision = action.get("decision", "")
    if action_type == "workflow_started":
        return f"{actor} started workflow"
    if action_type.endswith("_generated") and artifact:
        return f"{actor} generated {artifact.upper()}"
    if action_type.endswith("_regenerated") and gate:
        return f"{actor} regenerated {gate.upper()}"
    if action_type == "artifact_saved" and artifact:
        return f"{actor} saved {artifact.upper()} artifact"
    if action_type == "approval_decided" and gate and decision:
        verb = {
            "approved": "approved",
            "changes_requested": "requested changes on",
            "rejected": "rejected",
        }.get(decision, decision.replace("_", " "))
        return f"{actor} {verb} {gate.upper()}"
    return f"{actor} {action_type.replace('_', ' ')}".strip()


def build_action_items(workspace: Path, project_key: str = "") -> list[dict[str, object]]:
    action_log = _action_log_path(workspace, project_key)
    if not action_log.is_file():
        return []
    items = []
    for line in action_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = {
            key: value if key in {"client_context", "evidence_snapshot", "review_items"} else str(value)
            for key, value in raw.items()
        }
        before_node = action.get("before_node", "")
        after_node = action.get("after_node", "")
        action["title"] = build_action_title(action)
        action["node_transition"] = f"{before_node} -> {after_node}" if before_node or after_node else ""
        items.append(action)
    return list(reversed(items))


def build_smoke_run_items(workspace: Path) -> list[dict[str, str]]:
    root = workspace / "state" / "smoke-runs"
    if not root.is_dir():
        return []
    items: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        nested_report = report.get("report")
        display_report = dict(nested_report) if isinstance(nested_report, dict) and nested_report else report
        for run in display_report.get("runs", []):
            raw = dict(run)
            raw["report_path"] = str(path)
            raw["report_status"] = str(display_report.get("status", report.get("status", "")))
            items.append({key: str(value) for key, value in raw.items()})
    return items[:12]


def _run_readonly_command(command: list[str], workspace: Path, warnings: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=DEV_LOOP_SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        warnings.append(f"{command[0]} not available")
    except subprocess.TimeoutExpired:
        warnings.append(f"{command[0]} timed out")
    except OSError as exc:
        warnings.append(f"{command[0]} failed: {exc.__class__.__name__}")
    return None


def _parse_git_worktrees(output: str) -> list[dict[str, str]]:
    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            if current.get("path"):
                worktrees.append(
                    {
                        "path": current.get("path", ""),
                        "branch": current.get("branch", ""),
                        "head": current.get("head", ""),
                    }
                )
            current = {}
            continue
        key, _, value = stripped.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value[:7]
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
    if current.get("path"):
        worktrees.append(
            {
                "path": current.get("path", ""),
                "branch": current.get("branch", ""),
                "head": current.get("head", ""),
            }
        )
    return worktrees


def _check_status_value(check: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = str(check.get(key, "")).lower()
        if value:
            return value
    return ""


def _map_ci_rollup(status_check_rollup: object) -> str:
    if not isinstance(status_check_rollup, list) or not status_check_rollup:
        return "none"

    has_pending = False
    for raw_check in status_check_rollup:
        if not isinstance(raw_check, dict):
            has_pending = True
            continue
        conclusion = _check_status_value(raw_check, "conclusion")
        state = _check_status_value(raw_check, "state", "status")
        if conclusion in {"failure", "startup_failure", "cancelled", "timed_out", "action_required"}:
            return "fail"
        if state in {"failure", "failed", "error", "cancelled", "timed_out", "action_required"}:
            return "fail"
        if conclusion in {"", "neutral", "skipped"} and state not in {"success", "completed"}:
            has_pending = True
        elif state in {"", "queued", "pending", "in_progress", "waiting", "requested", "expected"}:
            has_pending = True

    return "pending" if has_pending else "pass"


def _request_origin_is_same_origin(*, origin: str, host: str) -> bool:
    if not origin:
        return True
    if not host:
        return False
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    request_host = host.split("@")[-1].lower()
    origin_host = parsed.netloc.lower()
    if origin_host == request_host:
        return True
    local_hosts = {"localhost", "127.0.0.1", "::1", "[::1]"}
    origin_name = (parsed.hostname or "").lower()
    request_name = request_host.rsplit(":", 1)[0]
    return origin_name in local_hosts and request_name in local_hosts


def _dispatch_worktree_path(workspace: Path, name: str) -> Path:
    return workspace.resolve().parent / f"goodteam-dev-{name}"


def _ensure_dispatch_worktree(workspace: Path, *, name: str, branch: str) -> Path:
    worktree = _dispatch_worktree_path(workspace, name)
    workspace_root = workspace.resolve()
    resolved_worktree = worktree.resolve() if worktree.exists() else worktree.absolute().resolve(strict=False)
    if resolved_worktree == workspace_root or workspace_root in resolved_worktree.parents:
        raise ValueError("dispatch worktree must be outside the live repo")
    if not worktree.exists():
        subprocess.run(
            ["git", "worktree", "add", str(worktree), "-b", branch, "main"],
            cwd=workspace,
            check=True,
        )
    _link_dispatch_worktree_venv(workspace, worktree)
    return worktree


def _link_dispatch_worktree_venv(workspace: Path, worktree: Path) -> None:
    workspace_root = workspace.resolve()
    worktree_root = worktree.resolve() if worktree.exists() else worktree.absolute().resolve(strict=False)
    source_bin = workspace_root / ".venv" / "bin"
    if not source_bin.exists():
        return
    venv_dir = worktree_root / ".venv"
    bin_link = venv_dir / "bin"
    if venv_dir.exists() or venv_dir.is_symlink():
        return
    venv_dir.mkdir(parents=True, exist_ok=True)
    bin_link.symlink_to(source_bin)


def _prepare_dispatch_track_inputs(
    worktree: Path,
    *,
    run_id: str,
    task: str,
    constraints: str,
) -> tuple[Path, Path | None]:
    input_dir = worktree / ".goodteam" / "dispatch-inputs" / run_id
    input_dir.mkdir(parents=True, exist_ok=True)
    task_file = input_dir / "task.md"
    task_file.write_text(task, encoding="utf-8")
    constraints_file = None
    if constraints.strip():
        constraints_file = input_dir / "constraints.md"
        constraints_file.write_text(constraints, encoding="utf-8")
    return task_file, constraints_file


def _dispatch_track_status_path(workspace: Path, run_id: str) -> Path:
    return workspace / "state" / "dispatch-tracks" / f"dispatch-track-{run_id}.json"


def _smoke_run_status_path(workspace: Path, run_id: str) -> Path:
    return workspace / "state" / "smoke-runs" / f"smoke-run-{run_id}.json"


def _write_dispatch_track_status(
    workspace: Path,
    *,
    run_id: str,
    name: str,
    worktree: Path,
    status: str,
    error: str = "",
) -> None:
    status_path = _dispatch_track_status_path(workspace, run_id)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "name": name,
        "worktree": str(worktree),
        "status": status,
        "error": error,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    status_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_smoke_run_status(
    workspace: Path,
    *,
    run_id: str,
    suites: list[str],
    cycles: int,
    dry_run: bool,
    worker_mode: str,
    status: str,
    report: dict[str, object] | None = None,
    error: str = "",
) -> None:
    status_path = _smoke_run_status_path(workspace, run_id)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "suites": suites,
        "cycles": cycles,
        "dry_run": dry_run,
        "worker_mode": worker_mode,
        "status": status,
        "report": report or {},
        "error": error,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    status_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _start_dispatch_track_background(
    *,
    workspace: Path,
    run_id: str,
    name: str,
    worktree: Path,
    tracks: list[dict[str, object]],
    verify_commands: dict[str, str],
    constraints_file: Path | None,
    response_written: threading.Event | None = None,
) -> None:
    projects_dir = workspace / "projects"

    def run_dispatch_track() -> None:
        if response_written is not None:
            response_written.wait()
        _write_dispatch_track_status(
            workspace,
            run_id=run_id,
            name=name,
            worktree=worktree,
            status="started",
        )
        try:
            dispatch_tracks(
                project_root=worktree,
                tracks=tracks,
                verify_commands=verify_commands,
                constraints_file=constraints_file,
                projects_dir=projects_dir,
            )
        except Exception as exc:
            _write_dispatch_track_status(
                workspace,
                run_id=run_id,
                name=name,
                worktree=worktree,
                status="failed",
                error=str(exc),
            )
            return
        _write_dispatch_track_status(
            workspace,
            run_id=run_id,
            name=name,
            worktree=worktree,
            status="completed",
        )

    thread = threading.Thread(
        target=run_dispatch_track,
        daemon=True,
    )
    thread.start()


def run_smoke_suite(**kwargs) -> dict[str, object]:
    from goodteam_os.smoke import run_smoke_suite as _run_smoke_suite

    return _run_smoke_suite(**kwargs)


def _start_smoke_run_background(
    *,
    workspace: Path,
    run_id: str,
    suites: list[str],
    cycles: int,
    dry_run: bool,
    worker_mode: str,
) -> None:
    def run_smoke() -> None:
        _write_smoke_run_status(
            workspace,
            run_id=run_id,
            suites=suites,
            cycles=cycles,
            dry_run=dry_run,
            worker_mode=worker_mode,
            status="started",
        )
        try:
            report = run_smoke_suite(
                workspace=workspace,
                suites=suites,
                cycles=cycles,
                autonomy="full",
                dry_run=dry_run,
                worker_mode=worker_mode,
            )
        except Exception as exc:
            _write_smoke_run_status(
                workspace,
                run_id=run_id,
                suites=suites,
                cycles=cycles,
                dry_run=dry_run,
                worker_mode=worker_mode,
                status="failed",
                error=str(exc),
            )
            return
        status = "failed" if str(report.get("status", "")) == "failed" else "completed"
        _write_smoke_run_status(
            workspace,
            run_id=run_id,
            suites=suites,
            cycles=cycles,
            dry_run=dry_run,
            worker_mode=worker_mode,
            status=status,
            report=report,
        )

    threading.Thread(target=run_smoke, daemon=True).start()


def start_smoke_run(workspace: Path, request: dict[str, object]) -> dict[str, object]:
    raw_suite = request.get("suite", request.get("suites", "self"))
    if isinstance(raw_suite, list):
        suites = [str(value).strip() for value in raw_suite if str(value).strip()]
    else:
        suites = [part.strip() for part in str(raw_suite or "self").split(",") if part.strip()]
    suites = suites or ["self"]
    invalid_suites = [suite for suite in suites if suite not in {"contentops", "self"}]
    if invalid_suites:
        raise ValueError(f"Unknown smoke suite: {', '.join(invalid_suites)}")
    cycles = int(request.get("cycles", 1) or 1)
    if cycles < 1 or cycles > 5:
        raise ValueError("cycles must be between 1 and 5")
    worker_mode = str(request.get("worker_mode", "fake") or "fake").strip()
    if worker_mode not in {"fake", "noop", "codex"}:
        raise ValueError("worker_mode must be one of: fake, noop, codex")
    # The web control plane starts in dry-run mode by default. Real smoke can be
    # enabled explicitly once the operator wants the workflow-mutating path.
    dry_run = bool(request.get("dry_run", True))
    run_id = f"smoke-{uuid4()}"
    _start_smoke_run_background(
        workspace=workspace,
        run_id=run_id,
        suites=suites,
        cycles=cycles,
        dry_run=dry_run,
        worker_mode=worker_mode,
    )
    return {
        "run_id": run_id,
        "suites": suites,
        "cycles": cycles,
        "dry_run": dry_run,
        "worker_mode": worker_mode,
        "status": "started",
    }


def start_dispatch_track(
    workspace: Path,
    request: dict[str, object],
    *,
    response_written: threading.Event | None = None,
) -> dict[str, object]:
    name = str(request.get("name", "")).strip()
    role = str(request.get("role", "")).strip()
    adapter = str(request.get("adapter", "")).strip()
    task = str(request.get("task", ""))
    constraints = str(request.get("constraints", ""))
    verify = str(request.get("verify", "")).strip()
    if not DISPATCH_TRACK_NAME_RE.fullmatch(name):
        raise ValueError("name must be a slug using lowercase letters, numbers, and hyphens")
    if not role:
        raise ValueError("role is required")
    if not adapter:
        raise ValueError("adapter is required")
    if not task.strip():
        raise ValueError("task is required")

    branch = f"feat/{name}"
    worktree = _dispatch_worktree_path(workspace, name)
    safety = ensure_dispatch_project_root_is_safe(worktree, projects_dir=workspace / "projects")
    if not safety.get("safe"):
        raise ValueError(str(safety.get("reason", "unsafe dispatch target")))
    worktree = _ensure_dispatch_worktree(workspace, name=name, branch=branch)

    run_id = f"dispatch-{uuid4()}"
    task_file, constraints_file = _prepare_dispatch_track_inputs(
        worktree,
        run_id=run_id,
        task=task,
        constraints=constraints,
    )
    tracks = [parse_dispatch_track_spec(f"{role}:{adapter}:{task_file}", base_dir=worktree)]
    verify_commands = {}
    if verify:
        verify_commands = dict([parse_dispatch_verify_spec(f"{role}:{verify}")])

    _start_dispatch_track_background(
        workspace=workspace,
        run_id=run_id,
        name=name,
        worktree=worktree,
        tracks=tracks,
        verify_commands=verify_commands,
        constraints_file=constraints_file,
        response_written=response_written,
    )
    return {
        "run_id": run_id,
        "name": name,
        "branch": branch,
        "worktree": str(worktree),
        "status": "started",
    }


def _parse_gh_pull_requests(output: str, warnings: list[str]) -> list[dict[str, object]]:
    try:
        raw_pull_requests = json.loads(output)
    except json.JSONDecodeError:
        warnings.append("gh pr list returned invalid json")
        return []
    if not isinstance(raw_pull_requests, list):
        warnings.append("gh pr list returned unexpected json")
        return []

    pull_requests: list[dict[str, object]] = []
    for item in raw_pull_requests:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("number", 0))
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        pull_requests.append(
            {
                "number": number,
                "title": str(item.get("title", "")),
                "head_branch": str(item.get("headRefName", "")),
                "ci": _map_ci_rollup(item.get("statusCheckRollup")),
                "url": str(item.get("url", "")),
            }
        )
    return pull_requests


def _land_command_for_pull_request(
    number: int,
    head_branch: str,
    worktrees: list[dict[str, str]],
    *,
    review_rationale_path: str = "",
) -> str:
    matching_worktree = next((worktree for worktree in worktrees if worktree.get("branch") == head_branch), None)
    command = f"scripts/land-pr.sh {number}"
    if matching_worktree is None:
        return command
    command = f"{command} --worktree {matching_worktree['path']}"
    if review_rationale_path:
        command = f"{command} --review-rationale {review_rationale_path}"
    return command


def _review_rationale_path(root: Path, number: int) -> Path:
    return root / ".goodteam" / "provenance" / f"pr-{number}" / "review-rationale.md"


def _non_empty_file_exists(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _server_actions_enabled() -> bool:
    return os.environ.get("GOODTEAM_SERVER_ACTIONS", "").strip() == "1"


def _server_action_nonce() -> str:
    return os.environ.get("GOODTEAM_SERVER_ACTION_NONCE", "").strip()


def _server_action_audit_dir(root: Path) -> Path:
    return root / ".goodteam" / "server-actions"


def _tail_text(value: str, *, max_chars: int = 2000) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _decode_subprocess_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _positive_int(value: object, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _optional_absolute_path(value: object, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{field} must be an absolute path")
    return str(path)


def _git_safe_ref(value: object, field: str = "ref") -> str:
    ref = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]{7,80}", ref):
        raise ValueError(f"{field} must be a git-safe ref or SHA")
    if ref.startswith(("-", "/", ".")) or ref.endswith(("/", ".")):
        raise ValueError(f"{field} must be a git-safe ref or SHA")
    if ".." in ref or "//" in ref or "@{" in ref:
        raise ValueError(f"{field} must be a git-safe ref or SHA")
    for component in ref.split("/"):
        if (
            not component
            or component in {".", ".."}
            or component.startswith(("-", "."))
            or component.endswith(".lock")
        ):
            raise ValueError(f"{field} must be a git-safe ref or SHA")
    return ref


def _git_safe_tag(value: object, field: str = "tag") -> str:
    tag = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,80}", tag):
        raise ValueError(f"{field} must be a git-safe tag")
    if tag.startswith(("-", "/", ".")) or tag.endswith(("/", ".")):
        raise ValueError(f"{field} must be a git-safe tag")
    if ".." in tag or "//" in tag or "@{" in tag:
        raise ValueError(f"{field} must be a git-safe tag")
    for component in tag.split("/"):
        if (
            not component
            or component in {".", ".."}
            or component.startswith(("-", "."))
            or component.endswith(".lock")
        ):
            raise ValueError(f"{field} must be a git-safe tag")
    return tag


def _tag_release_safety_report() -> dict[str, object]:
    return {
        "server_execution": "report_only",
        "blocked_by": [
            "tag_name_policy",
            "signed_tag_policy",
            "remote_push_policy",
            "rollback_policy",
            "human_release_decision",
        ],
        "future_enablement_requires": [
            "GOODTEAM_SERVER_ACTIONS=1",
            "GOODTEAM_SERVER_ACTION_NONCE",
            "typed confirmation: TAG RELEASE <tag>",
            "argv allowlist with shell=False",
            "audit record before any remote push",
            "human-reviewed release notes and rollback plan",
        ],
    }


def _server_action_expected_confirmation(action: str, request: dict[str, object]) -> str:
    if action == "land_pr":
        pr_number = _positive_int(request.get("pr"), "pr")
        return f"LAND PR {pr_number}"
    if action == "graduate_release":
        ref = _git_safe_ref(request.get("ref", ""))
        return f"GRADUATE RELEASE {ref}"
    if action == "tag_release":
        tag = _git_safe_tag(request.get("tag", ""), "tag")
        return f"TAG RELEASE {tag}"
    raise ValueError(f"Unsupported server action: {action}")


def _land_pr_action_argv(root: Path, request: dict[str, object]) -> list[str]:
    pr_number = _positive_int(request.get("pr"), "pr")
    dev_loop = build_dev_loop(root)
    readiness = dev_loop.get("land_tag_readiness", {})
    candidates = readiness.get("pull_requests", []) if isinstance(readiness, dict) else []
    matching = next(
        (
            item
            for item in candidates
            if isinstance(item, dict) and int(item.get("pr", 0) or 0) == pr_number
        ),
        None,
    )
    if matching is None:
        raise ValueError(f"PR {pr_number} is not present in local land readiness")
    if not matching.get("ready"):
        missing = matching.get("missing", [])
        missing_text = ", ".join(str(item) for item in missing) if isinstance(missing, list) else str(missing)
        raise ValueError(f"PR {pr_number} is not ready to land: {missing_text}")
    worktree = _optional_absolute_path(matching.get("worktree", ""), "worktree")
    rationale = _optional_absolute_path(matching.get("review_rationale_path", ""), "review_rationale_path")
    if not worktree or not rationale:
        raise ValueError(f"PR {pr_number} is missing trusted land paths")
    return [
        "scripts/land-pr.sh",
        str(pr_number),
        "--worktree",
        worktree,
        "--review-rationale",
        rationale,
    ]


def _server_action_argv(root: Path, action: str, request: dict[str, object]) -> list[str]:
    if action == "land_pr":
        return _land_pr_action_argv(root, request)
    if action == "graduate_release":
        ref = _git_safe_ref(request.get("ref", ""))
        return ["scripts/graduate-release.sh", "--ref", ref, "--yes"]
    if action == "tag_release":
        raise ValueError("tag_release is report-only until tag safety design is complete")
    raise ValueError(f"Unsupported server action: {action}")


def run_gated_server_action(
    root: Path,
    request: dict[str, object],
    *,
    origin: str = "",
    actor: str = "local-dashboard",
) -> dict[str, object]:
    if not _server_actions_enabled():
        raise PermissionError("server action execution is disabled")
    expected_nonce = _server_action_nonce()
    supplied_nonce = str(request.get("nonce", ""))
    if not expected_nonce or not hmac.compare_digest(supplied_nonce, expected_nonce):
        raise PermissionError("server action nonce is missing or invalid")

    action = str(request.get("action", "")).strip()
    expected_confirmation = _server_action_expected_confirmation(action, request)
    typed_confirmation = str(request.get("confirm", "")).strip()
    if typed_confirmation != expected_confirmation:
        raise ValueError(f"typed confirmation must exactly match: {expected_confirmation}")
    argv = _server_action_argv(root, action, request)

    started_at = datetime.now(UTC).isoformat()
    timed_out = False
    returncode: int | None
    stdout = ""
    stderr = ""
    try:
        result = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=SERVER_ACTION_TIMEOUT_SECONDS,
            check=False,
        )
        returncode = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None
        stdout = _decode_subprocess_text(exc.output)
        stderr = _decode_subprocess_text(exc.stderr)
    finished_at = datetime.now(UTC).isoformat()
    audit = {
        "id": f"server-action-{uuid4()}",
        "timestamp": finished_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "actor": actor,
        "origin": origin,
        "action": action,
        "argv": argv,
        "typed_confirmation": typed_confirmation,
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout_tail": _tail_text(stdout),
        "stderr_tail": _tail_text(stderr),
        "rollback_hint": "Use GitHub PR/release rollback guidance from the audit context; do not rerun blindly.",
    }
    audit_dir = _server_action_audit_dir(root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / f"{audit['id']}.json"
    audit["audit_path"] = str(audit_path)
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "completed" if returncode == 0 else "failed",
        "action": action,
        "argv": argv,
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout_tail": audit["stdout_tail"],
        "stderr_tail": audit["stderr_tail"],
        "audit_path": str(audit_path),
    }


SERVER_ACTION_AUDIT_FIELDS = (
    "id",
    "timestamp",
    "started_at",
    "finished_at",
    "actor",
    "origin",
    "action",
    "returncode",
    "timed_out",
    "argv",
    "typed_confirmation",
    "audit_path",
    "stdout_tail",
    "stderr_tail",
    "rollback_hint",
)


def _server_action_audit_limit(limit: object) -> int:
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        parsed = 25
    return min(max(parsed, 1), 100)


def _sanitize_server_action_audit_record(record: dict[str, object]) -> dict[str, object]:
    return {field: record[field] for field in SERVER_ACTION_AUDIT_FIELDS if field in record}


def build_server_action_audit_log(root: Path | None, *, limit: int = 25) -> dict[str, object]:
    warnings: list[str] = []
    if root is None:
        return {
            "available": False,
            "audit_dir": "",
            "records": [],
            "warnings": ["no project selected"],
            "generated_at": datetime.now(UTC).isoformat(),
        }

    audit_dir = _server_action_audit_dir(root.resolve())
    records: list[dict[str, object]] = []
    if audit_dir.is_dir():
        for path in sorted(audit_dir.glob("*.json")):
            try:
                raw_record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                warnings.append(f"ignored malformed audit record: {path.name}")
                continue
            if not isinstance(raw_record, dict):
                warnings.append(f"ignored malformed audit record: {path.name}")
                continue
            records.append(_sanitize_server_action_audit_record(raw_record))

    records.sort(key=lambda record: str(record.get("timestamp", "")), reverse=True)
    return {
        "available": True,
        "audit_dir": str(audit_dir),
        "records": records[: _server_action_audit_limit(limit)],
        "warnings": warnings,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _worktree_status_for_path(path: str, root: Path, warnings: list[str]) -> str:
    if not path:
        return "missing"
    result = _run_readonly_command(["git", "-C", path, "status", "--short"], root, warnings)
    if result is None:
        return "unknown"
    if result.returncode != 0:
        warnings.append(f"git status failed for worktree {path}")
        return "unknown"
    return "dirty" if result.stdout.strip() else "clean"


def build_land_tag_readiness(
    pull_requests: list[dict[str, object]],
    pre_review_presence_by_pr: dict[int, bool],
    worktrees: list[dict[str, str]],
    worktree_status_by_path: dict[str, str],
    root: Path,
) -> dict[str, object]:
    readiness_items: list[dict[str, object]] = []
    for pull_request in pull_requests:
        number = int(pull_request["number"])
        head_branch = str(pull_request.get("head_branch", ""))
        ci = str(pull_request.get("ci", "unknown"))
        pre_review = pre_review_presence_by_pr.get(number, False)
        matching_worktree = next((worktree for worktree in worktrees if worktree.get("branch") == head_branch), None)
        worktree_path = str(matching_worktree.get("path", "")) if matching_worktree else ""
        worktree_status = worktree_status_by_path.get(worktree_path, "missing") if worktree_path else "missing"
        rationale_path = _review_rationale_path(root, number)
        review_rationale_present = _non_empty_file_exists(rationale_path)
        missing: list[str] = []
        if ci != "pass":
            missing.append("ci_not_green")
        if not pre_review:
            missing.append("missing_pre_review")
        if matching_worktree is None:
            missing.append("missing_worktree")
        elif worktree_status == "dirty":
            missing.append("dirty_worktree")
        elif worktree_status != "clean":
            missing.append("worktree_status_unknown")
        if not review_rationale_present:
            missing.append("missing_rationale")
        readiness_items.append(
            {
                "pr": number,
                "ready": not missing,
                "missing": missing,
                "command": _land_command_for_pull_request(
                    number,
                    head_branch,
                    worktrees,
                    review_rationale_path=str(rationale_path) if review_rationale_present else "",
                ),
                "ci": ci,
                "pre_review": pre_review,
                "head_branch": head_branch,
                "worktree": worktree_path,
                "worktree_status": worktree_status,
                "review_rationale": review_rationale_present,
                "review_rationale_path": str(rationale_path),
            }
        )
    return {
        "server_execution": "disabled_by_default",
        "pull_requests": readiness_items,
    }


def _release_dir_for_root(root: Path) -> Path:
    configured = os.environ.get("GOODTEAM_RELEASE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return root / "../goodteam-release"


def _readonly_head(path: Path, *, cwd: Path, warnings: list[str], warning_label: str) -> str:
    result = _run_readonly_command(["git", "-C", str(path), "rev-parse", "HEAD"], cwd, warnings)
    if result is None:
        return ""
    if result.returncode != 0:
        warnings.append(f"{warning_label} HEAD unavailable")
        return ""
    return result.stdout.strip()


def build_release_readiness(root: Path, warnings: list[str]) -> dict[str, object]:
    if not (root / "scripts" / "graduate-release.sh").is_file() or not (root / "scripts" / "release-up.sh").is_file():
        return {
            "available": False,
            "reason": "release scripts unavailable",
        }
    release_dir = _release_dir_for_root(root)
    dev_head = _readonly_head(root, cwd=root, warnings=warnings, warning_label="dev")
    release_exists = release_dir.is_dir()
    release_head = ""
    if release_exists:
        release_head = _readonly_head(release_dir, cwd=root, warnings=warnings, warning_label="release")

    ref = dev_head or "HEAD"
    command_prefix = f"GOODTEAM_RELEASE_DIR={release_dir} "
    return {
        "available": True,
        "release_dir": str(release_dir),
        "release_exists": release_exists,
        "dev_head": dev_head,
        "release_head": release_head,
        "up_to_date": bool(dev_head and release_head and dev_head == release_head),
        "release_up_command": f"{command_prefix}scripts/release-up.sh --ref {ref}",
        "graduate_command": f"{command_prefix}scripts/graduate-release.sh --ref {ref}",
    }


def _server_action_capability(
    *,
    available: bool,
    mode: str,
    reason: str = "",
    command: str = "",
    readiness: dict[str, object] | None = None,
) -> dict[str, object]:
    capability: dict[str, object] = {
        "available": available,
        "mode": mode,
        "reason": reason,
    }
    if command:
        capability["command"] = command
    if readiness is not None:
        capability["readiness"] = readiness
    return capability


def build_server_action_execution_policy(root: Path | None) -> dict[str, object]:
    env_gate_configured = _server_actions_enabled()
    nonce_configured = bool(_server_action_nonce())
    destructive_actions_available = bool(root is not None and env_gate_configured and nonce_configured)
    audit_dir = str(_server_action_audit_dir(root.resolve())) if root is not None else ""
    blocked_actions: dict[str, list[str]] = {
        "tag_release": ["tag_release_safety_design_required"],
    }
    enabled_actions: list[str] = []
    if destructive_actions_available:
        enabled_actions = ["land_pr", "graduate_release"]
    else:
        blockers = []
        if root is None:
            blockers.append("no_project_selected")
        if not env_gate_configured:
            blockers.append("disabled_by_default")
        if not nonce_configured:
            blockers.append("missing_nonce")
        blocked_actions["land_pr"] = blockers
        blocked_actions["graduate_release"] = blockers
    return {
        "status": "guarded_execute" if destructive_actions_available else "disabled",
        "destructive_actions_available": destructive_actions_available,
        "env_gate_configured": env_gate_configured,
        "nonce_configured": nonce_configured,
        "same_origin_required": True,
        "typed_confirmation_required": True,
        "argv_allowlist_required": True,
        "audit_required": True,
        "audit_dir": audit_dir,
        "timeout_seconds": SERVER_ACTION_TIMEOUT_SECONDS,
        "enabled_actions": enabled_actions,
        "blocked_actions": blocked_actions,
    }


def build_server_action_capabilities(
    root: Path | None,
    *,
    release_readiness: dict[str, object] | None = None,
    land_tag_readiness: dict[str, object] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, object]:
    capability_warnings: list[str] = [] if warnings is None else warnings
    if release_readiness is None:
        release_readiness = (
            build_release_readiness(root, capability_warnings) if root is not None else {"available": False}
        )
    graduate_command = str(release_readiness.get("graduate_command", ""))
    destructive_actions_available = bool(root is not None and _server_actions_enabled() and _server_action_nonce())
    destructive_reason = "" if destructive_actions_available else "disabled_by_default"
    destructive_mode = "guarded_execute" if destructive_actions_available else "human_command_only"
    return {
        "actions": {
            "dispatch_track": _server_action_capability(
                available=True,
                mode="execute",
            ),
            "dry_smoke": _server_action_capability(
                available=True,
                mode="execute",
            ),
            "land_pr": _server_action_capability(
                available=destructive_actions_available,
                mode=destructive_mode,
                reason=destructive_reason,
                readiness=land_tag_readiness,
            ),
            "tag_release": _server_action_capability(
                available=False,
                mode="report_only",
                reason="tag_release_safety_design_required",
                readiness=_tag_release_safety_report(),
            ),
            "graduate_release": _server_action_capability(
                available=destructive_actions_available,
                mode=destructive_mode,
                reason=destructive_reason,
                command=graduate_command,
            ),
        },
        "execution_policy": build_server_action_execution_policy(root),
        "release_readiness": release_readiness,
        "warnings": capability_warnings,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def build_dev_loop(root: Path | None) -> dict[str, object]:
    warnings: list[str] = []
    worktrees: list[dict[str, str]] = []
    pull_requests: list[dict[str, object]] = []

    if root is None:
        warnings.append("no project selected")
        return {
            "worktrees": worktrees,
            "pull_requests": pull_requests,
            "pre_reviews": [],
            "is_git_repo": False,
            "warnings": warnings,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    root = root.resolve()
    git_root_result = _run_readonly_command(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        root,
        warnings,
    )
    git_root = ""
    if git_root_result is not None and git_root_result.returncode == 0:
        git_root = git_root_result.stdout.strip()
    try:
        valid_git_root = bool(git_root) and Path(git_root).resolve().is_relative_to(root)
    except OSError:
        valid_git_root = False
    if not valid_git_root:
        warnings.append("project repo has no git history yet")
        return {
            "worktrees": worktrees,
            "pull_requests": pull_requests,
            "pre_reviews": [],
            "is_git_repo": False,
            "warnings": warnings,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    git_result = _run_readonly_command(
        ["git", "-C", str(root), "worktree", "list", "--porcelain"],
        root,
        warnings,
    )
    if git_result is not None:
        if git_result.returncode == 0:
            worktrees = _parse_git_worktrees(git_result.stdout)
        else:
            warnings.append("git worktree list failed")
    worktree_status_by_path = {
        worktree["path"]: _worktree_status_for_path(worktree["path"], root, warnings)
        for worktree in worktrees
        if worktree.get("path")
    }

    gh_result = _run_readonly_command(
        ["gh", "pr", "list", "--json", "number,title,headRefName,url,statusCheckRollup"],
        root,
        warnings,
    )
    if gh_result is not None:
        if gh_result.returncode == 0:
            pull_requests = _parse_gh_pull_requests(gh_result.stdout, warnings)
        else:
            warnings.append("gh pr list failed")

    pre_reviews = []
    pre_review_presence_by_pr: dict[int, bool] = {}
    for pull_request in pull_requests:
        number = int(pull_request["number"])
        report_path = PRE_REVIEW_REPORT_DIR / f"pre-review-{number}-report.md"
        present = report_path.is_file()
        pre_review_presence_by_pr[number] = present
        pre_reviews.append(
            {
                "pr": number,
                "present": present,
                "report_path": str(report_path),
            }
        )
    for pull_request in pull_requests:
        number = int(pull_request["number"])
        head_branch = str(pull_request["head_branch"])
        matching_worktree = next((worktree for worktree in worktrees if worktree.get("branch") == head_branch), None)
        worktree_path = str(matching_worktree.get("path", "")) if matching_worktree else ""
        rationale_path = _review_rationale_path(root, number)
        rationale_present = _non_empty_file_exists(rationale_path)
        worktree_status = worktree_status_by_path.get(worktree_path, "missing") if worktree_path else "missing"
        pull_request["land_ready"] = (
            pull_request["ci"] == "pass"
            and pre_review_presence_by_pr.get(number, False)
            and worktree_status == "clean"
            and rationale_present
        )
        pull_request["land_command"] = _land_command_for_pull_request(
            number,
            head_branch,
            worktrees,
            review_rationale_path=str(rationale_path) if rationale_present else "",
        )

    release_readiness = build_release_readiness(root, warnings)
    land_tag_readiness = build_land_tag_readiness(
        pull_requests,
        pre_review_presence_by_pr,
        worktrees,
        worktree_status_by_path,
        root,
    )
    return {
        "worktrees": worktrees,
        "pull_requests": pull_requests,
        "pre_reviews": pre_reviews,
        "is_git_repo": True,
        "land_tag_readiness": land_tag_readiness,
        "release_readiness": release_readiness,
        "server_actions": build_server_action_capabilities(
            root,
            release_readiness=release_readiness,
            land_tag_readiness=land_tag_readiness,
        ),
        "warnings": warnings,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def build_agile_board(state: AgileDevState | None) -> dict[str, object]:
    ledger = _task_ledger_from_state(state)
    if ledger is None:
        return serialize_task_board(build_task_board([]))
    return serialize_task_board(build_task_board(LocalTaskStore(ledger.parents[2]).list_tasks()))


def _task_ledger_from_state(state: AgileDevState | None) -> Path | None:
    if state is None:
        return None
    path = ""
    for resource in state.resources.values():
        if ResourceCapability.BOARD_SOURCE in resource.capabilities and resource.path:
            path = resource.path
            break
    if not path:
        path = str(state.artifacts.get("task_ledger", "")).strip()
    if not path:
        return None
    ledger = Path(path)
    if ledger.name != "tasks.json" or not ledger.is_file():
        return None
    return ledger


def maybe_send_favicon_response(handler: object, path: str) -> bool:
    if path != "/favicon.ico":
        return False
    handler.send_response(204)
    handler.send_header("Content-Length", "0")
    handler.end_headers()
    return True


def _allowed_cors_origin(origin: str, *, host: str, port: int) -> str:
    if not origin:
        return ""
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"}:
        return ""
    hostname = parsed.hostname or ""
    allowed_hosts = {"localhost", "127.0.0.1", "::1"}
    if host not in {"0.0.0.0", "::"}:
        allowed_hosts.add(host)
    if hostname not in allowed_hosts:
        return ""
    expected_port = port if parsed.scheme == "http" else 443
    if parsed.port not in {None, expected_port}:
        return ""
    return origin


def _send_cors_headers(handler: object, *, host: str, port: int) -> None:
    origin = _allowed_cors_origin(str(handler.headers.get("Origin", "")), host=host, port=port)
    if origin:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")


def discover_project_directories(base_dir: Path | None = None) -> list[dict[str, str]]:
    base_dir = Path.home() if base_dir is None else base_dir
    if not base_dir.is_dir():
        return []
    ignored_names = {".git", ".github", ".venv", "__pycache__", "node_modules", ".data", ".next", "dist", "build"}
    directories = []
    candidates = [base_dir]
    for _ in range(2):
        next_candidates = []
        for parent in candidates:
            for path in sorted(parent.iterdir(), key=lambda item: item.name.lower()):
                if path.name.startswith(".") or path.name in ignored_names or not path.is_dir():
                    continue
                directories.append({"name": path.name, "path": str(path)})
                next_candidates.append(path)
        candidates = next_candidates
    unique = []
    seen = set()
    for item in directories:
        if item["path"] in seen:
            continue
        seen.add(item["path"])
        unique.append(item)
    return unique


class FsListBadRequestError(ValueError):
    pass


class FsListNotFoundError(ValueError):
    pass


class FsListPermissionError(ValueError):
    pass


class FsMkdirConflictError(ValueError):
    pass


def list_directory_for_picker(path_value: str | None) -> dict[str, object]:
    raw_path = "" if path_value is None else path_value.strip()
    if not raw_path:
        requested = Path.home()
    else:
        requested = Path(raw_path)
        if not requested.is_absolute():
            raise FsListBadRequestError("path must be absolute.")

    try:
        resolved = requested.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FsListNotFoundError("path not found.") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise FsListBadRequestError("path is invalid.") from exc

    if not resolved.is_dir():
        raise FsListNotFoundError("path is not a directory.")

    parent = None if resolved.parent == resolved else str(resolved.parent)
    entries: list[dict[str, object]] = []
    try:
        children = sorted(resolved.iterdir(), key=lambda item: item.name.lower())
    except PermissionError as exc:
        raise FsListPermissionError("path is not readable.") from exc
    except OSError as exc:
        raise FsListBadRequestError("path cannot be read.") from exc

    for child in children:
        if len(entries) >= FS_LIST_MAX_ENTRIES:
            break
        if child.name in FS_LIST_IGNORED_NAMES:
            continue
        try:
            if not child.is_dir():
                continue
            child_path = child.resolve(strict=True)
            is_git_repo = (child / ".git").exists()
        except (OSError, RuntimeError, ValueError):
            continue
        entries.append({"name": child.name, "path": str(child_path), "is_git_repo": is_git_repo})

    return {"path": str(resolved), "parent": parent, "entries": entries}


def validate_single_path_segment(name_value: object) -> str:
    name = "" if name_value is None else str(name_value)
    if not name or name in {".", ".."} or "\x00" in name:
        raise FsListBadRequestError("name must be a non-empty single path segment.")
    if "/" in name or "\\" in name or ".." in name or set(name) == {"."}:
        raise FsListBadRequestError("name must be a non-empty single path segment.")
    return name


def create_subdirectory(parent_value: str | Path | None, name_value: object) -> dict[str, object]:
    raw_parent = "" if parent_value is None else str(parent_value).strip()
    if not raw_parent:
        raise FsListBadRequestError("parent is required.")
    parent = Path(raw_parent).expanduser()
    if not parent.is_absolute():
        raise FsListBadRequestError("parent must be absolute.")
    name = validate_single_path_segment(name_value)
    try:
        resolved_parent = parent.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FsListNotFoundError("parent not found.") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise FsListBadRequestError("parent is invalid.") from exc
    if not resolved_parent.is_dir():
        raise FsListNotFoundError("parent is not a directory.")
    target = resolved_parent / name
    if target.exists():
        raise FsMkdirConflictError("path already exists.")
    try:
        target.mkdir()
        resolved_target = target.resolve(strict=True)
    except FileExistsError as exc:
        raise FsMkdirConflictError("path already exists.") from exc
    except OSError as exc:
        raise FsListBadRequestError("directory cannot be created.") from exc
    return {"path": str(resolved_target)}


def _project_directory_base(repos: dict[str, object], fallback: Path) -> Path:
    repo_paths = [Path(str(path)).expanduser() for path in repos.values() if str(path).strip()]
    existing_repo_paths = [path.resolve() for path in repo_paths if path.is_dir()]
    if not existing_repo_paths:
        return fallback

    common_path = Path(os.path.commonpath([str(path) for path in existing_repo_paths]))
    return common_path if common_path.is_dir() else existing_repo_paths[0].parent


def _slug(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(part for part in cleaned.split("-") if part) or "project"


def _validate_directory_path(label: str, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} path must be absolute.")
    if not path.is_dir():
        raise ValueError(f"{label} path does not exist or is not a directory.")
    return path


def _single_line_config_value(label: str, value: str, *, default: str = "") -> str:
    text = value.strip() or default
    if "\n" in text or "\r" in text:
        raise ValueError(f"{label} must be a single line.")
    return text


def _config_scalar(label: str, value: str, *, default: str = "") -> str:
    text = _single_line_config_value(label, value, default=default)
    if not text:
        return ""
    if any(char in text for char in [":", "#", '"', "'"]) or text != text.strip():
        return json.dumps(text)
    return text


def _config_mapping_key(label: str, value: str) -> str:
    key = _single_line_config_value(label, value)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise ValueError(f"{label} may only contain letters, numbers, underscores, and hyphens.")
    return key


GIT_INIT_SUBPROCESS_TIMEOUT_SECONDS = 30


def _request_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _run_git_project_command(
    command: list[str],
    root_path: Path,
    warnings: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            cwd=root_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=GIT_INIT_SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        warnings.append(f"{command[0]} not available")
    except subprocess.TimeoutExpired:
        warnings.append(f"{command[0]} timed out")
    except OSError as exc:
        warnings.append(f"{command[0]} failed: {exc.__class__.__name__}")
    return None


def _git_configured_identity(root_path: Path, warnings: list[str]) -> bool:
    name = _run_git_project_command(["git", "-C", str(root_path), "config", "--get", "user.name"], root_path, warnings)
    email = _run_git_project_command(["git", "-C", str(root_path), "config", "--get", "user.email"], root_path, warnings)
    return bool(
        name is not None
        and name.returncode == 0
        and name.stdout.strip()
        and email is not None
        and email.returncode == 0
        and email.stdout.strip()
    )


def _initialize_project_git(
    *,
    root_path: Path,
    project_name: str,
    project_key: str,
    init_git: bool,
    create_remote: bool,
    remote_private: bool,
) -> dict[str, object]:
    result: dict[str, object] = {"initialized": False, "remote_created": False, "warnings": []}
    warnings = result["warnings"]
    assert isinstance(warnings, list)
    existing_git = (root_path / ".git").exists()

    if existing_git:
        warnings.append("git init skipped: project root already contains .git")
    elif init_git:
        readme = root_path / "README.md"
        gitignore = root_path / ".gitignore"
        try:
            if not readme.exists():
                readme.write_text(f"# {project_name}\n", encoding="utf-8")
            if not gitignore.exists():
                gitignore.write_text(".DS_Store\n.env\n.venv/\n__pycache__/\n", encoding="utf-8")
        except OSError as exc:
            warnings.append(f"git scaffold files skipped: {exc.__class__.__name__}")

        init = _run_git_project_command(["git", "-C", str(root_path), "init", "-b", "main"], root_path, warnings)
        if init is not None and init.returncode != 0:
            fallback = _run_git_project_command(["git", "-C", str(root_path), "init"], root_path, warnings)
            if fallback is not None and fallback.returncode == 0:
                _run_git_project_command(["git", "-C", str(root_path), "branch", "-M", "main"], root_path, warnings)
                init = fallback
        if init is None or init.returncode != 0:
            stderr = "" if init is None else init.stderr.strip()
            warnings.append(f"git init failed{f': {stderr}' if stderr else ''}")
        else:
            add = _run_git_project_command(["git", "-C", str(root_path), "add", "-A"], root_path, warnings)
            if add is None or add.returncode != 0:
                stderr = "" if add is None else add.stderr.strip()
                warnings.append(f"git add failed{f': {stderr}' if stderr else ''}")
            else:
                commit_command = ["git", "-C", str(root_path), "commit", "-m", "Initial commit (GoodTeam scaffold)"]
                if not _git_configured_identity(root_path, warnings):
                    commit_command[3:3] = ["-c", "user.name=GoodTeam", "-c", "user.email=noreply@goodteam.local"]
                commit = _run_git_project_command(commit_command, root_path, warnings)
                if commit is None or commit.returncode != 0:
                    stderr = "" if commit is None else commit.stderr.strip()
                    warnings.append(f"git commit failed{f': {stderr}' if stderr else ''}")
                else:
                    result["initialized"] = True

    if create_remote:
        if existing_git:
            warnings.append("create-remote skipped: project root already contains .git")
        else:
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            if not token:
                warnings.append("create-remote requested but no GITHUB_TOKEN/GH_TOKEN set")
            else:
                visibility = "--private" if remote_private else "--public"
                gh = _run_git_project_command(
                    [
                        "gh",
                        "repo",
                        "create",
                        project_key.lower(),
                        visibility,
                        "--source",
                        str(root_path),
                        "--remote",
                        "origin",
                        "--push",
                    ],
                    root_path,
                    warnings,
                    env={**os.environ, "GH_TOKEN": token},
                )
                if gh is not None and gh.returncode == 0:
                    result["remote_created"] = True
                else:
                    stderr = "" if gh is None else gh.stderr.strip()
                    warnings.append(f"gh repo create failed{f': {stderr}' if stderr else ''}")
    return result


def initialize_project_git_for_request(workspace: Path, request: dict[str, object]) -> dict[str, object]:
    project_key = str(request.get("project_key", "") or "").strip()
    if not project_key:
        raise ValueError("project_key is required.")
    project_config = _project_config_for_key(workspace, project_key)
    target = load_project_target(project_config)
    root_path = resolve_project_runtime_root(dict(target.get("repos", {})))
    if root_path is None:
        raise ValueError(f"No runtime root found for project: {normalize_project_key(project_key)}")
    project_name = str(target.get("name", "") or target.get("key", "") or project_key)
    return _initialize_project_git(
        root_path=root_path,
        project_name=project_name,
        project_key=project_key,
        init_git=True,
        create_remote=_request_bool(request.get("create_remote"), False),
        remote_private=_request_bool(request.get("remote_private"), True),
    )


def create_local_project_config(
    *,
    workspace: Path,
    project_name: str,
    project_key: str,
    project_root: str = "",
    docs_path: str = "",
    backend_path: str = "",
    frontend_path: str = "",
    task_sync: list[str] | None = None,
    plane_api_host_url: str = "",
    plane_workspace_slug: str = "",
    plane_project_id: str = "",
    plane_api_key_env: str = "",
    plane_mcp_server: str = "",
    worker_backend_mode: str = "codex",
    workers: dict[str, str] | None = None,
    model_profile: str = "balanced",
    delivery_mode: str = "init_project",
    delivery_standards: str = "",
    role_model_overrides: dict[str, str] | None = None,
    deployment_backend_mode: str = "",
    staging_ssh_target: str = "",
    staging_remote_dir: str = "",
    staging_git_ref: str = "",
    staging_public_url: str = "",
    staging_deploy_command: str = "",
    staging_healthcheck_command: str = "",
    staging_smoke_command: str = "",
    delivery_contract_required: bool = False,
    delivery_contract_minimum_evidence: str = "infrastructure",
    delivery_contract_fatal_patterns: str = "",
    delivery_contract_acceptance: str = "",
    init_git: bool = True,
    create_remote: bool = False,
    remote_private: bool = True,
) -> dict[str, object]:
    name = _single_line_config_value("Project name", project_name)
    key = _single_line_config_value("Project key", project_key).upper()
    if not name:
        raise ValueError("Project name is required.")
    if not key:
        raise ValueError("Project key is required.")

    repos = []
    if project_root.strip():
        root_path = _validate_directory_path("Project root", project_root)
        repos.append(("root", root_path))
        try:
            if not (root_path / "docs").exists():
                create_subdirectory(root_path, "docs")
        except FsMkdirConflictError:
            pass
        except Exception as exc:
            import warnings

            warnings.warn(f"project docs directory creation skipped for {root_path}: {exc}", RuntimeWarning, stacklevel=2)
    else:
        if docs_path.strip():
            repos.append(("docs", _validate_directory_path("Docs", docs_path)))
        if backend_path.strip():
            repos.append(("backend", _validate_directory_path("Backend", backend_path)))
        if frontend_path.strip():
            repos.append(("frontend", _validate_directory_path("Frontend", frontend_path)))
    if not repos:
        raise ValueError("Select at least one project directory.")

    git_result: dict[str, object] = {"initialized": False, "remote_created": False, "warnings": []}
    if project_root.strip():
        try:
            git_result = _initialize_project_git(
                root_path=root_path,
                project_name=name,
                project_key=key,
                init_git=init_git,
                create_remote=create_remote,
                remote_private=remote_private,
            )
        except Exception as exc:
            git_result["warnings"].append(f"git setup skipped: {exc.__class__.__name__}")

    path = workspace / "projects" / f"{_slug(key)}.local.yaml"
    profile = _single_line_config_value("Model profile", model_profile, default="balanced")
    mode = normalize_delivery_mode(delivery_mode)
    overrides = {
        _config_mapping_key("Role model override role", str(role)): _single_line_config_value(
            "Role model override model", str(model)
        )
        for role, model in (role_model_overrides or {}).items()
        if str(role).strip() and str(model).strip()
    }
    lines = [
        f"name: {_config_scalar('Project name', name)}",
        f"key: {_config_scalar('Project key', key)}",
        f"model_profile: {_config_scalar('Model profile', profile)}",
        f"delivery_mode: {mode}",
    ]
    custom_standards = [line.strip() for line in delivery_standards.splitlines() if line.strip()]
    if mode == "custom" and custom_standards:
        lines.append("delivery_standards:")
        lines.extend(
            f"  standard_{index:04d}: {_config_scalar('Delivery standard', standard)}"
            for index, standard in enumerate(custom_standards, start=1)
        )
    if overrides:
        lines.append("role_model_overrides:")
        lines.extend(f"  {role}: {_config_scalar('Role model override model', model)}" for role, model in sorted(overrides.items()))
    lines.extend([
        "repos:",
    ])
    lines.extend(f"  {repo}: {_config_scalar('Repo path', str(repo_path))}" for repo, repo_path in repos)
    sync_values = [_single_line_config_value("Task sync value", str(value)) for value in (task_sync or []) if value]
    lines.extend(["task_backend:", "  mode: local", "  sync:"])
    if sync_values:
        lines[-1] = "  sync: " + ",".join(_config_scalar("Task sync value", value) for value in sync_values)
    else:
        lines[-1] = "  sync: "
    plane_config = {
        "api_host_url": _single_line_config_value("Plane API host URL", plane_api_host_url),
        "workspace_slug": _single_line_config_value("Plane workspace slug", plane_workspace_slug),
        "project_id": _single_line_config_value("Plane project ID", plane_project_id),
        "api_key_env": _single_line_config_value("Plane API key env", plane_api_key_env, default="PLANE_API_KEY"),
        "mcp_server": _single_line_config_value("Plane MCP server", plane_mcp_server, default="plane"),
    }
    if "plane" in sync_values:
        lines.append("  plane:")
        lines.extend(f"    {key}: {_config_scalar('Plane config value', value)}" for key, value in plane_config.items() if value)
    worker_mode = _single_line_config_value("Worker backend mode", worker_backend_mode, default="codex")
    worker_overrides = {
        _config_mapping_key("Worker role", str(role)): _single_line_config_value("Worker backend", str(backend))
        for role, backend in (workers or {}).items()
        if str(role).strip() and str(backend).strip()
    }
    _validate_worker_backend_config(mode=worker_mode, workers=worker_overrides, role_models=overrides)
    lines.extend(["worker_backend:", f"  mode: {_config_scalar('Worker backend mode', worker_mode)}"])
    if worker_overrides:
        lines.append("workers:")
        lines.extend(
            f"  {role}: {_config_scalar('Worker backend', backend)}"
            for role, backend in _ordered_worker_overrides(worker_overrides).items()
        )
    deployment_backend = {
        "mode": _single_line_config_value("Deployment backend mode", deployment_backend_mode),
        "staging_ssh_target": _single_line_config_value("Staging SSH target", staging_ssh_target),
        "staging_remote_dir": _single_line_config_value("Staging remote dir", staging_remote_dir),
        "staging_git_ref": _single_line_config_value("Staging git ref", staging_git_ref),
        "staging_public_url": _single_line_config_value("Staging public URL", staging_public_url),
        "staging_deploy_command": _single_line_config_value("Staging deploy command", staging_deploy_command),
        "staging_healthcheck_command": _single_line_config_value(
            "Staging healthcheck command", staging_healthcheck_command
        ),
        "staging_smoke_command": _single_line_config_value("Staging smoke command", staging_smoke_command),
    }
    if deployment_backend["mode"]:
        lines.extend(["deployment_backend:", f"  mode: {_config_scalar('Deployment backend mode', deployment_backend['mode'])}"])
        for config_key in [
            "staging_ssh_target",
            "staging_remote_dir",
            "staging_git_ref",
            "staging_public_url",
            "staging_deploy_command",
            "staging_healthcheck_command",
            "staging_smoke_command",
        ]:
            value = deployment_backend[config_key]
            if value:
                lines.append(f"  {config_key}: {_config_scalar(config_key, value)}")
    delivery_contract_lines = _delivery_contract_lines(
        required=delivery_contract_required,
        minimum_evidence=delivery_contract_minimum_evidence,
        fatal_patterns=delivery_contract_fatal_patterns,
        acceptance=delivery_contract_acceptance,
    )
    if delivery_contract_lines:
        lines.extend(delivery_contract_lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    task_backend: dict[str, object] = {"mode": "local", "sync": sync_values}
    if "plane" in sync_values:
        task_backend["plane"] = plane_config
    return {
        "project_config": str(path),
        "project_key": key,
        "project_name": name,
        "repos": {repo: str(repo_path) for repo, repo_path in repos},
        "task_backend": task_backend,
        "worker_backend": {"mode": worker_mode},
        "deployment_backend": {"mode": deployment_backend["mode"], **({key: value for key, value in deployment_backend.items() if key != "mode" and value})},
        "delivery_contract": delivery_contract_report(load_project_target(path)),
        "model_profile": profile,
        "delivery_mode": mode,
        "delivery_standards": custom_standards if mode == "custom" else [],
        "role_model_overrides": overrides,
        "git": git_result,
    }


def _delivery_contract_lines(
    *,
    required: bool,
    minimum_evidence: str,
    fatal_patterns: str,
    acceptance: str,
) -> list[str]:
    pattern_values = [
        _single_line_config_value("Delivery fatal pattern", line)
        for line in fatal_patterns.splitlines()
        if line.strip()
    ]
    checks = []
    for raw_line in acceptance.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|", 2)]
        if len(parts) != 3 or not all(parts):
            continue
        check_id, check_type, command = parts
        if check_type not in {"ssh_command", "local_command"}:
            check_type = "ssh_command"
        checks.append(
            (
                _single_line_config_value("Delivery acceptance check ID", check_id),
                _single_line_config_value("Delivery acceptance check type", check_type),
                _single_line_config_value("Delivery acceptance command", command),
            )
        )
    if not required and not pattern_values and not checks:
        return []
    evidence = _single_line_config_value(
        "Delivery contract minimum evidence",
        minimum_evidence,
        default="functional" if required else "infrastructure",
    )
    lines = [
        "delivery_contract:",
        f"  required: {'true' if required else 'false'}",
        f"  minimum_evidence: {evidence}",
    ]
    if pattern_values:
        lines.append("  fatal_output_patterns:")
        for index, pattern in enumerate(pattern_values, start=1):
            lines.append(f"    pattern_{index}: {_config_scalar('Delivery fatal pattern', pattern)}")
    if checks:
        lines.append("  staging_acceptance:")
        for index, (check_id, check_type, command) in enumerate(checks, start=1):
            lines.extend(
                [
                    f"    check_{index}:",
                    f"      id: {_config_scalar('Delivery acceptance check ID', check_id)}",
                    f"      type: {_config_scalar('Delivery acceptance check type', check_type)}",
                    f"      command: {_config_scalar('Delivery acceptance command', command)}",
                ]
            )
    return lines


def workflow_steps_for_state(state: AgileDevState | None) -> list[dict[str, str]]:
    source_type = state.source_type if state is not None else "new_project"
    return workflow_steps_for_source(source_type)


def resolve_dashboard_state(workspace: Path, project_key: str = "") -> AgileDevState | None:
    store = StateStore(workspace)
    if project_key:
        return store.read_project_current(project_key)
    return store.read_current()


def build_workflow_graph(workspace: Path, project_key: str = "") -> dict[str, object]:
    def _map_to_graph_node(node: str) -> str:
        if node.endswith("_review_gate"):
            return node.removesuffix("_review_gate")
        if node.startswith("await_") and node.endswith("_approval"):
            return node.removeprefix("await_").removesuffix("_approval")
        return node

    key = ""
    if project_key.strip():
        try:
            key = normalize_project_key(project_key)
        except ValueError:
            key = ""

    edge_fields = (
        ("on_approved_next", "approved"),
        ("on_changes_requested", "changes_requested"),
        ("on_rejected", "rejected"),
    )
    edges: list[dict[str, str]] = []
    identifiers = set(WORKFLOW_NODES) | set(GATE_TRANSITIONS)
    for gate, transition in GATE_TRANSITIONS.items():
        identifiers.add(gate)
        identifiers.add(transition.gate)
        for field, edge_type in edge_fields:
            target = getattr(transition, field, None)
            if target:
                edges.append({"from": transition.gate, "to": target, "type": edge_type})
                identifiers.add(target)
    for node in WORKFLOW_NODES.values():
        for dependency in getattr(node, "depends_on", ()):
            edges.append({"from": dependency, "to": node.name, "type": "dependency"})
            identifiers.add(node.name)
            identifiers.add(dependency)
        for field, edge_type in (("on_error", "error"), ("on_blocked", "blocked")):
            target = getattr(node, field, "")
            if target:
                edges.append({"from": node.name, "to": target, "type": edge_type})
                identifiers.add(node.name)
                identifiers.add(target)

    nodes = []
    for identifier in sorted(identifiers):
        workflow_node = WORKFLOW_NODES.get(identifier)
        if workflow_node is not None:
            nodes.append(
                {
                    "name": workflow_node.name,
                    "gate": workflow_node.run_guard_gate,
                    "runner": workflow_node.runner_fn,
                    "kind": "stage",
                }
            )
        elif identifier in GATE_TRANSITIONS:
            nodes.append({"name": identifier, "kind": "gate"})
        elif identifier in {"rejected", "blocked"}:
            nodes.append({"name": identifier, "kind": "terminal"})
        elif identifier.startswith("revise_"):
            nodes.append({"name": identifier, "kind": "revise"})
        else:
            nodes.append({"name": identifier, "kind": "role"})

    run = None
    if key:
        state = resolve_dashboard_state(workspace, project_key=key)
        if is_active_run(state):
            run = {
                "run_id": state.run_id,
                "current_node": state.current_node,
                "next_node": state.next_node,
                "current_graph_node": _map_to_graph_node(state.current_node),
                "next_graph_node": _map_to_graph_node(state.next_node),
                "approvals": {gate: str(decision) for gate, decision in state.approvals.items()},
            }

    return {
        "project_key": key,
        "nodes": nodes,
        "edges": edges,
        "execution_plan": _workflow_execution_plan_payload(),
        "workflow_execution_plan_v2": _workflow_execution_plan_v2_payload(),
        "run": run,
    }


def _workflow_execution_plan_payload() -> dict[str, object]:
    try:
        return workflow_execution_plan(WORKFLOW_NODES, gate_transitions=GATE_TRANSITIONS).to_dict()
    except ValueError as exc:
        return {
            "scheduler": "unavailable",
            "parallel_level_execution": "deferred",
            "levels": [],
            "error": str(exc),
        }


def _workflow_execution_plan_v2_payload() -> dict[str, object]:
    return workflow_execution_plan_v2(WORKFLOW_NODES, gate_transitions=GATE_TRANSITIONS)


def is_active_run(state: AgileDevState | None) -> bool:
    return state is not None and bool(state.next_node)


_DASHBOARD_STEPPER_PRESENTATION: dict[str, tuple[dict[str, str], ...]] = {
    "takeover": (
        {"label": "Intake", "node": "takeover_review_gate", "approval": "takeover"},
        {"label": "Research", "node": "research", "artifact": "research_report"},
    ),
    "product_requirement": ({"label": "PRD", "node": "product_requirement", "artifact": "prd", "approval": "prd"},),
    "prd": (),
    "architect": ({"label": "RFC", "node": "architect", "artifact": "rfc"},),
    "rfc": ({"label": "Arch Rev", "node": "rfc_review_gate", "approval": "rfc"},),
    "security": (
        {"label": "Security", "node": "security_review_gate", "approval": "security", "artifact": "rfc"},
    ),
    "planner": (
        {"label": "Planning", "node": "planner", "artifact": "task_breakdown"},
        {"label": "UI Design", "node": "ui", "artifact": "ui_spec"},
        {"label": "Impl", "node": "implementation", "artifact": "implementation_handoff"},
        {"label": "QA", "node": "qa", "artifact": "qa_report"},
        {"label": "Code Review", "node": "code_review", "artifact": "code_review"},
        {"label": "Staging", "node": "staging", "approval": "staging", "artifact": "staging_report"},
    ),
    "staging": (),
    "release": ({"label": "Release", "node": "release", "approval": "release", "artifact": "release_notes"},),
    "retro_memory": ({"label": "Retro", "node": "retro_memory", "artifact": "retro_memory"},),
}


def _dashboard_stepper_table_order() -> list[str]:
    return ["takeover", *workflow_execution_plan(WORKFLOW_NODES, gate_transitions=GATE_TRANSITIONS).order]


def _dashboard_stepper_steps() -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    for identifier in _dashboard_stepper_table_order():
        for step in _DASHBOARD_STEPPER_PRESENTATION.get(identifier, ()):
            steps.append(dict(step))
    return steps


def _dashboard_stepper_node_sequence(steps: list[dict[str, str]]) -> dict[str, int]:
    sequence: dict[str, int] = {}
    for index, step in enumerate(steps, start=1):
        sequence[step["node"]] = index
        approval = step.get("approval")
        if approval:
            sequence[f"await_{approval}_approval"] = index
    for gate, transition in GATE_TRANSITIONS.items():
        await_state = f"await_{gate}_approval"
        if await_state in sequence:
            continue
        if gate in sequence:
            sequence[await_state] = sequence[gate]
        elif transition.on_approved_next in sequence:
            sequence[await_state] = sequence[transition.on_approved_next]
    sequence["complete"] = len(steps) + 1
    return sequence


def _normalize_start_delivery_standards(value: list[str] | str) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def start_workflow(
    *,
    workspace: Path,
    idea: str,
    project_key: str,
    model_config: Path,
    model_profile: str,
    delivery_mode: str = "",
    delivery_standards: list[str] | str = "",
    source_type: str = "new_project",
    project_config: Path | None = None,
    role_model_overrides: list[str] | None = None,
    load_profile: str = "greenfield",
    dry_run: bool = False,
) -> dict[str, object]:
    if not idea.strip():
        raise ValueError("Workflow goal is required.")
    key = normalize_project_key(project_key)
    if not dry_run and is_active_run(resolve_dashboard_state(workspace, project_key=key)):
        raise ValueError("An active run already exists. Finish, archive, or resume it before starting another run.")

    if source_type not in {"new_project", "existing_project"}:
        raise ValueError(f"Unknown workflow source type: {source_type}")
    if source_type == "existing_project" and project_config is None:
        raise ValueError("Project config is required for existing project adoption.")

    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    resolved_profile, role_models = resolve_project_model_profile(
        model_config,
        target,
        explicit_profile=model_profile,
        role_overrides=role_model_overrides or [],
    )
    state = create_initial_state(
        run_id=str(uuid4()),
        project_key=key,
        idea=idea.strip(),
    )
    state.source_type = source_type
    state.source_ref = str(project_config) if source_type == "existing_project" and project_config is not None else "manual"
    state.model_profile = resolved_profile
    state.delivery_mode = normalize_delivery_mode(delivery_mode or str(target.get("delivery_mode", "") or "init_project"))
    state.delivery_standards = _normalize_start_delivery_standards(delivery_standards) if state.delivery_mode == "custom" else []
    state.role_models = role_models
    profile = resolve_profile(load_profile or "greenfield")
    state.load_profile = profile.name
    use_fix_profile = profile.name == "fix" and source_type != "existing_project"

    if dry_run:
        if source_type == "existing_project":
            planned_artifacts = {"takeover": str(workspace / "artifacts" / "runs" / state.run_id / "takeover" / "takeover-context.md")}
            state.current_node = "takeover_review_gate"
            state.next_node = "await_takeover_approval"
            state.approvals["takeover"] = ApprovalDecision.PENDING
        elif use_fix_profile:
            planned_artifacts = {"fix_plan": str(workspace / "artifacts" / "runs" / state.run_id / "fix_plan" / "fix-plan.md")}
            state.current_node = "fix_plan_review_gate"
            state.next_node = "await_fix_plan_approval"
            state.approvals["fix_plan"] = ApprovalDecision.PENDING
        else:
            planned_artifacts = {"prd": str(workspace / "artifacts" / "runs" / state.run_id / "prd" / "prd.md")}
            state.current_node = "prd_review_gate"
            state.next_node = "await_prd_approval"
        payload = asdict(state)
        payload["dry_run"] = True
        payload["planned_artifacts"] = planned_artifacts
        return payload

    if source_type == "existing_project":
        result = run_adoption_until_next_gate(state=state, workspace=workspace, project_config=project_config)
    elif use_fix_profile:
        result = run_fix_until_next_gate(state=state, workspace=workspace, project_config=project_config)
    else:
        result = run_until_next_gate(state=state, workspace=workspace, project_config=project_config)
    state_store = StateStore(root=workspace)
    state_store.write_project_current(result)
    state_store.write_project_checkpoint(result)
    append_action(
        workspace,
        run_id=result.run_id,
        actor="human",
        action="workflow_started",
        project_key=result.project_key,
        before_node="",
        after_node=result.next_node,
    )
    for artifact, artifact_path in result.artifacts.items():
        if artifact in {"task_ledger"}:
            continue
        append_action(
            workspace,
            run_id=result.run_id,
            actor="system",
            action=f"{artifact}_generated",
            project_key=result.project_key,
            before_node="",
            after_node=result.next_node,
            artifact=artifact,
            artifact_path=artifact_path,
        )
    notify_current_approval_gate(result, workspace=workspace)
    payload = asdict(result)
    payload["dry_run"] = False
    return payload


def report_bug(
    *,
    workspace: Path,
    project_key: str,
    description: str,
    title: str = "",
    severity: str = "p2",
) -> dict[str, object]:
    """Start a lightweight `fix` workflow run from a human-reported bug.

    Builds the defect context (`[BUG] ...`) into the run idea and dispatches the
    `fix` load profile, which stops at the `await_fix_plan_approval` gate with a
    `fix_plan` artifact. The active-run guard in `start_workflow` still applies;
    its ValueError propagates so the route can map it to a 400.
    """
    title = (title or "").strip()
    description = (description or "").strip()
    severity = (severity or "p2").strip() or "p2"
    if not description and not title:
        raise ValueError("Bug description is required.")

    # Resolve the project's config so the fix run inherits repo/model context.
    # Config-light projects may have none — pass through.
    project_config: Path | None
    try:
        project_config = _project_config_for_key(workspace, project_key)
    except ValueError:
        project_config = None

    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    model_profile = str(target.get("model_profile", "") or "") or "balanced"

    heading = f"[BUG][{severity}] {title}".strip() if title else f"[BUG][{severity}]"
    idea = f"{heading}\n\n{description}".strip()

    result = start_workflow(
        workspace=workspace,
        idea=idea,
        project_key=project_key,
        model_config=Path("models.yaml"),
        model_profile=model_profile,
        source_type="new_project",
        project_config=project_config,
        load_profile="fix",
    )
    result["severity"] = severity
    result["title"] = title
    return result


def decide_approval(
    *,
    workspace: Path,
    approval: str,
    decision: str,
    project_key: str = "",
    debate_ref: str = "",
    note: str = "",
    decision_origin: str = "web",
    actor_ref: str = "",
    review_mode: str = "",
    review_session_id: str = "",
    client_context: dict[str, object] | None = None,
    review_items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    try:
        approval_decision = ApprovalDecision(decision)
    except ValueError as exc:
        raise ValueError(f"Unknown approval decision: {decision}") from exc

    state_store = StateStore(workspace)
    state = state_store.read_project_current(project_key) if project_key else state_store.read_current()
    if state is None:
        raise ValueError("No active run to approve.")
    key = normalize_project_key(project_key or state.project_key)

    # Resolve the approved-next target through the run's load profile. Greenfield
    # keeps the historical module-function call path (so monkeypatch-based tests
    # that spy on web.approved_next_for_gate stay valid, including its
    # "Unknown approval gate" raise ordering); other profiles (e.g. fix) resolve
    # against their own captured gate-transition table.
    profile = resolve_profile(getattr(state, "load_profile", "") or "greenfield")
    if profile.name == "greenfield":
        approved_next = approved_next_for_gate(approval)
    else:
        approved_next = profile.approved_next_for_gate(approval)

    if approval not in state.approvals:
        raise ValueError(f"Approval gate is not present in current state: {approval}")
    if state.next_node != f"await_{approval}_approval":
        raise ValueError(f"Approval gate is not currently actionable: {approval}")

    resolved_actor_ref = actor_ref
    if not resolved_actor_ref and decision_origin == "web":
        resolved_actor_ref = os.getenv("GOODTEAM_OPERATOR_REF") or "web:local"
    evidence_snapshot = build_approval_evidence_snapshot(
        state=state,
        workspace=workspace,
        project_key=key,
        debate_ref=debate_ref,
    )
    before_node = state.next_node
    state.approvals[approval] = approval_decision
    state.current_node = f"{approval}_review_gate"
    state.next_node = gate_next_node(state, gate=approval, approved_next=approved_next)
    append_action(
        workspace,
        run_id=state.run_id,
        actor="human",
        action="approval_decided",
        project_key=key,
        gate=approval,
        decision=str(approval_decision),
        before_node=before_node,
        after_node=state.next_node,
        artifact=_approval_artifact_for_gate(approval),
        artifact_path=state.artifacts.get(_approval_artifact_for_gate(approval), ""),
        debate_ref=debate_ref,
        note=note.strip(),
        decision_origin=decision_origin,
        actor_ref=resolved_actor_ref,
        review_mode=review_mode,
        review_session_id=review_session_id,
        client_context=client_context,
        evidence_snapshot=evidence_snapshot,
        review_items=review_items,
    )
    if approval == "takeover" and approval_decision == ApprovalDecision.APPROVED:
        regenerate_before_node = state.next_node
        state_store.write_project_current(state, key)
        state_store.write_project_checkpoint(state, key)
        project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
        state = run_until_next_gate(state=state, workspace=workspace, project_config=project_config)
        append_action(
            workspace,
            run_id=state.run_id,
            actor="system",
            action="prd_generated",
            project_key=key,
            gate="prd",
            before_node=regenerate_before_node,
            after_node=state.next_node,
            artifact="prd",
            artifact_path=state.artifacts.get("prd", ""),
        )
    elif approval_decision == ApprovalDecision.APPROVED:
        generated_before = set(state.artifacts)
        project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
        if project_config is None and project_key:
            try:
                project_config = _project_config_for_key(workspace, project_key)
            except ValueError:
                project_config = None
        state = advance_approved_workflow(state=state, workspace=workspace, project_config=project_config)
        for artifact in sorted(set(state.artifacts) - generated_before):
            append_action(
                workspace,
                run_id=state.run_id,
                actor="system",
                action=f"{artifact}_generated",
                project_key=key,
                gate=approval,
                before_node=before_node,
                after_node=state.next_node,
                artifact=artifact,
                artifact_path=state.artifacts.get(artifact, ""),
            )
    elif approval in {"prd", "rfc"} and approval_decision == ApprovalDecision.REJECTED:
        regenerate_before_node = state.next_node
        state.approvals[approval] = ApprovalDecision.PENDING
        project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
        if project_config is None and project_key:
            try:
                project_config = _project_config_for_key(workspace, project_key)
            except ValueError:
                project_config = None
        if approval == "prd":
            state = run_until_next_gate(state=state, workspace=workspace, project_config=project_config)
        else:
            state = regenerate_rfc_review_gate(state=state, workspace=workspace, project_config=project_config)
        append_action(
            workspace,
            run_id=state.run_id,
            actor="system",
            action=f"{approval}_regenerated",
            project_key=key,
            gate=approval,
            before_node=regenerate_before_node,
            after_node=state.next_node,
            artifact=approval_artifact_map().get(approval, approval),
            artifact_path=state.artifacts.get(approval_artifact_map().get(approval, approval), ""),
        )
    elif approval in {"prd", "rfc"} and approval_decision == ApprovalDecision.CHANGES_REQUESTED:
        revision_note = note.strip()
        regenerate_before_node = state.next_node
        state.change_requests[approval] = revision_note
        state.approvals[approval] = ApprovalDecision.PENDING
        project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
        if project_config is None and project_key:
            try:
                project_config = _project_config_for_key(workspace, project_key)
            except ValueError:
                project_config = None
        revision_role = "product_requirement" if approval == "prd" else "architect"
        uses_worker_revision = should_generate_artifact_with_worker(project_config, revision_role)
        if approval == "prd":
            state = run_until_next_gate(state=state, workspace=workspace, project_config=project_config)
        else:
            state = regenerate_rfc_review_gate(state=state, workspace=workspace, project_config=project_config)
        if not uses_worker_revision:
            state.change_requests.pop(approval, None)
        append_action(
            workspace,
            run_id=state.run_id,
            actor="system",
            action=f"{approval}_revised",
            project_key=key,
            gate=approval,
            before_node=regenerate_before_node,
            after_node=state.next_node,
            artifact=approval_artifact_map().get(approval, approval),
            artifact_path=state.artifacts.get(approval_artifact_map().get(approval, approval), ""),
            note=revision_note,
        )
    project_config_for_sync = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
    if project_config_for_sync is None or not project_config_for_sync.is_file():
        try:
            project_config_for_sync = _project_config_for_key(workspace, key)
        except ValueError:
            project_config_for_sync = None
    sync_project_stage_tasks(state, project_config_for_sync)
    state_store.write_project_current(state, key)
    state_store.write_project_checkpoint(state, key)
    notify_current_approval_gate(state, workspace=workspace)

    try:
        from goodteam_os.telegram import update_active_tg_review_on_web_decision
        update_active_tg_review_on_web_decision(
            workspace=workspace,
            project_key=key,
            gate=approval,
            decision=decision,
        )
    except Exception:
        pass

    payload = asdict(state)
    payload["decision"] = str(approval_decision)
    return payload


def regenerate_rfc_review_gate(
    *,
    state: AgileDevState,
    workspace: Path,
    project_config: Path | None = None,
) -> AgileDevState:
    if should_generate_artifact_with_worker(project_config, "architect"):
        return run_architecture_until_gate(state=state, workspace=workspace, project_config=project_config)
    rfc_path = ArtifactStore(root=workspace, mirror_root=artifact_mirror_root(project_config)).write_markdown(
        state.run_id,
        "rfc",
        "rfc.md",
        render_downstream_artifact_markdown("rfc", state, project_config),
    )
    register_markdown_artifact(state, "rfc", rfc_path)
    state.current_node = "rfc_review_gate"
    state.next_node = gate_next_node(state, gate="rfc", approved_next="planner")
    sync_project_stage_tasks(state, project_config)
    return state


def retry_blocked_workflow(*, workspace: Path, project_key: str) -> dict[str, object]:
    key = normalize_project_key(project_key)
    state_store = StateStore(workspace)
    state = state_store.read_project_current(key)
    if state is None:
        raise ValueError(f"No active run for project: {key}")
    if state.current_node != "implementation" or state.next_node != "blocked":
        raise ValueError(f"Current run is not blocked at implementation: {key}")

    before_node = state.next_node
    project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
    if project_config is None or not project_config.is_file():
        try:
            project_config = _project_config_for_key(workspace, key)
        except ValueError:
            project_config = None
    state.role_models = refresh_state_role_models(state, project_config)
    state_store.write_project_current(state, key)
    state_store.write_project_checkpoint(state, key)

    generated_before = set(state.artifacts)
    staging_retry = any(blocker.startswith("staging ") for blocker in state.blockers)
    state.blockers = []
    if staging_retry:
        staging_result = run_staging_backend(project_config=project_config)
        from goodteam_os.graph import render_staging_report_markdown
        from goodteam_os.artifacts import ArtifactStore

        store = ArtifactStore(root=workspace, mirror_root=artifact_mirror_root(project_config))
        staging_path = store.write_markdown(
            state.run_id,
            "staging_report",
            "staging-report.md",
            render_staging_report_markdown(state, project_config, staging_result),
        )
        register_markdown_artifact(state, "staging_report", staging_path, reviewable=True)
        if str(staging_result.get("status", "")) != "passed":
            failed_step = str(staging_result.get("failed_step", "") or "deploy")
            state.current_node = "implementation"
            state.next_node = "blocked"
            evidence_gap = str(staging_result.get("evidence_gap", "") or "")
            if failed_step == "acceptance" and evidence_gap:
                state.blockers = [f"staging acceptance failed: {evidence_gap}"]
            else:
                state.blockers = [f"staging {failed_step} failed"]
        else:
            state.current_node = "staging_review_gate"
            state.next_node = "await_staging_approval"
    else:
        state.current_node = "rfc_review_gate"
        state.next_node = "planner"
        state = advance_approved_workflow(state=state, workspace=workspace, project_config=project_config)
    sync_project_stage_tasks(state, project_config)
    state_store.write_project_current(state, key)
    state_store.write_project_checkpoint(state, key)

    append_action(
        workspace,
        run_id=state.run_id,
        actor="human",
        action="blocked_workflow_retried",
        project_key=key,
        before_node=before_node,
        after_node=state.next_node,
    )
    for artifact in sorted(set(state.artifacts) - generated_before):
        append_action(
            workspace,
            run_id=state.run_id,
            actor="system",
            action=f"{artifact}_generated",
            project_key=key,
            before_node=before_node,
            after_node=state.next_node,
            artifact=artifact,
            artifact_path=state.artifacts.get(artifact, ""),
        )

    notify_current_approval_gate(state, workspace=workspace)
    payload = asdict(state)
    payload["retried"] = True
    return payload


def refresh_state_role_models(state: AgileDevState, project_config: Path | None) -> dict[str, dict[str, str]]:
    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    try:
        _profile, role_models = resolve_project_model_profile(
            Path("models.yaml"),
            target,
            explicit_profile=state.model_profile,
            role_overrides=[],
        )
    except Exception:
        return state.role_models
    return role_models


def worker_model_label(state: AgileDevState | None, start_context: dict[str, object] | None = None) -> str:
    if state is not None:
        model = str(dict(state.role_models.get("implementation", {})).get("primary", "")).strip()
        if model:
            return model.removeprefix("codex:")
    overrides = dict((start_context or {}).get("role_model_overrides", {}) or {})
    model = str(overrides.get("implementation", "") or overrides.get("frontend", "") or overrides.get("backend", "") or "").strip()
    if model:
        return model.removeprefix("codex:")
    return DEFAULT_CODEX_MODEL


def terminate_project_run(*, workspace: Path, project_key: str) -> dict[str, object]:
    key = normalize_project_key(project_key)
    state_store = StateStore(workspace)
    state = state_store.read_project_current(key)
    if state is None:
        raise ValueError(f"No active run for project: {key}")
    before_node = state.next_node
    archive_path = state_store.archive_project_current(key, reason="cancelled")
    append_action(
        workspace,
        run_id=state.run_id,
        actor="human",
        action="run_terminated",
        project_key=key,
        before_node=before_node,
        after_node="cancelled",
    )
    return {
        "terminated": True,
        "project_key": key,
        "run_id": state.run_id,
        "archive_path": str(archive_path),
    }


def delete_project(*, workspace: Path, project_key: str) -> dict[str, object]:
    key = normalize_project_key(project_key)
    config_path = _project_config_for_key(workspace, key)
    projects_dir = (workspace / "projects").resolve()
    resolved_config = config_path.resolve()
    if projects_dir not in resolved_config.parents or not resolved_config.name.endswith(".local.yaml"):
        raise ValueError("Project deletion is limited to local project configs under workspace/projects.")

    state_store = StateStore(workspace)
    state_projects_dir = (workspace / "state" / "projects").resolve()
    state_dir = state_store.project_state_dir(key).resolve()
    if state_projects_dir not in state_dir.parents:
        raise ValueError("Project deletion is limited to project-scoped GoodTeam state.")

    resolved_config.unlink()
    if state_dir.exists():
        shutil.rmtree(state_dir)

    return {
        "deleted": True,
        "project_key": key,
        "project_config": str(config_path),
        "state_dir": str(state_store.project_state_dir(key)),
    }


def open_path_in_file_manager(*, workspace: Path, relative_path: str) -> dict[str, object]:
    target_path = (workspace / relative_path).resolve()
    resolved_workspace = workspace.resolve()
    if not target_path.is_relative_to(resolved_workspace):
        raise ValueError("Access denied: Path is outside workspace.")

    if not target_path.exists():
        if target_path.parent.exists():
            target_path = target_path.parent
        else:
            target_path = workspace.resolve()

    import sys
    import subprocess
    path_str = str(target_path)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path_str], check=False)
        elif sys.platform == "win32":
            os.startfile(path_str)
        else:
            subprocess.run(["xdg-open", path_str], check=False)
        return {"opened": True, "path": path_str}
    except Exception as exc:
        return {"opened": False, "error": str(exc)}



def build_role_observations(state: AgileDevState | None) -> list[dict[str, str]]:
    if state is None:
        return []

    observations = []
    for role in list_role_definitions():
        artifact = role["artifact"]
        model_config = state.role_models.get(role["key"], {})
        status = "No activity"
        if artifact == "state":
            status = "Active"
        elif artifact in state.artifacts:
            status = "Artifact ready"
        if artifact in state.approvals and state.next_node == f"await_{artifact}_approval":
            status = "Waiting for approval"
        if role["key"] == state.current_node:
            status = "Active"
        if role["key"] == state.next_node:
            status = "Ready"

        observations.append(
            {
                "name": role["name"],
                "artifact": artifact,
                "status": status,
                "description": role["description"],
                "artifact_path": state.artifacts.get(artifact, ""),
                "model": model_config.get("primary", "unassigned"),
                "mode": model_config.get("mode", "default"),
            }
        )
    return observations


def build_workflow_steps(state: AgileDevState | None) -> list[dict[str, str]]:
    steps = []
    for index, step in enumerate(workflow_steps_for_state(state), start=1):
        artifact = step.get("artifact", "")
        approval = step.get("approval", "")
        status = "not-started"
        status_label = "Not started"
        action_label = ""
        feedback = "No work recorded yet."

        if state is not None:
            approval_value = str(state.approvals.get(str(approval), "")) if approval else ""
            if artifact and artifact in state.artifacts:
                status = "done"
                status_label = "Done"
                feedback = "Artifact is available."
            if approval and approval_value == "pending" and state.next_node == f"await_{approval}_approval":
                status = "review-needed"
                status_label = "Needs review"
                action_label = f"Review {approval.upper()}"
                feedback = "Human review is required before the workflow can continue."
            elif approval and approval_value == "approved":
                status = "done"
                status_label = "Approved"
                feedback = "Approval completed."
            elif approval and approval_value in {"changes_requested", "rejected"}:
                status = "blocked"
                status_label = approval_value.replace("_", " ").title()
                feedback = "Workflow is blocked by this decision."
            elif step.get("node") == state.current_node:
                status = "active"
                status_label = "Active"
                feedback = "Worker is marked as active in current state."
            elif step.get("node") == state.next_node:
                status = "ready"
                status_label = "Ready"
                feedback = "Ready to run; no worker execution or artifact has been recorded yet."

        steps.append(
            {
                "number": str(index),
                "key": str(step["key"]),
                "title": str(step["title"]),
                "roles": str(step["roles"]),
                "artifact": str(artifact),
                "approval": str(approval),
                "summary": str(step["summary"]),
                "feedback": feedback,
                "status": status,
                "status_label": status_label,
                "action_label": action_label,
                "artifact_path": state.artifacts.get(str(artifact), "") if state is not None and artifact else "",
            }
        )
    return steps


def read_artifact_preview(path: str, max_chars: int = 8000) -> str:
    if not path:
        return ""
    artifact_path = Path(path)
    if not artifact_path.is_file():
        return ""
    content = artifact_path.read_text(encoding="utf-8")
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n[Preview truncated]"


def _slug_heading(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(part for part in slug.split("-") if part) or "section"


def _render_inline_markdown(value: str) -> str:
    text = escape(value)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    return text


def render_markdown_preview(markdown: str) -> str:
    lines = markdown.splitlines()
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    code_lines: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{_render_inline_markdown(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            if in_code:
                blocks.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
                code_lines.clear()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            blocks.append(f'<h{level} id="{_slug_heading(title)}">{_render_inline_markdown(title)}</h{level}>')
            continue
        list_item = re.match(r"^[-*]\s+(.+)$", stripped)
        if list_item:
            flush_paragraph()
            list_items.append(_render_inline_markdown(list_item.group(1)))
            continue
        flush_list()
        paragraph.append(stripped)

    if in_code:
        blocks.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
    flush_paragraph()
    flush_list()
    return "\n".join(blocks)


def _resolve_registered_artifact(workspace: Path, artifact_key: str, project_key: str = "") -> tuple[AgileDevState, Path]:
    store = StateStore(workspace)
    state = store.read_project_current(project_key) if project_key else store.read_current()
    if state is None:
        raise ValueError("No active run state found.")
    resource = state.resources.get(artifact_key)
    if resource is not None:
        if resource.kind not in {ResourceKind.MARKDOWN_ARTIFACT, ResourceKind.PROPOSED_MARKDOWN_ARTIFACT}:
            raise ValueError(f"Resource is not a markdown artifact: {artifact_key}")
        if ResourceCapability.READ not in resource.capabilities:
            raise ValueError(f"Resource does not allow read: {artifact_key}")
        artifact_path = resource.path
    else:
        artifact_path = state.artifacts.get(artifact_key, "")
    if not artifact_path:
        raise ValueError(f"Artifact is not registered in current run: {artifact_key}")

    path = Path(artifact_path)
    run_root = (workspace / "artifacts" / "runs" / state.run_id).resolve()
    if path.is_absolute():
        path = path.resolve()
    else:
        candidates = [(workspace / path).resolve(), path.resolve()]
        path = next(
            (
                candidate
                for candidate in candidates
                if run_root in [candidate, *candidate.parents]
            ),
            candidates[0],
        )
    if resource is None or resource.scope == ResourceScope.RUN:
        if run_root not in [path, *path.parents]:
            raise ValueError("Artifact path must stay inside the current run artifacts directory.")
    elif resource.scope != ResourceScope.PROJECT_RUNTIME:
        raise ValueError("Artifact path must stay inside the current run artifacts directory.")
    if path.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError("Only markdown artifacts can be edited in the dashboard.")
    if not path.is_file():
        raise ValueError(f"Artifact file does not exist: {artifact_key}")
    return state, path


def render_file_path(path: str, artifact_key: str = "") -> str:
    if not path or not path.strip():
        return '<span class="empty-path">none</span>'
    p = Path(path)
    art_attr = f' data-artifact="{escape(artifact_key)}"' if artifact_key else ''
    return f"""
    <span class="file-path-chip" data-path="{escape(path)}"{art_attr}>
      <code>{escape(p.name)}</code>
      <span class="file-path-tooltip">{escape(path)}</span>
    </span>
    """


def build_resource_items(state: AgileDevState | None) -> list[dict[str, object]]:
    if state is None:
        return []
    return [
        resource_to_dict(resource)
        for _key, resource in sorted(state.resources.items())
    ]


def read_artifact(*, workspace: Path, artifact_key: str, project_key: str = "") -> dict[str, object]:
    state, path = _resolve_registered_artifact(workspace, artifact_key, project_key)
    return {
        "artifact": artifact_key,
        "path": str(path),
        "content": path.read_text(encoding="utf-8"),
        "resource": resource_to_dict(state.resources[artifact_key]) if artifact_key in state.resources else {},
    }


def read_open_questions(*, workspace: Path, artifact_key: str, project_key: str = "") -> dict[str, object]:
    _, path = _resolve_registered_artifact(workspace, artifact_key, project_key)
    return {
        "artifact": artifact_key,
        "questions": extract_open_questions(path.read_text(encoding="utf-8")),
    }


def answer_open_questions(
    *,
    workspace: Path,
    project_key: str,
    artifact_key: str,
    answers: list[object],
) -> dict[str, object]:
    _, path = _resolve_registered_artifact(workspace, artifact_key, project_key)
    guidance_parts: list[str] = []
    for raw_answer in answers:
        if not isinstance(raw_answer, dict):
            continue
        answer = str(raw_answer.get("answer", "")).strip()
        if not answer:
            continue
        question = str(raw_answer.get("question", "")).strip()
        guidance_parts.append(f"Q: {question}\nA: {answer}\n\n")
    return refine_artifact(
        workspace=workspace,
        project_key=project_key,
        artifact_key=artifact_key,
        content=path.read_text(encoding="utf-8"),
        guidance="".join(guidance_parts),
    )


def save_artifact(*, workspace: Path, artifact_key: str, content: str, project_key: str = "") -> dict[str, object]:
    state, path = _resolve_registered_artifact(workspace, artifact_key, project_key)
    path.write_text(content, encoding="utf-8")

    key = normalize_project_key(project_key or state.project_key)
    project_config = Path(state.source_ref) if state.source_ref and state.source_ref != "manual" else None
    if project_config is None or not project_config.is_file():
        try:
            project_config = _project_config_for_key(workspace, key)
        except ValueError:
            project_config = None
    sync_project_stage_tasks(state, project_config)
    edit_log = StateStore(workspace).project_state_dir(key) / "edit-log.jsonl" if project_key else workspace / "state" / "edit-log.jsonl"
    edit_log.parent.mkdir(parents=True, exist_ok=True)
    edit_event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": state.run_id,
        "artifact": artifact_key,
        "path": str(path),
        "bytes": len(content.encode("utf-8")),
    }
    with edit_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(edit_event) + "\n")

    append_action(
        workspace,
        run_id=state.run_id,
        actor="human",
        action="artifact_saved",
        project_key=key if project_key else "",
        before_node=state.next_node,
        after_node=state.next_node,
        artifact=artifact_key,
        artifact_path=str(path),
    )

    return {
        "artifact": artifact_key,
        "path": str(path),
        "bytes": edit_event["bytes"],
        "saved": True,
    }


def build_approval_reviews(state: AgileDevState | None, *, workspace: Path = Path(".")) -> list[dict[str, object]]:
    if state is None:
        return []

    reviews = []
    approval_artifacts = approval_artifact_map()
    profile = resolve_profile(getattr(state, "load_profile", "") or "greenfield")
    relevant_gates = None if profile.name == "greenfield" else set(profile.gate_names())
    for approval, decision in state.approvals.items():
        if relevant_gates is not None and approval not in relevant_gates:
            continue
        artifact = approval_artifacts.get(approval, approval)
        artifact_path = state.artifacts.get(artifact, "")
        review = {
            "approval": approval,
            "decision": str(decision),
            "is_pending": str(decision) == ApprovalDecision.PENDING,
            "is_actionable": str(decision) == ApprovalDecision.PENDING and state.next_node == f"await_{approval}_approval",
            "artifact": artifact,
            "artifact_path": artifact_path,
            "content": read_artifact_preview(artifact_path),
            "review_roles": PRD_REVIEW_ROLES if approval == "prd" else [],
        }
        advisors = advisors_for_gate(approval)
        if advisors:
            messages = [
                {
                    "id": str(message.get("id", "")),
                    "from_role": str(message.get("from_role", "")),
                    "to": list(message.get("to", [])) if isinstance(message.get("to", []), list) else [],
                    "kind": str(message.get("kind", "")),
                    "gate": str(message.get("gate", "")),
                    "body": str(message.get("body", "")),
                    "in_reply_to": str(message.get("in_reply_to", "")),
                }
                for message in read_messages(workspace, state.project_key, state.run_id, gate=approval)
            ]
            review["committee"] = {
                "advisors": list(advisors),
                "convened": bool(messages),
                "messages": messages,
            }
        if review["is_actionable"]:
            evidence_snapshot = build_approval_evidence_snapshot(
                state=state,
                workspace=workspace,
                project_key=state.project_key,
            )
            review["evidence_snapshot"] = evidence_snapshot
            review["recommendation"] = build_approval_recommendation(
                state=state,
                review=review,
                evidence_snapshot=evidence_snapshot,
                workspace=workspace,
            )
        reviews.append(review)
    return reviews


def build_start_console_context(
    workspace: Path,
    model_config: Path = Path("models.yaml"),
    project_config: Path | None = None,
    project_base: Path | None = None,
) -> dict[str, object]:
    project_base = Path.home() if project_base is None else project_base
    profiles = list_model_profiles(model_config) if model_config.is_file() else ["balanced"]
    project_report: dict[str, object] = {}
    if project_config is not None and project_config.is_file():
        project_report = check_project_target(load_project_target(project_config))
    project = dict(project_report.get("project", {}))
    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    deployment_backend = deployment_backend_config(project_config) if project_config is not None and project_config.is_file() else {"mode": ""}
    delivery_contract = delivery_contract_report(target) if target else delivery_contract_report({})
    directory_base = _project_directory_base(dict(target.get("repos", {})), project_base)
    return {
        "workspace": str(workspace),
        "model_config": str(model_config),
        "project_config": str(project_config or ""),
        "project_report": project_report,
        "profiles": profiles,
        "project_key": str(project.get("key", "GTO") or "GTO"),
        "project_name": str(project.get("name", "GoodTeam") or "GoodTeam"),
        "model_profile": str(target.get("model_profile", "balanced") or "balanced"),
        "delivery_mode": normalize_delivery_mode(str(target.get("delivery_mode", "init_project") or "init_project")),
        "role_model_overrides": dict(target.get("role_model_overrides", {})) if isinstance(target.get("role_model_overrides", {}), dict) else {},
        "task_backend": normalize_task_backend(target.get("task_backend", {"mode": "local", "sync": []})),
        "worker_backend": normalize_worker_backend(target.get("worker_backend", {"mode": "codex"})),
        "worker_backend_options": WORKER_BACKEND_OPTIONS,
        "worker_roles": WORKER_ROLES,
        "deployment_backend": deployment_backend,
        "delivery_contract": delivery_contract,
        "project_base": str(directory_base),
        "candidate_dirs": discover_project_directories(directory_base),
    }


def discover_project_configs(workspace: Path) -> list[Path]:
    project_dir = workspace / "projects"
    if not project_dir.is_dir():
        return []
    paths = [*project_dir.glob("*.local.yaml")]
    unique = sorted({path.resolve(): path for path in paths}.values(), key=lambda item: item.name.lower())
    return unique


def build_project_overview_items(workspace: Path, model_config: Path = Path("models.yaml")) -> list[dict[str, str]]:
    items = []
    store = StateStore(workspace)
    for config_path in discover_project_configs(workspace):
        target = load_project_target(config_path)
        project_key = normalize_project_key(str(target.get("key", config_path.stem)))
        state = store.read_project_current(project_key)
        action_items = build_action_items(workspace, project_key=project_key)
        items.append(
            {
                "project_name": str(target.get("name", project_key) or project_key),
                "project_key": project_key,
                "project_config": str(config_path),
                "status": "active" if is_active_run(state) else "idle",
                "run_id": state.run_id if state is not None else "",
                "next_node": state.next_node if state is not None else "",
                "model_profile": str(target.get("model_profile", "balanced") or "balanced"),
                "delivery_mode": normalize_delivery_mode(str(target.get("delivery_mode", "init_project") or "init_project")),
                "last_action": action_items[0].get("timestamp", "") if action_items else "",
            }
        )
    return items


def _project_config_for_key(workspace: Path, project_key: str) -> Path:
    key = normalize_project_key(project_key)
    for config_path in discover_project_configs(workspace):
        target = load_project_target(config_path)
        if normalize_project_key(str(target.get("key", config_path.stem))) == key:
            return config_path
    fallback = workspace / "projects" / f"{key.lower()}.local.yaml"
    if fallback.is_file():
        return fallback
    raise ValueError(f"No local project config found for project: {key}")


def resolve_project_root_for_request(workspace: Path, request: dict[str, object]) -> Path:
    project_key = str(request.get("project", "") or request.get("project_key", "")).strip()
    if not project_key:
        return workspace
    config_path = _project_config_for_key(workspace, project_key)
    target = load_project_target(config_path)
    repos = target.get("repos", {})
    if isinstance(repos, dict) and repos:
        return resolve_project_runtime_root({str(key): Path(str(value)) for key, value in repos.items()})
    return config_path.parent


CONTEXT_URL_PER_SOURCE_BYTES = 1_048_576
CONTEXT_URL_TOTAL_BYTES = 5_242_880
CONTEXT_URL_TIMEOUT_SECONDS = 5


def context_estimate_for_request(workspace: Path, request: dict[str, object]) -> dict[str, object]:
    project_key = str(request.get("project", "") or request.get("project_key", "")).strip()
    if not project_key:
        raise ValueError("project_key is required")
    project_config, project_root = _context_project_paths(workspace, project_key)
    declared_sources = _normalize_context_sources(request.get("sources", []))
    if declared_sources:
        _persist_context_sources(project_config, declared_sources)
        sources = _context_source_packets(project_root, declared_sources)
        estimate = project_context.estimate_context_tokens(sources)
        needs_generation = project_context.read_context_entry(project_root) is None
    else:
        result = project_context.load_or_generate_context(
            project_config=project_config,
            root=project_root,
            generate=False,
        )
        estimate = result.get("estimate") or {"bytes": 0, "est_tokens": 0, "source_count": 0}
        needs_generation = bool(result.get("needs_generation", result.get("entry") is None))
    return {
        "estimate": estimate,
        "needs_generation": needs_generation,
        "recommended_model": os.getenv("GOODTEAM_CONTEXT_MODEL", "gpt-5.4-mini"),
    }


def context_generate_for_request(workspace: Path, request: dict[str, object]) -> dict[str, object]:
    if request.get("confirm") is not True:
        raise ValueError("confirmation required")
    project_key = str(request.get("project", "") or request.get("project_key", "")).strip()
    if not project_key:
        raise ValueError("project_key is required")
    project_config, project_root = _context_project_paths(workspace, project_key)
    declared_sources = _normalize_context_sources(request.get("sources", []))
    if declared_sources:
        _persist_context_sources(project_config, declared_sources)
        sources = _context_source_packets(project_root, declared_sources)
        estimate = project_context.estimate_context_tokens(sources)
        generated = project_context.generate_context_entry(
            sources=sources,
            root=project_root,
        )
        return {
            "generated": True,
            "understanding_path": generated["understanding_path"],
            "index_path": generated["index_path"],
            "estimate": estimate,
        }
    result = project_context.load_or_generate_context(
        project_config=project_config,
        root=project_root,
        generate=True,
    )
    entry = result.get("entry") or {}
    return {
        "generated": True,
        "understanding_path": str(project_root / project_context.CONTEXT_DIR_NAME / project_context.UNDERSTANDING_FILE),
        "index_path": str(project_root / project_context.CONTEXT_DIR_NAME / project_context.INDEX_FILE),
        "estimate": result.get("estimate") or {"bytes": 0, "est_tokens": 0, "source_count": 0},
        "source": entry.get("source", "generated") if isinstance(entry, dict) else "generated",
    }


def _context_project_paths(workspace: Path, project_key: str) -> tuple[Path, Path]:
    project_config = _project_config_for_key(workspace, project_key)
    target = load_project_target(project_config)
    root = resolve_project_runtime_root(dict(target.get("repos", {})))
    if root is None:
        raise ValueError(f"No project root found for project: {project_key}")
    return project_config, root


def _normalize_context_sources(raw_sources: object) -> list[dict[str, str]]:
    if not isinstance(raw_sources, list):
        return []
    sources: list[dict[str, str]] = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", "")).strip().lower()
        value = str(raw.get("value", "")).strip()
        if kind not in {"file", "dir", "url"} or not value:
            continue
        sources.append({"kind": kind, "value": value})
    return sources


def _context_source_packets(root: Path, declared_sources: list[dict[str, str]]) -> list[dict[str, object]]:
    packets: list[dict[str, object]] = []
    total_url_bytes = 0
    for source in declared_sources:
        kind = source["kind"]
        value = source["value"]
        if kind == "url":
            packet = _fetch_context_url_source(
                value,
                per_source_cap=CONTEXT_URL_PER_SOURCE_BYTES,
                timeout=CONTEXT_URL_TIMEOUT_SECONDS,
            )
            content = str(packet.get("content", ""))
            url_bytes = len(content.encode("utf-8"))
            if url_bytes > CONTEXT_URL_PER_SOURCE_BYTES:
                raise ValueError(f"URL source exceeds {CONTEXT_URL_PER_SOURCE_BYTES} byte cap: {value}")
            total_url_bytes += url_bytes
            if total_url_bytes > CONTEXT_URL_TOTAL_BYTES:
                raise ValueError(f"URL sources exceed {CONTEXT_URL_TOTAL_BYTES} byte total cap")
            packets.append(packet)
            continue
        path = _resolve_context_local_path(root, value)
        if kind == "file":
            if not path.is_file():
                raise ValueError(f"Context file not found: {value}")
            packets.append(
                {
                    "repo": "declared_context",
                    "path": str(path),
                    "purpose": "Declared project context file",
                    "kind": kind,
                    "value": value,
                }
            )
            continue
        if not path.is_dir():
            raise ValueError(f"Context directory not found: {value}")
        packets.append(
            {
                "repo": "declared_context",
                "path": str(path),
                "purpose": "Project context directory",
                "summary": project_context.build_code_skeleton(path),
                "kind": kind,
                "value": value,
            }
        )
    return packets


def _resolve_context_local_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _fetch_context_url_source(url: str, *, per_source_cap: int, timeout: float) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported context URL scheme: {url}")
    with urlopen(url, timeout=timeout) as response:
        body = response.read(per_source_cap + 1)
    content = body.decode("utf-8", errors="replace")
    return {
        "repo": "declared_context",
        "url": url,
        "content": content,
        "purpose": "Untrusted URL context source",
        "kind": "url",
        "value": url,
    }


def _persist_context_sources(project_config: Path, sources: list[dict[str, str]]) -> None:
    existing = project_config.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    skipping = False
    for line in existing:
        if line.startswith("context_sources:"):
            skipping = True
            continue
        if skipping:
            if line and not line.startswith(" "):
                skipping = False
            else:
                continue
        if not skipping:
            kept.append(line)
    kept.append("context_sources:")
    for index, source in enumerate(sources, start=1):
        kept.append(f"  source_{index:04d}:")
        kept.append(f"    kind: {_config_scalar('Context source kind', source['kind'])}")
        kept.append(f"    value: {_config_scalar('Context source value', source['value'])}")
    project_config.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")


def build_dashboard_handler(workspace: Path):
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            normalized_path = self.path
            if normalized_path.startswith("/api/"):
                normalized_path = normalized_path.removeprefix("/api")
            if normalized_path not in {"/context-estimate", "/context-generate", "/project-config"}:
                self.send_error(404)
                return
            if not _request_origin_is_same_origin(
                origin=str(self.headers.get("Origin", "")),
                host=str(self.headers.get("Host", "")),
            ):
                self._send_json(403, {"error": "cross-origin POST requests are not allowed"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                request = json.loads(raw_body.decode("utf-8") or "{}")
                if normalized_path == "/context-estimate":
                    result = context_estimate_for_request(workspace, request)
                elif normalized_path == "/context-generate":
                    result = context_generate_for_request(workspace, request)
                elif (
                    request.get("action") == "update_worker_backends"
                    or (
                        ("project" in request or "project_key" in request)
                        and "workers" in request
                        and "project_name" not in request
                    )
                ):
                    result = update_project_worker_backend_settings(
                        workspace=workspace,
                        request=request,
                    )
                else:
                    result = create_local_project_config(
                        workspace=workspace,
                        project_name=str(request.get("project_name", "")),
                        project_key=str(request.get("project_key", "")),
                        project_root=str(request.get("project_root", "")),
                        docs_path=str(request.get("docs_path", "")),
                        backend_path=str(request.get("backend_path", "")),
                        frontend_path=str(request.get("frontend_path", "")),
                        task_sync=[str(value) for value in request.get("task_sync", [])],
                        plane_api_host_url=str(request.get("plane_api_host_url", "")),
                        plane_workspace_slug=str(request.get("plane_workspace_slug", "")),
                        plane_project_id=str(request.get("plane_project_id", "")),
                        plane_api_key_env=str(request.get("plane_api_key_env", "")),
                        plane_mcp_server=str(request.get("plane_mcp_server", "")),
                        worker_backend_mode=str(request.get("worker_backend_mode", "codex")),
                        workers=dict(request.get("workers", {})),
                        model_profile=str(request.get("model_profile", "balanced")),
                        delivery_mode=str(request.get("delivery_mode", "init_project")),
                        delivery_standards=str(request.get("delivery_standards", "")),
                        role_model_overrides=dict(request.get("role_model_overrides", {})),
                        init_git=_request_bool(request.get("init_git"), True),
                        create_remote=_request_bool(request.get("create_remote"), False),
                        remote_private=_request_bool(request.get("remote_private"), True),
                    )
                self._send_json(200, result)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            _send_cors_headers(self, host="127.0.0.1", port=int(self.server.server_port))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardHandler


def normalize_task_backend(value: object) -> dict[str, object]:
    backend = dict(value) if isinstance(value, dict) else {}
    mode = str(backend.get("mode", "local") or "local")
    sync = backend.get("sync", [])
    if isinstance(sync, list):
        sync_values = [str(item) for item in sync if item]
    elif isinstance(sync, dict):
        sync_values = []
    else:
        sync_values = [part.strip() for part in str(sync).split(",") if part.strip()] if sync else []
    return {"mode": mode, "sync": sync_values}


def normalize_worker_backend(value: object) -> dict[str, object]:
    backend = dict(value) if isinstance(value, dict) else {}
    return {"mode": str(backend.get("mode", "codex") or "codex")}


def update_project_worker_backend_settings(workspace: Path, request: dict[str, object]) -> dict[str, object]:
    project_key = str(request.get("project", "") or request.get("project_key", "")).strip()
    if not project_key:
        raise ValueError("project is required")
    mode = str(request.get("worker_backend_mode", "") or "").strip()
    if not mode:
        raise ValueError("worker_backend_mode is required")
    workers_value = request.get("workers", {})
    if not isinstance(workers_value, dict):
        raise ValueError("workers must be an object")
    workers = {str(role).strip(): str(backend).strip() for role, backend in workers_value.items()}
    role_models_present = "role_models" in request or "role_model_overrides" in request
    role_models_value = request.get("role_models", request.get("role_model_overrides", {}))
    if not isinstance(role_models_value, dict):
        raise ValueError("role_models must be an object")
    role_models = {str(role).strip(): str(model).strip() for role, model in role_models_value.items()}
    _validate_worker_backend_config(mode=mode, workers=workers, role_models=role_models)

    config_path = _project_config_for_key(workspace, project_key)
    target = load_project_target(config_path)
    existing_role_models = target.get("role_model_overrides", {})
    role_model_overrides = (
        {str(role): str(model) for role, model in existing_role_models.items()}
        if not role_models_present and isinstance(existing_role_models, dict)
        else {}
    )
    for role, model in role_models.items():
        if not model or model.lower() == "default":
            role_model_overrides.pop(role, None)
        else:
            role_model_overrides[role] = model
    update_worker_backend_config(
        config_path,
        mode=mode,
        workers=_ordered_worker_overrides(workers),
        role_model_overrides=_ordered_role_model_overrides(role_model_overrides),
    )
    report = check_project_target(load_project_target(config_path))
    return {
        "ok": True,
        "role_worker_backends": report["role_worker_backends"],
        "worker_backends_preflight": report["worker_backends_preflight"],
        "role_model_overrides": _ordered_role_model_overrides(role_model_overrides),
    }


def _validate_worker_backend_config(*, mode: str, workers: dict[str, str], role_models: dict[str, str] | None = None) -> None:
    valid_backends = set(WORKER_BACKEND_ACCEPTED_OPTIONS)
    if mode not in valid_backends:
        raise ValueError(f"Unknown worker backend: {mode}. Use one of: {', '.join(WORKER_BACKEND_ACCEPTED_OPTIONS)}")
    valid_roles = set(WORKER_ROLES)
    for role, backend in workers.items():
        if not role:
            raise ValueError("worker role cannot be empty")
        if role not in valid_roles:
            raise ValueError(f"Unknown worker role: {role}. Use one of: {', '.join(WORKER_ROLES)}")
        if backend not in valid_backends:
            raise ValueError(f"Unknown worker backend for {role}: {backend}. Use one of: {', '.join(WORKER_BACKEND_ACCEPTED_OPTIONS)}")
    for role, model in (role_models or {}).items():
        if not role:
            raise ValueError("worker role cannot be empty")
        if role not in valid_roles:
            raise ValueError(f"Unknown worker role: {role}. Use one of: {', '.join(WORKER_ROLES)}")


def _ordered_worker_overrides(workers: dict[str, str]) -> dict[str, str]:
    ordered: dict[str, str] = {}
    for role in WORKER_ROLES:
        if workers.get(role):
            ordered[role] = workers[role]
    for role in sorted(set(workers) - set(WORKER_ROLES)):
        if workers.get(role):
            ordered[role] = workers[role]
    return ordered


def _ordered_role_model_overrides(role_models: dict[str, str]) -> dict[str, str]:
    ordered: dict[str, str] = {}
    for role in WORKER_ROLES:
        model = role_models.get(role, "").strip()
        if model:
            ordered[role] = model
    for role in sorted(set(role_models) - set(WORKER_ROLES)):
        model = role_models.get(role, "").strip()
        if model:
            ordered[role] = model
    return ordered


def normalize_delivery_mode(value: str) -> str:
    mode = (value or "init_project").strip()
    if mode not in DELIVERY_MODES:
        raise ValueError(f"Unknown delivery mode: {mode}")
    return mode


def render_delivery_mode_options(selected_delivery_mode: str) -> str:
    labels = {
        "mvp": "MVP / validation slice",
        "init_project": "Init project / production starter",
        "multi_service": "Multi-service product",
        "static_site": "Static site / landing",
        "cli_tool": "CLI tool",
        "library": "Library / SDK",
        "data_pipeline": "Data pipeline",
        "mobile_app": "Mobile app",
        "feature_increment": "Feature increment",
        "custom": "Custom",
    }
    selected = normalize_delivery_mode(selected_delivery_mode)
    return "\n".join(
        f'<option value="{mode}"{" selected" if mode == selected else ""}>{escape(labels.get(mode, mode))}</option>'
        for mode in DELIVERY_MODES
    )


def render_review_roles(review_roles: list[str]) -> str:
    if not review_roles:
        return ""
    items = "".join(f"<li>{escape(role)}</li>" for role in review_roles)
    return f'<div class="review-roles"><span>Review roles</span><ul>{items}</ul></div>'


def render_task_cards(tasks: list[dict[str, object]]) -> str:
    if not tasks:
        return '<div class="empty-state">No tasks in this lane.</div>'
    return "\n".join(
        f"""
        <article class="task-card task-{escape(str(task.get("priority", "")))}">
          <div class="role-topline">
            <h4>{escape(str(task.get("id", "")))} {escape(str(task.get("title", "")))}</h4>
            <span class="role-status">{escape(str(task.get("priority", "")))}</span>
          </div>
          <p>{escape(str(task.get("description", "")))}</p>
          <dl class="role-meta">
            <dt>Role</dt><dd>{escape(str(task.get("role", "")))}</dd>
            <dt>Status</dt><dd>{escape(str(task.get("status", "")))}</dd>
          </dl>
          {render_inline_list("Acceptance", list(task.get("acceptance_criteria", [])))}
          {render_inline_list("Artifacts", list(task.get("artifact_refs", [])), code=True)}
        </article>
        """
        for task in tasks
    )


def render_inline_list(label: str, values: list[object], *, code: bool = False) -> str:
    clean = [str(value) for value in values if str(value).strip()]
    if not clean:
        return ""
    items = "".join(
        f"<li>{f'<code>{escape(value)}</code>' if code else escape(value)}</li>"
        for value in clean
    )
    return f'<div class="task-list"><span>{escape(label)}</span><ul>{items}</ul></div>'


def build_worker_job_items(project_root: Path | None) -> list[dict[str, str]]:
    if project_root is None:
        return []
    items = []
    for job in WorkerJobStore(project_root).list_jobs():
        items.append(
            {
                "id": str(job.get("id", "")),
                "role": str(job.get("role", "")),
                "adapter": str(job.get("adapter", "")),
                "status": str(job.get("status", "")),
                "updated_at": str(job.get("updated_at", "")),
                "heartbeat_at": str(job.get("heartbeat_at", "")),
                "pid": str(job.get("pid", "")),
                "summary": str(job.get("summary", "")),
            }
        )
    return items


def build_memory_event_citations(project_root: Path, event_id: str) -> list[dict[str, str]]:
    job_root = WorkerJobStore(project_root).root
    if not job_root.is_dir():
        return []
    citations: list[dict[str, str]] = []
    for path in sorted(job_root.iterdir(), key=lambda item: item.name):
        job = _read_json_file(path / "job.json")
        status = _read_json_file(path / "status.json")
        if job is None or status is None:
            continue
        memory_citations = status.get("memory_citations", [])
        if not isinstance(memory_citations, list) or event_id not in {str(item) for item in memory_citations}:
            continue
        merged = {**job, **status}
        citations.append(
            {
                "id": str(merged.get("id", path.name)),
                "role": str(merged.get("role", "")),
                "adapter": str(merged.get("adapter", "")),
                "status": str(merged.get("status", "")),
                "updated_at": str(merged.get("updated_at", "")),
                "summary": str(merged.get("summary", "")),
            }
        )
    return citations


def _memory_job_sort_key(job: dict[str, object]) -> tuple[str, str, str]:
    return (
        str(job.get("updated_at", "")),
        str(job.get("created_at", "")),
        str(job.get("id", "")),
    )


def _read_json_file(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_relevant_event(event: dict[str, object]) -> dict[str, object]:
    score = event.get("score", 0.0)
    try:
        normalized_score = float(score)
    except (TypeError, ValueError):
        normalized_score = 0.0
    return {
        "id": str(event.get("id", "")),
        "score": normalized_score,
        "horizon": str(event.get("horizon", "")),
        "text": str(event.get("text", "")),
        "source": str(event.get("source", "")),
        "timestamp": str(event.get("timestamp", "")),
    }


def build_memory_overview(project_root: Path | None, project_key: str) -> dict[str, object]:
    if project_root is None:
        return {"project_key": project_key, "roles": []}

    memory_root = project_root / ".goodteam" / "memory" / "roles"
    if not memory_root.is_dir():
        return {"project_key": project_key, "roles": []}

    role_store = RoleMemoryStore(project_root)
    job_store = WorkerJobStore(project_root)
    latest_jobs: dict[str, dict[str, object]] = {}
    for job in job_store.list_jobs():
        role_key = str(job.get("role", "")).strip()
        if not role_key:
            continue
        current = latest_jobs.get(role_key)
        if current is None or _memory_job_sort_key(job) > _memory_job_sort_key(current):
            latest_jobs[role_key] = job

    roles: list[dict[str, object]] = []
    for role_dir in sorted(path for path in memory_root.iterdir() if path.is_dir()):
        role_key = role_dir.name
        packet = role_store.build_pickup_packet(role_key)
        role_payload: dict[str, object] = {
            "role": role_key,
            "summaries": {
                "long-term": str(dict(packet.get("summaries", {})).get("long-term", "")),
                "mid-term": str(dict(packet.get("summaries", {})).get("mid-term", "")),
            },
            "short_term": str(packet.get("short_term", "")),
            "event_count": int(packet.get("event_count", 0)),
        }
        latest_job = latest_jobs.get(role_key)
        if latest_job is not None:
            job_id = str(latest_job.get("id", ""))
            pickup_packet = _read_json_file(job_store.root / job_id / "role_pickup_packet.json")
            if pickup_packet is not None:
                role_payload["latest_retrieval"] = {
                    "job_id": job_id,
                    "relevant_events": [
                        _normalize_relevant_event(event)
                        for event in pickup_packet.get("relevant_events", [])
                        if isinstance(event, dict)
                    ],
                }
        roles.append(role_payload)

    return {"project_key": project_key, "roles": roles}


def build_memory_events(
    project_root: Path | None,
    project_key: str,
    role: str,
    *,
    query: str = "",
    limit: int = 50,
) -> dict[str, object]:
    if project_root is None:
        return {"project_key": project_key, "role": role, "events": []}

    role_store = RoleMemoryStore(project_root)
    if not role_store.role_root(role).is_dir():
        return {"project_key": project_key, "role": role, "events": []}
    return {
        "project_key": project_key,
        "role": role,
        "events": role_store.list_events(role, query=query, limit=limit),
    }


def build_memory_event_detail(project_root: Path | None, event_id: str) -> dict[str, object] | None:
    if project_root is None:
        return None

    role_store = RoleMemoryStore(project_root)
    event = role_store.find_event(event_id)
    if event is None:
        return None
    supersede_info = role_store.event_supersede_info(event_id) or {
        "supersedes": "",
        "superseded_by": [],
        "is_superseded": False,
    }
    return {
        "id": event_id,
        "role": str(event.get("role_key", "")),
        "horizon": str(event.get("horizon", "")),
        "timestamp": str(event.get("timestamp", "")),
        "source": str(event.get("source", "")),
        "text": str(event.get("text", "")),
        "action": str(event.get("action", "")),
        "supersedes": str(supersede_info.get("supersedes", "")),
        "superseded_by": list(supersede_info.get("superseded_by", [])),
        "is_superseded": bool(supersede_info.get("is_superseded", False)),
        "cited_by_jobs": build_memory_event_citations(project_root, event_id),
    }


DASHBOARD_CSS = """:root {
  color-scheme: light;
  --bg: #F7F7F7;
  --panel: #ffffff;
  --panel-2: #F1F1F1;
  --surface: #ffffff;
  --ink: #17211d;
  --text: #17211d;
  --muted: #777777;
  --line: #E5E5E5;
  --accent: #3D8B00;
  --accent-soft: #E8F9D9;
  --blue: #1CB0F6;
  --red: #FF4B4B;
  --code-bg: #F1F1F1;

  /* Duolingo palette variables */
  --success: #58CC02; --success-dark: #3D8B00; --success-bg: #E8F9D9;
  --info: #1CB0F6;    --info-dark: #0A7BAF;    --info-bg: #D0F0FF;
  --warning: #FF9600; --warning-dark: #B36B00; --warning-bg: #FFF0D4;
  --error: #FF4B4B;   --error-dark: #B03030;   --error-bg: #FFE0E0;
  --neutral: #AFAFAF; --neutral-dark: #6B6B6B; --neutral-bg: #F0F0F0;
  --border-shadow: #D1D1D1;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "Nunito", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 13px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  height: 100vh;
  overflow: hidden;
}
code, pre, .command-box, .editor-status {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
}
.dashboard-layout {
  display: flex;
  height: 100vh;
  width: 100vw;
}
.sidebar {
  width: 220px;
  background: #ffffff;
  border-right: 2px solid var(--line);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  height: 100%;
}
.sidebar-header {
  padding: 20px;
  border-bottom: 2px solid var(--line);
}
.logo {
  display: flex;
  align-items: center;
  gap: 8px;
}
.logo-box {
  width: 32px;
  height: 32px;
  background: var(--success);
  border: none;
  color: #ffffff;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 900;
  font-size: 16px;
  box-shadow: 0 3px 0 var(--success-dark);
}
.logo-text {
  font-weight: 900;
  font-size: 18px;
  color: var(--success-dark);
  letter-spacing: -0.02em;
}
.sidebar-project {
  font-weight: 900;
  font-size: 16px;
  color: var(--text);
  margin-top: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sidebar-project-meta {
  font-size: 11px;
  font-weight: 700;
  color: var(--neutral);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-top: 4px;
}
.sidebar-nav {
  flex-grow: 1;
  padding: 16px 0;
  overflow-y: auto;
}
.nav-item {
  display: flex;
  align-items: center;
  width: 100%;
  height: 48px;
  padding: 0 20px;
  background: transparent;
  border: none;
  color: var(--muted);
  font-weight: 700;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
  transition: all 0.15s ease;
  position: relative;
}
.nav-item:hover {
  background: var(--bg);
  color: var(--text);
}
.nav-item.active {
  background: var(--success-bg);
  color: var(--success-dark);
  font-weight: 800;
}
.nav-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 6px;
  bottom: 6px;
  width: 4px;
  background: var(--success);
  border-radius: 0 4px 4px 0;
}
.sidebar-footer {
  padding: 16px 20px;
  border-top: 2px solid var(--line);
  font-size: 11px;
  color: #AFAFAF;
  font-weight: bold;
}
.main-wrapper {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}
.top-bar {
  height: 56px;
  background: #ffffff;
  border-bottom: 2px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  flex-shrink: 0;
}
.top-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 700;
}
.breadcrumb a {
  color: var(--muted);
  text-decoration: none;
  cursor: pointer;
}
.breadcrumb a:hover {
  color: var(--text);
}
.breadcrumb .current {
  color: var(--text);
  font-weight: 800;
}
.breadcrumb-sep {
  color: var(--line);
}
.top-right {
  display: flex;
  align-items: center;
  gap: 16px;
}
.run-id {
  font-size: 10px;
  color: #AFAFAF;
  font-weight: 800;
  text-transform: uppercase;
}
.run-id code {
  background: #F1F1F1;
  padding: 2px 6px;
  border-radius: 6px;
  color: #555555;
}
.top-status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: 999px;
  font-weight: 800;
  text-transform: uppercase;
  border: none;
  font-size: 11px;
  padding: 6px 14px;
  letter-spacing: 0.04em;
}
.top-status-badge::before {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
  display: inline-block;
}
.status-complete::before, .status-approved::before {
  display: none;
}
.status-pending::before, .status-blocked::before, .status-changes_requested::before {
  animation: badge-pulse 1.4s ease-in-out infinite;
}
@keyframes badge-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.status-complete, .status-approved {
  background: var(--success-bg);
  color: var(--success-dark);
}
.status-running, .status-active {
  background: var(--success-bg);
  color: var(--success-dark);
}
.status-pending, .status-blocked, .status-changes_requested {
  background: var(--warning-bg);
  color: var(--warning-dark);
}
.status-failed, .status-error, .status-rejected {
  background: var(--error-bg);
  color: var(--error-dark);
}
.status-neutral, .status-no-activity, .status-no_activity {
  background: var(--neutral-bg);
  color: var(--neutral-dark);
}
.content-area {
  flex-grow: 1;
  padding: 24px;
  overflow-y: auto;
}

/* File Path Tooltips styling */
.file-path-chip, .file-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 6px;
  background: var(--neutral-bg);
  color: var(--text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  transition: background .12s;
  border: none;
  position: relative;
}
.file-path-chip:hover, .file-chip:hover {
  background: var(--info-bg);
  color: var(--info-dark);
}
.file-path-chip code {
  background: transparent;
  color: inherit;
  border: none;
  padding: 0;
  font-size: inherit;
}
.file-path-tooltip {
  pointer-events: none;
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  margin-bottom: 8px;
  background: #18181b;
  color: #ffffff;
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 10px;
  font-family: monospace;
  white-space: normal;
  word-break: break-all;
  width: max-content;
  max-width: 250px;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.15s ease, visibility 0.15s ease;
  box-shadow: 0 4px 6px rgba(0,0,0,0.1);
  z-index: 100;
}
.file-path-chip:hover .file-path-tooltip {
  opacity: 1;
  visibility: visible;
}
.empty-path {
  color: var(--muted);
  font-style: italic;
  font-size: 11px;
}

/* Cards styling */
section, article, .role-card, .action-card, .approval-card, .task-card, .kanban-column, .card {
  border: 2px solid var(--line);
  background: var(--panel);
  padding: 20px;
  border-radius: 16px;
  box-shadow: 0 2px 0 var(--border-shadow);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
section:hover, article:hover, .role-card:hover, .action-card:hover, .approval-card:hover, .task-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 0 var(--border-shadow);
}
.card-title {
  font-size: 11px;
  font-weight: 800;
  color: var(--neutral);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 14px;
}
.kanban-column {
  background: #F9F9F9;
  min-height: 360px;
  padding: 12px;
  box-shadow: none;
  border-radius: 16px;
}
.tab-panel {
  display: none;
}
.tab-panel.active {
  display: block;
}
.grid {
  display: grid;
  grid-template-columns: 1.2fr 0.8fr;
  gap: 20px;
}
h2 {
  margin: 0 0 16px;
  font-size: 11px;
  font-weight: 800;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.hero {
  background: #ffffff;
}
.completion-banner {
  display: grid;
  gap: 12px;
  padding-bottom: 20px;
  border-bottom: 2px solid var(--line);
  margin-bottom: 20px;
}
.eyebrow-line {
  margin: 0;
  color: var(--blue);
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.completion-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.wide {
  grid-column: 1 / -1;
}
.start-workflow-drawer {
  grid-column: 1 / -1;
  border: 2px solid var(--line);
  background: var(--panel);
  border-radius: 16px;
  padding: 0;
  overflow: hidden;
  box-shadow: 0 2px 0 var(--border-shadow);
}
.start-workflow-drawer summary {
  cursor: pointer;
  padding: 14px 20px;
  color: var(--accent);
  background: var(--accent-soft);
  font-weight: 800;
  text-transform: uppercase;
  font-size: 11px;
  list-style-position: inside;
}
.start-workflow-panel {
  border: 0;
  border-top: 2px solid var(--line);
  border-radius: 0;
  min-height: 0;
  box-shadow: none;
}
.console-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.mode-switch {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 16px;
}
.mode-button {
  min-height: 38px;
  color: var(--muted);
  font-weight: 800;
  font-size: 11px;
  text-transform: uppercase;
}
.mode-button.active {
  border-color: #3D8B00;
  color: #3D8B00;
  background: #E8F9D9;
  box-shadow: 0 2px 0 #3D8B00;
}
label {
  display: grid;
  gap: 6px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
textarea, select, input {
  width: 100%;
  border: 2px solid var(--line);
  background: var(--panel);
  color: var(--text);
  padding: 10px 14px;
  font: inherit;
  font-size: 13px;
  min-height: 38px;
  border-radius: 12px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
textarea:focus, select:focus, input:focus {
  outline: none;
  border-color: #58CC02;
}
textarea {
  min-height: 118px;
  resize: vertical;
  line-height: 1.45;
}
.checkbox-label {
  display: flex;
  align-items: center;
  gap: 10px;
  text-transform: none;
  color: var(--text);
  font-weight: 700;
  font-size: 13px;
}
.checkbox-label input {
  width: 16px;
  height: 16px;
  min-height: 16px;
  accent-color: #58CC02;
}
.command-box {
  margin-top: 14px;
  border: 2px solid var(--line);
  background: var(--code-bg);
  padding: 14px;
  min-height: 116px;
  color: var(--text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  text-align: left;
  border-radius: 12px;
}
.adapter-config {
  border: 2px solid var(--line);
  padding: 14px;
  display: grid;
  gap: 12px;
  background: #F9F9F9;
  border-radius: 12px;
}
.adapter-config[hidden] {
  display: none;
}
.signal-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 16px;
}
.signal-row.compact {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.signal {
  border: 2px solid var(--line);
  background: var(--panel);
  padding: 12px;
  min-height: 72px;
  border-radius: 12px;
  box-shadow: 0 2px 0 var(--border-shadow);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.signal span {
  display: block;
  color: var(--muted);
  font-size: 10px;
  font-weight: 800;
  text-transform: uppercase;
  margin-bottom: 4px;
  letter-spacing: 0.05em;
}
.signal strong {
  font-size: 15px;
  color: var(--text);
  font-weight: 800;
}
.roles {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.waterfall {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.agile-board {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  align-items: start;
}
.kanban-column-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding: 0 4px;
}
.kanban-column-head h3 {
  margin: 0;
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
  color: var(--muted);
}
.kanban-column-head span {
  background: #E5E5E5;
  color: #555555;
  font-size: 10px;
  font-weight: 800;
  padding: 2px 6px;
  border-radius: 10px;
}
.task-stack {
  display: grid;
  gap: 12px;
}
.task-card h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--text);
}
.task-card p {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.4;
  margin: 8px 0;
}
.task-high {
  border-left: 3px solid var(--red);
}
.task-list {
  margin-top: 8px;
  border-top: 1px solid var(--panel-2);
  padding-top: 8px;
}
.task-list span {
  color: var(--muted);
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.task-list ul {
  margin: 4px 0 0;
  padding-left: 14px;
  color: var(--text);
  font-size: 12px;
  line-height: 1.4;
  display: block;
}
.task-list li {
  border-top: none;
  padding-top: 2px;
  margin: 2px 0;
  display: list-item;
}
.flow-card {
  display: grid;
  grid-template-columns: 36px 1fr;
  gap: 12px;
  border: 2px solid var(--line);
  background: var(--panel);
  padding: 16px;
  min-height: 172px;
  border-radius: 16px;
  box-shadow: 0 2px 0 var(--border-shadow);
  transition: border-color 0.15s ease;
}
.flow-index {
  width: 28px;
  height: 28px;
  border: 2px solid var(--line);
  border-radius: 6px;
  display: grid;
  place-items: center;
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
}
.flow-topline {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.flow-status {
  border: none;
  color: var(--muted);
  padding: 4px 10px;
  font-size: 10px;
  font-weight: 800;
  text-transform: uppercase;
  border-radius: 999px;
  white-space: nowrap;
}
.flow-card p {
  color: var(--muted);
  line-height: 1.45;
  margin: 8px 0 12px;
  font-size: 12px;
}
.flow-card .flow-feedback {
  color: var(--text);
  margin-top: 0;
  font-weight: 500;
}
.flow-done {
  border-color: var(--success);
}
.flow-done .flow-index {
  background: var(--success-bg);
  color: var(--success-dark);
  border-color: var(--success);
}
.flow-done .flow-status {
  color: var(--success-dark);
  background: var(--success-bg);
}
.flow-active {
  border-color: var(--info);
}
.flow-active .flow-index {
  background: var(--info-bg);
  color: var(--info-dark);
  border-color: var(--info);
}
.flow-active .flow-status {
  color: var(--info-dark);
  background: var(--info-bg);
}
.flow-ready {
  border-color: var(--warning);
}
.flow-ready .flow-index {
  background: var(--warning-bg);
  color: var(--warning-dark);
  border-color: var(--warning);
}
.flow-ready .flow-status {
  color: var(--warning-dark);
  background: var(--warning-bg);
}
.flow-review-needed {
  border-color: var(--accent);
  background: var(--panel);
}
.flow-review-needed .flow-index,
.flow-review-needed .flow-status {
  color: var(--text);
  border-color: var(--accent);
  background: var(--accent-soft);
}
.flow-blocked {
  border-color: var(--red);
}
.flow-blocked .flow-index {
  background: var(--error-bg);
  color: var(--error-dark);
  border-color: var(--red);
}
.flow-blocked .flow-status {
  color: var(--error-dark);
  background: var(--error-bg);
}
.role-card {
  border: 2px solid var(--line);
  background: var(--panel);
  padding: 16px;
  min-height: 190px;
  border-radius: 16px;
  box-shadow: 0 2px 0 var(--border-shadow);
}
.approval-card {
  border: 2px solid var(--line);
  background: var(--panel);
  padding: 18px;
  margin-top: 16px;
  border-radius: 16px;
  box-shadow: 0 2px 0 var(--border-shadow);
}
.action-card {
  border: 2px solid var(--line);
  background: var(--panel);
  padding: 14px;
  border-radius: 16px;
  box-shadow: 0 2px 0 var(--border-shadow);
}
.action-timeline {
  display: grid;
  gap: 12px;
}
.artifact-preview {
  margin: 14px 0 0;
  padding: 20px;
  border: 2px solid var(--line);
  background: var(--panel);
  color: var(--text);
  overflow-wrap: break-word;
  max-height: 520px;
  overflow: auto;
  line-height: 1.5;
  border-radius: 16px;
  box-shadow: inset 0 1px 2px rgba(0,0,0,0.02);
}
.markdown-preview h1,
.markdown-preview h2,
.markdown-preview h3 {
  color: var(--text);
  text-transform: none;
  margin: 20px 0 10px;
  line-height: 1.25;
  font-weight: 600;
}
.markdown-preview h1:first-child,
.markdown-preview h2:first-child,
.markdown-preview h3:first-child {
  margin-top: 0;
}
.markdown-preview h1 {
  font-size: 20px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 8px;
}
.markdown-preview h2 {
  font-size: 16px;
  color: var(--accent);
}
.markdown-preview h3 {
  font-size: 14px;
}
.markdown-preview p {
  margin: 8px 0;
  color: #3f3f46;
}
.markdown-preview ul {
  margin: 8px 0 12px;
  padding-left: 20px;
  display: block;
  list-style: disc;
}
.markdown-preview li {
  display: list-item;
  border-top: 0;
  padding-top: 0;
  margin: 4px 0;
}
.markdown-preview code {
  background: var(--code-bg);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 2px 4px;
  font-size: 0.9em;
  color: var(--text);
  text-align: left;
}
.markdown-preview pre {
  margin: 12px 0;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #18181b;
  color: #f4f4f5;
  overflow: auto;
}
.markdown-preview pre code {
  background: transparent;
  border: 0;
  color: inherit;
  padding: 0;
}
.artifact-editor {
  margin-top: 14px;
  border: 2px solid var(--line);
  background: var(--panel);
  padding: 16px;
  border-radius: 16px;
}
.artifact-editor textarea {
  min-height: 420px;
  line-height: 1.5;
  white-space: pre;
  overflow: auto;
  resize: vertical;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  background: var(--panel);
}
.editor-status {
  display: block;
  text-align: left;
  margin-top: 10px;
  color: var(--muted);
}
.role-topline {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.25;
  color: var(--text);
}
.role-status {
  border: none;
  color: var(--muted);
  background: var(--panel-2);
  padding: 4px 10px;
  font-size: 10px;
  font-weight: 800;
  text-transform: uppercase;
  border-radius: 999px;
  white-space: nowrap;
}
.role-status.status-active, .role-status.status-waiting_for_approval, .role-status.status-ready {
  background: var(--success-bg);
  color: var(--success-dark);
}
.role-status.status-artifact_ready {
  background: var(--info-bg);
  color: var(--info-dark);
}
.role-status.status-no_activity, .role-status.status-no-activity {
  background: var(--neutral-bg);
  color: var(--neutral-dark);
}
.role-card p {
  color: var(--muted);
  line-height: 1.45;
  margin: 10px 0;
  font-size: 12px;
}
.role-meta {
  grid-template-columns: 80px 1fr;
  gap: 6px 12px;
  font-size: 12px;
}
.review-roles {
  margin-top: 12px;
  color: var(--muted);
  font-size: 12px;
}
.review-roles span {
  display: block;
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 600;
  font-size: 10px;
}
.review-roles ul {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
  padding: 0;
}
.review-roles li {
  border: 2px solid var(--line);
  padding: 3px 6px;
  color: var(--text);
  background: var(--panel-2);
  border-radius: 6px;
  font-size: 11px;
}
.idea {
  font-size: 18px;
  font-weight: 500;
  line-height: 1.4;
  margin: 0;
  color: var(--text);
}
dl {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 8px 16px;
  margin: 0;
  font-size: 13px;
}
dt { color: var(--muted); font-weight: 500; }
dd { margin: 0; overflow-wrap: anywhere; color: var(--text); font-weight: 600; }
ul {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 10px;
}
li {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}
code {
  color: var(--muted);
  overflow-wrap: anywhere;
  text-align: right;
}
strong { color: var(--text); font-weight: 600; }
.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 18px;
}
.approval-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 14px;
}
@media (max-width: 760px) {
  header, .grid { display: block; }
  .status { margin-top: 16px; }
  section { margin-top: 16px; }
  .console-grid, .signal-row { grid-template-columns: 1fr; }
  .waterfall { grid-template-columns: 1fr; }
  .roles { grid-template-columns: 1fr; }
  dl { grid-template-columns: 1fr; }
  li { display: block; }
  code { display: block; text-align: left; margin-top: 6px; }
}
@media (min-width: 761px) and (max-width: 1040px) {
  .roles { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .waterfall { grid-template-columns: 1fr; }
}

/* Progress Bar styling */
.progress-container {
  background: var(--panel);
  border: 2px solid var(--line);
  box-shadow: 0 2px 0 var(--border-shadow);
  border-radius: 16px;
  padding: 16px 20px;
  margin-bottom: 20px;
  max-width: 600px;
}
.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  font-weight: 800;
  color: var(--muted);
  text-transform: uppercase;
  margin-bottom: 8px;
}
.progress-count {
  color: var(--success-dark);
  font-weight: 800;
}
.progress-bar-bg {
  width: 100%;
  background: var(--panel-2);
  height: 12px;
  border-radius: 6px;
  overflow: hidden;
}
.progress-bar-fill {
  background: var(--success);
  height: 100%;
  border-radius: 6px;
  transition: width 0.3s ease;
}

/* Approvals page list styling */
.approval-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.approval-list-item {
  background: var(--panel);
  border: 2px solid var(--line);
  border-radius: 16px;
  padding: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-shadow: 0 2px 0 var(--border-shadow);
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.approval-list-item:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 0 var(--border-shadow);
}
.approval-list-item.status-approved {
  border-color: var(--success);
  box-shadow: 0 2px 0 var(--success);
}
.approval-list-item.status-approved:hover {
  box-shadow: 0 4px 0 var(--success);
}
.approval-list-item.status-pending {
  border-color: var(--warning);
  box-shadow: 0 2px 0 var(--warning);
  background: #FFFBF4;
}
.approval-list-item.status-pending:hover {
  box-shadow: 0 4px 0 var(--warning);
}
.approval-item-top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.lock-icon {
  font-size: 14px;
}
.approval-title {
  font-weight: 800;
  font-size: 12px;
  color: #17211d;
  text-transform: uppercase;
}
.approval-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.02em;
  border: none;
  white-space: nowrap;
}
.approval-badge.status-approved {
  background: var(--success-bg);
  color: var(--success-dark);
}
.approval-badge.status-pending {
  background: var(--warning-bg);
  color: var(--warning-dark);
}
.approval-badge.status-rejected {
  background: var(--error-bg);
  color: var(--error-dark);
}
.approval-card.status-approved {
  border-color: var(--success);
  box-shadow: 0 2px 0 var(--success);
}
.approval-card.status-pending {
  border-color: var(--warning);
  box-shadow: 0 2px 0 var(--warning);
  background: #FFFBF4;
}

/* === PIPELINE STEPPER === */
.stepper { display: flex; align-items: flex-start; gap: 0; padding: 8px 4px; overflow-x: auto; margin-top: 14px; }
.step { display: flex; flex-direction: column; align-items: center; flex-shrink: 0; min-width: 70px; }
.step-circle { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 14px; }
.step.done .step-circle { background: var(--success); color: white; box-shadow: 0 3px 0 var(--success-dark); }
.step.current .step-circle { background: var(--warning); color: white; box-shadow: 0 3px 0 var(--warning-dark); animation: stepper-pulse 1.6s ease-in-out infinite; }
.step.pending .step-circle { background: white; color: var(--neutral); border: 2px solid var(--line); box-shadow: 0 2px 0 var(--border-shadow); }
.step-line { flex: 1; height: 3px; margin-top: 16px; min-width: 24px; border-radius: 2px; }
.step-line.done { background: var(--success); }
.step-line.pending { background: var(--line); background-image: linear-gradient(to right, var(--line) 50%, transparent 50%); background-size: 8px 3px; background-repeat: repeat-x; }
.step-label { font-size: 10px; font-weight: 700; color: var(--muted); margin-top: 6px; text-align: center; line-height: 1.2; max-width: 80px; }
.step.current .step-label { color: var(--warning-dark); font-weight: 900; }
@keyframes stepper-pulse { 0%,100% { opacity:1 } 50% { opacity:0.6 } }

/* === ACTIVITY TIMELINE === */
.activity-item { display: flex; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--line); }
.activity-item:last-child { border-bottom: none; }
.activity-icon { width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; flex-shrink: 0; font-weight: bold; }
.activity-icon.success { background: var(--success-bg); color: var(--success-dark); }
.activity-icon.info    { background: var(--info-bg); color: var(--info-dark); }
.activity-icon.warning { background: var(--warning-bg); color: var(--warning-dark); }
.activity-icon.danger  { background: var(--error-bg); color: var(--error-dark); }
.activity-icon.neutral { background: var(--neutral-bg); color: var(--neutral-dark); }
.activity-text { flex: 1; }
.activity-title { font-size: 13px; font-weight: 700; color: var(--text); }
.activity-time { font-size: 11px; font-weight: 600; color: var(--muted); margin-top: 2px; }

/* === KPI === */
.kpi-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 8px; }
.kpi-card { background: white; border: 2px solid var(--line); border-radius: 14px; box-shadow: 0 2px 0 var(--border-shadow); padding: 16px 18px; display: flex; flex-direction: column; align-items: start; }
.kpi-label { font-size: 11px; font-weight: 800; color: var(--muted); letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 4px; }
.kpi-value { font-size: 32px; font-weight: 900; line-height: 1; }
.kpi-value.success { color: var(--success-dark); }
.kpi-value.info { color: var(--info-dark); }
.kpi-value.neutral { color: var(--neutral-dark); }

/* === ACTION REQUIRED ALERT === */
.alert { background: var(--warning-bg); border: 2px solid var(--warning); border-radius: 16px; padding: 18px 22px; display: flex; gap: 16px; box-shadow: 0 2px 0 var(--warning-dark); position: relative; margin-bottom: 20px; }
.alert-icon { font-size: 28px; flex-shrink: 0; }
.alert-body { flex: 1; }
.alert-title { font-size: 16px; font-weight: 900; color: var(--warning-dark); margin-bottom: 4px; }
.alert-desc { font-size: 13px; color: var(--text-primary); font-weight: 600; margin-bottom: 12px; }
.alert-actions { display: flex; gap: 10px; flex-wrap: wrap; }

/* === STICKY APPROVE BAR === */
.sticky-bar { position: sticky; bottom: 0; background: white; border-top: 2px solid var(--line); padding: 14px 18px; display: flex; gap: 10px; margin: 0 -18px -18px; border-radius: 0 0 14px 14px; box-shadow: 0 -4px 12px rgba(0,0,0,0.05); z-index: 10; }

/* === BUTTON UNIFICATION === */
button, .button, .btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 18px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 800;
  cursor: pointer;
  border: 2px solid var(--line);
  background: white;
  color: var(--text);
  box-shadow: 0 3px 0 var(--border-shadow);
  transition: all 0.1s ease;
  text-decoration: none;
  text-transform: uppercase;
}
button:active, .button:active, .btn:active {
  transform: translateY(3px);
  box-shadow: none;
}
button.primary, .button.primary, .btn-primary {
  border-color: var(--success-dark);
  background: var(--success);
  color: white;
  box-shadow: 0 3px 0 var(--success-dark);
}
button.primary:hover, .button.primary:hover, .btn-primary:hover {
  background: var(--success);
  color: white;
}
button.danger, .button.danger, .btn-danger {
  border-color: var(--red);
  color: var(--error-dark);
  background: white;
  box-shadow: 0 3px 0 var(--red);
}
button.danger:hover, .button.danger:hover, .btn-danger:hover {
  background: var(--error-bg);
}"""

PROJECT_OVERVIEW_CSS = """:root {
  color-scheme: light;
  --bg: #F7F7F7;
  --panel: #ffffff;
  --panel-2: #F1F1F1;
  --surface: #ffffff;
  --ink: #17211d;
  --muted: #777777;
  --line: #E5E5E5;
  --accent: #3D8B00;
  --accent-soft: #E8F9D9;
  --red: #FF4B4B;
  --border-shadow: #D1D1D1;
  --success: #58CC02; --success-dark: #3D8B00; --success-bg: #E8F9D9;
  --info: #1CB0F6;    --info-dark: #0A7BAF;    --info-bg: #D0F0FF;
  --warning: #FF9600; --warning-dark: #B36B00; --warning-bg: #FFF0D4;
  --error: #FF4B4B;   --error-dark: #B03030;   --error-bg: #FFE0E0;
  --neutral: #AFAFAF; --neutral-dark: #6B6B6B; --neutral-bg: #F0F0F0;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "Nunito", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--ink);
  font-size: 13px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
.top-bar {
  height: 60px;
  background: #ffffff;
  border-bottom: 2px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  position: sticky;
  top: 0;
  z-index: 10;
}
.logo {
  display: flex;
  align-items: center;
  gap: 8px;
}
.logo-box {
  width: 32px;
  height: 32px;
  background: var(--success);
  border: none;
  color: #ffffff;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 900;
  font-size: 16px;
  box-shadow: 0 3px 0 var(--success-dark);
}
.logo-text {
  font-weight: 900;
  font-size: 18px;
  color: var(--success-dark);
  letter-spacing: -0.02em;
}
main {
  width: min(1120px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 24px 0;
}
.projects {
  display: grid;
  gap: 16px;
  margin-top: 16px;
}
.project-card, .legacy {
  display: grid;
  grid-template-columns: 0.8fr 1.5fr auto;
  gap: 18px;
  align-items: center;
  border: 2px solid var(--line);
  background: var(--panel);
  padding: 20px;
  border-radius: 16px;
  box-shadow: 0 2px 0 var(--border-shadow);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.project-card:hover, .legacy:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 0 var(--border-shadow);
}
.project-active {
  border-color: var(--success);
  background: #ffffff;
}
h2 {
  margin: 0 0 6px;
  font-size: 16px;
  font-weight: 800;
  color: var(--ink);
}
p {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}
dl {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 6px 12px;
  margin: 0;
  font-size: 12px;
  font-weight: 600;
}
dt { color: var(--muted); }
dd { margin: 0; overflow-wrap: anywhere; color: var(--ink); font-weight: 700; }
code { color: var(--muted); font-family: monospace; font-size: 11px; }

/* File Path Tooltips styling */
.file-path-chip, .file-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 6px;
  background: var(--neutral-bg);
  color: var(--text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  transition: background .12s;
  border: none;
  position: relative;
}
.file-path-chip:hover, .file-chip:hover {
  background: var(--info-bg);
  color: var(--info-dark);
}
.file-path-chip code {
  background: transparent;
  color: inherit;
  border: none;
  padding: 0;
  font-size: inherit;
}
.file-path-tooltip {
  pointer-events: none;
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  margin-bottom: 8px;
  background: #18181b;
  color: #ffffff;
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 10px;
  font-family: monospace;
  white-space: normal;
  word-break: break-all;
  width: max-content;
  max-width: 250px;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.15s ease, visibility 0.15s ease;
  box-shadow: 0 4px 6px rgba(0,0,0,0.1);
  z-index: 100;
}
.file-path-chip:hover .file-path-tooltip {
  opacity: 1;
  visibility: visible;
}

.project-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: end;
}
.button, button {
  border: 2px solid var(--line);
  background: var(--panel);
  color: var(--ink);
  padding: 8px 16px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
  text-decoration: none;
  cursor: pointer;
  box-shadow: 0 3px 0 var(--border-shadow);
  transition: all 0.1s ease;
  display: inline-flex;
  align-items: center;
}
.button:active, button:active {
  transform: translateY(3px);
  box-shadow: none;
}
.primary {
  border-color: var(--success-dark);
  background: var(--success);
  color: #ffffff;
  box-shadow: 0 3px 0 var(--success-dark);
}
.primary:hover {
  background: var(--success);
  color: #ffffff;
}
.danger {
  border-color: var(--red);
  color: var(--red);
  background: var(--panel);
  box-shadow: 0 3px 0 rgba(255, 75, 75, 0.2);
}
.danger:hover {
  background: rgba(255, 75, 75, 0.05);
}
.empty-state {
  border: 2px dashed var(--line);
  background: var(--panel-2);
  color: var(--muted);
  padding: 24px;
  border-radius: 16px;
  text-align: center;
  font-weight: 700;
}

/* === ACTION REQUIRED ALERT (Legacy run warning alert style) === */
.alert { background: var(--warning-bg); border: 2px solid var(--warning); border-radius: 16px; padding: 18px 22px; display: flex; gap: 16px; box-shadow: 0 2px 0 var(--warning-dark); position: relative; margin-bottom: 20px; }
.alert-icon { font-size: 28px; flex-shrink: 0; }
.alert-body { flex: 1; }
.alert-title { font-size: 16px; font-weight: 900; color: var(--warning-dark); margin-bottom: 4px; }
.alert-desc { font-size: 13px; color: var(--ink); font-weight: 600; margin-bottom: 12px; }
.alert-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.badge { display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 800; letter-spacing: 0.02em; border: none; white-space: nowrap; }
.badge.error { background: var(--error-bg); color: var(--error-dark); }
.badge.success { background: var(--success-bg); color: var(--success-dark); }

@media (max-width: 780px) {
  .top-bar { padding: 0 16px; }
  .project-card, .legacy { display: block; }
  .project-actions { justify-content: start; margin-top: 14px; }
  dl { margin-top: 14px; }
}"""

def build_recent_activity_html(actions: list[dict[str, str]]) -> str:
    html_items = []
    for action in actions[:6]:
        action_type = action.get("action", "")
        if "approve" in action_type or "approved" in action.get("decision", ""):
            icon = "✓"
            color_class = "success"
        elif "reject" in action_type or "rejected" in action.get("decision", ""):
            icon = "✗"
            color_class = "danger"
        elif "pending" in action_type or "wait" in action_type:
            icon = "⏳"
            color_class = "warning"
        elif "generated" in action_type:
            icon = "📄"
            color_class = "info"
        else:
            icon = "⚡"
            color_class = "neutral"
        timestamp_str = action.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(timestamp_str)
            time_display = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            time_display = timestamp_str
        title = action.get("title", "")
        artifact_path = action.get("artifact_path", "")
        if artifact_path:
            artifact_name = Path(artifact_path).name
            title = title.replace(artifact_name.upper(), f'<span class="file-path-chip" data-path="{escape(artifact_path)}"><code>{escape(artifact_name)}</code></span>')
        html_items.append(f'''
        <div class="activity-item">
          <div class="activity-icon {color_class}">{icon}</div>
          <div class="activity-text">
            <div class="activity-title">{title}</div>
            <div class="activity-time">{time_display}</div>
          </div>
        </div>
        ''')
    if not html_items:
        return '<div class="empty-state">No activity recorded yet.</div>'
    return "\n".join(html_items)


def render_dashboard(state: AgileDevState | None, start_context: dict[str, object] | None = None) -> str:
    if start_context is None:
        start_context = build_start_console_context(Path("."))
    else:
        default_context = build_start_console_context(Path(str(start_context.get("workspace", "."))))
        start_context = {**default_context, **start_context}
    workspace = Path(str(start_context.get("workspace", ".")))
    active_run = is_active_run(state)
    closed_run = state is not None and not active_run
    start_workflow_script = (
        ""
        if active_run
        else f"""
    // Enter the real workflow goal
    const workspace = {str(start_context["workspace"])!r};
    const projectKey = {str(start_context["project_key"])!r};
    const modelConfig = {str(start_context["model_config"])!r};

    // Toggles and layout
    const modeNew = document.getElementById("mode-new");
    const modeExisting = document.getElementById("mode-existing");
    const sourceTypeInput = document.getElementById("source_type");
    const existingFields = document.getElementById("existing-project-fields");

    if (modeNew && modeExisting) {{
      modeNew.addEventListener("click", () => {{
        modeNew.classList.add("active");
        modeExisting.classList.remove("active");
        sourceTypeInput.value = "new_project";
        existingFields.style.display = "none";
      }});
      modeExisting.addEventListener("click", () => {{
        modeExisting.classList.add("active");
        modeNew.classList.remove("active");
        sourceTypeInput.value = "existing_project";
        existingFields.style.display = "flex";
      }});
    }}

    // Show/hide Plane config
    const taskSyncCheckbox = document.querySelector('input[name="task_sync"][value="plane"]');
    const planeSyncFields = document.getElementById("plane-sync-fields");
    if (taskSyncCheckbox && planeSyncFields) {{
      taskSyncCheckbox.addEventListener("change", () => {{
        planeSyncFields.style.display = taskSyncCheckbox.checked ? "grid" : "none";
      }});
    }}

    // Show/hide Delivery Contract fields
    const deliveryRequiredCheckbox = document.querySelector('input[name="delivery_contract_required"]');
    const deliveryContractFields = document.getElementById("delivery-contract-fields");
    if (deliveryRequiredCheckbox && deliveryContractFields) {{
      deliveryRequiredCheckbox.addEventListener("change", () => {{
        deliveryContractFields.style.display = deliveryRequiredCheckbox.checked ? "grid" : "none";
      }});
    }}

    // Show/hide Deployment fields
    const deploymentModeSelect = document.querySelector('select[name="deployment_backend_mode"]');
    const deploymentBackendFields = document.getElementById("deployment-backend-fields");
    if (deploymentModeSelect && deploymentBackendFields) {{
      deploymentModeSelect.addEventListener("change", () => {{
        deploymentBackendFields.style.display = deploymentModeSelect.value === "ssh_compose" ? "grid" : "none";
      }});
    }}

    // Form submission handler
    const form = document.getElementById("start-workflow-form");
    if (form) {{
      form.addEventListener("submit", async (event) => {{
        event.preventDefault();
        await handleStartWorkflow(false);
      }});
    }}

    const dryRunBtn = document.getElementById("start-dry-run-btn");
    if (dryRunBtn) {{
      dryRunBtn.addEventListener("click", async () => {{
        await handleStartWorkflow(true);
      }});
    }}

    async function handleStartWorkflow(dryRun) {{
      const consoleBox = document.getElementById("dry-run-console");
      if (consoleBox) {{
        consoleBox.style.display = "block";
        consoleBox.textContent = dryRun ? "Running dry run..." : "Starting workflow...";
      }}
      
      try {{
        const formData = new FormData(form);
        const data = {{}};
        formData.forEach((value, key) => {{
          if (key === "task_sync" || key === "role_model_overrides") {{
            // handled separately or collected as list
          }} else {{
            data[key] = value;
          }}
        }});
        
        // Collect arrays/checkboxes
        data.task_sync = [];
        form.querySelectorAll('input[name="task_sync"]:checked').forEach((cb) => {{
          data.task_sync.push(cb.value);
        }});
        
        data.delivery_contract_required = !!form.querySelector('input[name="delivery_contract_required"]:checked');
        data.dry_run = dryRun;
        
        // Parse overrides
        const overridesText = form.querySelector('[name="role_model_overrides"]')?.value || "";
        const overrides = {{}};
        overridesText.split("\\n").forEach((line) => {{
          const parts = line.trim().split("=", 2);
          if (parts.length === 2 && parts[0].trim() && parts[1].trim()) {{
            overrides[parts[0].trim()] = parts[1].trim();
          }}
        }});
        data.role_model_overrides = overrides;
        
        let configPath = data.project_config || "";
        const sourceType = sourceTypeInput.value;
        
        // If adopting and config file dropdown is "(create new config below)" (i.e. empty string)
        if (sourceType === "existing_project" && !configPath) {{
          if (consoleBox) consoleBox.textContent = "Creating project config first...";
          const configResp = await fetch("/project-config", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{
              project_name: data.project_name || "",
              project_key: data.project_key || "",
              docs_path: data.docs_path || "",
              backend_path: data.backend_path || "",
              frontend_path: data.frontend_path || "",
              task_sync: data.task_sync,
              plane_api_host_url: data.plane_api_host_url || "",
              plane_workspace_slug: data.plane_workspace_slug || "",
              plane_project_id: data.plane_project_id || "",
              plane_api_key_env: data.plane_api_key_env || "",
              plane_mcp_server: data.plane_mcp_server || "",
              worker_backend_mode: data.worker_backend_mode || "codex",
              deployment_backend_mode: data.deployment_backend_mode || "",
              staging_ssh_target: data.staging_ssh_target || "",
              staging_remote_dir: data.staging_remote_dir || "",
              staging_git_ref: data.staging_git_ref || "",
              staging_public_url: data.staging_public_url || "",
              staging_deploy_command: data.staging_deploy_command || "",
              staging_healthcheck_command: data.staging_healthcheck_command || "",
              staging_smoke_command: data.staging_smoke_command || "",
              delivery_contract_required: data.delivery_contract_required,
              delivery_contract_minimum: data.delivery_contract_minimum || "infrastructure",
              delivery_contract_fatal_patterns: data.delivery_contract_fatal_patterns || "",
              delivery_contract_acceptance: data.delivery_contract_acceptance || "",
              model_profile: data.model_profile || "balanced",
              delivery_mode: data.delivery_mode || "init_project",
              role_model_overrides: overrides,
            }})
          }});
          
          const configPayload = await configResp.json();
          if (!configResp.ok) {{
            throw new Error(configPayload.error || "Failed to create project config.");
          }}
          configPath = configPayload.project_config;
          if (consoleBox) consoleBox.textContent = "Project config created: " + configPath + "\\nStarting workflow...";
        }}
        
        const roleModelOverridesList = [];
        Object.entries(overrides).forEach(([role, model]) => {{
          roleModelOverridesList.push(role + "=" + model);
        }});
        
        const startResp = await fetch("/start", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            idea: data.idea || "",
            project_key: data.project_key || projectKey,
            model_config: modelConfig,
            model_profile: data.model_profile || "balanced",
            delivery_mode: data.delivery_mode || "init_project",
            source_type: sourceType,
            project_config: configPath,
            role_model_overrides: roleModelOverridesList,
            dry_run: dryRun,
          }})
        }});
        
        const startPayload = await startResp.json();
        if (!startResp.ok) {{
          throw new Error(startPayload.error || "Failed to start workflow.");
        }}
        
        if (consoleBox) {{
          consoleBox.textContent = JSON.stringify(startPayload, null, 2);
        }}
        if (!dryRun) {{
          window.setTimeout(() => window.location.reload(), 600);
        }}
      }} catch (error) {{
        if (consoleBox) {{
          consoleBox.textContent = "Error: " + error.message;
        }}
        alert(error.message);
      }}
    }}
    """
    )
    role_observations = build_role_observations(state)
    workflow_steps = build_workflow_steps(state)
    role_status_counts: dict[str, int] = {}
    for role in role_observations:
        role_status_counts[role["status"]] = role_status_counts.get(role["status"], 0) + 1
        
    kpi_grid_html = ""
    # Map mockup themes success/info/neutral:
    # Active -> success, Artifact ready -> info, No activity -> neutral
    for status in ["Active", "Artifact ready", "No activity", "Waiting for approval", "Ready"]:
        count = role_status_counts.get(status, 0)
        # Skip if count is 0 and it's not one of the main ones (Active, Artifact ready, No activity)
        if count == 0 and status not in ("Active", "Artifact ready", "No activity"):
            continue
        # Map label
        label = "Idle" if status == "No activity" else status
        theme_class = (
            "success" if status in ("Active", "Waiting for approval")
            else "info" if status in ("Artifact ready", "Ready")
            else "neutral"
        )
        kpi_grid_html += f"""
        <div class="kpi-card">
          <div class="kpi-label">{escape(label)}</div>
          <div class="kpi-value {theme_class}">{count}</div>
        </div>
        """
        
    role_summary_items = "\
".join(
        f'<div class="signal"><span>{escape(status)}</span>{count}</div>'
        for status, count in sorted(role_status_counts.items())
    )
    if not role_summary_items:
        role_summary_items = '<div class="empty-state">No active run state found.</div>'
    
    artifact_items = "\
".join(
        f"<li><span>{escape(name.upper())}</span>{render_file_path(path, name)}</li>"
        for name, path in (state.artifacts if state is not None else {}).items()
    )
    if not artifact_items:
        artifact_items = '<li><span>No artifacts recorded</span><code>state/current.json has no artifact entries</code></li>'

    # Approvals Progress Calculations
    approvals_dict = state.approvals if state is not None else {}
    total_approvals = len(approvals_dict) or 6
    approved_count = sum(1 for d in approvals_dict.values() if str(d) == "approved")
    progress_percent = int(approved_count / total_approvals * 100) if total_approvals else 0
    
    progress_bar_html = f"""
    <div class="progress-container">
      <div class="progress-header">
        <span>Approval Pipeline Progress</span>
        <span class="progress-count">{approved_count} of {total_approvals} Gates Approved</span>
      </div>
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" style="width: {progress_percent}%"></div>
      </div>
    </div>
    """

    approval_items = "\
".join(
        f"""
        <li class="approval-list-item status-{str(decision).lower().replace(' ', '_')}">
          <div class="approval-item-top">
            <span class="lock-icon">{"🔓" if str(decision) == "approved" else "🔒"}</span>
            <span class="approval-title">{escape(name.upper())} Review</span>
          </div>
          <span class="approval-badge status-{str(decision).lower().replace(' ', '_')}">
            {escape("✓ Approved" if str(decision) == "approved" else "⏳ Pending" if str(decision) == "pending" else str(decision).replace("_", " ").upper())}
          </span>
        </li>
        """
        for name, decision in approvals_dict.items()
    )
    if not approval_items:
        approval_items = '<li class="empty-state">No approvals recorded</li>'

    approval_review_cards = "\
".join(
        f"""
        <article class="approval-card" id="approval-{escape(review["approval"])}">
          <div class="role-topline">
            <h3>{escape(review["approval"].upper())} Review</h3>
            <span class="role-status status-{escape(str(review["decision"]).lower().replace(' ', '_'))}">{escape(review["decision"])}</span>
          </div>
          <dl class="role-meta">
            <dt>Artifact</dt><dd>{escape(review["artifact"])}</dd>
            <dt>Path</dt><dd>{render_file_path(review["artifact_path"], review["artifact"]) if review["artifact_path"] else 'not available'}</dd>
          </dl>
          {render_review_roles(review["review_roles"])}
          {f'''
          <div class="artifact-editor" id="editor-{escape(review["artifact"])}" hidden>
            <textarea data-editor-for="{escape(review["artifact"])}" spellcheck="false"></textarea>
            <div class="approval-actions">
              <button class="primary save-artifact" data-artifact="{escape(review["artifact"])}" type="button">Save Markdown</button>
              <button class="cancel-edit" data-artifact="{escape(review["artifact"])}" type="button">Cancel</button>
            </div>
            <code class="editor-status" id="editor-status-{escape(review["artifact"])}">Unsaved edits stay in this browser until saved.</code>
          </div>
          ''' if review["artifact_path"] and str(review["artifact_path"]).lower().endswith((".md", ".markdown")) else ''}
          {f'<div class="artifact-preview markdown-preview">{render_markdown_preview(review["content"])}</div>' if review["content"] else '<div class="empty-state">No artifact content available for this approval yet.</div>'}
          {f'''
          <div class="sticky-bar">
            <button class="primary approval-decision" data-approval="{escape(review["approval"])}" data-decision="approved" type="button">Approve</button>
            <button class="approval-decision" data-approval="{escape(review["approval"])}" data-decision="changes_requested" type="button">Request Changes</button>
            <button class="danger approval-decision" data-approval="{escape(review["approval"])}" data-decision="rejected" type="button">Reject</button>
            <div style="flex: 1;"></div>
            {f'<button class="edit-artifact" data-artifact="{escape(review["artifact"])}" type="button" style="box-shadow:none;">Edit Artifact</button>' if review["artifact_path"] and str(review["artifact_path"]).lower().endswith((".md", ".markdown")) else ''}
          </div>
          ''' if review["is_actionable"] else ''}
        </article>
        """
        for review in build_approval_reviews(state, workspace=workspace)
    )
    if not approval_review_cards:
        approval_review_cards = '<div class="empty-state">No approval review items available.</div>'
    
    project_key = state.project_key if state is not None else str(start_context.get("project_key", ""))
    
    # Empty State for Actions
    if not build_action_items(workspace, project_key=project_key if state is not None else ""):
        action_cards = """
        <div class="empty-state">
          <div class="empty-icon">📭</div>
          <div class="empty-title">Workflow hasn't started</div>
          <div class="empty-desc">Actions will appear here as workers run and approvals are made. Try triggering the first action.</div>
          <button class="button primary" onclick="document.querySelector('[data-tab=&quot;overview&quot;]').click();" type="button">⚡ Go to Overview</button>
        </div>
        """
    else:
        action_cards = "\
".join(
            f"""
            <article class="action-card">
              <div class="role-topline">
                <h3>{escape(action["title"])}</h3>
                <span class="role-status">{escape(action["actor"])}</span>
              </div>
              <dl class="role-meta">
                <dt>Time</dt><dd>{escape(action.get("timestamp", ""))}</dd>
                <dt>Action</dt><dd>{escape(action.get("action", ""))}</dd>
                <dt>Gate</dt><dd>{escape(action.get("gate", "") or "none")}</dd>
                <dt>Decision</dt><dd>{escape(action.get("decision", "") or "none")}</dd>
                <dt>Node</dt><dd>{escape(action.get("node_transition", "") or "none")}</dd>
                <dt>Artifact</dt><dd>{render_file_path(action.get("artifact_path", ""), action.get("artifact", "")) if action.get("artifact_path", "") else escape(action.get("artifact", "") or "none")}</dd>
              </dl>
            </article>
            """
            for action in build_action_items(workspace, project_key=project_key if state is not None else "")
        )
    
    # Empty State for Smoke Runs
    if not build_smoke_run_items(workspace):
        smoke_cards = """
        <div class="empty-state">
          <div class="empty-icon">🧪</div>
          <div class="empty-title">No smoke tests yet</div>
          <div class="empty-desc">Smoke runs are auto-triggered after Staging completes. You'll see test results here when ready.</div>
        </div>
        """
    else:
        smoke_cards = "\
".join(
            f"""
            <article class="action-card">
              <div class="role-topline">
                <h3>{escape(item.get("project_key", ""))} cycle {escape(item.get("cycle", ""))}</h3>
                <span class="role-status status-{escape(str(item.get("status", "")).lower().replace(' ', '_'))}">{escape(item.get("status", ""))}</span>
              </div>
              <dl class="role-meta">
                <dt>Suite</dt><dd>{escape(item.get("suite", ""))}</dd>
                <dt>Classification</dt><dd>{escape(item.get("classification", "") or "none")}</dd>
                <dt>Next Task</dt><dd>{escape(item.get("next_task", "") or "none")}</dd>
                <dt>Report</dt><dd>{render_file_path(item.get("report_path", "")) if item.get("report_path", "") else "none"}</dd>
              </dl>
            </article>
            """
            for item in build_smoke_run_items(workspace)
        )
    
    agile_board = build_agile_board(state)
    agile_columns = dict(agile_board.get("columns", {}))
    agile_counts = dict(agile_board.get("counts", {}))
    agile_column_labels = {
        "backlog": "Backlog",
        "doing": "Doing",
        "blocked": "Blocked",
        "done": "Done",
    }
    
    # Empty State for Agile Board
    if sum(agile_counts.values()) == 0:
        agile_board_cards = """
        <div class="empty-state" style="grid-column: 1 / -1;">
          <div class="empty-icon">📋</div>
          <div class="empty-title">Task board is empty</div>
          <div class="empty-desc">Tasks materialize after the Planner step. Currently waiting for <span class="file-path-chip"><code>task_breakdown</code></span></div>
          <button class="button" onclick="document.querySelector('[data-tab=&quot;overview&quot;]').click();" type="button">→ Go to Overview</button>
        </div>
        """
    else:
        agile_board_cards = "\
".join(
            f"""
            <section class="kanban-column">
              <div class="kanban-column-head">
                <h3 style="color: {'var(--warning-dark)' if column == 'blocked' else 'var(--success-dark)' if column == 'done' else 'var(--muted)'};">{escape(agile_column_labels[column])}</h3>
                <span>{escape(str(agile_counts.get(column, 0)))}</span>
              </div>
              <div class="task-stack">
                {render_task_cards(list(agile_columns.get(column, [])))}
              </div>
            </section>
            """
            for column in ["backlog", "doing", "blocked", "done"]
        )
    
    role_cards = "\
".join(
        f"""
        <article class="role-card">
          <div class="role-topline">
            <h3>{escape(role["name"])}</h3>
            <span class="role-status status-{escape(str(role["status"]).lower().replace(' ', '_'))}">{escape(role["status"])}</span>
          </div>
          <p>{escape(role["description"])}</p>
          <dl class="role-meta">
            <dt>Model</dt><dd>{escape(role["model"])}</dd>
            <dt>Mode</dt><dd>{escape(role["mode"])}</dd>
            <dt>Artifact</dt><dd>{escape(role["artifact"])}</dd>
          </dl>
          {render_file_path(role["artifact_path"], role["artifact"]) if role["artifact_path"] else ''}
        </article>
        """
        for role in role_observations
    )
    if not role_cards:
        role_cards = '<div class="empty-state">No role state available because there is no active run.</div>'
    waterfall_cards = "\
".join(
        f"""
        <article class="flow-card flow-{escape(step["status"])}">
          <div class="flow-index">{escape(step["number"])}</div>
          <div class="flow-body">
            <div class="flow-topline">
              <h3>{escape(step["title"])}</h3>
              <span class="flow-status">{escape(step["status_label"])}</span>
            </div>
            <p>{escape(step["summary"])}</p>
            <p class="flow-feedback">{escape(step["feedback"])}</p>
            <dl class="role-meta">
              <dt>Roles</dt><dd>{escape(step["roles"])}</dd>
              <dt>Artifact</dt><dd>{escape(step["artifact"] or "none")}</dd>
              {f'<dt>Path</dt><dd>{render_file_path(step["artifact_path"], step["artifact"])}</dd>' if step["artifact_path"] else ''}
            </dl>
            {f'<button class="primary review-link" data-approval="{escape(step["approval"])}" type="button">{escape(step["action_label"])}</button>' if step["action_label"] else ''}
          </div>
        </article>
        """
        for step in workflow_steps
    )
    current_step = next(
        (step for step in workflow_steps if step["status"] in {"active", "ready", "review-needed", "blocked"}),
        None,
    )
    current_work_html = (
        f"""
        <section class="card" style="margin-top: 20px;">
          <div class="card-title">Current Work</div>
          <dl class="role-meta">
            <dt>Step</dt><dd>{escape(current_step["title"])}</dd>
            <dt>Status</dt><dd><span class="flow-status">{escape(current_step["status_label"])}</span></dd>
            <dt>Roles</dt><dd>{escape(current_step["roles"])}</dd>
            <dt>Artifact</dt><dd>{escape(current_step["artifact"] or "none")}</dd>
            <dt>Feedback</dt><dd>{escape(current_step["feedback"])}</dd>
          </dl>
        </section>
        """
        if current_step is not None
        else ""
    )
    project_report = dict(start_context.get("project_report", {}))
    task_backend = normalize_task_backend(start_context.get("task_backend", {"mode": "local", "sync": []}))
    task_sync_values = [str(value) for value in task_backend.get("sync", [])]
    task_sync_label = "Local only" if not task_sync_values else "Local + " + ", ".join(task_sync_values)
    worker_backend = normalize_worker_backend(start_context.get("worker_backend", {"mode": "codex"}))
    worker_backend_mode = str(worker_backend.get("mode", "codex"))
    deployment_backend = dict(start_context.get("deployment_backend", {"mode": ""}))
    deployment_mode = str(deployment_backend.get("mode", "") or "")
    delivery_contract = dict(start_context.get("delivery_contract", {}))
    delivery_contract_required = bool(delivery_contract.get("required", False))
    delivery_contract_minimum = str(delivery_contract.get("minimum_evidence", "infrastructure") or "infrastructure")
    delivery_contract_level = str(delivery_contract.get("evidence_level", "infrastructure") or "infrastructure")
    delivery_contract_acceptance_count = str(delivery_contract.get("acceptance_count", 0))
    delivery_contract_configured = bool(delivery_contract.get("configured", False))
    project_root = Path(state.context_packet_path).parents[2] if state is not None and state.context_packet_path else None
    
    # Empty state for Workers tab
    if state is None or not build_worker_job_items(project_root):
        worker_job_items = """
        <div class="empty-state">
          <div class="empty-icon">🤖</div>
          <div class="empty-title">No background jobs</div>
          <div class="empty-desc">Worker dispatches will appear here once the Planning step is approved.</div>
          <button class="button" onclick="document.querySelector('[data-tab=&quot;overview&quot;]').click();" type="button">→ Go to Overview</button>
        </div>
        """
    else:
        worker_job_items = "\
".join(
            (
                f"<li><span>{escape(item['role'])} / {escape(item['adapter'])}</span>"
                f"<code style='display: flex; align-items: center; gap: 8px; justify-content: flex-end;'><span class='role-status status-{escape(item['status'].lower().replace(' ', '_'))}' style='padding: 2px 6px; font-size: 9px; line-height: 1; text-transform: uppercase;'>{escape(item['status'])}</span> · {escape(item['id'])}"
                f"{' · pid ' + escape(item['pid']) if item['pid'] else ''}"
                f"{' · heartbeat ' + escape(item['heartbeat_at']) if item['heartbeat_at'] else ''}"
                f"{' · ' + escape(item['summary']) if item['summary'] else ''}</code></li>"
            )
            for item in build_worker_job_items(project_root)
        )
        
    workers = dict(project_report.get("workers", {}))
    worker_items = "\
".join(
        f"<li><span>{escape(name)}</span><code>{escape(str(dict(worker).get('path') or dict(worker).get('command') or 'missing'))}</code></li>"
        for name, worker in workers.items()
    )
    if not worker_items:
        worker_items = "<li><span>No worker config</span><code>Project target not loaded</code></li>"
    providers = dict(project_report.get("providers", {}))
    provider_items = "\
".join(
        f"<li><span>{escape(name)}</span><strong>{'configured' if dict(provider).get('configured') else 'not configured'}</strong></li>"
        for name, provider in providers.items()
    )
    if not provider_items:
        provider_items = "<li><span>No providers</span><code>Project target not loaded</code></li>"
    profiles = [str(profile) for profile in start_context.get("profiles", ["balanced"])]
    selected_profile = str(start_context.get("model_profile", "balanced") or "balanced")
    selected_delivery_mode = normalize_delivery_mode(str(start_context.get("delivery_mode", "init_project") or "init_project"))
    profile_options = "\
".join(
        f'<option value="{escape(profile)}"{" selected" if profile == selected_profile else ""}>{escape(profile)}</option>'
        for profile in profiles
    )
    delivery_mode_options = render_delivery_mode_options(selected_delivery_mode)
    worker_model = worker_model_label(state, start_context)
    start_workflow_section = ""
    if state is None:
        start_workflow_section = f"""
        <section class="wide start-workflow-panel">
          <h2>Start Workflow</h2>
          <form id="start-workflow-form" style="display: grid; gap: 16px;">
            <div class="mode-switch">
              <button type="button" class="mode-button active" id="mode-new">Start New Project</button>
              <button type="button" class="mode-button" id="mode-existing">Adopt Existing Project</button>
            </div>
            <input type="hidden" name="source_type" id="source_type" value="new_project">
            
            <label>
              Goal Description
              <textarea name="idea" placeholder="Describe the feature or project goal..." required></textarea>
            </label>
            
            <div class="console-grid">
              <label>
                Model Profile
                <select name="model_profile">
                  {profile_options}
                </select>
              </label>
              <label>
                Delivery Mode
                <select name="delivery_mode">
                  {delivery_mode_options}
                </select>
              </label>
            </div>
            
            <div id="existing-project-fields" style="display: none; grid-gap: 16px; flex-direction: column;">
              <label>
                Local Project Config file (.local.yaml)
                <select name="project_config">
                  <option value="">(create new config below)</option>
                  {"".join(f'<option value="{escape(str(path))}">{escape(path.name)} ({escape(str(path))})</option>' for path in discover_project_configs(workspace))}
                </select>
              </label>
              <div id="new-config-fields" style="display: grid; gap: 16px; border: 2px solid var(--line); border-radius: 12px; padding: 16px; background: #fafafa;">
                <h4 style="margin: 0; font-size: 11px; text-transform: uppercase; color: var(--muted);">New Project Configuration</h4>
                <div class="console-grid">
                  <label>Project Name <input type="text" name="new_project_name" placeholder="e.g. AgentsMesh"></label>
                  <label>Project Key <input type="text" name="new_project_key" placeholder="e.g. AGENTSMESH"></label>
                </div>
                <div class="console-grid">
                  <label>
                    Docs Path (absolute)
                    <input type="text" name="docs_path" placeholder="/Users/luke/path/to/docs" list="candidate-dirs">
                  </label>
                  <label>
                    Backend Path (absolute)
                    <input type="text" name="backend_path" placeholder="/Users/luke/path/to/backend" list="candidate-dirs">
                  </label>
                </div>
                <div class="console-grid">
                  <label>
                    Frontend Path (absolute)
                    <input type="text" name="frontend_path" placeholder="/Users/luke/path/to/frontend" list="candidate-dirs">
                  </label>
                  <label>
                    Sync Tasks
                    <div style="display: flex; gap: 12px; margin-top: 6px;">
                      <label class="checkbox-label"><input type="checkbox" name="task_sync" value="plane"> Plane</label>
                    </div>
                  </label>
                </div>
                
                <div id="plane-sync-fields" style="display: none; gap: 12px; grid-template-columns: repeat(2, 1fr); border-top: 1px solid var(--line); padding-top: 12px;">
                  <label>Plane Host URL <input type="text" name="plane_api_host_url" value="https://app.plane.so"></label>
                  <label>Plane Workspace <input type="text" name="plane_workspace_slug" placeholder="workspace-slug"></label>
                  <label>Plane Project ID <input type="text" name="plane_project_id" placeholder="project-uuid"></label>
                  <label>Plane API Key Env Var <input type="text" name="plane_api_key_env" value="PLANE_API_KEY"></label>
                  <label style="grid-column: 1 / -1;">Plane MCP Server Name <input type="text" name="plane_mcp_server" value="plane"></label>
                </div>
                
                <div style="border-top: 1px solid var(--line); padding-top: 12px; display: grid; gap: 12px;">
                  <label class="checkbox-label"><input type="checkbox" name="delivery_contract_required" value="true"> Require Delivery Contract</label>
                  <div id="delivery-contract-fields" style="display: none; gap: 12px; grid-template-columns: 1fr; margin-left: 20px;">
                    <label>
                      Minimum Evidence Level
                      <select name="delivery_contract_minimum">
                        <option value="infrastructure">Infrastructure (Build & Test only)</option>
                        <option value="functional">Functional (Requires Staging Acceptance evidence)</option>
                      </select>
                    </label>
                    <label>
                      Fatal Output Patterns (one per line)
                      <textarea name="delivery_contract_fatal_patterns" placeholder="e.g. [ERROR]&#10;failed to start" style="min-height: 60px;"></textarea>
                    </label>
                    <label>
                      Staging Acceptance Checks (id | type | command)
                      <textarea name="delivery_contract_acceptance" placeholder="e.g. reg-chk | ssh_command | curl -f http://107.148.180.35:8088/health" style="min-height: 60px;"></textarea>
                    </label>
                  </div>
                </div>
                
                <div style="border-top: 1px solid var(--line); padding-top: 12px; display: grid; gap: 12px;">
                  <label>
                    Deployment Mode
                    <select name="deployment_backend_mode">
                      <option value="">No Staging Deployment</option>
                      <option value="ssh_compose">SSH + Docker Compose</option>
                    </select>
                  </label>
                  <div id="deployment-backend-fields" style="display: none; gap: 12px; grid-template-columns: repeat(2, 1fr); margin-left: 20px;">
                    <label>SSH Target (user@host) <input type="text" name="staging_ssh_target" placeholder="root@107.148.180.35"></label>
                    <label>Remote Deployment Directory <input type="text" name="staging_remote_dir" placeholder="/srv/agentsmesh"></label>
                    <label>Git Target Ref (branch/commit) <input type="text" name="staging_git_ref" placeholder="main"></label>
                    <label>Public Verification URL <input type="text" name="staging_public_url" placeholder="http://107.148.180.35:8088"></label>
                    <label style="grid-column: 1 / -1;">Custom Deploy Command (remotely run) <input type="text" name="staging_deploy_command" placeholder="bash deploy/selfhost/init-project.sh"></label>
                    <label style="grid-column: 1 / -1;">Custom Healthcheck Command (remotely run) <input type="text" name="staging_healthcheck_command" placeholder="docker compose ps --format json"></label>
                    <label style="grid-column: 1 / -1;">Custom Smoke Test Command (locally run) <input type="text" name="staging_smoke_command" placeholder="npm run test:staging"></label>
                  </div>
                </div>
                
                <div style="border-top: 1px solid var(--line); padding-top: 12px;">
                  <label>
                    Model overrides (one per line, e.g. implementation=codex:gpt-5.5)
                    <textarea name="role_model_overrides" placeholder="e.g. implementation=codex:gpt-5.5" style="min-height: 60px;"></textarea>
                  </label>
                </div>
                
                <datalist id="candidate-dirs">
                  {"".join(f'<option value="{escape(item["path"])}">{escape(item["name"])}</option>' for item in start_context.get("candidate_dirs", []))}
                </datalist>
              </div>
            </div>
            
            <div class="approval-actions" style="margin-top: 10px;">
              <button class="primary" type="submit" data-adopt-label="Start adoption" data-new-label="Start new project">Start Workflow</button>
              <button class="btn-secondary" id="start-dry-run-btn" type="button" style="box-shadow: none;">Dry Run Planning</button>
            </div>
          </form>
          <div id="dry-run-console" class="command-box" style="display: none; margin-top: 16px;"></div>
        </section>
        """
    elif closed_run:
        start_workflow_section = f"""
        <details class="start-workflow-drawer">
          <summary>Start Another Workflow</summary>
          <div class="start-workflow-panel">
            <h3>Start Workflow</h3>
            <form id="start-workflow-form" style="display: grid; gap: 16px;">
              <div class="mode-switch">
                <button type="button" class="mode-button active" id="mode-new">Start New Project</button>
                <button type="button" class="mode-button" id="mode-existing">Adopt Existing Project</button>
              </div>
              <input type="hidden" name="source_type" id="source_type" value="new_project">
              
              <label>
                Goal Description
                <textarea name="idea" placeholder="Describe the feature or project goal..." required></textarea>
              </label>
              
              <div class="console-grid">
                <label>
                  Model Profile
                  <select name="model_profile">
                    {profile_options}
                  </select>
                </label>
                <label>
                  Delivery Mode
                  <select name="delivery_mode">
                    {delivery_mode_options}
                  </select>
                </label>
              </div>
              
              <div id="existing-project-fields" style="display: none; grid-gap: 16px; flex-direction: column;">
                <label>
                  Local Project Config file (.local.yaml)
                  <select name="project_config">
                    <option value="">(create new config below)</option>
                    {"".join(f'<option value="{escape(str(path))}">{escape(path.name)} ({escape(str(path))})</option>' for path in discover_project_configs(workspace))}
                  </select>
                </label>
                <div id="new-config-fields" style="display: grid; gap: 16px; border: 2px solid var(--line); border-radius: 12px; padding: 16px; background: #fafafa;">
                  <h4 style="margin: 0; font-size: 11px; text-transform: uppercase; color: var(--muted);">New Project Configuration</h4>
                  <div class="console-grid">
                    <label>Project Name <input type="text" name="new_project_name" placeholder="e.g. AgentsMesh"></label>
                    <label>Project Key <input type="text" name="new_project_key" placeholder="e.g. AGENTSMESH"></label>
                  </div>
                  <div class="console-grid">
                    <label>
                      Docs Path (absolute)
                      <input type="text" name="docs_path" placeholder="/Users/luke/path/to/docs" list="candidate-dirs">
                    </label>
                    <label>
                      Backend Path (absolute)
                      <input type="text" name="backend_path" placeholder="/Users/luke/path/to/backend" list="candidate-dirs">
                    </label>
                  </div>
                  <div class="console-grid">
                    <label>
                      Frontend Path (absolute)
                      <input type="text" name="frontend_path" placeholder="/Users/luke/path/to/frontend" list="candidate-dirs">
                    </label>
                    <label>
                      Sync Tasks
                      <div style="display: flex; gap: 12px; margin-top: 6px;">
                        <label class="checkbox-label"><input type="checkbox" name="task_sync" value="plane"> Plane</label>
                      </div>
                    </label>
                  </div>
                  
                  <div id="plane-sync-fields" style="display: none; gap: 12px; grid-template-columns: repeat(2, 1fr); border-top: 1px solid var(--line); padding-top: 12px;">
                    <label>Plane Host URL <input type="text" name="plane_api_host_url" value="https://app.plane.so"></label>
                    <label>Plane Workspace <input type="text" name="plane_workspace_slug" placeholder="workspace-slug"></label>
                    <label>Plane Project ID <input type="text" name="plane_project_id" placeholder="project-uuid"></label>
                    <label>Plane API Key Env Var <input type="text" name="plane_api_key_env" value="PLANE_API_KEY"></label>
                    <label style="grid-column: 1 / -1;">Plane MCP Server Name <input type="text" name="plane_mcp_server" value="plane"></label>
                  </div>
                  
                  <div style="border-top: 1px solid var(--line); padding-top: 12px; display: grid; gap: 12px;">
                    <label class="checkbox-label"><input type="checkbox" name="delivery_contract_required" value="true"> Require Delivery Contract</label>
                    <div id="delivery-contract-fields" style="display: none; gap: 12px; grid-template-columns: 1fr; margin-left: 20px;">
                      <label>
                        Minimum Evidence Level
                        <select name="delivery_contract_minimum">
                          <option value="infrastructure">Infrastructure (Build & Test only)</option>
                          <option value="functional">Functional (Requires Staging Acceptance evidence)</option>
                        </select>
                      </label>
                      <label>
                        Fatal Output Patterns (one per line)
                        <textarea name="delivery_contract_fatal_patterns" placeholder="e.g. [ERROR]&#10;failed to start" style="min-height: 60px;"></textarea>
                      </label>
                      <label>
                        Staging Acceptance Checks (id | type | command)
                        <textarea name="delivery_contract_acceptance" placeholder="e.g. reg-chk | ssh_command | curl -f http://107.148.180.35:8088/health" style="min-height: 60px;"></textarea>
                      </label>
                    </div>
                  </div>
                  
                  <div style="border-top: 1px solid var(--line); padding-top: 12px; display: grid; gap: 12px;">
                    <label>
                      Deployment Mode
                      <select name="deployment_backend_mode">
                        <option value="">No Staging Deployment</option>
                        <option value="ssh_compose">SSH + Docker Compose</option>
                      </select>
                    </label>
                    <div id="deployment-backend-fields" style="display: none; gap: 12px; grid-template-columns: repeat(2, 1fr); margin-left: 20px;">
                      <label>SSH Target (user@host) <input type="text" name="staging_ssh_target" placeholder="root@107.148.180.35"></label>
                      <label>Remote Deployment Directory <input type="text" name="staging_remote_dir" placeholder="/srv/agentsmesh"></label>
                      <label>Git Target Ref (branch/commit) <input type="text" name="staging_git_ref" placeholder="main"></label>
                      <label>Public Verification URL <input type="text" name="staging_public_url" placeholder="http://107.148.180.35:8088"></label>
                      <label style="grid-column: 1 / -1;">Custom Deploy Command (remotely run) <input type="text" name="staging_deploy_command" placeholder="bash deploy/selfhost/init-project.sh"></label>
                      <label style="grid-column: 1 / -1;">Custom Healthcheck Command (remotely run) <input type="text" name="staging_healthcheck_command" placeholder="docker compose ps --format json"></label>
                      <label style="grid-column: 1 / -1;">Custom Smoke Test Command (locally run) <input type="text" name="staging_smoke_command" placeholder="npm run test:staging"></label>
                    </div>
                  </div>
                  
                  <div style="border-top: 1px solid var(--line); padding-top: 12px;">
                    <label>
                      Model overrides (one per line, e.g. implementation=codex:gpt-5.5)
                      <textarea name="role_model_overrides" placeholder="e.g. implementation=codex:gpt-5.5" style="min-height: 60px;"></textarea>
                    </label>
                  </div>
                  
                  <datalist id="candidate-dirs">
                    {"".join(f'<option value="{escape(item["path"])}">{escape(item["name"])}</option>' for item in start_context.get("candidate_dirs", []))}
                  </datalist>
                </div>
              </div>
              
              <div class="approval-actions" style="margin-top: 10px;">
                <button class="primary" type="submit" data-adopt-label="Start adoption" data-new-label="Start new project">Start Workflow</button>
                <button class="btn-secondary" id="start-dry-run-btn" type="button" style="box-shadow: none;">Dry Run Planning</button>
              </div>
            </form>
            <div id="dry-run-console" class="command-box" style="display: none; margin-top: 16px;"></div>
          </div>
        </details>
        """

    header_status = state.next_node or state.current_node if state is not None else "no_active_run"
    status_class_norm = str(header_status).lower().strip()
    is_complete = status_class_norm in {'complete', 'done', 'pass', 'success'}
    if is_complete:
        status_badge_html = """
        <div class="top-status-badge status-complete">
          <span>✅ COMPLETE</span>
        </div>
        """
    elif status_class_norm == 'approved':
        status_badge_html = """
        <div class="top-status-badge status-approved">
          <span>✓ APPROVED</span>
        </div>
        """
    elif status_class_norm == 'pending':
        status_badge_html = """
        <div class="top-status-badge status-pending">
          <span>⏳ PENDING</span>
        </div>
        """
    else:
        status_class = "status-running" if status_class_norm in {'running', 'active', 'ready'} else "status-pending" if status_class_norm in {'pending', 'blocked', 'changes_requested', 'review-needed', 'await_prd_approval', 'await_rfc_approval', 'await_staging_approval', 'await_security_approval', 'await_release_approval', 'await_takeover_approval'} else "status-failed" if status_class_norm in {'failed', 'error', 'rejected'} else "status-neutral"
        status_badge_html = f"""
        <div class="top-status-badge {status_class}">
          <span>{escape(header_status.replace('_', ' ').upper())}</span>
        </div>
        """

    # Generate Recent Activity HTML
    recent_activity_html = build_recent_activity_html(build_action_items(workspace, project_key=project_key if state is not None else ""))

    # Generate Current Run HTML inside left card
    blocker_html = ""
    if state is not None and state.blockers:
        blocker_items = "".join(f"<li>{escape(blocker)}</li>" for blocker in state.blockers)
        blocker_html = f"""
        <div class="empty-state blocker-state"><strong>Blocked</strong><ul>{blocker_items}</ul></div>
        <div class="approval-actions">
          <button class="primary retry-blocked" type="button">Retry blocked workers</button>
        </div>
        """

    current_run_html = (
        f"""
        <section class="hero wide">
          <div class="completion-banner">
            <p class="eyebrow-line">Run Complete</p>
            <p class="idea" style="font-weight: 700;">{escape(state.idea)}</p>
            <div class="completion-actions" style="margin-top: 14px;">
              <a class="button primary" href="/">Open project overview</a>
              <button class="review-link" data-approval="release" type="button">Review release notes</button>
            </div>
          </div>
          <div class="meta-table-wrapper" style="margin-top: 14px;">
            <div style="display: none;"><div class="status">{escape(header_status)}</div></div>
            <div style="display: none;"><div class="signal"><span>Next</span>{escape(state.next_node or state.current_node)}</div></div>
            <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
              <tr style="border-bottom: 1px solid var(--line);"><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase; width: 120px;">Run ID</td><td style="padding: 6px 0; font-family: monospace; font-size: 12px; font-weight: 700;">{escape(state.run_id)}</td></tr>
              <tr style="border-bottom: 1px solid var(--line);"><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase;">Project</td><td style="padding: 6px 0; font-weight: 700;">{escape(state.project_key)}</td></tr>
              <tr style="border-bottom: 1px solid var(--line);"><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase;">Mode</td><td style="padding: 6px 0; font-weight: 700;">{escape(state.source_type)}</td></tr>
              <tr style="border-bottom: 1px solid var(--line);"><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase;">Next Node</td><td style="padding: 6px 0; font-weight: 700;"><span class="approval-badge status-{status_class_norm}">{escape(header_status.replace('_', ' ').upper())}</span></td></tr>
              <tr style="border-bottom: 1px solid var(--line);"><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase;">Model Profile</td><td style="padding: 6px 0; font-weight: 700;">{escape(state.model_profile)}</td></tr>
              <tr style="border-bottom: 1px solid var(--line);"><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase;">Delivery</td><td style="padding: 6px 0; font-weight: 700;">{escape(state.delivery_mode)}</td></tr>
              <tr><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase;">Worker Model</td><td style="padding: 6px 0; font-weight: 700;"><code>{escape(worker_model)}</code></td></tr>
            </table>
          </div>
          {blocker_html}
        </section>
        """
        if closed_run
        else (
        f"""
        <section class="hero">
          <p class="idea" style="font-weight: 700; margin-bottom: 14px;">{escape(state.idea)}</p>
          <div class="meta-table-wrapper">
            <div style="display: none;"><div class="status">{escape(header_status)}</div></div>
            <div style="display: none;"><div class="signal"><span>Next</span>{escape(state.next_node or state.current_node)}</div></div>
            <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
              <tr style="border-bottom: 1px solid var(--line);"><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase; width: 120px;">Run ID</td><td style="padding: 6px 0; font-family: monospace; font-size: 12px; font-weight: 700;">{escape(state.run_id)}</td></tr>
              <tr style="border-bottom: 1px solid var(--line);"><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase;">Project</td><td style="padding: 6px 0; font-weight: 700;">{escape(state.project_key)}</td></tr>
              <tr style="border-bottom: 1px solid var(--line);"><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase;">Mode</td><td style="padding: 6px 0; font-weight: 700;">{escape(state.source_type)}</td></tr>
              <tr style="border-bottom: 1px solid var(--line);"><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase;">Next Node</td><td style="padding: 6px 0; font-weight: 700;"><span class="approval-badge status-{status_class_norm}">{escape(header_status.replace('_', ' ').upper())}</span></td></tr>
              <tr style="border-bottom: 1px solid var(--line);"><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase;">Model Profile</td><td style="padding: 6px 0; font-weight: 700;">{escape(state.model_profile)}</td></tr>
              <tr style="border-bottom: 1px solid var(--line);"><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase;">Delivery</td><td style="padding: 6px 0; font-weight: 700;">{escape(state.delivery_mode)}</td></tr>
              <tr><td style="padding: 6px 0; color: var(--muted); font-weight: 800; font-size: 11px; text-transform: uppercase;">Worker Model</td><td style="padding: 6px 0; font-weight: 700;"><code>{escape(worker_model)}</code></td></tr>
            </table>
          </div>
          {blocker_html}
        </section>
        """
        if state is not None
        else '<div class="empty-state">No active run. Start a workflow to create state/current.json.</div>'
        )
    )

    state_panel_html = (
        f"""
        <dl>
          <dt>Run</dt><dd>{escape(state.run_id)}</dd>
          <dt>Project</dt><dd>{escape(state.project_key)}</dd>
          <dt>Current Node</dt><dd>{escape(state.current_node)}</dd>
          <dt>Next Node</dt><dd>{escape(state.next_node)}</dd>
          <dt>Model Profile</dt><dd>{escape(state.model_profile)}</dd>
          <dt>Delivery Mode</dt><dd>{escape(state.delivery_mode)}</dd>
          <dt>Source</dt><dd>{escape(state.source_type)} / {escape(state.source_ref)}</dd>
          <dt>Project Memory</dt><dd>{escape(state.context_packet_path or "not initialized")}</dd>
          <dt>Local Task Ledger</dt><dd>{escape(state.artifacts.get("task_ledger", "not initialized"))}</dd>
        </dl>
        """
        if state is not None
        else '<div class="empty-state">state/current.json not found.</div>'
    )

    steps = _dashboard_stepper_steps()
    node_sequence = _dashboard_stepper_node_sequence(steps)
    current_seq = 0
    if state is not None:
        if state.next_node and state.next_node.startswith("await_") and state.next_node.endswith("_approval"):
            current_seq = node_sequence.get(state.next_node, 1)
        else:
            current_seq = node_sequence.get(state.current_node, 1)
            
    stepper_html = []
    done_count = 0
    for idx, step_info in enumerate(steps, start=1):
        if idx < current_seq:
            status_class = "done"
            circle_content = "✓"
            done_count += 1
        elif idx == current_seq:
            status_class = "current"
            circle_content = "⏳"
        else:
            status_class = "pending"
            circle_content = str(idx)
            
        step_item = f"""
        <div class="step {status_class}">
          <div class="step-circle">{circle_content}</div>
          <div class="step-label">{escape(step_info["label"])}</div>
        </div>
        """
        stepper_html.append(step_item)
        if idx < len(steps):
            line_class = "done" if idx < current_seq else "pending"
            stepper_html.append(f'<div class="step-line {line_class}"></div>')
            
    stepper_output_html = f"""
    <div class="card">
      <div class="card-title">Pipeline Progress · {done_count} of {len(steps)} steps complete</div>
      <div class="stepper">
        {"".join(stepper_html)}
      </div>
    </div>
    """

    # Floated Action Required Alert
    actionable_reviews = [r for r in build_approval_reviews(state, workspace=workspace) if r["is_actionable"]]
    action_required_alert_html = ""
    if actionable_reviews:
        review = actionable_reviews[0]
        approval_key = review["approval"]
        review_roles_info = ""
        if review["review_roles"]:
            review_roles_info = f"<strong>Reviewers:</strong> {escape(', '.join(review['review_roles']))} ·"
        artifact_basename = Path(review["artifact_path"]).name if review["artifact_path"] else review["artifact"]
        read_report_btn = '<button class="button" onclick="document.querySelector(\'[data-tab=&quot;approvals&quot;]\').click();" type="button">📖 Read Report</button>' if review["artifact_path"] else ""
        action_required_alert_html = f"""
        <div class="alert">
          <div class="alert-icon">⏳</div>
          <div class="alert-body">
            <div class="alert-title">Action Required: {escape(approval_key.upper())} Review</div>
            <div class="alert-desc">
              The {escape(review["artifact"].replace("_", " ").upper())} report has been generated and is waiting for your approval.
              {review_roles_info}
              <strong>Artifact:</strong> <span class="file-path-chip" data-path="{escape(review["artifact_path"])}" data-artifact="{escape(review["artifact"])}"><code>{escape(artifact_basename)}</code></span>
            </div>
            <div class="alert-actions">
              {read_report_btn}
              <button class="button primary approval-decision" data-approval="{escape(approval_key)}" data-decision="approved" type="button">✓ Approve</button>
              <button class="button approval-decision" data-approval="{escape(approval_key)}" data-decision="changes_requested" type="button">↻ Request Changes</button>
              <button class="button danger approval-decision" data-approval="{escape(approval_key)}" data-decision="rejected" type="button">✗ Reject</button>
            </div>
          </div>
        </div>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>GoodTeam AI Team OS</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Nunito:ital,wght@0,300..900;1,300..900&display=swap" rel="stylesheet">
  <style>
{DASHBOARD_CSS}
  </style>
</head>
<body>
  <div class="dashboard-layout">
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="logo">
          <div class="logo-box">G</div>
          <span class="logo-text">GoodTeam</span>
        </div>
        <div class="sidebar-project">{escape(str(start_context.get("project_name", "Dashboard")))}</div>
        <div class="sidebar-project-meta">{escape(project_key)} · ACTIVE</div>
      </div>
      <nav class="sidebar-nav">
        <button class="nav-item active" data-tab="overview" type="button">📊 Overview</button>
        <button class="nav-item" data-tab="roles" type="button">👥 Roles</button>
        <button class="nav-item" data-tab="artifacts" type="button">📄 Artifacts</button>
        <button class="nav-item" data-tab="actions" type="button">⚡ Actions</button>
        <button class="nav-item" data-tab="board" type="button">📋 Agile Board</button>
        <button class="nav-item" data-tab="smoke" type="button">🚀 Smoke Runs</button>
        <button class="nav-item" data-tab="workers" type="button">⚙️ Workers</button>
        <button class="nav-item" data-tab="approvals" type="button">✅ Approvals</button>
        <button class="nav-item" data-tab="system" type="button">🛠️ System</button>
      </nav>
      <div class="sidebar-footer">
        <span>v0.1.0</span>
      </div>
    </aside>
    
    <div class="main-wrapper">
      <header class="top-bar">
        <div class="top-left">
          <div class="logo-box" style="width: 28px; height: 28px; font-size: 14px; border-radius: 8px; margin-right: 4px;">G</div>
          <nav class="breadcrumb">
            <a href="/">GoodTeam</a>
            <span class="breadcrumb-sep">›</span>
            <a href="/?project={escape(project_key)}">{escape(str(start_context.get("project_name", "Dashboard")))}</a>
            {f'''
            <span class="breadcrumb-sep">›</span>
            <span class="current">Run {escape(state.run_id)}</span>
            ''' if state else ''}
          </nav>
        </div>
        <div class="top-right">
          <div style="display: none;"><div class="status">{escape(header_status)}</div></div>
          {status_badge_html}
        </div>
      </header>
      
      <main class="content-area">
        <script>window.GOODTEAM_PROJECT_KEY = {project_key!r};</script>
        
        <!-- Tab Panels -->
        <div class="tab-panel active" id="tab-overview">
          {action_required_alert_html}
          {stepper_output_html}
          {current_work_html}

          <div style="display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px; margin-top: 20px; align-items: start;">
            <!-- LEFT: Current Run -->
            <div class="card">
              <div class="card-title">Current Run</div>
              {current_run_html}
              
              <!-- KPI Summary / Role Status -->
              <div style="margin-top: 20px; padding-top: 20px; border-top: 2px solid var(--line);">
                <div class="card-title">Role Status</div>
                <div class="kpi-grid">
                  {kpi_grid_html}
                </div>
              </div>
            </div>
            
            <!-- RIGHT: Recent Activity -->
            <div class="card">
              <div class="card-title">Recent Activity</div>
              <div class="timeline-wrapper">
                {recent_activity_html}
              </div>
              <button class="button" onclick="document.querySelector('[data-tab=&quot;actions&quot;]').click();" style="width: 100%; margin-top: 14px; text-transform: uppercase;" type="button">View all activity →</button>
            </div>
          </div>
          
          <!-- Collapsible Waterfall Details block -->
          <details class="start-workflow-drawer" style="margin-top: 20px;">
            <summary>Workflow Waterfall (Show all 13 step details)</summary>
            <div style="padding: 20px; background: white;">
              <div class="waterfall">{waterfall_cards}</div>
            </div>
          </details>
          
          {start_workflow_section}
        </div>
        
        <div class="tab-panel" id="tab-roles">
          <section class="wide">
            <h2>Role Summary</h2>
            <div style="display:none">Role Observability</div>
            <div class="roles">{role_cards}</div>
          </section>
        </div>
        
        <div class="tab-panel" id="tab-artifacts">
          <section>
            <h2>Artifacts</h2>
            <ul>{artifact_items}</ul>
          </section>
        </div>
        
        <div class="tab-panel" id="tab-actions">
          <section>
            <h2>Actions</h2>
            <div class="action-timeline">{action_cards}</div>
          </section>
        </div>
        
        <div class="tab-panel" id="tab-board">
          <section class="wide">
            <h2>Agile Board</h2>
            <div class="agile-board">{agile_board_cards}</div>
          </section>
        </div>
        
        <div class="tab-panel" id="tab-smoke">
          <section>
            <h2>Smoke Runs</h2>
            <div class="action-timeline">{smoke_cards}</div>
          </section>
        </div>
        
        <div class="tab-panel" id="tab-workers">
          <section>
            <h2>Worker Jobs</h2>
            <div class="action-timeline">{worker_job_items}</div>
          </section>
        </div>
        
        <div class="tab-panel" id="tab-approvals">
          {progress_bar_html}
          <div style="display: grid; grid-template-columns: 1fr 2fr; gap: 20px; align-items: start;">
            <ul class="approval-list">{approval_items}</ul>
            <div class="approval-cards-wrapper">{approval_review_cards}</div>
          </div>
        </div>
        
        <div class="tab-panel" id="tab-system">
          <div class="grid">
            <section>
              <h2>State</h2>
              {state_panel_html}
            </section>
            <section>
              <h2>Project Target</h2>
              <dl>
                <dt>Name</dt><dd>{escape(str(start_context["project_name"]))}</dd>
                <dt>Key</dt><dd>{escape(str(start_context["project_key"]))}</dd>
                <dt>Config</dt><dd>{render_file_path(str(start_context["project_config"])) if start_context.get("project_config") else "none"}</dd>
                <dt>Model Config</dt><dd>{render_file_path(str(start_context["model_config"])) if start_context.get("model_config") else "none"}</dd>
                <dt>Delivery Mode</dt><dd>{escape(selected_delivery_mode)}</dd>
                <dt>Task Backend</dt><dd>{escape(task_sync_label)}</dd>
                <dt>Worker Backend</dt><dd>{escape(worker_backend_mode)}</dd>
                <dt>Deployment Backend</dt><dd>{escape(deployment_mode or "none")}</dd>
                <dt>Staging SSH</dt><dd>{escape(str(deployment_backend.get("staging_ssh_target", "")) or "not configured")}</dd>
                <dt>Staging Remote Dir</dt><dd>{escape(str(deployment_backend.get("staging_remote_dir", "")) or "not configured")}</dd>
                <dt>Delivery Contract</dt><dd>{'required' if delivery_contract_required else 'optional'} / {'configured' if delivery_contract_configured else 'insufficient'}</dd>
                <dt>Minimum Evidence</dt><dd>{escape(delivery_contract_minimum)}</dd>
                <dt>Evidence Level</dt><dd>{escape(delivery_contract_level)}</dd>
                <dt>Acceptance Checks</dt><dd>{escape(delivery_contract_acceptance_count)}</dd>
              </dl>
            </section>
            <section>
              <h2>Workers</h2>
              <ul>{worker_items}</ul>
            </section>
            <section>
              <h2>Providers</h2>
              <ul>{provider_items}</ul>
            </section>
          </div>
        </div>
      </main>
    </div>
  </div>
  
  <!-- File Preview Modal -->
  <dialog id="file-preview-dialog" style="border: 2px solid var(--line); border-radius: 16px; padding: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.15); max-width: 800px; width: 90%; background: white; max-height: 80vh;">
    <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid var(--line); padding-bottom: 12px; margin-bottom: 16px;">
      <h3 style="margin: 0; font-size: 14px; font-weight: 800; color: var(--accent); text-transform: uppercase;" id="file-preview-title">File Preview</h3>
      <button type="button" class="button" onclick="document.getElementById('file-preview-dialog').close()" style="box-shadow: none; padding: 4px 8px;">✕ Close</button>
    </div>
    <div id="file-preview-content" class="markdown-preview" style="overflow-y: auto; max-height: calc(60vh - 40px); font-family: monospace; white-space: pre-wrap; background: var(--bg); padding: 16px; border-radius: 12px; border: 2px solid var(--line); font-size: 12px;">
      Loading file content...
    </div>
  </dialog>

  <script>
    {start_workflow_script}
    document.querySelectorAll(".nav-item").forEach((button) => {{
      button.addEventListener("click", () => {{
        document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        document.getElementById("tab-" + button.dataset.tab).classList.add("active");
      }});
    }});
    document.querySelectorAll(".review-link").forEach((button) => {{
      button.addEventListener("click", () => {{
        document.querySelector('[data-tab="approvals"]').click();
        const target = document.getElementById("approval-" + button.dataset.approval);
        if (target) {{
          target.scrollIntoView({{ behavior: "smooth", block: "start" }});
        }}
      }});
    }});
    document.querySelectorAll(".approval-decision").forEach((button) => {{
      button.addEventListener("click", async () => {{
        button.disabled = true;
        try {{
          const response = await fetch("/approve", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{
              project_key: window.GOODTEAM_PROJECT_KEY,
              approval: button.dataset.approval,
              decision: button.dataset.decision
            }})
          }});
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || "Failed to update approval.");
          }}
          window.setTimeout(() => window.location.reload(), 300);
        }} catch (error) {{
          button.disabled = false;
          const card = button.closest(".approval-card");
          if (card) {{
            const message = document.createElement("div");
            message.className = "empty-state";
            message.textContent = error.message;
            card.prepend(message);
          }}
        }}
      }});
    }});
    document.querySelectorAll(".edit-artifact").forEach((button) => {{
      button.addEventListener("click", async () => {{
        const artifact = button.dataset.artifact;
        const editor = document.getElementById("editor-" + artifact);
        const textarea = document.querySelector(`[data-editor-for="${{artifact}}"]`);
        const status = document.getElementById("editor-status-" + artifact);
        button.disabled = true;
        status.textContent = "Loading artifact...";
        try {{
          const response = await fetch("/artifact?key=" + encodeURIComponent(artifact) + "&project=" + encodeURIComponent(window.GOODTEAM_PROJECT_KEY));
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || "Failed to read artifact.");
          }}
          textarea.value = payload.content;
          editor.hidden = false;
          status.textContent = payload.path;
        }} catch (error) {{
          status.textContent = error.message;
        }} finally {{
          button.disabled = false;
        }}
      }});
    }});
    document.querySelectorAll(".cancel-edit").forEach((button) => {{
      button.addEventListener("click", () => {{
        const editor = document.getElementById("editor-" + button.dataset.artifact);
        if (editor) {{
          editor.hidden = true;
        }}
      }});
    }});
    document.querySelectorAll(".save-artifact").forEach((button) => {{
      button.addEventListener("click", async () => {{
        const artifact = button.dataset.artifact;
        const textarea = document.querySelector(`[data-editor-for="${{artifact}}"]`);
        const status = document.getElementById("editor-status-" + artifact);
        button.disabled = true;
        status.textContent = "Saving...";
        try {{
          const response = await fetch("/artifact", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ project_key: window.GOODTEAM_PROJECT_KEY, artifact, content: textarea.value }})
          }});
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || "Failed to save artifact.");
          }}
          status.textContent = "Saved " + payload.bytes + " bytes. Reloading preview...";
          window.setTimeout(() => window.location.reload(), 350);
        }} catch (error) {{
          status.textContent = error.message;
          button.disabled = false;
        }}
      }});
    }});
    document.querySelectorAll(".retry-blocked").forEach((button) => {{
      button.addEventListener("click", async () => {{
        button.disabled = true;
        button.textContent = "Retrying workers...";
        try {{
          const response = await fetch("/retry", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ project_key: window.GOODTEAM_PROJECT_KEY }})
          }});
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || "Failed to retry blocked workflow.");
          }}
          window.setTimeout(() => window.location.reload(), 300);
        }} catch (error) {{
          button.textContent = error.message;
          button.disabled = false;
        }}
      }});
    }});
    
    // File Path Chip Click Handler
    document.addEventListener("click", async (event) => {{
      const chip = event.target.closest(".file-path-chip, .file-chip");
      if (!chip) return;
      
      const path = chip.dataset.path || "";
      const artifact = chip.dataset.artifact || path || chip.innerText.trim();
      if (!path && !artifact) return;
      
      const dialog = document.getElementById("file-preview-dialog");
      const title = document.getElementById("file-preview-title");
      const content = document.getElementById("file-preview-content");
      
      if (!dialog) return;
      
      const filename = path ? path.split("/").pop() : artifact;
      title.textContent = "File Preview: " + filename;
      content.textContent = "Loading file content...";
      dialog.showModal();
      
      try {{
        const response = await fetch("/artifact?key=" + encodeURIComponent(artifact) + "&project=" + encodeURIComponent(project));
        const payload = await response.json();
        if (!response.ok) {{
          throw new Error(payload.error || "Failed to load file content.");
        }}
        content.textContent = payload.content;
      }} catch (error) {{
        content.textContent = "Error: " + error.message;
      }}
    }});
    
    // Coordinate-based light-dismiss dialog close
    document.querySelectorAll("dialog").forEach((dialog) => {{
      dialog.addEventListener("click", (event) => {{
        const rect = dialog.getBoundingClientRect();
        if (
          event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom
        ) {{
          dialog.close();
        }}
      }});
    }});
  </script>
</body>
</html>"""

def render_project_overview(workspace: Path, model_config: Path = Path("models.yaml")) -> str:
    project_items = build_project_overview_items(workspace, model_config=model_config)
    legacy_state = StateStore(workspace).read_current()
    cards = "\
".join(
        f"""
        <article class="project-card project-{escape(item["status"])}">
          <div>
            <h2>{escape(item["project_name"])}</h2>
            <p>{escape(item["project_key"])} · {escape(item["status"])}</p>
          </div>
          <dl>
            <dt>Model</dt><dd>{escape(item["model_profile"])}</dd>
            <dt>Delivery</dt><dd>{escape(item["delivery_mode"])}</dd>
            <dt>Next</dt><dd>{escape(item["next_node"] or "no active run")}</dd>
            <dt>Config</dt><dd>{render_file_path(item["project_config"])}</dd>
            <dt>Last Action</dt><dd>{escape(item["last_action"] or "none")}</dd>
          </dl>
          <div class="project-actions" style="display: flex; align-items: center; gap: 8px;">
            <a class="button primary" href="/?project={escape(item["project_key"])}">Open project</a>
            <a class="button" href="/?project={escape(item["project_key"])}">{'Resume' if item["status"] == "active" else 'Start'}</a>
            
            <div class="menu-container" style="position: relative; display: inline-block;">
              <button class="menu-trigger button" type="button" style="width: 32px; height: 32px; padding: 0; font-size: 18px; display: flex; align-items: center; justify-content: center; border-radius: 50%; box-shadow: 0 2px 0 var(--border-shadow);">⋮</button>
              <div class="menu-dropdown" style="display: none; position: absolute; right: 0; top: 100%; background: white; border: 2px solid var(--line); border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); z-index: 50; min-width: 140px; margin-top: 4px;">
                {f'<button class="terminate-project" data-project="{escape(item["project_key"])}" type="button" style="width: 100%; text-align: left; padding: 8px 12px; background: transparent; border: none; border-radius: 0; box-shadow: none; font-size: 11px; font-weight: 700; color: var(--red); justify-content: start;">Terminate run</button>' if item["status"] == "active" else ''}
                <button class="danger delete-project" data-project="{escape(item["project_key"])}" type="button" style="width: 100%; text-align: left; padding: 8px 12px; background: transparent; border: none; border-radius: 0; box-shadow: none; font-size: 11px; font-weight: 700; color: var(--red); justify-content: start;">Delete project</button>
              </div>
            </div>
          </div>
        </article>
        """
        for item in project_items
    )
    if not cards:
        cards = '<div class="empty-state">No local project configs found. Create a real project config with the dashboard form or add projects/&lt;project-key&gt;.local.yaml.</div>'
    
    legacy_notice = (
        f"""
        <div class="alert" style="background:var(--info-bg);border-color:var(--info);box-shadow:0 2px 0 var(--info-dark); margin-bottom: 20px; display: flex; gap: 16px; border: 2px solid var(--info); border-radius: 16px; padding: 18px 22px;">
          <div class="alert-icon">ℹ️</div>
          <div class="alert-body" style="flex: 1;">
            <div class="alert-title" style="color:var(--info-dark); font-weight: 900; font-size: 16px; margin-bottom: 4px;">Legacy Run Found</div>
            <div class="alert-desc" style="font-size: 13px; color: var(--ink); font-weight: 600; margin-bottom: 12px;">
              An older run state exists outside the project registry: <strong>{escape(legacy_state.project_key)}</strong>
              (status: <span class="badge error">{escape(legacy_state.next_node or legacy_state.current_node)}</span>) — referenced in <span class="file-path-chip" data-path="state/current.json"><code>state/current.json</code></span>.
              This typically happens from before the registry was introduced. You can archive it safely.
            </div>
            <div class="alert-actions" style="display: flex; gap: 10px; flex-wrap: wrap;">
              <button class="button view-files-btn" type="button">📁 View Files</button>
              <button class="button archive-legacy-btn" type="button">🗑 Archive Legacy State</button>
              <button class="button" onclick="this.closest(\'.alert\').style.display=\'none\';" style="background:transparent;box-shadow:none; border:none;" type="button">Dismiss</button>
            </div>
          </div>
        </div>
        """
        if legacy_state is not None
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GoodTeam Projects</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Nunito:ital,wght@0,300..900;1,300..900&display=swap" rel="stylesheet">
  <style>
{PROJECT_OVERVIEW_CSS}
  </style>
</head>
<body>
  <div class="top-bar">
    <div class="logo">
      <div class="logo-box">G</div>
      <span class="logo-text">GoodTeam</span>
      <span style="color: var(--line); margin: 0 8px;">|</span>
      <span style="font-size: 11px; font-weight: 800; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em;">Projects Control Room</span>
    </div>
  </div>
  <main>
    {legacy_notice}
    <section class="projects">
      {cards}
      <button id="add-project-btn" class="button" style="padding: 16px; border-style: dashed; border-width: 2px; border-color: var(--line); background: transparent; border-radius: 16px; font-weight: 800; font-size: 13px; color: var(--muted); cursor: pointer; text-align: center; justify-content: center; width: 100%; box-shadow: none;">+ Add or Create Project</button>
    </section>
  </main>
  
  <!-- Add Project Dialog -->
  <dialog id="add-project-dialog" style="border: 2px solid var(--line); border-radius: 16px; padding: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.15); max-width: 500px; width: 90%; background: white;">
    <form id="add-project-form" style="display: grid; gap: 16px;">
      <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid var(--line); padding-bottom: 12px; margin-bottom: 8px;">
        <h3 style="margin: 0; font-size: 16px; font-weight: 900; color: var(--ink);">Add Project Config</h3>
        <button type="button" class="button" onclick="document.getElementById('add-project-dialog').close()" style="box-shadow: none; padding: 4px 8px; border: none; background: transparent; font-weight: 900; cursor: pointer;">✕</button>
      </div>
      
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
        <label style="display: grid; gap: 6px; font-weight: 800; font-size: 11px; color: var(--muted); text-transform: uppercase;">
          Project Name
          <input type="text" name="project_name" placeholder="e.g. My App" style="width: 100%; border: 2px solid var(--line); border-radius: 12px; padding: 10px; font-size: 13px;" required />
        </label>
        <label style="display: grid; gap: 6px; font-weight: 800; font-size: 11px; color: var(--muted); text-transform: uppercase;">
          Project Key
          <input type="text" name="project_key" placeholder="e.g. MYAPP" style="width: 100%; border: 2px solid var(--line); border-radius: 12px; padding: 10px; font-size: 13px;" required />
        </label>
      </div>

      <div style="border-top: 1px solid var(--line); padding-top: 12px;">
        <span style="font-weight: 800; font-size: 11px; color: var(--muted); text-transform: uppercase; display: block; margin-bottom: 8px;">Repository Paths (Absolute paths, at least one is required)</span>
        <div style="display: grid; gap: 10px;">
          <label style="display: grid; gap: 4px; font-weight: 700; font-size: 11px; color: var(--ink);">
            Docs Repository
            <input type="text" name="docs_path" placeholder="/absolute/path/to/docs" style="width: 100%; border: 2px solid var(--line); border-radius: 12px; padding: 8px 10px; font-size: 12px;" />
          </label>
          <label style="display: grid; gap: 4px; font-weight: 700; font-size: 11px; color: var(--ink);">
            Backend Repository
            <input type="text" name="backend_path" placeholder="/absolute/path/to/backend" style="width: 100%; border: 2px solid var(--line); border-radius: 12px; padding: 8px 10px; font-size: 12px;" />
          </label>
          <label style="display: grid; gap: 4px; font-weight: 700; font-size: 11px; color: var(--ink);">
            Frontend Repository
            <input type="text" name="frontend_path" placeholder="/absolute/path/to/frontend" style="width: 100%; border: 2px solid var(--line); border-radius: 12px; padding: 8px 10px; font-size: 12px;" />
          </label>
        </div>
      </div>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; border-top: 1px solid var(--line); padding-top: 12px;">
        <label style="display: grid; gap: 6px; font-weight: 800; font-size: 11px; color: var(--muted); text-transform: uppercase;">
          Model Profile
          <select name="model_profile" style="width: 100%; border: 2px solid var(--line); border-radius: 12px; padding: 10px; font-size: 13px; background: white;">
            <option value="balanced">Balanced</option>
            <option value="cheap">Cheap</option>
            <option value="max_quality">Max Quality</option>
          </select>
        </label>
        <label style="display: grid; gap: 6px; font-weight: 800; font-size: 11px; color: var(--muted); text-transform: uppercase;">
          Delivery Mode
          <select name="delivery_mode" style="width: 100%; border: 2px solid var(--line); border-radius: 12px; padding: 10px; font-size: 13px; background: white;">
            <option value="init_project">Init Project</option>
            <option value="mvp">MVP</option>
            <option value="multi_service">Multi Service</option>
          </select>
        </label>
      </div>

      <div style="display: flex; gap: 10px; justify-content: flex-end; margin-top: 12px; border-top: 1px solid var(--line); padding-top: 12px;">
        <button type="button" class="button" onclick="document.getElementById('add-project-dialog').close()" style="box-shadow: none;">Cancel</button>
        <button type="submit" class="button primary" style="box-shadow: none;">Create Project</button>
      </div>
    </form>
  </dialog>
  
  <!-- Delete Confirm Dialog -->
  <dialog id="delete-confirm-dialog" style="border: 2px solid var(--line); border-radius: 16px; padding: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.15); max-width: 400px; width: 100%; background: white;">
    <form method="dialog" style="display: grid; gap: 16px;">
      <h3 style="margin: 0; font-size: 16px; font-weight: 900; color: var(--red);">Delete Project?</h3>
      <p style="margin: 0; color: var(--muted); font-size: 12px; line-height: 1.4;">
        This action is permanent and will delete the project <strong id="delete-project-name"></strong> from GoodTeam registry and local state.
      </p>
      <label style="display: grid; gap: 6px; font-weight: 800; font-size: 11px; color: var(--muted); text-transform: uppercase;">
        Please type the project key to confirm:
        <input type="text" id="delete-confirm-input" placeholder="e.g. AGENTSMESH" style="width: 100%; border: 2px solid var(--line); border-radius: 12px; padding: 10px; font-size: 13px;" required />
      </label>
      <div style="display: flex; gap: 10px; justify-content: flex-end; margin-top: 8px;">
        <button type="button" class="button" onclick="document.getElementById('delete-confirm-dialog').close()" style="box-shadow: none;">Cancel</button>
        <button type="submit" id="delete-confirm-submit-btn" class="button primary danger" style="background: var(--red); border-color: var(--red); box-shadow: none;" disabled>Delete</button>
      </div>
    </form>
  </dialog>

  <!-- File Preview Modal -->
  <dialog id="file-preview-dialog" style="border: 2px solid var(--line); border-radius: 16px; padding: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.15); max-width: 800px; width: 90%; background: white; max-height: 80vh;">
    <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid var(--line); padding-bottom: 12px; margin-bottom: 16px;">
      <h3 style="margin: 0; font-size: 14px; font-weight: 800; color: var(--accent); text-transform: uppercase;" id="file-preview-title">File Preview</h3>
      <button type="button" class="button" onclick="document.getElementById('file-preview-dialog').close()" style="box-shadow: none; padding: 4px 8px;">✕ Close</button>
    </div>
    <div id="file-preview-content" class="markdown-preview" style="overflow-y: auto; max-height: calc(60vh - 40px); font-family: monospace; white-space: pre-wrap; background: var(--bg); padding: 16px; border-radius: 12px; border: 2px solid var(--line); font-size: 12px;">
      Loading file content...
    </div>
  </dialog>

  <script>
    document.querySelectorAll(".terminate-project").forEach((button) => {{
      button.addEventListener("click", async () => {{
        button.disabled = true;
        try {{
          const response = await fetch("/terminate", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ project_key: button.dataset.project }})
          }});
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || "Failed to terminate run.");
          }}
          window.setTimeout(() => window.location.reload(), 250);
        }} catch (error) {{
          button.textContent = error.message;
          button.disabled = false;
        }}
      }});
    }});

    // Three-dot Action Trigger Dropdown Menu
    document.querySelectorAll(".menu-trigger").forEach((trigger) => {{
      trigger.addEventListener("click", (event) => {{
        event.stopPropagation();
        document.querySelectorAll(".menu-dropdown").forEach((d) => {{
          if (d !== trigger.nextElementSibling) d.style.display = "none";
        }});
        const dropdown = trigger.nextElementSibling;
        dropdown.style.display = dropdown.style.display === "block" ? "none" : "block";
      }});
    }});
    document.addEventListener("click", () => {{
      document.querySelectorAll(".menu-dropdown").forEach((d) => d.style.display = "none");
    }});

    // Dangerous project delete protection
    let projectToDelete = "";
    document.querySelectorAll(".delete-project").forEach((btn) => {{
      btn.addEventListener("click", () => {{
        projectToDelete = btn.dataset.project;
        document.getElementById("delete-project-name").textContent = projectToDelete;
        document.getElementById("delete-confirm-input").value = "";
        document.getElementById("delete-confirm-submit-btn").disabled = true;
        document.getElementById("delete-confirm-dialog").showModal();
      }});
    }});
    document.getElementById("delete-confirm-input").addEventListener("input", (e) => {{
      const value = e.target.value.trim().toUpperCase();
      document.getElementById("delete-confirm-submit-btn").disabled = (value !== projectToDelete);
    }});
    document.querySelector("#delete-confirm-dialog form").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const dialog = document.getElementById("delete-confirm-dialog");
      const submitBtn = document.getElementById("delete-confirm-submit-btn");
      submitBtn.disabled = true;
      try {{
        const response = await fetch("/project-delete", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ project_key: projectToDelete }})
        }});
        const payload = await response.json();
        if (!response.ok) {{
          throw new Error(payload.error || "Failed to delete project.");
        }}
        dialog.close();
        window.location.reload();
      }} catch (error) {{
        alert(error.message);
        submitBtn.disabled = false;
      }}
    }});

    // Legacy Run Alert Close/Dismiss or Archive
    document.querySelector(".archive-legacy-btn")?.addEventListener("click", async () => {{
      const btn = document.querySelector(".archive-legacy-btn");
      btn.disabled = true;
      try {{
        const response = await fetch("/terminate", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ project_key: "" }})
        }});
        if (!response.ok) {{
          const payload = await response.json();
          throw new Error(payload.error || "Failed to archive legacy state.");
        }}
        window.location.reload();
      }} catch (error) {{
        alert(error.message);
        btn.disabled = false;
      }}
    }});

    // View Files Button Action
    document.querySelector(".view-files-btn")?.addEventListener("click", async () => {{
      const btn = document.querySelector(".view-files-btn");
      btn.disabled = true;
      try {{
        const response = await fetch("/open-path", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ path: "artifacts/runs" }})
        }});
        if (!response.ok) {{
          const payload = await response.json();
          throw new Error(payload.error || "Failed to open folder.");
        }}
      }} catch (error) {{
        alert(error.message);
      }} finally {{
        btn.disabled = false;
      }}
    }});

    // File Path Chip Click Handler
    document.addEventListener("click", async (event) => {{
      const chip = event.target.closest(".file-path-chip, .file-chip");
      if (!chip) return;
      
      const path = chip.dataset.path || "";
      const artifact = chip.dataset.artifact || path || chip.innerText.trim();
      if (!path && !artifact) return;
      
      const dialog = document.getElementById("file-preview-dialog");
      const title = document.getElementById("file-preview-title");
      const content = document.getElementById("file-preview-content");
      
      if (!dialog) return;
      
      const filename = path ? path.split("/").pop() : artifact;
      title.textContent = "File Preview: " + filename;
      content.textContent = "Loading file content...";
      dialog.showModal();
      
      try {{
        const response = await fetch("/artifact?key=" + encodeURIComponent(artifact) + "&project=" + encodeURIComponent(project));
        const payload = await response.json();
        if (!response.ok) {{
          throw new Error(payload.error || "Failed to load file content.");
        }}
        content.textContent = payload.content;
      }} catch (error) {{
        content.textContent = "Error: " + error.message;
      }}
    }});

    // Add Project Modal Action
    const addProjBtn = document.getElementById("add-project-btn");
    const addProjDialog = document.getElementById("add-project-dialog");
    const addProjForm = document.getElementById("add-project-form");
    
    if (addProjBtn && addProjDialog && addProjForm) {{
      addProjBtn.addEventListener("click", () => {{
        addProjForm.reset();
        addProjDialog.showModal();
      }});
      
      addProjForm.addEventListener("submit", async (e) => {{
        e.preventDefault();
        const submitBtn = addProjForm.querySelector('button[type="submit"]');
        submitBtn.disabled = true;
        submitBtn.textContent = "Creating...";
        
        const data = {{
          project_name: addProjForm.querySelector('[name="project_name"]').value.trim(),
          project_key: addProjForm.querySelector('[name="project_key"]').value.trim(),
          docs_path: addProjForm.querySelector('[name="docs_path"]').value.trim(),
          backend_path: addProjForm.querySelector('[name="backend_path"]').value.trim(),
          frontend_path: addProjForm.querySelector('[name="frontend_path"]').value.trim(),
          model_profile: addProjForm.querySelector('[name="model_profile"]').value,
          delivery_mode: addProjForm.querySelector('[name="delivery_mode"]').value,
          worker_backend_mode: "codex"
        }};
        
        try {{
          const response = await fetch("/project-config", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify(data)
          }});
          const payload = await response.json();
          if (!response.ok) {{
            throw new Error(payload.error || "Failed to create project.");
          }}
          addProjDialog.close();
          window.location.reload();
        }} catch (error) {{
          alert(error.message);
          submitBtn.disabled = false;
          submitBtn.textContent = "Create Project";
        }}
      }});
    }}
    
    // Coordinate-based light-dismiss dialog close
    document.querySelectorAll("dialog").forEach((dialog) => {{
      dialog.addEventListener("click", (event) => {{
        const rect = dialog.getBoundingClientRect();
        if (
          event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom
        ) {{
          dialog.close();
        }}
      }});
    }});
  </script>
</body>
</html>"""


def serve(workspace: Path, host: str = "127.0.0.1", port: int = 8788) -> None:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            project_key = query.get("project", [""])[0]
            if maybe_send_favicon_response(self, parsed.path):
                return
            if parsed.path in {"/artifact", "/api/artifact"}:
                try:
                    artifact_key = query.get("key", [""])[0]
                    self._send_json(200, read_artifact(workspace=workspace, project_key=project_key, artifact_key=artifact_key))
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path == "/api/open-questions":
                try:
                    artifact_key = query.get("artifact", [""])[0]
                    self._send_json(200, read_open_questions(workspace=workspace, project_key=project_key, artifact_key=artifact_key))
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path == "/api/state":
                try:
                    if not project_key:
                        legacy_state = StateStore(workspace).read_current()
                        res_data = {
                            "type": "overview",
                            "project_items": build_project_overview_items(workspace),
                            "legacy_state": asdict(legacy_state) if legacy_state is not None else None,
                            "resources": build_resource_items(legacy_state),
                        }
                    else:
                        project_config = _project_config_for_key(workspace, project_key)
                        start_context = build_start_console_context(workspace, project_config=project_config)
                        state = resolve_dashboard_state(workspace, project_key=project_key)
                        project_root = resolve_project_runtime_root(dict(load_project_target(project_config).get("repos", {})))
                        
                        res_data = {
                            "type": "project",
                            "state": asdict(state) if state is not None else None,
                            "resources": build_resource_items(state),
                            "start_context": start_context,
                            "active_run": is_active_run(state),
                            "role_observations": build_role_observations(state),
                            "workflow_steps": build_workflow_steps(state),
                            "approval_reviews": build_approval_reviews(state, workspace=workspace),
                            "action_items": build_action_items(workspace, project_key=project_key),
                            "smoke_run_items": build_smoke_run_items(workspace),
                            "agile_board": build_agile_board(state),
                            "worker_jobs": build_worker_job_items(project_root),
                        }
                    self._send_json(200, res_data)
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path == "/api/resources":
                try:
                    state = resolve_dashboard_state(workspace, project_key=project_key) if project_key else StateStore(workspace).read_current()
                    self._send_json(
                        200,
                        {
                            "project_key": normalize_project_key(project_key or state.project_key) if state is not None else "",
                            "resources": build_resource_items(state),
                            "legacy_artifacts": dict(state.artifacts) if state is not None else {},
                        },
                    )
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path == "/api/workflow/graph":
                try:
                    self._send_json(200, build_workflow_graph(workspace, project_key=project_key))
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path == "/api/devloop":
                project_root = None
                if project_key:
                    try:
                        project_config = _project_config_for_key(workspace, project_key)
                        project_root = resolve_project_runtime_root(
                            dict(load_project_target(project_config).get("repos", {}))
                        )
                    except Exception:
                        project_root = None
                self._send_json(200, build_dev_loop(project_root))
                return
            if parsed.path == "/api/server-actions":
                project_root = None
                if project_key:
                    try:
                        project_config = _project_config_for_key(workspace, project_key)
                        project_root = resolve_project_runtime_root(
                            dict(load_project_target(project_config).get("repos", {}))
                        )
                    except Exception:
                        project_root = None
                self._send_json(200, build_server_action_capabilities(project_root))
                return
            if parsed.path == "/api/server-actions/audit":
                project_root = None
                if project_key:
                    try:
                        project_config = _project_config_for_key(workspace, project_key)
                        project_root = resolve_project_runtime_root(
                            dict(load_project_target(project_config).get("repos", {}))
                        )
                    except Exception:
                        project_root = None
                self._send_json(
                    200,
                    build_server_action_audit_log(
                        project_root,
                        limit=_server_action_audit_limit(query.get("limit", ["25"])[0]),
                    ),
                )
                return
            if parsed.path == "/api/fs/list":
                try:
                    self._send_json(200, list_directory_for_picker(query.get("path", [""])[0]))
                except FsListBadRequestError as exc:
                    self._send_json(400, {"error": str(exc)})
                except FsListNotFoundError as exc:
                    self._send_json(404, {"error": str(exc)})
                except FsListPermissionError as exc:
                    self._send_json(403, {"error": str(exc)})
                return
            if parsed.path == "/api/memory":
                try:
                    if not project_key:
                        self._send_json(200, {"project_key": "", "roles": []})
                        return
                    project_config = _project_config_for_key(workspace, project_key)
                    repos = dict(load_project_target(project_config).get("repos", {}))
                    project_root = resolve_project_runtime_root(repos)
                    self._send_json(200, build_memory_overview(project_root, project_key))
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path == "/api/memory/events":
                try:
                    role = query.get("role", [""])[0].strip()
                    memory_query = query.get("q", [""])[0].strip()
                    if not project_key or not role:
                        raise ValueError("project and role are required.")
                    try:
                        limit = int(query.get("limit", ["50"])[0])
                    except ValueError:
                        limit = 50
                    project_config = _project_config_for_key(workspace, project_key)
                    repos = dict(load_project_target(project_config).get("repos", {}))
                    project_root = resolve_project_runtime_root(repos)
                    self._send_json(
                        200,
                        build_memory_events(
                            project_root,
                            project_key,
                            role,
                            query=memory_query,
                            limit=limit,
                        ),
                    )
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path == "/api/memory/event":
                try:
                    event_id = query.get("id", [""])[0].strip()
                    if not project_key or not event_id:
                        raise ValueError("project and id are required.")
                    project_config = _project_config_for_key(workspace, project_key)
                    repos = dict(load_project_target(project_config).get("repos", {}))
                    project_root = resolve_project_runtime_root(repos)
                    event_detail = build_memory_event_detail(project_root, event_id)
                    if event_detail is None:
                        self._send_json(404, {"error": f"event not found: {event_id}"})
                    else:
                        self._send_json(200, event_detail)
                except Exception as exc:
                    message = str(exc)
                    if message.startswith("event not found: "):
                        self._send_json(404, {"error": message})
                    else:
                        self._send_json(400, {"error": message})
                return
            if parsed.path == "/api/memory/cross":
                try:
                    role = query.get("role", [""])[0].strip()
                    memory_query = query.get("q", [""])[0].strip()
                    scope = [
                        key.strip()
                        for key in query.get("scope", [""])[0].split(",")
                        if key.strip()
                    ]
                    if not role or not memory_query or not scope:
                        raise ValueError("role, q, and non-empty scope are required.")
                    try:
                        limit = int(query.get("k", ["5"])[0])
                    except ValueError:
                        limit = 5
                    results = retrieve_cross_project_memory(
                        workspace,
                        role,
                        memory_query,
                        scope=scope,
                        k=limit,
                    )
                    self._send_json(200, {"results": results})
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path == "/api/agents":
                try:
                    self._send_json(200, {"agents": discover_installed_agents()})
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if parsed.path == "/api/messages":
                try:
                    run_id = query.get("run", [""])[0].strip()
                    gate = query.get("gate", [None])[0]
                    if not project_key or not run_id:
                        raise ValueError("project and run are required.")
                    self._send_json(
                        200,
                        {
                            "messages": read_messages(
                                workspace,
                                project_key,
                                run_id,
                                gate=gate,
                            )
                        },
                    )
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                return

            # Try serving from frontend/dist first
            dist_dir = (workspace / "frontend" / "dist").resolve()
            if not parsed.path.startswith("/api/"):
                rel_path = parsed.path.lstrip("/")
                if not rel_path or rel_path == "index.html":
                    filepath = dist_dir / "index.html"
                else:
                    filepath = (dist_dir / rel_path).resolve()
                
                if filepath.is_file() and filepath.is_relative_to(dist_dir):
                    try:
                        import mimetypes
                        content_type, _ = mimetypes.guess_type(str(filepath))
                        if not content_type:
                            if filepath.suffix == ".css":
                                content_type = "text/css"
                            elif filepath.suffix == ".js":
                                content_type = "application/javascript"
                            else:
                                content_type = "application/octet-stream"
                        body = filepath.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", str(len(body)))
                        _send_cors_headers(self, host=host, port=port)
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    except Exception:
                        pass

            if parsed.path not in {"/", "/index.html"}:
                self.send_error(404)
                return
            if not project_key:
                body = render_project_overview(workspace).encode("utf-8")
            else:
                project_config = _project_config_for_key(workspace, project_key)
                start_context = build_start_console_context(workspace, project_config=project_config)
                body = render_dashboard(resolve_dashboard_state(workspace, project_key=project_key), start_context).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            _send_cors_headers(self, host=host, port=port)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            normalized_path = self.path
            if normalized_path.startswith("/api/"):
                normalized_path = normalized_path.removeprefix("/api")

            if normalized_path not in {"/start", "/report-bug", "/context-estimate", "/context-generate", "/approve", "/artifact", "/project-config", "/terminate", "/project-delete", "/retry", "/generate-prd", "/generate-rfc", "/refine-artifact", "/answer-open-questions", "/accept-refinement", "/discard-refinement", "/open-path", "/debate", "/dispatch-track", "/smoke-run", "/fs/mkdir", "/server-actions/run", "/git-init"}:
                self.send_error(404)
                return
            if not _request_origin_is_same_origin(
                origin=str(self.headers.get("Origin", "")),
                host=str(self.headers.get("Host", "")),
            ):
                self._send_json(403, {"error": "cross-origin POST requests are not allowed"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                request = json.loads(raw_body.decode("utf-8") or "{}")
                if normalized_path == "/fs/mkdir":
                    try:
                        result = create_subdirectory(
                            parent_value=request.get("parent", ""),
                            name_value=request.get("name", ""),
                        )
                    except FsMkdirConflictError as exc:
                        self._send_json(409, {"error": str(exc)})
                        return
                    except FsListNotFoundError as exc:
                        self._send_json(404, {"error": str(exc)})
                        return
                    except FsListBadRequestError as exc:
                        self._send_json(400, {"error": str(exc)})
                        return
                elif normalized_path == "/start":
                    result = start_workflow(
                        workspace=workspace,
                        idea=str(request.get("idea", "")),
                        project_key=str(request.get("project_key", "")),
                        model_config=Path(str(request.get("model_config", "models.yaml"))),
                        model_profile=str(request.get("model_profile", "balanced")),
                        delivery_mode=str(request.get("delivery_mode", "")),
                        delivery_standards=request.get("delivery_standards", ""),
                        source_type=str(request.get("source_type", "new_project")),
                        project_config=Path(str(request["project_config"])) if str(request.get("project_config", "")).strip() else None,
                        role_model_overrides=[str(value) for value in request.get("role_model_overrides", [])],
                        dry_run=bool(request.get("dry_run", False)),
                    )
                elif normalized_path == "/report-bug":
                    result = report_bug(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        description=str(request.get("description", "")),
                        title=str(request.get("title", "")),
                        severity=str(request.get("severity", "p2")),
                    )
                elif normalized_path == "/context-estimate":
                    result = context_estimate_for_request(workspace, request)
                elif normalized_path == "/context-generate":
                    result = context_generate_for_request(workspace, request)
                elif normalized_path == "/approve":
                    result = decide_approval(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        approval=str(request.get("approval", "")),
                        decision=str(request.get("decision", "")),
                        debate_ref=str(request.get("debate_ref", "")),
                        note=str(request.get("note", "")),
                    )
                elif normalized_path == "/debate":
                    project_key = str(request.get("project_key", ""))
                    run_id = str(request.get("run", ""))
                    gate = str(request.get("gate", ""))
                    challenger_role = str(request.get("challenger_role", "qa"))
                    owner_role = str(request.get("owner_role", "architect"))
                    state = resolve_dashboard_state(workspace, project_key=project_key)
                    artifact_key = approval_artifact_map().get(gate, gate)
                    artifact_ref = state.artifacts.get(artifact_key, "") if state is not None else ""
                    result = {
                        "messages": run_gate_debate(
                            workspace,
                            project_key,
                            run_id,
                            gate,
                            challenger_role=challenger_role,
                            owner_role=owner_role,
                            dispatch_fn=default_debate_dispatch(workspace, project_key, run_id),
                            artifact_ref=artifact_ref,
                        )
                    }
                elif normalized_path == "/artifact":
                    result = save_artifact(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        artifact_key=str(request.get("artifact", "")),
                        content=str(request.get("content", "")),
                    )
                elif normalized_path == "/terminate":
                    result = terminate_project_run(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                    )
                elif normalized_path == "/project-delete":
                    result = delete_project(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                    )
                elif normalized_path == "/retry":
                    result = retry_blocked_workflow(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                    )
                elif normalized_path == "/generate-prd":
                    result = generate_prd(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                    )
                elif normalized_path == "/generate-rfc":
                    result = generate_rfc(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                    )
                elif normalized_path == "/refine-artifact":
                    result = refine_artifact(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        artifact_key=str(request.get("artifact", "")),
                        content=str(request.get("content", "")),
                    )
                elif normalized_path == "/answer-open-questions":
                    raw_answers = request.get("answers", [])
                    result = answer_open_questions(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        artifact_key=str(request.get("artifact", "")),
                        answers=raw_answers if isinstance(raw_answers, list) else [],
                    )
                elif normalized_path == "/accept-refinement":
                    result = accept_refinement(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        artifact_key=str(request.get("artifact", "")),
                    )
                elif normalized_path == "/discard-refinement":
                    result = discard_refinement(
                        workspace=workspace,
                        project_key=str(request.get("project_key", "")),
                        artifact_key=str(request.get("artifact", "")),
                    )
                elif normalized_path == "/open-path":
                    result = open_path_in_file_manager(
                        workspace=workspace,
                        relative_path=str(request.get("path", "")),
                    )
                elif normalized_path == "/git-init":
                    result = initialize_project_git_for_request(workspace, request)
                elif normalized_path == "/dispatch-track":
                    response_written = threading.Event()
                    result = start_dispatch_track(workspace, request, response_written=response_written)
                    try:
                        self._send_json(202, result)
                    finally:
                        response_written.set()
                    return
                elif normalized_path == "/smoke-run":
                    result = start_smoke_run(workspace, request)
                    self._send_json(202, result)
                    return
                elif normalized_path == "/server-actions/run":
                    project_root = resolve_project_root_for_request(workspace, request)
                    result = run_gated_server_action(
                        project_root,
                        request,
                        origin=str(self.headers.get("Origin", "")),
                    )
                    self._send_json(202, result)
                    return
                # `/project-config` carries two operations: the worker-backends
                # panel sends `project` + `workers` for an existing project; the
                # create-config form sends neither. Route both explicitly so an
                # unrelated path can never silently fall through to create-config.
                elif normalized_path == "/project-config" and (
                    request.get("action") == "update_worker_backends"
                    or (
                        ("project" in request or "project_key" in request)
                        and "workers" in request
                        and "project_name" not in request
                    )
                ):
                    result = update_project_worker_backend_settings(
                        workspace=workspace,
                        request=request,
                    )
                elif normalized_path == "/project-config":
                    result = create_local_project_config(
                        workspace=workspace,
                        project_name=str(request.get("project_name", "")),
                        project_key=str(request.get("project_key", "")),
                        project_root=str(request.get("project_root", "")),
                        docs_path=str(request.get("docs_path", "")),
                        backend_path=str(request.get("backend_path", "")),
                        frontend_path=str(request.get("frontend_path", "")),
                        task_sync=[str(value) for value in request.get("task_sync", [])],
                        plane_api_host_url=str(request.get("plane_api_host_url", "")),
                        plane_workspace_slug=str(request.get("plane_workspace_slug", "")),
                        plane_project_id=str(request.get("plane_project_id", "")),
                        plane_api_key_env=str(request.get("plane_api_key_env", "")),
                        plane_mcp_server=str(request.get("plane_mcp_server", "")),
                        worker_backend_mode=str(request.get("worker_backend_mode", "codex")),
                        workers=dict(request.get("workers", {})),
                        deployment_backend_mode=str(request.get("deployment_backend_mode", "")),
                        staging_ssh_target=str(request.get("staging_ssh_target", "")),
                        staging_remote_dir=str(request.get("staging_remote_dir", "")),
                        staging_git_ref=str(request.get("staging_git_ref", "")),
                        staging_public_url=str(request.get("staging_public_url", "")),
                        staging_deploy_command=str(request.get("staging_deploy_command", "")),
                        staging_healthcheck_command=str(request.get("staging_healthcheck_command", "")),
                        staging_smoke_command=str(request.get("staging_smoke_command", "")),
                        delivery_contract_required=bool(request.get("delivery_contract_required", False)),
                        delivery_contract_minimum_evidence=str(request.get("delivery_contract_minimum_evidence", "infrastructure")),
                        delivery_contract_fatal_patterns=str(request.get("delivery_contract_fatal_patterns", "")),
                        delivery_contract_acceptance=str(request.get("delivery_contract_acceptance", "")),
                        model_profile=str(request.get("model_profile", "balanced")),
                        delivery_mode=str(request.get("delivery_mode", "init_project")),
                        delivery_standards=str(request.get("delivery_standards", "")),
                        role_model_overrides=dict(request.get("role_model_overrides", {})),
                        init_git=_request_bool(request.get("init_git"), True),
                        create_remote=_request_bool(request.get("create_remote"), False),
                        remote_private=_request_bool(request.get("remote_private"), True),
                    )
                self._send_json(200, result)
            except PermissionError as exc:
                self._send_json(403, {"error": str(exc)})
            except Exception as exc:
                self._send_json(400, {"error": str(exc)})

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            _send_cors_headers(self, host=host, port=port)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            _send_cors_headers(self, host=host, port=port)
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def log_message(self, format: str, *args: object) -> None:
            return

    ThreadingHTTPServer((host, port), DashboardHandler).serve_forever()
