"""Host-generated bounded source evidence from an already verified capture.

No request path, worker Markdown path, Git command or source-directory read is
accepted here. A preview is made only from bytes captured by the host collector.
"""
from collections import Counter
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re

from goodteam_os.memory_storage import atomic_write
from goodteam_os.resources import register_markdown_artifact

MAX_FILE_BYTES = 64 * 1024
MAX_PREVIEW_BYTES = 512 * 1024
TEXT_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".sql", ".sh"}
SENSITIVE_NAME = re.compile(r"(?:^|[._-])(?:env|credentials?|secrets?|tokens?|passwords?|keys?|keystores?|service[_-]?accounts?|id_rsa|id_dsa|id_ecdsa|id_ed25519)(?:$|[._-])", re.I)
SENSITIVE_TEXT = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:api[_-]?key|token|password|secret|authorization)[\"']?\s*[:=]\s*[\"']?[^\s\"']{8,}|"
    r"Bearer\s+[A-Za-z0-9._~+/-]{8,}|"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", re.I,
)


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _allowed_name(name):
    if not isinstance(name, str) or not name or not re.fullmatch(r"[\w./ @+-]+", name):
        return False
    path = PurePosixPath(name)
    return (not path.is_absolute() and path.as_posix() == name
            and all(part not in {"", ".", "..", ".git", ".ssh", ".aws"} and not SENSITIVE_NAME.search(part) for part in name.split("/"))
            and path.suffix.lower() in TEXT_SUFFIXES)


def build_source_preview(*, capture, result, project_key, run_id):
    """Called inside collect after its double-capture identity check, not by HTTP."""
    files = capture["files"]
    manifest = {name: {"mode": item["mode"], "size": len(item["data"]),
                       "sha256": sha256(item["data"]).hexdigest()} for name, item in files.items()}
    manifest_version = "sha256:" + sha256(_canonical(manifest)).hexdigest()
    binding = {key: result[key] for key in ("job_id", "baseline_commit", "result_commit")}
    binding.update(project_key=project_key, run_id=run_id, manifest_version=manifest_version)
    sections = ["# Retained source preview\n\nBinding: " + _canonical(binding).decode() + "\n\n",
                "Host-observed cumulative task result; this is not test acceptance.\n\n"]
    excluded = Counter()
    for name in sorted(set(result["changed_files"])):
        item = files.get(name)
        if (not _allowed_name(name) or not item or item["mode"] not in {"100644", "100755"}
                or len(item["data"]) > MAX_FILE_BYTES):
            excluded["unsafe path, unsupported extension/mode, missing file or size limit"] += 1
            continue
        try:
            content = item["data"].decode("utf-8")
        except UnicodeError:
            excluded["binary or non-UTF-8"] += 1
            continue
        if (any(ord(char) < 32 and char not in "\t\r\n" for char in content)
                or SENSITIVE_TEXT.search(content)):
            excluded["control bytes or credential-like text"] += 1
            continue
        # A fence longer than every source run prevents source Markdown escaping.
        fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", content)), default=0))
        section = ("## " + json.dumps(name, ensure_ascii=True) + "\n\n"
                   + "SHA256: " + manifest[name]["sha256"] + "\n\n"
                   + fence + "text\n" + content + ("" if content.endswith("\n") else "\n") + fence + "\n\n")
        if len(("".join(sections) + section).encode()) > MAX_PREVIEW_BYTES - 1024:
            excluded["total preview limit"] += 1
            continue
        sections.append(section)
    sections.append(f"Excluded or unavailable changed files: {sum(excluded.values())}. No source path was followed from a report.\n")
    sections.append("Exclusion reasons: " + json.dumps(dict(excluded), sort_keys=True) + "\n")
    content = "".join(sections)
    return {"content": content, "version": "sha256:" + sha256(content.encode()).hexdigest(),
            "manifest_version": manifest_version, "binding": binding}


def register_source_preview(workspace, state, result):
    """Consume a collector-generated bundle; keep only its binding in saved state."""
    bundle = result.pop("source_preview", None)
    if bundle is None:
        return
    if workspace is None:
        raise ValueError("Source preview requires a durable workflow workspace.")
    binding = {key: result[key] for key in ("job_id", "baseline_commit", "result_commit", "project_key", "run_id")}
    binding["manifest_version"] = bundle["manifest_version"]
    if (result.get("project_key") != state.project_key or result.get("run_id") != state.run_id
            or bundle.get("binding") != binding
            or not bundle["content"].startswith("# Retained source preview\n\nBinding: " + _canonical(binding).decode() + "\n\n")):
        raise ValueError("Source preview does not belong to this task result.")
    raw = bundle["content"].encode()
    version = "sha256:" + sha256(raw).hexdigest()
    if version != bundle["version"] or len(raw) > MAX_PREVIEW_BYTES:
        raise ValueError("Source preview changed before registration.")
    # IDs are host state; reject invalid ones before composing a destination.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", state.run_id):
        raise ValueError("Invalid source preview run.")
    path = workspace / "artifacts" / "runs" / state.run_id / "source_preview" / (version[7:] + ".md")
    atomic_write(path, raw, maximum=MAX_PREVIEW_BYTES)
    register_markdown_artifact(state, "source_preview", path, reviewable=False, editable=False)
    result["source_preview_version"] = version
    result["source_manifest_version"] = bundle["manifest_version"]


def source_preview_matches(raw, state):
    """Check saved host result identity as well as report bytes on every read."""
    result = state.release_candidate.get("repository_result", {})
    if not isinstance(result, dict):
        return False
    binding = {key: result.get(key) for key in ("job_id", "baseline_commit", "result_commit", "project_key", "run_id")}
    binding["manifest_version"] = result.get("source_manifest_version")
    return (binding["project_key"] == state.project_key and binding["run_id"] == state.run_id
            and all(isinstance(value, str) and value for value in binding.values())
            and result.get("source_preview_version") == "sha256:" + sha256(raw).hexdigest()
            and raw.startswith(b"# Retained source preview\n\nBinding: " + _canonical(binding) + b"\n\n"))
