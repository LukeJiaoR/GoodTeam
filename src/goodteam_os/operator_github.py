from __future__ import annotations

import base64
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from goodteam_os.execution.github import (
    GitHubIssueClosed,
    GitHubIssueNotFound,
    GitHubIssueSnapshot,
)
from goodteam_os.execution.models import GitHubBinding
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
    worker_backend: str
    merge_method: str
    base_branch: str


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
            if identity.lower() != wanted:
                continue
            backend = str(dict(target.get("worker_backend", {})).get("mode", "codex") or "codex")
            merge_method = str(target.get("merge_method", "squash") or "squash").lower()
            if merge_method not in {"merge", "squash", "rebase"}:
                merge_method = "squash"
            return KnownProject(
                key=str(target.get("key", config_path.stem)).strip().upper(),
                config_path=config_path.resolve(),
                project_root=project_root,
                repository=identity,
                worker_backend=backend,
                merge_method=merge_method,
                base_branch=str(target.get("base_branch", "") or "").strip(),
            )
    raise ValueError(
        f"Unknown project repository: {repository}. Register it locally first; unknown-project adoption is tracked in #339."
    )


class GhCliGitHubClient:
    """Concrete GitHub adapter using the existing routed ``gh`` installation."""

    def __init__(self, *, cwd: Path, base_ref: str = ""):
        self.cwd = Path(cwd).resolve()
        self.base_ref = base_ref.strip()

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

    def create_or_update_branch(self, *, repo: str, branch_name: str, base_ref: str = "main") -> dict[str, Any]:
        existing = self._json_optional(["gh", "api", f"repos/{repo}/git/ref/heads/{quote(branch_name, safe='/')}"])
        if existing:
            return {"sha": _nested(existing, "object", "sha"), "reused": True}
        base = self._json(["gh", "api", f"repos/{repo}/git/ref/heads/{quote(base_ref, safe='/')}"])
        base_sha = str(_nested(base, "object", "sha"))
        created = self._json(
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"repos/{repo}/git/refs",
                "-f",
                f"ref=refs/heads/{branch_name}",
                "-f",
                f"sha={base_sha}",
            ]
        )
        return {"sha": _nested(created, "object", "sha"), "base_sha": base_sha, "reused": False}

    def commit_files(
        self,
        *,
        repo: str,
        branch_name: str,
        message: str,
        files: dict[str, str],
    ) -> dict[str, Any]:
        last_commit = ""
        for path, content in files.items():
            encoded_path = quote(path.strip("/"), safe="/")
            existing = self._json_optional(
                ["gh", "api", f"repos/{repo}/contents/{encoded_path}?ref={quote(branch_name, safe='')}"],
            )
            args = [
                "gh",
                "api",
                "-X",
                "PUT",
                f"repos/{repo}/contents/{encoded_path}",
                "-f",
                f"message={message}",
                "-f",
                f"content={base64.b64encode(content.encode('utf-8')).decode('ascii')}",
                "-f",
                f"branch={branch_name}",
            ]
            if existing and existing.get("sha"):
                args.extend(["-f", f"sha={existing['sha']}"])
            result = self._json(args)
            last_commit = str(_nested(result, "commit", "sha") or "")
        return {"commit_sha": last_commit}

    def create_pr(
        self,
        *,
        repo: str,
        issue_number: int | None,
        branch_name: str,
        execution_id: str,
        title: str,
        body: str,
    ) -> GitHubBinding:
        existing = self._json(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                branch_name,
                "--state",
                "open",
                "--json",
                "number,url",
            ]
        )
        if isinstance(existing, list) and existing:
            number = int(existing[0]["number"])
        else:
            command = [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    repo,
                    "--head",
                    branch_name,
                    "--title",
                    title,
                    "--body",
                    body,
                ]
            if self.base_ref:
                command.extend(["--base", self.base_ref])
            url = self._text(command).strip()
            number = int(url.rstrip("/").rsplit("/", 1)[-1])
        return GitHubBinding(
            repo=repo,
            issue_number=issue_number or 0,
            pr_number=number,
            branch_name=branch_name,
            execution_id=execution_id,
        )

    def find_open_pr_by_head(self, repo: str, branch_name: str) -> dict[str, Any] | None:
        value = self._json(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                branch_name,
                "--state",
                "open",
                "--json",
                "number,url,headRefName,headRefOid,baseRefName,baseRefOid",
            ]
        )
        if not isinstance(value, list) or not value:
            return None
        pr = dict(value[0])
        return {
            **pr,
            "head_sha": pr.get("headRefOid", ""),
            "base_sha": pr.get("baseRefOid", ""),
        }

    def fetch_checks(self, repo: str, ref: str) -> dict[str, Any]:
        return self._json(["gh", "api", f"repos/{repo}/commits/{ref}/check-runs"])

    def fetch_pr_reviews(self, repo: str, pr_number: int) -> list[dict[str, Any]]:
        value = self._json(["gh", "api", f"repos/{repo}/pulls/{pr_number}/reviews"])
        return value if isinstance(value, list) else []

    def fetch_pr_comments(self, repo: str, pr_number: int) -> list[dict[str, Any]]:
        value = self._json(["gh", "api", f"repos/{repo}/issues/{pr_number}/comments"])
        return value if isinstance(value, list) else []

    def fetch_pr(self, repo: str, pr_number: int) -> dict[str, Any]:
        payload = self._json(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repo,
                "--json",
                "number,url,state,isDraft,mergeable,mergedAt,mergeCommit,headRefName,headRefOid,baseRefName,baseRefOid",
            ]
        )
        merge_commit = dict(payload.get("mergeCommit", {}) or {})
        return {
            **payload,
            "merged": str(payload.get("state", "")).upper() == "MERGED",
            "merged_at": payload.get("mergedAt", ""),
            "merge_commit_sha": merge_commit.get("oid", ""),
            "head_sha": payload.get("headRefOid", ""),
            "base_sha": payload.get("baseRefOid", ""),
        }

    def fetch_pr_diff(self, repo: str, pr_number: int) -> dict[str, Any]:
        payload = self._json(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repo,
                "--json",
                "files,headRefOid,baseRefName",
            ]
        )
        patch = self._text(["gh", "pr", "diff", str(pr_number), "--repo", repo])
        return {
            "base_ref": payload.get("baseRefName", ""),
            "head_sha": payload.get("headRefOid", ""),
            "changed_files": [
                {
                    "path": file.get("path", ""),
                    "status": file.get("changeType", ""),
                    "additions": file.get("additions", 0),
                    "deletions": file.get("deletions", 0),
                }
                for file in payload.get("files", [])
                if isinstance(file, dict)
            ],
            "patch": patch,
        }

    def merge_pr(self, repo: str, pr_number: int, *, head_sha: str, method: str) -> dict[str, Any]:
        return self._json(
            [
                "gh",
                "api",
                "-X",
                "PUT",
                f"repos/{repo}/pulls/{pr_number}/merge",
                "-f",
                f"sha={head_sha}",
                "-f",
                f"merge_method={method}",
            ]
        )

    def issue_comment_once(self, repo: str, issue_number: int, *, marker: str, body: str) -> dict[str, Any]:
        comments = self._json(["gh", "api", f"repos/{repo}/issues/{issue_number}/comments?per_page=100"])
        for comment in comments if isinstance(comments, list) else []:
            if marker in str(comment.get("body", "")):
                return {"id": comment.get("id"), "reused": True}
        created = self._json(
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"repos/{repo}/issues/{issue_number}/comments",
                "-f",
                f"body={body}\n\n{marker}",
            ]
        )
        return {"id": created.get("id"), "reused": False}

    def close_issue(self, repo: str, issue_number: int) -> dict[str, Any]:
        return self._json(
            ["gh", "api", "-X", "PATCH", f"repos/{repo}/issues/{issue_number}", "-f", "state=closed"]
        )

    def add_pr_comment(self, repo: str, pr_number: int, body: str) -> dict[str, Any]:
        return self._json(
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"repos/{repo}/issues/{pr_number}/comments",
                "-f",
                f"body={body}",
            ]
        )

    def _json(self, command: list[str]) -> Any:
        output = self._text(command)
        try:
            return json.loads(output or "null")
        except json.JSONDecodeError as exc:
            raise GitHubCLIError(f"GitHub CLI returned invalid JSON for {' '.join(command[:3])}") from exc

    def _json_optional(self, command: list[str]) -> Any:
        try:
            return self._json(command)
        except GitHubCLIError as exc:
            if "404" in str(exc) or "not found" in str(exc).lower():
                return None
            raise

    def _text(self, command: list[str]) -> str:
        result = subprocess.run(command, cwd=self.cwd, capture_output=True, check=False, text=True)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise GitHubCLIError(message or f"GitHub CLI exited with {result.returncode}")
        return result.stdout


def _repository_identity(project_root: Path) -> str:
    if not project_root.is_dir():
        return ""
    result = subprocess.run(
        ["git", "-C", str(project_root), "remote", "get-url", "origin"],
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


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current
