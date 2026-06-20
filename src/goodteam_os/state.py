import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

from goodteam_os.resources import (
    ResourceDescriptor,
    derive_resources_from_legacy_artifacts,
    legacy_artifacts_view,
    resources_from_json,
    resources_to_json,
)


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
    delivery_standards: list[str] = field(default_factory=list)
    context_packet_path: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    resources: dict[str, ResourceDescriptor] = field(default_factory=dict)
    approvals: dict[str, ApprovalDecision] = field(default_factory=dict)
    change_requests: dict[str, str] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    model_profile: str = "balanced"
    role_models: dict[str, dict[str, str]] = field(default_factory=dict)
    current_node: str = "start"
    next_node: str = "product_requirement"
    load_profile: str = "greenfield"


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
        directory = self.root / "state" / "checkpoints" / state.run_id
        return self._write_checkpoint(directory, state)

    def write_project_checkpoint(self, state: AgileDevState, project_key: str | None = None) -> Path:
        key = normalize_project_key(project_key or state.project_key)
        state.project_key = key
        directory = self.project_state_dir(key) / "checkpoints" / state.run_id
        return self._write_checkpoint(directory, state)

    def latest_checkpoint_path(self, run_id: str, current_node: str) -> Path | None:
        directory = self.root / "state" / "checkpoints" / run_id
        return self._latest_checkpoint_path(directory, current_node)

    def latest_project_checkpoint_path(
        self,
        project_key: str,
        run_id: str,
        current_node: str,
    ) -> Path | None:
        directory = self.project_state_dir(project_key) / "checkpoints" / run_id
        return self._latest_checkpoint_path(directory, current_node)

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
        data.setdefault("delivery_standards", [])
        data.setdefault("context_packet_path", "")
        data.setdefault("artifacts", {})
        resources_payload = data.get("resources", {})
        if resources_payload:
            data["resources"] = resources_from_json(resources_payload)
        else:
            data["resources"] = derive_resources_from_legacy_artifacts(
                data.get("artifacts", {}),
                run_id=str(data.get("run_id", "")),
                workspace=self.root,
            )
        data["artifacts"] = legacy_artifacts_view(data["resources"])
        data.setdefault("change_requests", {})
        data.setdefault("blockers", [])
        data.setdefault("model_profile", "balanced")
        data.setdefault("role_models", {})
        data.setdefault("load_profile", "greenfield")
        return AgileDevState(**data)

    def _write(self, path: Path, state: AgileDevState) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(state)
        payload["resources"] = resources_to_json(state.resources)
        payload["artifacts"] = legacy_artifacts_view(state.resources) if state.resources else dict(state.artifacts)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _write_checkpoint(self, directory: Path, state: AgileDevState) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        while True:
            path = self._next_checkpoint_path(directory, state.current_node)
            sequence = self._checkpoint_sequence(path, state.current_node)
            try:
                with path.open("x", encoding="utf-8") as file:
                    payload = asdict(state)
                    payload["resources"] = resources_to_json(state.resources)
                    payload["artifacts"] = legacy_artifacts_view(state.resources) if state.resources else dict(state.artifacts)
                    payload["checkpoint"] = {
                        "node": state.current_node,
                        "sequence": sequence,
                    }
                    json.dump(payload, file, indent=2)
                return path
            except FileExistsError:
                continue

    def _next_checkpoint_path(self, directory: Path, current_node: str) -> Path:
        latest = self._latest_checkpoint_path(directory, current_node)
        sequence = self._checkpoint_sequence(latest, current_node) if latest is not None else 0
        return directory / f"{current_node}-{sequence + 1:03d}.json"

    def _latest_checkpoint_path(self, directory: Path, current_node: str) -> Path | None:
        if not directory.exists():
            return None
        candidates = [
            (sequence, path)
            for path in directory.glob("*.json")
            if (sequence := self._checkpoint_sequence(path, current_node)) is not None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _checkpoint_sequence(self, path: Path, current_node: str) -> int | None:
        if path.stem == current_node:
            return 1
        prefix = f"{current_node}-"
        if not path.stem.startswith(prefix):
            return None
        suffix = path.stem[len(prefix) :]
        if not suffix.isdecimal():
            return None
        return int(suffix)


def normalize_project_key(value: str) -> str:
    key = "".join(char.upper() if char.isalnum() else "-" for char in value.strip())
    key = "-".join(part for part in key.split("-") if part)
    if not key:
        raise ValueError("Project key is required.")
    return key


def _safe_path_part(value: str) -> str:
    part = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(item for item in part.split("-") if item) or "archived"
