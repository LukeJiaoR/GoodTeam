from pathlib import Path


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root

    def write_markdown(self, run_id: str, section: str, filename: str, content: str) -> Path:
        path = self.root / "artifacts" / "runs" / run_id / section / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path
