"""Configuration paths and local CLI readiness checks for GoodTeam v2."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


DEFAULT_STATE_DIR = Path.home() / ".goodteam-v2"


def get_state_dir(state_dir: str | Path | None = None) -> Path:
    raw = state_dir if state_dir is not None else os.environ.get("GOODTEAM_STATE_DIR", "")
    path = Path(raw).expanduser().resolve() if str(raw).strip() else DEFAULT_STATE_DIR.expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path(state_dir: str | Path | None = None) -> Path:
    return get_state_dir(state_dir) / "goodteam.db"


def providers_path(state_dir: str | Path | None = None) -> Path:
    return get_state_dir(state_dir) / "providers.json"


def skills_dir(state_dir: str | Path | None = None) -> Path:
    path = get_state_dir(state_dir) / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_skills_dir(state_dir: str | Path | None = None) -> Path:
    path = get_state_dir(state_dir) / "runtime-skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def worktrees_dir(state_dir: str | Path | None = None) -> Path:
    path = get_state_dir(state_dir) / "worktrees"
    path.mkdir(parents=True, exist_ok=True)
    return path


def attachments_dir(state_dir: str | Path | None = None) -> Path:
    path = get_state_dir(state_dir) / "attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _probe_cmd(args: list[str], *, env_override: str | None = None) -> dict[str, object]:
    exe = os.environ.get(env_override, "").strip() if env_override else ""
    if not exe:
        exe = shutil.which(args[0]) or ""
    if not exe:
        return {"available": False, "path": "", "version": "", "error": f"{args[0]} not found on PATH"}
    try:
        proc = subprocess.run(
            [exe, *args[1:]],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
            check=False,
        )
        out = (proc.stdout or proc.stderr or "").strip().splitlines()
        return {
            "available": proc.returncode == 0,
            "path": exe,
            "version": out[0][:160] if out else "",
            "error": "" if proc.returncode == 0 else "\n".join(out[:3])[:300],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "path": exe, "version": "", "error": str(exc)[:200]}


def _probe_gh_auth(gh_path: str) -> dict[str, object]:
    if not gh_path:
        return {"authenticated": False, "account": "", "error": "gh not installed"}
    try:
        proc = subprocess.run(
            [gh_path, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
            check=False,
        )
        combined = "\n".join(
            line for line in ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()
            if "token" not in line.lower()
        ).strip()
        account = ""
        for line in combined.splitlines():
            if "account " in line:
                account = line.split("account ", 1)[1].split()[0].strip()
                break
        return {
            "authenticated": proc.returncode == 0,
            "account": account,
            "error": "" if proc.returncode == 0 else combined[:300],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"authenticated": False, "account": "", "error": str(exc)[:200]}


def doctor(state_dir: str | Path | None = None) -> dict[str, object]:
    codex = _probe_cmd(["codex", "--version"], env_override="GOODTEAM_CODEX_PATH")
    claude = _probe_cmd(["claude", "--version"], env_override="GOODTEAM_CLAUDE_PATH")
    git = _probe_cmd(["git", "--version"])
    gh = _probe_cmd(["gh", "--version"])
    gh_auth = _probe_gh_auth(str(gh["path"])) if gh["available"] else {"authenticated": False, "account": "", "error": "gh unavailable"}
    return {
        "state_dir": str(get_state_dir(state_dir)),
        "codex": codex,
        "claude": claude,
        "git": git,
        "gh": {**gh, **gh_auth},
    }
