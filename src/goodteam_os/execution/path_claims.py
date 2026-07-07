from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


CLAIM_WARNING_BROAD = "path_claims_broad_or_low_confidence"
CLAIM_WARNING_UNRESOLVED = "path_claims_unresolved"
_PATH_PREFIXES = (
    ".github/",
    "docs/",
    "frontend/",
    "projects/",
    "scripts/",
    "src/",
    "state/",
    "tests/",
)
_ROOT_FILES = ("AGENTS.md", "CLAUDE.md", "README.md", "models.yaml", "pyproject.toml")
_PATH_RE = re.compile(
    r"(?<![\w:/.-])("
    r"(?:\.github|docs|frontend|projects|scripts|src|state|tests)/[^\s<>\[\]{}\"'`]+"
    r"|AGENTS\.md|CLAUDE\.md|README\.md|models\.yaml|pyproject\.toml"
    r")"
)


@dataclass(frozen=True)
class PathClaim:
    path: str
    source: str
    confidence: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PathClaimInference:
    claimed_paths: list[str]
    claims: list[PathClaim]
    warnings: list[str]


def infer_execution_path_claims(
    *,
    task_text: str,
    context_text: str,
    expected_artifacts: dict[str, Any] | list[Any] | None,
    existing_changed_files: list[Any] | None = None,
    task_contract: dict[str, Any] | None = None,
) -> PathClaimInference:
    builder = _ClaimBuilder()
    builder.add_values(
        _contract_path_scope_values(task_contract),
        source="task_contract.path_scope",
    )
    builder.add_values(
        _contract_expected_artifact_values(task_contract),
        source="task_contract.expected_artifacts",
    )
    builder.add_text(task_text, source="task_text")
    builder.add_text(context_text, source="context_text")
    builder.add_values(_artifact_values(expected_artifacts), source="expected_artifacts")
    builder.add_values(_artifact_values(existing_changed_files or []), source="existing_changed_files")
    combined_text = f"{task_text}\n{context_text}"
    builder.add_area_mappings(combined_text)
    if not builder.claims:
        builder.add_broad_claims(combined_text)
    return PathClaimInference(
        claimed_paths=[claim.path for claim in builder.claims],
        claims=list(builder.claims),
        warnings=_warnings(builder.claims),
    )


def normalize_repo_path(value: Any) -> str:
    text = _trim_path_text(str(value))
    if not text or _is_unsafe_path(text):
        return ""
    text = text.replace("\\", "/")
    if _has_glob(text):
        text = _glob_directory(text)
    if text.endswith("/"):
        return text
    return text.strip("/")


def path_claims_to_dicts(claims: list[PathClaim] | list[dict[str, Any]] | tuple[Any, ...]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for claim in claims:
        if isinstance(claim, PathClaim):
            result.append(claim.to_dict())
        elif isinstance(claim, dict):
            result.append(_claim_dict(claim))
    return [claim for claim in result if claim.get("path")]


class _ClaimBuilder:
    def __init__(self) -> None:
        self.claims: list[PathClaim] = []
        self._seen: set[str] = set()

    def add_text(self, text: str, *, source: str) -> None:
        for raw_path in _extract_paths(text):
            self.add(raw_path, source=source, confidence="high", reason="explicit repo-relative path")

    def add_values(self, values: list[Any], *, source: str) -> None:
        for value in values:
            for raw_path in _candidate_paths(value):
                self.add(raw_path, source=source, confidence="high", reason="explicit repo-relative path")

    def add_area_mappings(self, text: str) -> None:
        normalized = _normalized_text(_text_without_paths(text))
        broad_signal = _has_broad_signal(normalized)
        for keywords, paths, reason in _area_mappings():
            if reason == "matched documentation area" and broad_signal:
                continue
            if any(keyword in normalized for keyword in keywords):
                for path in paths:
                    self.add(path, source="area_mapping", confidence="medium", reason=reason)

    def add_broad_claims(self, text: str) -> None:
        normalized = _normalized_text(text)
        if not _has_broad_signal(normalized):
            return
        for path in ("src/goodteam_os/", "tests/", "docs/"):
            self.add(path, source="area_mapping", confidence="low", reason="broad repository area")

    def add(self, raw_path: Any, *, source: str, confidence: str, reason: str) -> None:
        path = normalize_repo_path(raw_path)
        if not path or path in self._seen:
            return
        if (
            path.endswith("/")
            and not source.startswith("task_contract.")
            and any(existing.startswith(path) for existing in self._seen)
        ):
            return
        self._seen.add(path)
        self.claims.append(PathClaim(path=path, source=source, confidence=confidence, reason=reason))


def _extract_paths(text: str) -> list[str]:
    return [match.group(1) for match in _PATH_RE.finditer(text or "")]


def _text_without_paths(text: str) -> str:
    return _PATH_RE.sub(" ", text or "")


def _artifact_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        values: list[Any] = []
        for item in value.values():
            values.extend(_artifact_values(item))
        return values
    if isinstance(value, list | tuple):
        values = []
        for item in value:
            values.extend(_artifact_values(item))
        return values
    return [value]


def _contract_path_scope_values(task_contract: dict[str, Any] | None) -> list[Any]:
    if not isinstance(task_contract, dict):
        return []
    path_scope = task_contract.get("path_scope")
    if not isinstance(path_scope, dict):
        return []
    values: list[Any] = []
    for key in ("focus_paths", "allowed_paths"):
        values.extend(_artifact_values(path_scope.get(key)))
    return values


def _contract_expected_artifact_values(task_contract: dict[str, Any] | None) -> list[Any]:
    if not isinstance(task_contract, dict):
        return []
    return _artifact_values(task_contract.get("expected_artifacts"))


def _candidate_paths(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(value[key]) for key in ("path", "artifact_path", "file") if key in value]
    if isinstance(value, str):
        return _extract_paths(value) or [value]
    return []


def _area_mappings() -> list[tuple[tuple[str, ...], tuple[str, ...], str]]:
    return [
        (
            ("worker", "codex", "claude", "runtime"),
            ("src/goodteam_os/workers.py", "tests/test_workers.py"),
            "matched worker runtime area",
        ),
        (
            ("sandbox", "worktree", "cleanup"),
            ("src/goodteam_os/execution/sandbox.py", "tests/test_execution_sandbox_integration.py"),
            "matched sandbox area",
        ),
        (
            ("review workbench", "workbench", "browser workbench"),
            (
                "src/goodteam_os/execution/review_workbench.py",
                "src/goodteam_os/execution/review_workbench_browser.py",
                "src/goodteam_os/execution/review_workbench_browser_sandbox.py",
                "tests/test_review_workbench_sandbox_evidence.py",
            ),
            "matched review workbench area",
        ),
        (
            ("ledger", "evidence"),
            (
                "src/goodteam_os/execution/ledger.py",
                "src/goodteam_os/execution/api.py",
                "tests/test_execution_ledger.py",
                "tests/test_execution_api.py",
            ),
            "matched ledger evidence area",
        ),
        (("docs", "document", "documentation"), ("docs/",), "matched documentation area"),
    ]


def _warnings(claims: list[PathClaim]) -> list[str]:
    if not claims:
        return [CLAIM_WARNING_UNRESOLVED]
    if any(claim.confidence == "low" for claim in claims):
        return [CLAIM_WARNING_BROAD]
    return []


def _claim_dict(claim: dict[str, Any]) -> dict[str, str]:
    return {
        "path": normalize_repo_path(claim.get("path", "")),
        "source": str(claim.get("source", "")),
        "confidence": str(claim.get("confidence", "")),
        "reason": str(claim.get("reason", "")),
    }


def _trim_path_text(value: str) -> str:
    text = value.strip().strip("`'\"")
    return text.strip(" \t\r\n.,;:)]}>")


def _is_unsafe_path(path: str) -> bool:
    if "://" in path or path.startswith(("/", "~", "$")):
        return True
    normalized = path.replace("\\", "/")
    if ":" in normalized:
        return True
    parts = [part for part in normalized.split("/") if part]
    return ".." in parts or not _is_repo_relative(normalized)


def _is_repo_relative(path: str) -> bool:
    return path.startswith(_PATH_PREFIXES) or path in _ROOT_FILES


def _has_glob(path: str) -> bool:
    return any(token in path for token in ("*", "?", "["))


def _glob_directory(path: str) -> str:
    prefix = path.rsplit("/", 1)[0] if "/" in path else ""
    return f"{prefix}/" if prefix else ""


def _normalized_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def _has_broad_signal(text: str) -> bool:
    return any(token in text for token in ("backend", "codebase", "repository", "repo", "source"))
