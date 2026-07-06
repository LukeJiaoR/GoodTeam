from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from goodteam_os.execution.github import GitHubExecutionBindingStore
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import (
    ExecutionActor,
    ExecutionRun, ExecutionRunStatus, LedgerEvent, LedgerEventType,
)
from goodteam_os.execution.pr_diff_contract import (
    DIFF_ARTIFACT_SCHEMA_VERSION, DIFF_ARTIFACT_TRANSITION,
    artifact_json_path, artifact_patch_path, relative_artifact_path,
)
from goodteam_os.execution.review_workbench_contract import utc_now


Clock = Callable[[], str]


class PullRequestDiffArtifactService:
    def __init__(
        self,
        project_root: Path,
        github_client: object | None,
        *,
        base_ref: str = "main",
        clock: Clock | None = None,
    ):
        self.project_root = Path(project_root)
        self.github_client = github_client
        self.base_ref = base_ref
        self._clock = clock or utc_now
        self.ledger = ExecutionLedger(self.project_root)
        self.binding_store = GitHubExecutionBindingStore(self.project_root)

    def persist(self, execution_id: str) -> dict[str, Any]:
        run = self.ledger.read_run(execution_id)
        if run is None:
            return _unavailable("execution_run_missing")
        if run.status is not ExecutionRunStatus.SUCCEEDED:
            return _unavailable("execution_not_completed")

        binding = self.binding_store.read_binding(execution_id)
        if binding is None or binding.pr_number is None:
            return _unavailable("pr_binding_missing")

        source = self._fetch_source(binding.repo, binding.pr_number)
        changed_files = _changed_files(source)
        if not changed_files:
            return _unavailable("diff_source_unavailable")

        artifact_path = artifact_json_path(self.project_root, execution_id)
        patch_path = artifact_patch_path(self.project_root, execution_id)
        patch_text = _patch_text(source)
        existing = _read_json(artifact_path)
        payload = _artifact_payload(
            execution_id,
            repo=binding.repo,
            pr_number=binding.pr_number,
            branch_name=binding.branch_name or run.branch_name,
            base_ref=_source_string(source, "base_ref") or self.base_ref,
            head_sha=_source_string(source, "head_sha") or _head_sha(self.ledger, execution_id),
            changed_files=changed_files,
            patch_rel_path=relative_artifact_path(execution_id, "pr-diff.patch")
            if patch_text
            else "",
            created_at=_created_at(existing, self._clock),
        )

        if not _same_artifact(existing, payload):
            _write_artifacts(artifact_path, patch_path, payload, patch_text)
            event = self._append_persisted_event(run, artifact_path, patch_path, changed_files)
            return _persisted_result("persisted", artifact_path, patch_path, event, changed_files)

        event = self._existing_or_append_event(run, artifact_path, patch_path, changed_files)
        return _persisted_result("deduped", artifact_path, patch_path, event, changed_files)

    def _fetch_source(self, repo: str, pr_number: int) -> dict[str, Any] | None:
        if self.github_client is None:
            return None
        fetch = getattr(self.github_client, "fetch_pr_diff", None)
        if not callable(fetch):
            return None
        payload = fetch(repo, pr_number)
        return dict(payload) if isinstance(payload, dict) else None

    def _existing_or_append_event(
        self,
        run: ExecutionRun,
        artifact_path: Path,
        patch_path: Path,
        changed_files: list[dict[str, Any]],
    ) -> LedgerEvent:
        existing = _persisted_event(self.ledger, run.execution_id, artifact_path)
        if existing is not None:
            return existing
        return self._append_persisted_event(run, artifact_path, patch_path, changed_files)

    def _append_persisted_event(
        self,
        run: ExecutionRun,
        artifact_path: Path,
        patch_path: Path,
        changed_files: list[dict[str, Any]],
    ) -> LedgerEvent:
        existing = _persisted_event(self.ledger, run.execution_id, artifact_path)
        if existing is not None:
            return existing
        event = _ledger_event(
            self.ledger,
            run,
            payload={
                "transition": DIFF_ARTIFACT_TRANSITION,
                "execution_id": run.execution_id,
                "artifact_path": str(artifact_path),
                "patch_path": str(patch_path) if patch_path.is_file() else "",
                "changed_file_count": len(changed_files),
                "source": "review_workbench",
                "schema_version": DIFF_ARTIFACT_SCHEMA_VERSION,
            },
            created_at=self._clock(),
        )
        self.ledger.append_event(event)
        return event


def _artifact_payload(
    execution_id: str,
    *,
    repo: str,
    pr_number: int,
    branch_name: str,
    base_ref: str,
    head_sha: str,
    changed_files: list[dict[str, Any]],
    patch_rel_path: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": DIFF_ARTIFACT_SCHEMA_VERSION,
        "execution_id": execution_id,
        "repo": repo,
        "pr_number": pr_number,
        "branch_name": branch_name,
        "base_ref": base_ref,
        "head_sha": head_sha,
        "changed_files": changed_files,
        "patch_path": patch_rel_path,
        "created_at": created_at,
    }


def _changed_files(source: dict[str, Any] | None) -> list[dict[str, Any]]:
    if source is None:
        return []
    files = source.get("changed_files")
    if not isinstance(files, list):
        return []
    return [_changed_file(file) for file in files if isinstance(file, dict)]


def _changed_file(file: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": _string(file.get("path")),
        "status": _string(file.get("status")),
        "additions": _int(file.get("additions")),
        "deletions": _int(file.get("deletions")),
        "patch_excerpt": _string(file.get("patch_excerpt"))[:2000],
    }


def _patch_text(source: dict[str, Any] | None) -> str:
    if source is None:
        return ""
    return _string(source.get("patch") or source.get("patch_content"))


def _source_string(source: dict[str, Any] | None, key: str) -> str:
    if source is None:
        return ""
    return _string(source.get(key))


def _head_sha(ledger: ExecutionLedger, execution_id: str) -> str:
    for event in reversed(ledger.read_events(execution_id)):
        for key in ("head_sha", "commit_sha", "merge_commit_sha"):
            value = _string(event.payload.get(key))
            if value:
                return value
    return ""


def _created_at(existing: dict[str, Any] | None, clock: Clock) -> str:
    if existing is not None:
        value = _string(existing.get("created_at"))
        if value:
            return value
    return clock()


def _write_artifacts(
    artifact_path: Path,
    patch_path: Path,
    payload: dict[str, Any],
    patch_text: str,
) -> None:
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    if patch_text:
        patch_path.write_text(patch_text.rstrip() + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, dict) else None


def _same_artifact(existing: dict[str, Any] | None, payload: dict[str, Any]) -> bool:
    if existing is None:
        return False
    return _without_created_at(existing) == _without_created_at(payload)


def _without_created_at(payload: dict[str, Any]) -> dict[str, Any]:
    value = dict(payload)
    value.pop("created_at", None)
    return value


def _persisted_event(
    ledger: ExecutionLedger,
    execution_id: str,
    artifact_path: Path,
) -> LedgerEvent | None:
    for event in ledger.read_events(execution_id):
        if event.payload.get("transition") != DIFF_ARTIFACT_TRANSITION:
            continue
        if event.payload.get("artifact_path") == str(artifact_path):
            return event
    return None


def _ledger_event(
    ledger: ExecutionLedger,
    run: ExecutionRun,
    *,
    payload: dict[str, Any],
    created_at: str,
) -> LedgerEvent:
    event_number = len(ledger.read_events(run.execution_id)) + 1
    return LedgerEvent(
        event_id=(
            f"{run.execution_id}:{event_number:04d}:"
            f"{LedgerEventType.STATUS_CHANGED.value}:{DIFF_ARTIFACT_TRANSITION}"
        ),
        execution_id=run.execution_id,
        task_id=run.task_id,
        event_type=LedgerEventType.STATUS_CHANGED,
        actor=ExecutionActor.SYSTEM,
        payload=payload,
        created_at=created_at,
    )


def _persisted_result(
    status: str,
    artifact_path: Path,
    patch_path: Path,
    event: LedgerEvent,
    changed_files: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": status,
        "artifact_path": str(artifact_path),
        "patch_path": str(patch_path) if patch_path.is_file() else "",
        "changed_files": changed_files,
        "event": event.to_dict(),
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason, "changed_files": []}


def _string(value: object) -> str:
    return "" if value is None else str(value).strip()


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
