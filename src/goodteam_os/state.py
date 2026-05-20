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
            "security": ApprovalDecision.PENDING,
            "release": ApprovalDecision.PENDING,
        },
    )


class StateStore:
    def __init__(self, root: Path):
        self.root = root

    def write_current(self, state: AgileDevState) -> Path:
        path = self.root / "state" / "current.json"
        self._write(path, state)
        return path

    def write_checkpoint(self, state: AgileDevState) -> Path:
        path = self.root / "state" / "checkpoints" / state.run_id / f"{state.current_node}.json"
        self._write(path, state)
        return path

    def read_current(self) -> AgileDevState | None:
        path = self.root / "state" / "current.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        data["approvals"] = {
            key: ApprovalDecision(value) for key, value in data.get("approvals", {}).items()
        }
        data.setdefault("source_type", "idea")
        data.setdefault("source_ref", "manual")
        data.setdefault("context_packet_path", "")
        data.setdefault("artifacts", {})
        data.setdefault("blockers", [])
        data.setdefault("model_profile", "balanced")
        data.setdefault("role_models", {})
        return AgileDevState(**data)

    def _write(self, path: Path, state: AgileDevState) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
