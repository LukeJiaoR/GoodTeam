"""Five-source Skill installer: builtin, GitHub URL, .zip upload, local dir, and auto-discovery."""
from __future__ import annotations

from hashlib import sha256
import io
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit
import zipfile

from goodteam.config import skills_dir
from goodteam.skills.models import SkillError, SkillManifest, safe_subpath, slugify, validate_skill_dir
from goodteam.store import Store


MAX_ZIP_UNCOMPRESSED = 20 * 1024 * 1024
MAX_ZIP_FILES = 400
_SLUG_REPO_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}


def ensure_builtins(store: Store) -> list[dict[str, Any]]:
    builtin_root = Path(__file__).with_name("builtin")
    installed = []
    for child in sorted(builtin_root.iterdir()):
        if not child.is_dir() or not (child / "SKILL.md").is_file():
            continue
        manifest = validate_skill_dir(child)
        rec = store.upsert_skill(
            skill_id=f"builtin:{manifest.slug}",
            name=manifest.name,
            description=manifest.description,
            source_type="builtin",
            source_ref=f"builtin:{child.name}",
            commit_sha="",
            path=str(child.resolve()),
        )
        installed.append(rec)
    return installed


def parse_github_source(raw: str) -> tuple[str, str, str]:
    text = (raw or "").strip()
    if not text or text.startswith("-") or any(c.isspace() for c in text):
        raise SkillError("invalid_github_url", "Enter a GitHub repository URL or owner/repo.")
    if text.startswith("file://"):
        return text, "", ""
    m = _SLUG_REPO_RE.fullmatch(text)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}.git", "", ""
    parsed = urlsplit(text)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise SkillError("invalid_github_url", "Only https://github.com/owner/repo URLs or owner/repo slugs are supported.")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise SkillError("invalid_github_url", "GitHub URL must include owner and repository name.")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    clone_url = f"https://github.com/{owner}/{repo}.git"
    ref, subpath = "", ""
    if len(parts) >= 4 and parts[2] == "tree":
        ref = parts[3]
        subpath = "/".join(parts[4:])
    return clone_url, ref, subpath


def _discover_skill_dirs(search_root: Path) -> list[tuple[str, Path, SkillManifest]]:
    found: list[tuple[str, Path, SkillManifest]] = []
    base = search_root.resolve()
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not (Path(dirpath) / d).is_symlink())
        if "SKILL.md" in filenames:
            current = Path(dirpath)
            manifest = validate_skill_dir(current)
            rel = current.relative_to(base).as_posix()
            found.append(("." if rel == "." else rel, current, manifest))
            dirnames[:] = []  # Do not nest skills inside another skill directory.
    if not found:
        raise SkillError("no_skills_found", "No valid SKILL.md package found in the provided source.")
    return found


def _clone_repo(clone_url: str, ref: str, dest: Path) -> str:
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        if ref.startswith("-"):
            raise SkillError("invalid_ref", "Invalid git branch or tag name.")
        cmd.extend(["--branch", ref])
    cmd.extend(["--", clone_url, str(dest)])
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45, shell=False, check=False, env=env)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise SkillError("git_clone_failed", f"git clone failed: {exc}") from exc
    if proc.returncode != 0:
        raise SkillError("git_clone_failed", (proc.stderr or proc.stdout or "git clone failed")[:300])
    try:
        head = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, shell=False, check=False, env=env,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return head.stdout.strip() if head.returncode == 0 else ""


def _preview_entries(items: list[tuple[str, Path, SkillManifest]]) -> list[dict[str, Any]]:
    return [
        {
            "rel_path": rel,
            "slug": m.slug,
            "name": m.name,
            "description": m.description,
            "file_count": sum(1 for p in d.rglob("*") if p.is_file()),
        }
        for rel, d, m in items
    ]


def _copy_selected(
    store: Store,
    items: list[tuple[str, Path, SkillManifest]],
    *,
    selected_paths: list[str] | None,
    source_type: str,
    source_ref: str,
    commit_sha: str,
    state_dir: str | Path | None,
) -> list[dict[str, Any]]:
    chosen = items
    if selected_paths is not None:
        wanted = set(selected_paths)
        chosen = [item for item in items if item[0] in wanted]
        if not chosen:
            raise SkillError("no_skills_selected", "Select at least one skill to install.")
    root = skills_dir(state_dir)
    installed = []
    for _, src_dir, manifest in chosen:
        skill_id = f"{source_type}:{manifest.slug}"
        dest = (root / f"{source_type}-{manifest.slug}").resolve()
        dest.relative_to(root.resolve())
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src_dir, dest, symlinks=False)
        installed.append(
            store.upsert_skill(
                skill_id=skill_id,
                name=manifest.name,
                description=manifest.description,
                source_type=source_type,
                source_ref=source_ref,
                commit_sha=commit_sha,
                path=str(dest),
            )
        )
    return installed


def preview_github(url_or_slug: str) -> dict[str, Any]:
    clone_url, ref, subpath = parse_github_source(url_or_slug)
    with TemporaryDirectory(prefix="goodteam-gh-") as tmp:
        repo_dir = Path(tmp) / "repo"
        sha = _clone_repo(clone_url, ref, repo_dir)
        search_root = (repo_dir / subpath).resolve() if subpath else repo_dir.resolve()
        try:
            search_root.relative_to(repo_dir.resolve())
        except ValueError:
            raise SkillError("invalid_path", "Subpath escapes repository.") from None
        if not search_root.is_dir():
            raise SkillError("path_not_found", "Requested repository subpath does not exist.", 404)
        items = _discover_skill_dirs(search_root)
        return {"clone_url": clone_url, "ref": ref, "subpath": subpath, "commit_sha": sha, "skills": _preview_entries(items)}


def install_github(
    store: Store,
    url_or_slug: str,
    *,
    selected_paths: list[str] | None = None,
    state_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    clone_url, ref, subpath = parse_github_source(url_or_slug)
    with TemporaryDirectory(prefix="goodteam-gh-") as tmp:
        repo_dir = Path(tmp) / "repo"
        sha = _clone_repo(clone_url, ref, repo_dir)
        search_root = (repo_dir / subpath).resolve() if subpath else repo_dir.resolve()
        try:
            search_root.relative_to(repo_dir.resolve())
        except ValueError:
            raise SkillError("invalid_path", "Subpath escapes repository.") from None
        if not search_root.is_dir():
            raise SkillError("path_not_found", "Requested repository subpath does not exist.", 404)
        items = _discover_skill_dirs(search_root)
        return _copy_selected(
            store, items, selected_paths=selected_paths, source_type="github",
            source_ref=url_or_slug.strip(), commit_sha=sha, state_dir=state_dir,
        )


def _safe_extract_zip(zip_bytes: bytes, dest: Path) -> None:
    if len(zip_bytes) > MAX_ZIP_UNCOMPRESSED:
        raise SkillError("zip_too_large", "Zip archive exceeds 20MB limit.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise SkillError("invalid_zip", "Uploaded file is not a valid .zip archive.") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_FILES:
            raise SkillError("zip_too_large", f"Zip archive exceeds {MAX_ZIP_FILES} entries.")
        total_size = 0
        base = dest.resolve()
        for info in infos:
            name = info.filename
            if not name or name.startswith(("/", "\\")) or "\\" in name or "\x00" in name:
                raise SkillError("unsafe_zip_path", f"Unsafe zip entry path: {name}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise SkillError("unsafe_zip_symlink", "Symlinks are forbidden in uploaded zip archives.")
            parts = PurePosixPath(name).parts
            if any(part in ("..", "") for part in parts):
                raise SkillError("unsafe_zip_path", f"Unsafe zip entry path: {name}")
            if info.is_dir():
                continue
            if info.file_size > MAX_ZIP_UNCOMPRESSED:
                raise SkillError("zip_too_large", "Uncompressed zip contents exceed 20MB limit.")
            target = (base / PurePosixPath(*parts)).resolve()
            try:
                target.relative_to(base)
            except ValueError:
                raise SkillError("unsafe_zip_path", f"Unsafe zip entry path: {name}") from None
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as dst:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > MAX_ZIP_UNCOMPRESSED:
                        raise SkillError("zip_too_large", "Uncompressed zip contents exceed 20MB limit.")
                    dst.write(chunk)


def preview_zip(zip_bytes: bytes) -> dict[str, Any]:
    with TemporaryDirectory(prefix="goodteam-zip-") as tmp:
        root = Path(tmp) / "extracted"
        root.mkdir()
        _safe_extract_zip(zip_bytes, root)
        items = _discover_skill_dirs(root)
        return {"skills": _preview_entries(items)}


def install_zip(
    store: Store,
    zip_bytes: bytes,
    *,
    filename: str = "upload.zip",
    selected_paths: list[str] | None = None,
    state_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    with TemporaryDirectory(prefix="goodteam-zip-") as tmp:
        root = Path(tmp) / "extracted"
        root.mkdir()
        _safe_extract_zip(zip_bytes, root)
        items = _discover_skill_dirs(root)
        return _copy_selected(
            store, items, selected_paths=selected_paths, source_type="zip",
            source_ref=filename, commit_sha="", state_dir=state_dir,
        )


def install_local(store: Store, folder_path: str | Path, *, state_dir: str | Path | None = None) -> dict[str, Any]:
    src = Path(folder_path).expanduser().resolve()
    manifest = validate_skill_dir(src)
    return _copy_selected(
        store, [(".", src, manifest)], selected_paths=["."], source_type="local",
        source_ref=str(src), commit_sha="", state_dir=state_dir,
    )[0]


def discover_skills(
    store: Store,
    *,
    project_path: str | Path | None = None,
    include_user_dirs: bool = False,
) -> list[dict[str, Any]]:
    candidates: list[tuple[str, Path]] = []
    if project_path:
        candidates.append(("project", Path(project_path).expanduser().resolve() / ".agents" / "skills"))
    if include_user_dirs:
        candidates.append(("claude_home", Path.home() / ".claude" / "skills"))
        candidates.append(("codex_home", Path.home() / ".codex" / "skills"))
    discovered = []
    for label, scan_root in candidates:
        if not scan_root.is_dir():
            continue
        for child in sorted(scan_root.iterdir()):
            if not child.is_dir() or child.is_symlink() or not (child / "SKILL.md").is_file():
                continue
            try:
                manifest = validate_skill_dir(child)
            except SkillError:
                continue
            digest = sha256(str(child.resolve()).encode("utf-8")).hexdigest()[:6]
            skill_id = f"discovered:{slugify(manifest.name)}-{digest}"
            rec = store.upsert_skill(
                skill_id=skill_id,
                name=manifest.name,
                description=manifest.description,
                source_type="discovered",
                source_ref=f"{label}:{child.resolve()}",
                commit_sha="",
                path=str(child.resolve()),
            )
            discovered.append(rec)
    return discovered


def uninstall_skill(store: Store, skill_id: str, *, state_dir: str | Path | None = None) -> bool:
    rec = store.get_skill(skill_id)
    if not rec:
        return False
    if rec["source_type"] == "builtin":
        raise SkillError("cannot_uninstall_builtin", "Built-in skills cannot be uninstalled.")
    installed_root = skills_dir(state_dir).resolve()
    skill_path = Path(rec["path"]).resolve()
    if skill_path != installed_root and installed_root in skill_path.parents and skill_path.exists():
        shutil.rmtree(skill_path)
    # Remove unbound references from existing roles cleanly.
    for project in [None, *[p["id"] for p in store.list_projects()]]:
        for role in store.list_roles(project):
            filtered = [b for b in role["skill_bindings"] if b.get("skill_id") != skill_id]
            if len(filtered) != len(role["skill_bindings"]):
                store.upsert_role(
                    project_id=role["project_id"], key=role["key"], name=role["name"],
                    description=role["description"], system_prompt=role["system_prompt"],
                    execution_policy=role["execution_policy"], write_domains=role["write_domains"],
                    allowed_tools=role["allowed_tools"], adapter=role["adapter"],
                    provider_id=role["provider_id"], model=role["model"],
                    reasoning_effort=role["reasoning_effort"], skill_bindings=filtered,
                    is_builtin=role["is_builtin"],
                )
    return store.delete_skill(skill_id)


def read_skill_resource(skill: dict[str, Any], rel_path: str) -> str:
    target = safe_subpath(Path(skill["path"]), rel_path)
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SkillError("binary_resource", "Skill resource is not UTF-8 text.") from exc
