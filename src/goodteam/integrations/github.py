"""GitHub CLI (gh) integration for reading issues, opening PRs, and checking CI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from goodteam.store import Store


class GitHubError(RuntimeError):
    pass


def _gh_exe() -> str:
    exe = os.environ.get("GOODTEAM_GH_PATH", "").strip() or shutil.which("gh") or ""
    if not exe:
        raise GitHubError("gh CLI is not installed.")
    return exe


def _run_gh(repo_path: str | Path, args: list[str], *, timeout: int = 20) -> str:
    exe = _gh_exe()
    try:
        proc = subprocess.run(
            [exe, *args],
            cwd=Path(repo_path).expanduser().resolve(),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise GitHubError(f"gh {' '.join(args[:2])} failed: {exc}") from exc
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or f"gh {' '.join(args[:2])} failed").strip()
        raise GitHubError(msg[:400])
    return proc.stdout


def _parse_json(raw: str, label: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise GitHubError(f"Invalid JSON from gh {label}.") from exc


def repo_info(repo_path: str | Path) -> dict[str, Any]:
    out = _run_gh(repo_path, ["repo", "view", "--json", "nameWithOwner,defaultBranchRef,url"])
    return _parse_json(out, "repo view")


def list_issues(repo_path: str | Path, *, state: str = "open", limit: int = 30) -> list[dict[str, Any]]:
    if state not in {"open", "closed", "all"}:
        raise GitHubError("state must be open, closed, or all.")
    bounded = max(1, min(int(limit), 100))
    out = _run_gh(
        repo_path,
        ["issue", "list", "--state", state, "--limit", str(bounded),
         "--json", "number,title,state,labels,url,author,updatedAt"],
    )
    return _parse_json(out, "issue list")


def get_issue(repo_path: str | Path, number: int) -> dict[str, Any]:
    num = int(number)
    if num <= 0:
        raise GitHubError("Issue number must be positive.")
    out = _run_gh(
        repo_path,
        ["issue", "view", str(num), "--json", "number,title,body,state,labels,url,comments"],
    )
    return _parse_json(out, "issue view")


def import_issue_to_task(store: Store, project_id: str, repo_path: str | Path, number: int) -> dict[str, Any]:
    ref = f"github:issue:{int(number)}"
    for existing in store.list_tasks(project_id):
        if existing.get("source_ref") == ref:
            return existing
    issue = get_issue(repo_path, number)
    title = f"#{issue['number']}: {issue.get('title', '').strip()}"
    body = str(issue.get("body") or "").strip()
    url = str(issue.get("url") or "").strip()
    desc = f"{url}\n\n{body}".strip() if url else body
    return store.create_task(
        project_id=project_id,
        title=title[:200],
        description=desc[:8000],
        status="todo",
        source_ref=ref,
    )


def push_and_create_pr(
    repo_path: str | Path,
    *,
    branch: str,
    base: str,
    title: str,
    body: str = "",
) -> dict[str, Any]:
    repo = Path(repo_path).expanduser().resolve()
    if not branch or branch.startswith("-") or not base or base.startswith("-"):
        raise GitHubError("Invalid branch or base ref.")
    try:
        push = subprocess.run(
            ["git", "-C", str(repo), "push", "-u", "origin", "--", branch],
            capture_output=True,
            text=True,
            timeout=45,
            shell=False,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise GitHubError(f"git push failed: {exc}") from exc
    if push.returncode != 0:
        raise GitHubError((push.stderr or push.stdout or "git push failed").strip()[:400])

    exe = _gh_exe()
    try:
        pr_proc = subprocess.run(
            [exe, "pr", "create", "--base", base, "--head", branch,
             "--title", (title or branch)[:200], "--body", (body or "Created by GoodTeam v2.")[:8000]],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise GitHubError(f"gh pr create failed: {exc}") from exc
    if pr_proc.returncode == 0:
        lines = [line.strip() for line in pr_proc.stdout.splitlines() if line.strip()]
        url = lines[-1] if lines else ""
        return {"url": url, "branch": branch, "base": base}
    # If PR already exists for branch, return existing PR details cleanly.
    try:
        view_proc = subprocess.run(
            [exe, "pr", "view", branch, "--json", "url,number,state"],
            cwd=repo, capture_output=True, text=True, timeout=15, shell=False, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise GitHubError(f"gh pr view failed: {exc}") from exc
    if view_proc.returncode == 0:
        data = _parse_json(view_proc.stdout, "pr view")
        return {"url": data.get("url", ""), "number": data.get("number"), "state": data.get("state"), "branch": branch, "base": base}
    raise GitHubError((pr_proc.stderr or pr_proc.stdout or "gh pr create failed").strip()[:400])


def pr_checks(repo_path: str | Path, pr_ref: str) -> dict[str, Any]:
    if not pr_ref or str(pr_ref).startswith("-"):
        raise GitHubError("Invalid PR reference.")
    out = _run_gh(repo_path, ["pr", "view", str(pr_ref), "--json", "number,state,url,statusCheckRollup"])
    return _parse_json(out, "pr view")
