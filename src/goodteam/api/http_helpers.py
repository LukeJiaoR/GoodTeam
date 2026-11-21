"""HTTP JSON parsing, error mapping, and safe SPA static asset serving."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from goodteam.agents.worktree import WorktreeError
from goodteam.integrations.github import GitHubError
from goodteam.integrations.memory import MemoryError as ProjectMemoryError
from goodteam.providers import ProviderError
from goodteam.skills.models import SkillError


MAX_BODY_BYTES = 32 * 1024 * 1024

_EXTRA_MIME_TYPES = {
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
}


class HttpError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def decode_segment(segment: str) -> str:
    return unquote(segment)


def read_body_bytes(handler: BaseHTTPRequestHandler, *, max_bytes: int = MAX_BODY_BYTES) -> bytes:
    raw_len = handler.headers.get("Content-Length")
    if not raw_len:
        return b""
    try:
        length = int(raw_len)
    except ValueError as exc:
        raise HttpError(400, "invalid_content_length", "Invalid Content-Length header.") from exc
    if length < 0:
        raise HttpError(400, "invalid_content_length", "Negative Content-Length header.")
    if length > max_bytes:
        raise HttpError(413, "payload_too_large", f"Request body exceeds {max_bytes // (1024 * 1024)}MB limit.")
    return handler.rfile.read(length)


def read_json(
    handler: BaseHTTPRequestHandler,
    *,
    required: bool = False,
    max_bytes: int = MAX_BODY_BYTES,
) -> dict[str, Any]:
    raw = read_body_bytes(handler, max_bytes=max_bytes)
    if not raw:
        if required:
            raise HttpError(400, "empty_body", "JSON request body is required.")
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HttpError(400, "invalid_json", f"Invalid JSON payload: {exc}") from exc
    if not isinstance(data, dict):
        raise HttpError(400, "invalid_json", "JSON payload must be an object.")
    return data


def send_bytes(
    handler: BaseHTTPRequestHandler,
    data: bytes,
    *,
    content_type: str,
    status: int = 200,
    cache_control: str = "no-store",
) -> None:
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", cache_control)
        handler.end_headers()
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):
        pass


def send_json(handler: BaseHTTPRequestHandler, payload: Any, *, status: int = 200) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    send_bytes(handler, encoded, content_type="application/json; charset=utf-8", status=status)


def send_error_json(handler: BaseHTTPRequestHandler, status: int, code: str, message: str) -> None:
    send_json(handler, {"error": message, "code": code, "message": message}, status=status)


def map_exception(exc: Exception) -> tuple[int, str, str]:
    if isinstance(exc, HttpError):
        return exc.status, exc.code, exc.message
    if isinstance(exc, ProviderError):
        return exc.status, exc.code, str(exc)
    if isinstance(exc, SkillError):
        return exc.status, exc.code, str(exc)
    if isinstance(exc, ProjectMemoryError):
        return 400, "memory_error", str(exc)
    if isinstance(exc, WorktreeError):
        return 400, "worktree_error", str(exc)
    if isinstance(exc, GitHubError):
        return 400, "github_error", str(exc)
    if isinstance(exc, FileNotFoundError):
        return 404, "not_found", str(exc)
    if isinstance(exc, PermissionError):
        return 403, "forbidden", str(exc)
    if isinstance(exc, ValueError):
        return 400, "invalid_request", str(exc)
    return 500, "internal_error", str(exc)[:300] or "Internal server error."


def _mime_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _EXTRA_MIME_TYPES:
        return _EXTRA_MIME_TYPES[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def serve_static(handler: BaseHTTPRequestHandler, dist_dir: Path | None, raw_path: str) -> bool:
    if dist_dir is None or not dist_dir.is_dir():
        return False
    base = dist_dir.resolve()
    rel = unquote(raw_path).lstrip("/")
    candidate = (base / rel).resolve() if rel else (base / "index.html").resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        send_error_json(handler, 403, "forbidden", "Invalid asset path.")
        return True

    if candidate.is_file() and not candidate.is_symlink():
        cache = "public, max-age=31536000, immutable" if "/assets/" in raw_path else "no-cache"
        send_bytes(handler, candidate.read_bytes(), content_type=_mime_for(candidate), cache_control=cache)
        return True

    # SPA fallback for client-side routes (non-API, non-asset extensions)
    index_file = base / "index.html"
    if not raw_path.startswith("/api/") and index_file.is_file():
        send_bytes(handler, index_file.read_bytes(), content_type="text/html; charset=utf-8", cache_control="no-cache")
        return True
    return False
