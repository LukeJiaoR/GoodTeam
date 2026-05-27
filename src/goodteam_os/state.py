import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


@dataclass
class AgileDevState:
    run_id: str
    project_key: str
    idea: str
    source_type: str = "idea"
    source_ref: str = "manual"
    delivery_mode: str = "init_project"
    context_packet_path: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    approvals: dict[str, ApprovalDecision] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    model_profile: str = "balanced"
    role_models: dict[str, dict[str, str]] = field(default_factory=dict)
    current_node: str = "start"
    next_node: str = "product_requirement"


def create_initial_state(run_id: str, project_key: str, idea: str) -> AgileDevState:
    return AgileDevState(
        run_id=run_id,
        project_key=project_key,
        idea=idea,
        approvals={
            "prd": ApprovalDecision.PENDING,
            "rfc": ApprovalDecision.PENDING,
            "staging": ApprovalDecision.PENDING,
            "security": ApprovalDecision.PENDING,
            "release": ApprovalDecision.PENDING,
        },
    )


class StateStore:
    def __init__(self, root: Path):
        self.root = root

    def project_state_dir(self, project_key: str) -> Path:
        key = normalize_project_key(project_key)
        return self.root / "state" / "projects" / key

    def write_current(self, state: AgileDevState) -> Path:
        path = self.root / "state" / "current.json"
        self._write(path, state)
        return path

    def write_project_current(self, state: AgileDevState, project_key: str | None = None) -> Path:
        key = normalize_project_key(project_key or state.project_key)
        state.project_key = key
        path = self.project_state_dir(key) / "current.json"
        self._write(path, state)
        return path

    def write_checkpoint(self, state: AgileDevState) -> Path:
        path = self.root / "state" / "checkpoints" / state.run_id / f"{state.current_node}.json"
        self._write(path, state)
        return path

    def write_project_checkpoint(self, state: AgileDevState, project_key: str | None = None) -> Path:
        key = normalize_project_key(project_key or state.project_key)
        state.project_key = key
        path = self.project_state_dir(key) / "checkpoints" / state.run_id / f"{state.current_node}.json"
        self._write(path, state)
        return path

    def read_current(self) -> AgileDevState | None:
        path = self.root / "state" / "current.json"
        return self._read(path)

    def read_project_current(self, project_key: str) -> AgileDevState | None:
        return self._read(self.project_state_dir(project_key) / "current.json")

    def archive_project_current(self, project_key: str, reason: str = "cancelled") -> Path:
        key = normalize_project_key(project_key)
        state = self.read_project_current(key)
        if state is None:
            raise ValueError(f"No active run for project: {key}")
        safe_reason = _safe_path_part(reason)
        path = self.project_state_dir(key) / "archived" / f"{state.run_id}-{safe_reason}.json"
        self._write(path, state)
        (self.project_state_dir(key) / "current.json").unlink()
        return path

    def _read(self, path: Path) -> AgileDevState | None:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        data["approvals"] = {
            key: ApprovalDecision(value) for key, value in data.get("approvals", {}).items()
        }
        data.setdefault("source_type", "idea")
        data.setdefault("source_ref", "manual")
        data.setdefault("delivery_mode", "init_project")
        data.setdefault("context_packet_path", "")
        data.setdefault("artifacts", {})
        data.setdefault("blockers", [])
        data.setdefault("model_profile", "balanced")
        data.setdefault("role_models", {})
        return AgileDevState(**data)

    def _write(self, path: Path, state: AgileDevState) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def normalize_project_key(value: str) -> str:
    key = "".join(char.upper() if char.isalnum() else "-" for char in value.strip())
    key = "-".join(part for part in key.split("-") if part)
    if not key:
        raise ValueError("Project key is required.")
    return key


def _safe_path_part(value: str) -> str:
    part = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(item for item in part.split("-") if item) or "archived"
