"""Git worktree isolation, base_ref integration, diff collection, and merge gates."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from typing import Any

from goodteam.config import worktrees_dir


MAX_DIFF_BYTES = 200 * 1024
_COMMIT_AUTHOR_ARGS = ["-c", "user.name=GoodTeam", "-c", "user.email=goodteam@local"]


class WorktreeError(RuntimeError):
    pass


def _git(repo: Path, args: list[str], *, check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        check=False,
    )
    if check and proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or f"git {' '.join(args)} failed").strip()
        raise WorktreeError(msg[:400])
    return proc


def is_git_repo(repo_path: str | Path) -> bool:
    p = Path(repo_path).expanduser().resolve()
    if not p.is_dir():
        return False
    proc = _git(p, ["rev-parse", "--is-inside-work-tree"], check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def detect_default_branch(repo_path: str | Path) -> str:
    repo = Path(repo_path).expanduser().resolve()
    sym = _git(repo, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], check=False)
    if sym.returncode == 0 and sym.stdout.strip():
        candidate = sym.stdout.strip().removeprefix("origin/")
        if _git(repo, ["rev-parse", "--verify", candidate], check=False).returncode == 0:
            return candidate
    for branch in ("main", "master"):
        if _git(repo, ["rev-parse", "--verify", f"refs/heads/{branch}"], check=False).returncode == 0:
            return branch
    head = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"], check=False)
    if head.returncode == 0 and head.stdout.strip() and head.stdout.strip() != "HEAD":
        return head.stdout.strip()
    return "HEAD"


def _project_slug(repo: Path) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", repo.name).strip("-")[:40] or "repo"


def create_iteration_branch(repo_path: str | Path, iteration_id: str, *, base_ref: str | None = None) -> str:
    repo = Path(repo_path).expanduser().resolve()
    base = base_ref or detect_default_branch(repo)
    branch = f"goodteam/iter-{iteration_id}"
    if _git(repo, ["rev-parse", "--verify", f"refs/heads/{branch}"], check=False).returncode != 0:
        _git(repo, ["branch", branch, base])
    return branch


def create_worktree(
    repo_path: str | Path,
    run_id: str,
    *,
    base_ref: str | None = None,
    state_dir: str | Path | None = None,
) -> tuple[str, Path, str]:
    repo = Path(repo_path).expanduser().resolve()
    if _git(repo, ["rev-parse", "--verify", "HEAD"], check=False).returncode != 0:
        raise WorktreeError("Repository must have at least one commit before creating a Sub-agent worktree.")
    base = base_ref or detect_default_branch(repo)
    if base == "HEAD":
        base = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
    elif _git(repo, ["rev-parse", "--verify", base], check=False).returncode != 0:
        raise WorktreeError(f"Base ref '{base}' does not exist.")
    branch = f"goodteam/run-{run_id}"
    wt_root = (worktrees_dir(state_dir) / _project_slug(repo) / run_id).resolve()
    wt_root.parent.mkdir(parents=True, exist_ok=True)
    if wt_root.exists():
        _git(repo, ["worktree", "remove", "--force", str(wt_root)], check=False)
        shutil.rmtree(wt_root, ignore_errors=True)
    _git(repo, ["worktree", "add", "-b", branch, str(wt_root), base])
    return branch, wt_root, base


def _in_write_domains(path: str, write_domains: list[str]) -> bool:
    if not write_domains or "*" in write_domains:
        return True
    norm = path.replace("\\", "/").lstrip("./")
    for domain in write_domains:
        d = domain.rstrip("/")
        if d == "." or norm == d or norm.startswith(d + "/"):
            return True
    return False


def collect_result(
    worktree_path: str | Path,
    base_ref: str,
    *,
    write_domains: list[str] | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    wt = Path(worktree_path).resolve()
    status = _git(wt, ["status", "--porcelain"]).stdout.strip()
    if status:
        _git(wt, ["add", "-A"])
        _git(wt, [*_COMMIT_AUTHOR_ARGS, "commit", "--no-verify", "-m", "goodteam: subagent run output"])

    head = _git(wt, ["rev-parse", "HEAD"]).stdout.strip()
    names_raw = _git(wt, ["diff", "--name-only", f"{base_ref}...HEAD"]).stdout.strip()
    changed_files = [line for line in names_raw.splitlines() if line.strip()]

    numstat = _git(wt, ["diff", "--numstat", f"{base_ref}...HEAD"]).stdout.strip()
    insertions, deletions = 0, 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            insertions += int(parts[0]) if parts[0].isdigit() else 0
            deletions += int(parts[1]) if parts[1].isdigit() else 0

    diff_raw = _git(wt, ["diff", f"{base_ref}...HEAD"]).stdout
    if len(diff_raw.encode("utf-8", errors="ignore")) > MAX_DIFF_BYTES:
        diff_raw = diff_raw[: MAX_DIFF_BYTES // 2] + "\n\n... [diff truncated] ...\n"

    domains = write_domains or []
    scope_ok = (len(changed_files) == 0) if read_only else all(
        _in_write_domains(f, domains) for f in changed_files
    )
    return {
        "head_commit": head,
        "changed_files": changed_files,
        "diff_stat": {"files": len(changed_files), "insertions": insertions, "deletions": deletions},
        "diff_text": diff_raw,
        "scope_ok": scope_ok,
    }


def branch_diff(repo_path: str | Path, base_ref: str, branch_name: str) -> dict[str, Any]:
    repo = Path(repo_path).expanduser().resolve()
    names = _git(repo, ["diff", "--name-only", f"{base_ref}...{branch_name}"], check=False)
    diff = _git(repo, ["diff", f"{base_ref}...{branch_name}"], check=False)
    changed = [line for line in names.stdout.splitlines() if line.strip()]
    text = diff.stdout[:MAX_DIFF_BYTES]
    return {"changed_files": changed, "diff_text": text}


def _checked_out_worktree(repo: Path, branch: str) -> Path | None:
    proc = _git(repo, ["worktree", "list", "--porcelain"], check=False)
    current_wt: Path | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            current_wt = Path(line[len("worktree "):].strip()).resolve()
        elif line.startswith("branch ") and current_wt is not None:
            ref = line[len("branch "):].strip()
            if ref == f"refs/heads/{branch}":
                return current_wt
    return None


def merge_branch(repo_path: str | Path, source_branch: str, target_branch: str, *, message: str = "") -> str:
    repo = Path(repo_path).expanduser().resolve()
    msg = message or f"Merge {source_branch} into {target_branch}"
    checked = _checked_out_worktree(repo, target_branch)
    if checked is not None:
        dirty = [
            line for line in _git(checked, ["status", "--porcelain"]).stdout.splitlines()
            if line.strip() and not (
                line.startswith("?? ") and line[3:].strip().rstrip("/") in {".goodteam"} or
                line.startswith("?? ") and line[3:].strip().startswith(".goodteam/")
            )
        ]
        if dirty:
            raise WorktreeError(f"Working tree at {checked} has uncommitted changes; commit or stash before merging.")
        proc = _git(checked, [*_COMMIT_AUTHOR_ARGS, "merge", "--no-ff", source_branch, "-m", msg], check=False)
        if proc.returncode != 0:
            _git(checked, ["merge", "--abort"], check=False)
            raise WorktreeError((proc.stderr or proc.stdout or "Merge conflict").strip()[:400])
        return _git(checked, ["rev-parse", "HEAD"]).stdout.strip()

    with TemporaryDirectory(prefix="goodteam-merge-") as tmp:
        tmp_wt = Path(tmp) / "wt"
        _git(repo, ["worktree", "add", str(tmp_wt), target_branch])
        try:
            proc = _git(tmp_wt, [*_COMMIT_AUTHOR_ARGS, "merge", "--no-ff", source_branch, "-m", msg], check=False)
            if proc.returncode != 0:
                _git(tmp_wt, ["merge", "--abort"], check=False)
                raise WorktreeError((proc.stderr or proc.stdout or "Merge conflict").strip()[:400])
            return _git(tmp_wt, ["rev-parse", "HEAD"]).stdout.strip()
        finally:
            _git(repo, ["worktree", "remove", "--force", str(tmp_wt)], check=False)


def _is_safe_worktree_target(repo: Path, wt: Path, state_dir: str | Path | None) -> bool:
    if wt == repo or wt in repo.parents or repo in wt.parents:
        return False
    if state_dir is not None:
        wt_root = worktrees_dir(state_dir).resolve()
        return wt != wt_root and wt_root in wt.parents
    default_root = worktrees_dir(None).resolve()
    if wt != default_root and default_root in wt.parents:
        return True
    # Allow explicit caller-managed worktree paths under a 'worktrees' directory
    # ONLY if it is a linked git worktree (.git file, never a repo or non-git dir).
    return "worktrees" in wt.parts and (wt / ".git").is_file()


def cleanup_worktree(
    repo_path: str | Path,
    worktree_path: str | Path | None,
    *,
    branch_name: str | None = None,
    delete_branch: bool = False,
    state_dir: str | Path | None = None,
) -> None:
    repo = Path(repo_path).expanduser().resolve()
    if worktree_path:
        wt = Path(worktree_path).resolve()
        if _is_safe_worktree_target(repo, wt, state_dir):
            if wt.exists() and is_git_repo(repo):
                _git(repo, ["worktree", "remove", "--force", str(wt)], check=False)
            if wt.exists():
                shutil.rmtree(wt, ignore_errors=True)
    if is_git_repo(repo):
        _git(repo, ["worktree", "prune"], check=False)
        if delete_branch and branch_name:
            _git(repo, ["branch", "-D", branch_name], check=False)
