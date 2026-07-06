from __future__ import annotations

from pathlib import Path


DIFF_ARTIFACT_SCHEMA_VERSION = "execution_pr_diff.v1"
DIFF_ARTIFACT_TRANSITION = "diff_artifact_persisted"


def artifact_json_path(project_root: Path, execution_id: str) -> Path:
    return Path(project_root) / relative_artifact_path(execution_id, "pr-diff.json")


def artifact_patch_path(project_root: Path, execution_id: str) -> Path:
    return Path(project_root) / relative_artifact_path(execution_id, "pr-diff.patch")


def relative_artifact_path(execution_id: str, filename: str) -> str:
    return f".goodteam/executions/{execution_id}/artifacts/{filename}"
