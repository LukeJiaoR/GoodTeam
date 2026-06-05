from pathlib import Path
import warnings


class ArtifactStore:
    def __init__(self, root: Path, mirror_root: Path | None = None):
        self.root = root
        self.mirror_root = mirror_root

    def write_markdown(
        self,
        run_id: str,
        section: str,
        filename: str,
        content: str,
        *,
        mirror_root: Path | None = None,
    ) -> Path:
        path = self.root / "artifacts" / "runs" / run_id / section / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        target_mirror_root = mirror_root or self.mirror_root
        if target_mirror_root is not None:
            self._write_docs_mirror(target_mirror_root, filename, content)
        return path

    def _write_docs_mirror(self, mirror_root: Path, filename: str, content: str) -> None:
        try:
            docs_dir = mirror_root / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)
            (docs_dir / filename).write_text(content, encoding="utf-8")
        except OSError as exc:
            warnings.warn(f"artifact docs mirror skipped for {mirror_root}: {exc}", RuntimeWarning, stacklevel=2)
