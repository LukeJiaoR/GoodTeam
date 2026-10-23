import json
import os
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile

from goodteam_os.skills.task_context import workflow_skill_context

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
    # Immutable Team execution snapshot bound when the run starts. Later Team
    # changes must never rewrite what this run executed with.
    team_snapshot: dict[str, object] = field(default_factory=dict)
    current_node: str = "start"
    next_node: str = "product_requirement"
    load_profile: str = "greenfield"
    pending_transition: dict[str, object] = field(default_factory=dict)
    # A versioned reference to the task-start method lock, never live settings.
    # Empty is the explicit legacy representation; reads do not manufacture locks.
    task_skill_context: dict[str, object] = field(default_factory=dict)
    # Full raw repository input retained for this run; old runs stay unbound.
    repository_baseline: dict[str, object] = field(default_factory=dict)

    artifact_proposals: dict[str, dict[str, str]] = field(default_factory=dict)
    artifact_generations: dict[str, str] = field(default_factory=dict)
    release_candidate: dict[str, object] = field(default_factory=dict)
    # A derived bounded handoff, never an independent source of workflow truth.
    # Legacy reads rebuild it; persisted copies are refreshed on every write.
    continuity_state: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.task_skill_context = _state_skill_context(self)
        from goodteam_os.context_continuity import task_state_context

        self.continuity_state = task_state_context(self)


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
        primary = self.root / "state" / "projects" / key
        if ((primary / "current.json").is_file() or (primary / "readiness.json").is_file()
                or (primary / "archived").is_dir()):
            return primary
        for candidate in self._candidate_project_state_dirs(key):
            if ((candidate / "current.json").is_file() or (candidate / "readiness.json").is_file()
                    or (candidate / "archived").is_dir()):
                return candidate
        return primary

    def _candidate_project_state_dirs(self, key: str) -> list[Path]:
        candidates: list[Path] = []
        try:
            candidates.append(self.root.parent / "state" / "projects" / key)
        except Exception:
            pass
        goodteam_home = os.getenv("GOODTEAM_HOME")
        if goodteam_home:
            candidates.append(Path(goodteam_home).expanduser() / "state" / "projects" / key)
        else:
            candidates.append(Path.home() / ".goodteam" / "state" / "projects" / key)
            candidates.append(Path.home() / "state" / "projects" / key)
        try:
            from goodteam_os.project_registry import ProjectRegistry

            entry = ProjectRegistry().find_project(key)
            if entry:
                sp = entry.get("state_path")
                if sp:
                    candidates.append(Path(sp).expanduser().resolve())
                cp = entry.get("config_path")
                if cp:
                    config_parent = Path(cp).expanduser().resolve().parent
                    candidates.append(config_parent.parent / "state" / "projects" / key)
        except Exception:
            pass
        unique: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            unique.append(candidate)
        return unique

    def write_current(self, state: AgileDevState) -> Path:
        path = self.root / "state" / "current.json"
        self._write(path, state)
        return path

    def write_project_current(self, state: AgileDevState, project_key: str | None = None) -> Path:
        key = normalize_project_key(project_key or state.project_key)
        _state_skill_context(state, project_key=key)
        state.project_key = key
        path = self.project_state_dir(key) / "current.json"
        self._write(path, state)
        return path

    def write_checkpoint(self, state: AgileDevState) -> Path:
        directory = self.root / "state" / "checkpoints" / state.run_id
        return self._write_checkpoint(directory, state)

    def write_project_checkpoint(self, state: AgileDevState, project_key: str | None = None) -> Path:
        key = normalize_project_key(project_key or state.project_key)
        _state_skill_context(state, project_key=key)
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
        state = self._read(self.project_state_dir(project_key) / "current.json")
        if state is not None:
            _state_skill_context(state, project_key=normalize_project_key(project_key))
        return state

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

    def list_project_archived(self, project_key: str) -> list[tuple[Path, AgileDevState]]:
        archive_dir = self.project_state_dir(project_key) / "archived"
        if not archive_dir.is_dir():
            return []
        archived: list[tuple[Path, AgileDevState]] = []
        for path in sorted(
            archive_dir.glob("*.json"),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        ):
            try:
                state = self._read(path)
                if state is not None:
                    _state_skill_context(state, project_key=normalize_project_key(project_key))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            if state is not None:
                archived.append((path, state))
        return archived

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
        data.setdefault("team_snapshot", {})
        data.setdefault("task_skill_context", {})
        data.setdefault("repository_baseline", {})
        data.setdefault("load_profile", "greenfield")
        data.setdefault("pending_transition", {})
        return AgileDevState(**data)

    def _write(self, path: Path, state: AgileDevState) -> None:
        context = _state_skill_context(state)
        from goodteam_os.context_continuity import task_state_context

        state.continuity_state = task_state_context(state)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(state)
        payload["task_skill_context"] = context
        payload["resources"] = resources_to_json(state.resources)
        payload["artifacts"] = legacy_artifacts_view(state.resources) if state.resources else dict(state.artifacts)
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_checkpoint(self, directory: Path, state: AgileDevState) -> Path:
        context = _state_skill_context(state)
        from goodteam_os.context_continuity import task_state_context

        state.continuity_state = task_state_context(state)
        directory.mkdir(parents=True, exist_ok=True)
        while True:
            path = self._next_checkpoint_path(directory, state.current_node)
            sequence = self._checkpoint_sequence(path, state.current_node)
            try:
                with path.open("x", encoding="utf-8") as file:
                    payload = asdict(state)
                    payload["task_skill_context"] = context
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


def _state_skill_context(state: AgileDevState, *, project_key: str | None = None) -> dict[str, object]:
    context = state.task_skill_context
    # Preserve legacy construction even when a caller has not assigned its key yet.
    if isinstance(context, dict) and not context:
        return {}
    return workflow_skill_context(
        context, run_id=state.run_id,
        project_key=normalize_project_key(project_key if project_key is not None else state.project_key),
    )


def normalize_project_key(value: str) -> str:
    key = "".join(char.upper() if char.isalnum() else "-" for char in value.strip())
    key = "-".join(part for part in key.split("-") if part)
    if not key:
        raise ValueError("Project key is required.")
    return key


def _safe_path_part(value: str) -> str:
    part = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(item for item in part.split("-") if item) or "archived"
