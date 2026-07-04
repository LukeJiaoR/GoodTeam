from __future__ import annotations

import hashlib


_DIGEST_LENGTH = 32


def task_id_from_github_issue(repo: str, issue_number: int) -> str:
    normalized_repo = _normalize_repo(repo)
    normalized_issue_number = _validate_issue_number(issue_number)
    return f"task_{_digest(f'github_issue|{normalized_repo}|{normalized_issue_number}')}"


def execution_id_from_task(
    task_id: str,
    *,
    attempt: int | None = None,
    run_seed: str | None = None,
) -> str:
    if attempt is not None and run_seed is not None:
        raise ValueError("Use either attempt or run_seed, not both")

    normalized_task_id = _normalize_non_empty(task_id, "task_id")
    if run_seed is not None:
        seed = f"seed:{_normalize_non_empty(run_seed, 'run_seed')}"
    else:
        normalized_attempt = 1 if attempt is None else _validate_positive_int(attempt, "attempt")
        seed = f"attempt:{normalized_attempt}"
    return f"exec_{_digest(f'execution|{normalized_task_id}|{seed}')}"


def branch_name_from_execution_id(execution_id: str) -> str:
    normalized_execution_id = _normalize_non_empty(execution_id, "execution_id")
    return f"goodteam/{normalized_execution_id}"


def idempotency_key_for_operation(repo: str, issue_number: int, operation: str) -> str:
    normalized_repo = _normalize_repo(repo)
    normalized_issue_number = _validate_issue_number(issue_number)
    normalized_operation = _normalize_non_empty(operation, "operation").lower()
    seed = f"idempotency|{normalized_repo}|{normalized_issue_number}|{normalized_operation}"
    return f"idem_{_digest(seed)}"


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]


def _normalize_repo(repo: str) -> str:
    return _normalize_non_empty(repo, "repo").lower()


def _normalize_non_empty(value: object, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _validate_issue_number(issue_number: int) -> int:
    return _validate_positive_int(issue_number, "issue_number")


def _validate_positive_int(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive int")
    return value
