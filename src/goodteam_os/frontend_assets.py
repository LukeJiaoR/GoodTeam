from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

FRONTEND_DIST_ENV = "GOODTEAM_FRONTEND_DIST"


class FrontendAssetsNotFound(RuntimeError):
    def __init__(self, attempted_paths: Sequence[str | Path]) -> None:
        self.attempted_paths = tuple(str(Path(path).expanduser().resolve()) for path in attempted_paths)
        attempts = "\n".join(f"  - {path}" for path in self.attempted_paths) or "  - (none)"
        super().__init__(
            "GoodTeam could not find a usable React frontend build.\n"
            "Every frontend dist must be a directory containing index.html.\n"
            f"Attempted paths:\n{attempts}\n"
            "Build the checkout frontend with:\n"
            "  npm --prefix frontend install\n"
            "  npm --prefix frontend run build\n"
            f"Or set {FRONTEND_DIST_ENV} to the built frontend/dist directory."
        )


def _is_frontend_dist(path: Path) -> bool:
    return path.is_dir() and (path / "index.html").is_file()


def resolve_frontend_dist(
    *,
    environ: Mapping[str, str] | None = None,
    module_path: str | Path | None = None,
    prefix: str | Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    source_file = Path(__file__ if module_path is None else module_path).expanduser().resolve()
    install_prefix = Path(sys.prefix if prefix is None else prefix).expanduser().resolve()

    override = str(env.get(FRONTEND_DIST_ENV, "")).strip()
    if override:
        candidate = Path(override).expanduser().resolve()
        if _is_frontend_dist(candidate):
            return candidate
        raise FrontendAssetsNotFound([candidate])

    candidates = [
        source_file.parent / "frontend_dist",
        install_prefix / "share" / "goodteam" / "frontend" / "dist",
        source_file.parents[2] / "frontend" / "dist",
    ]
    attempted: list[Path] = []
    for raw_candidate in candidates:
        candidate = raw_candidate.resolve()
        if candidate in attempted:
            continue
        attempted.append(candidate)
        if _is_frontend_dist(candidate):
            return candidate
    raise FrontendAssetsNotFound(attempted)
