from __future__ import annotations

import re
from fnmatch import fnmatch
from hashlib import sha256
from pathlib import PurePosixPath

from goodteam_os.repository_access import RepositoryAccessError


TREE_DEPTH_MAX = 6
TREE_ENTRIES_MAX = 500
SEARCH_MATCHES_MAX = 100
QUERY_LENGTH_MAX = 256
FILE_READ_MAX_BYTES = 256 * 1024

SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|"
    r"cookie|password|passwd|secret|client[_-]?secret)\b"
    r"(\s*[:=]\s*)(?:[\"'][^\"'\r\n]*[\"']|[^\s,;#]+)"
)
AUTHORIZATION_VALUE = re.compile(
    r"(?im)(authorization\s*[:=]\s*)(?:bearer|basic)\s+[^\s,;]+"
)
TOKEN_SHAPE = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"sk_(?:live|test)_[A-Za-z0-9_]{8,}|"
    r"gh[opusr]_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{12,}|AKIA[A-Z0-9]{16})\b"
)
SENSITIVE_KEY = re.compile(
    r"(?i)^(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|"
    r"cookie|password|passwd|secret|client[_-]?secret)$"
)
SENSITIVE_PATH_PATTERNS = (
    ".env",
    ".env.*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials.json",
    "credentials.*",
    "secrets.*",
    "id_rsa",
    "id_ed25519",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
)


def hash_bytes(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def redact_observation_text(
    value: str,
    *,
    limit: int | None = None,
) -> tuple[str, bool, bool]:
    masked = AUTHORIZATION_VALUE.sub(r"\1<redacted>", value)
    masked = SECRET_ASSIGNMENT.sub(r"\1\2<redacted>", masked)
    masked = TOKEN_SHAPE.sub("<redacted-token>", masked)
    was_truncated = False
    if limit is not None and len(masked) > limit:
        masked = masked[:limit]
        was_truncated = True
    return masked, masked != value, was_truncated


def redact_observation_value(value: object, *, key_name: str | None = None) -> object:
    if isinstance(value, str):
        if key_name is not None and SENSITIVE_KEY.match(key_name):
            if key_name.lower() == "authorization":
                return "Authorization: <redacted>"
            if TOKEN_SHAPE.search(value):
                return "<redacted-token>"
            return "<redacted>"
        return redact_observation_text(value)[0]
    if isinstance(value, list):
        return [
            redact_observation_value(item, key_name=key_name)
            for item in value
        ]
    if type(value) is dict:
        return {
            str(key): redact_observation_value(item, key_name=str(key))
            for key, item in value.items()
        }
    return value


def redacted_path_label(path: str) -> str:
    name = PurePosixPath(path).name
    if not name:
        return "<redacted>"
    return f"<redacted>/{name}"


def reject_sensitive_path(path: str) -> None:
    if not path:
        return
    for part in PurePosixPath(path).parts:
        if any(fnmatch(part, pattern) for pattern in SENSITIVE_PATH_PATTERNS):
            raise RepositoryAccessError(
                "context_path_rejected",
                "Repository path is rejected by content policy.",
                details={
                    "path": redacted_path_label(path),
                    "reason": "sensitive_path",
                },
            )
