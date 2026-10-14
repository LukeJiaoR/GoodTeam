"""Private, explicit CLI configuration for the existing Operator service."""
from dataclasses import asdict
import json
from pathlib import Path

from goodteam_os.execution.task_snapshot import TaskInputError, canonical_bytes, digest
from goodteam_os.operator_candidate_review import OperatorCandidateReviewConfig
from goodteam_os.skills.codex_local import _private_file

SCHEMA = "goodteam.operator-candidate-config.v1"


def prepare_review_identity(workspace, state_dir, item):
    """Explicit local intake only; never dispatch the queued Builder/Reviewer."""
    from goodteam_os.operator import LocalOperatorActions
    from goodteam_os.operator_github import parse_github_issue_url
    from goodteam_os.execution.orchestrator import ExecutionOrchestrator
    from goodteam_os.execution.task_snapshot_store import store_for_project

    actions = LocalOperatorActions(workspace=workspace, state_dir=state_dir)
    root = actions._project(item)
    parsed = parse_github_issue_url(str(item["source_url"]))
    run = ExecutionOrchestrator(root, actions._client(item)).intake_github_issue(
        repo=str(item["repository_identity"]), issue_number=parsed.issue_number,
        adapter=str(item["metadata"].get("worker_backend", "codex")))
    store = store_for_project(root, existing_only=True)
    snapshot = store.load(run.execution_id) if store else None
    if snapshot is None:
        raise TaskInputError("operator_original_snapshot_required")
    return {"execution_id": run.execution_id, "builder_status": run.status.value,
            "task_snapshot": snapshot.reference, "worker_root": str(root),
            "reviewer_task": {"kind": "direct_task", "project_key": str(item["project_key"]),
                              "logical_id": "candidate-review:" + snapshot.payload_digest},
            "runtime_admission_granted": False}


def config_document(item, contract, *, argv, criteria, base_sha, reviewer_model, seconds):
    config = OperatorCandidateReviewConfig(contract_digest=digest(canonical_bytes(contract)), base_sha=base_sha,
        argv=tuple(argv), criteria=tuple(criteria), reviewer_model=reviewer_model, seconds=seconds)
    if any(i >= len(contract.get("acceptance_criteria", [])) for i in criteria):
        raise TaskInputError("validation_criteria_invalid")
    return {"schema_version": SCHEMA, "items": {str(item["id"]): asdict(config)}}


def load_config(path, *, expected_digest=""):
    raw = _private_file(path, maximum=65536)
    identity = digest(raw)
    if expected_digest and identity != expected_digest:
        raise TaskInputError("operator_startup_configuration_changed")
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "items"}
                or value["schema_version"] != SCHEMA or not isinstance(value["items"], dict)
                or not 1 <= len(value["items"]) <= 64):
            raise ValueError("invalid document")
        result = {}
        for item_id, data in value["items"].items():
            if (not isinstance(item_id, str) or not item_id or len(item_id) > 256
                    or not isinstance(data, dict) or set(data) != set(OperatorCandidateReviewConfig.__dataclass_fields__)
                    or not isinstance(data["argv"], list) or not isinstance(data["criteria"], list)):
                raise ValueError("invalid item")
            result[item_id] = OperatorCandidateReviewConfig(**{**data, "argv": tuple(data["argv"]),
                                                               "criteria": tuple(data["criteria"])})
        return result, identity
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise TaskInputError("operator_startup_configuration_invalid") from exc


def startup_arguments(*, operator_review_config=None, local_codex_policy=None,
                      trusted_codex_review_policy=None, operator_review_config_sha256=""):
    """Validate before stopping a service; pass only paths/hash to its children."""
    if local_codex_policy is not None and trusted_codex_review_policy is not None:
        raise ValueError("Choose one Reviewer startup authority.")
    if operator_review_config is None:
        if local_codex_policy is not None or trusted_codex_review_policy is not None or operator_review_config_sha256:
            raise ValueError("Operator Reviewer policy requires --operator-review-config.")
        return []
    if local_codex_policy is None and trusted_codex_review_policy is None:
        raise ValueError("Operator candidate configuration requires an existing local or trusted Reviewer policy.")
    path = Path(operator_review_config).expanduser().absolute()
    _configs, identity = load_config(path, expected_digest=operator_review_config_sha256)
    policy = Path(local_codex_policy or trusted_codex_review_policy).expanduser().absolute()
    _private_file(policy, maximum=65536)  # Same private-file boundary as existing policies.
    flag = "--local-codex-policy" if local_codex_policy is not None else "--trusted-codex-review-policy"
    return ["--operator-review-config", str(path), "--operator-review-config-sha256", identity, flag, str(policy)]
