"""[INPUT]: 已注册项目和 gh 的只读 Issue 数据。
[OUTPUT]: 校验仓库身份并读取 Issue 快照。
[POS]: Manager 外部输入的 GitHub 适配器。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from goodteam_os.execution.github import (
    GitHubIssueClosed,
    GitHubIssueNotFound,
    GitHubIssueSnapshot,
)
from goodteam_os.database import provider_environment
from goodteam_os.projects import load_project_target


ISSUE_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/issues/(?P<number>[1-9][0-9]*)/?$",
    re.IGNORECASE,
)
GITHUB_REFERENCE_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(?:pull|issues|discussions)/[1-9][0-9]*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedIssueURL:
    url: str
    repository: str
    issue_number: int


@dataclass(frozen=True)
class KnownProject:
    key: str
    config_path: Path
    project_root: Path
    repository: str


class GitHubCLIError(RuntimeError):
    pass


def parse_github_issue_url(value: str) -> ParsedIssueURL:
    if not isinstance(value, str):
        raise ValueError("GitHub Issue URL must be a string")
    parts = urlsplit(value.strip())
    normalized_url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))
    match = ISSUE_URL_RE.fullmatch(normalized_url)
    if match is None:
        raise ValueError("Expected a GitHub Issue URL like https://github.com/owner/repo/issues/123")
    repository = f"{match.group('owner')}/{match.group('repo')}"
    number = int(match.group("number"))
    return ParsedIssueURL(
        url=f"https://github.com/{repository}/issues/{number}",
        repository=repository,
        issue_number=number,
    )


def resolve_known_project(workspace: Path, repository: str) -> KnownProject:
    workspace = Path(workspace).resolve()
    candidates = sorted((workspace / "projects").glob("*.local.yaml"))
    if workspace.name == "projects":
        candidates.extend(sorted(workspace.glob("*.local.yaml")))
    wanted = repository.strip().lower()
    for config_path in candidates:
        try:
            target = load_project_target(config_path)
        except Exception:
            continue
        repos = target.get("repos", {})
        if not isinstance(repos, dict):
            continue
        for raw_path in repos.values():
            project_root = Path(str(raw_path)).expanduser()
            if not project_root.is_absolute():
                project_root = (config_path.parent / project_root).resolve()
            identity = _repository_identity(project_root)
            configured = str(target.get("repository", "") or target.get("github_repository", "")).strip()
            identity = configured or identity
            if identity.lower() == wanted:
                return KnownProject(key=str(target.get("key", config_path.stem)).strip().upper(),
                                    config_path=config_path.resolve(), project_root=project_root,
                                    repository=identity)
    raise ValueError(
        f"Unknown project repository: {repository}. Register it locally first; unknown-project adoption is tracked in #339."
    )


class GhCliGitHubClient:
    """Concrete GitHub adapter using the existing routed ``gh`` installation."""

    def __init__(self, *, cwd: Path):
        self.cwd = Path(cwd).resolve()

    def fetch_issue(self, repo: str, issue_number: int) -> GitHubIssueSnapshot:
        try:
            payload = self._json(
                [
                    "gh",
                    "issue",
                    "view",
                    str(issue_number),
                    "--repo",
                    repo,
                    "--json",
                    "number,title,body,url,state,labels,comments,updatedAt",
                ]
            )
        except GitHubCLIError as exc:
            if "not found" in str(exc).lower() or "could not resolve" in str(exc).lower():
                raise GitHubIssueNotFound(repo, issue_number) from exc
            raise
        if str(payload.get("state", "")).upper() != "OPEN":
            raise GitHubIssueClosed(repo, issue_number)
        repo_payload = self._json(["gh", "repo", "view", repo, "--json", "defaultBranchRef"])
        comments = [_normalized_comment(item) for item in payload.get("comments", []) if isinstance(item, dict)]
        body = str(payload.get("body", "") or "")
        references = sorted(
            {
                *GITHUB_REFERENCE_RE.findall(body),
                *(ref for comment in comments for ref in GITHUB_REFERENCE_RE.findall(str(comment.get("body", "")))),
            }
        )
        return GitHubIssueSnapshot(
            repo=repo,
            issue_number=issue_number,
            title=str(payload.get("title", "")),
            body=body,
            url=str(payload.get("url", "")),
            labels=[str(item.get("name", "")) for item in payload.get("labels", []) if isinstance(item, dict)],
            comments=comments,
            updated_at=str(payload.get("updatedAt", "")),
            default_branch=str(dict(repo_payload.get("defaultBranchRef", {}) or {}).get("name", "main")),
            references=references,
        )

    def _json(self, command: list[str]) -> Any:
        output = self._text(command)
        try:
            return json.loads(output or "null")
        except json.JSONDecodeError as exc:
            raise GitHubCLIError(f"GitHub CLI returned invalid JSON for {' '.join(command[:3])}") from exc

    def _text(self, command: list[str]) -> str:
        result = subprocess.run(command, cwd=self.cwd, env=provider_environment(), capture_output=True, check=False, text=True)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise GitHubCLIError(message or f"GitHub CLI exited with {result.returncode}")
        return result.stdout


def _repository_identity(project_root: Path) -> str:
    if not project_root.is_dir():
        return ""
    result = subprocess.run(
        ["git", "-C", str(project_root), "remote", "get-url", "origin"],
        env=provider_environment(),
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        return ""
    remote = result.stdout.strip()
    patterns = (
        re.compile(r"github\.com[:/](?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?$", re.IGNORECASE),
        re.compile(r"^[^:]+:(?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?$"),
    )
    for pattern in patterns:
        match = pattern.search(remote)
        if match:
            return match.group("repo").removesuffix(".git")
    return ""


def _normalized_comment(item: dict[str, Any]) -> dict[str, Any]:
    author = dict(item.get("author", {}) or item.get("user", {}) or {})
    return {
        "id": str(item.get("id", "")),
        "author": str(author.get("login", "")),
        "body": str(item.get("body", "") or ""),
        "created_at": str(item.get("createdAt", "") or item.get("created_at", "")),
        "url": str(item.get("url", "") or item.get("html_url", "")),
    }
