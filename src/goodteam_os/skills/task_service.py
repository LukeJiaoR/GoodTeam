"""Project-scoped composition of Team truth, immutable locks and current status.

Only an authorized bounded-task start calls ``begin``. Dispatch adapters must
carry the returned context, call ``snapshot`` with an unchanged logical command
key, and run ``preflight`` before each attempt. This service does not dispatch a
Worker, supply Runtime capability evidence, or authorize a provider invocation.
The web/graph/issue/direct/Manager callers still have to pass their own explicit
bounded identities; there is deliberately no run-id-to-task-id heuristic.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .availability import SkillAvailabilityStore
from .library import SkillLibraryService
from .models import SkillConflict, SkillError, bounded_text, canonical_json
from .task_context import task_skill_context, validate_task_skill_context
from .task_locks import TaskSkillIdentity, TaskSkillLockStore, TaskSkillPlan


class ProjectTaskSkillService:
    """Trusted server composition; store roots never come from job metadata.

    ``supported_loaders`` is a server-owned metadata-loader allowlist, not a
    user/job claim of Runtime support. It defaults to empty (fail closed).
    Even a successful preflight is not the #440/#445 Runtime activation gate.
    """

    def __init__(self, workspace: Path, project_key: str, *, worker_roots: tuple[Path, ...] = (),
                 supported_loaders: frozenset[str] = frozenset()):
        from goodteam_os.service import service_state_dir
        from goodteam_os.state import normalize_project_key
        from goodteam_os.team_control_plane import TeamControlPlaneStore

        bounded_text(project_key, maximum=128)
        if normalize_project_key(project_key) != project_key:
            raise SkillError("task_scope_unknown", "Task Skills require the canonical project key.")
        if (not isinstance(supported_loaders, frozenset)
                or any(not isinstance(item, str) or not item.strip() for item in supported_loaders)):
            raise SkillError("loader_unsupported", "Metadata loaders require an explicit server-owned allowlist.")
        for loader in supported_loaders:
            bounded_text(loader, maximum=128)
        self.project_key = project_key
        self.workspace = Path(workspace).expanduser().resolve()
        roots = tuple(dict.fromkeys((self.workspace, *(Path(root).expanduser().resolve() for root in worker_roots))))
        operator_root = Path(service_state_dir()).expanduser().resolve()
        if any(operator_root.is_relative_to(root) for root in roots):
            raise SkillError("unsafe_store_root", "Operator Skill state cannot be inside a Worker-writable root.")
        self.team = TeamControlPlaneStore(self.workspace, project_key)
        self.packages = SkillLibraryService(operator_root, worker_roots=roots).store
        self.locks = TaskSkillLockStore(operator_root / "skills" / "task-locks", self.packages, worker_roots=roots)
        self.availability = SkillAvailabilityStore(
            operator_root / "skills" / "availability", self.packages, worker_roots=roots,
        )
        self.supported_loaders = supported_loaders

    def _task(self, task: TaskSkillIdentity) -> TaskSkillIdentity:
        if not isinstance(task, TaskSkillIdentity) or task.project_key != self.project_key:
            raise SkillError("snapshot_scope_mismatch", "Bounded task does not belong to this project service.")
        return task

    def _context(self, context: object) -> tuple[dict[str, Any], TaskSkillIdentity]:
        validated = validate_task_skill_context(context)
        task = self._task(TaskSkillIdentity(**validated["task"]))
        if validated["task_lock_ref"]["scope"] != asdict(self.packages.scope):
            raise SkillError("snapshot_scope_mismatch", "Task Skill reference belongs to another Operator scope.")
        return validated, task

    def begin(self, task: TaskSkillIdentity, *, command_key: str, confirmed_input: dict[str, Any],
              subjects: tuple[str, ...], role_contracts: Mapping[str, dict[str, Any]],
              expected_team_revision: int,
              default_items: Mapping[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
        """Reserve/capture/pin/commit once against the confirmation's Team revision.

        The complete subject inventory, thin contracts and supplied default
        package references belong to the confirmed input. Changing them while
        reusing a key conflicts; a retry of identical input never re-reads Team.
        Missing/unavailable methods remain per-subject diagnostics, not fallback.
        """
        self._task(task)
        bounded_text(command_key, maximum=256)
        if (type(expected_team_revision) is not int or expected_team_revision < 0
                or not isinstance(subjects, tuple) or not 1 <= len(subjects) <= 64
                or any(not isinstance(subject, str) for subject in subjects)
                or len(set(subjects)) != len(subjects)):
            raise SkillError("invalid_snapshot", "Task start requires a revision and a bounded unique subject inventory.")
        for subject in subjects:
            bounded_text(subject)
        if (not isinstance(role_contracts, Mapping) or set(role_contracts) != set(subjects)
                or not isinstance(confirmed_input, dict)):
            raise SkillError("invalid_snapshot", "Each confirmed subject requires one thin Role contract.")
        defaults = {} if default_items is None else default_items
        if not isinstance(defaults, Mapping) or set(defaults) - set(subjects):
            raise SkillError("invalid_snapshot", "Default package references must belong to confirmed subjects.")
        request = deepcopy({
            "confirmed_input": confirmed_input,
            "subjects": sorted(subjects),
            "role_contracts": dict(role_contracts),
            "default_items": dict(defaults),
            "expected_team_revision": expected_team_revision,
        })
        try:
            canonical_json(request).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise SkillError("invalid_snapshot", "Confirmed task input must contain finite JSON values.") from exc

        def resolve_plan() -> TaskSkillPlan:
            # One serialized read yields all selected subjects from one Team
            # revision. No per-role latest lookup, lazy migration or Team write.
            with self.team._locked():
                state = self.team.read()
                if state is None or state["project_key"] != self.project_key:
                    raise SkillError("team_unavailable", "Current project Team state is unavailable.")
                if state["revision"] != expected_team_revision:
                    raise SkillConflict("team_revision_changed", "Team changed after task confirmation; review a new task proposal.")
                bindings = state.get("skill_bindings", {})
                if any(subject not in bindings for subject in request["subjects"]):
                    raise SkillError("binding_subject_unknown", "Task inventory contains an unknown Team or workflow subject.")
                selected = {subject: deepcopy(bindings[subject]) for subject in request["subjects"]}
                revision = state["revision"]
            # Only first-time authorized start initializes current-status
            # metadata. A replay/read must not recreate missing revocation data.
            self.availability.initialize()
            return TaskSkillPlan(revision, selected, request["role_contracts"], request["default_items"])

        ref = self.locks.begin(task, command_key=command_key, task_input=request, resolve_plan=resolve_plan)
        return task_skill_context(task, ref)

    def read(self, context: object) -> dict[str, Any]:
        """Read the committed lock; never capture, initialize status, or acquire pins."""
        validated, task = self._context(context)
        return self.locks.read(validated["task_lock_ref"], task=task)

    def snapshot(self, context: object, *, subject: str, command_key: str, job_input: dict[str, Any],
                 loader_version: str, required_skill_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        """Derive a registered job subset without changing the logical job key."""
        validated, task = self._context(context)
        return self.locks.snapshot(
            validated["task_lock_ref"], task=task, subject=subject, command_key=command_key,
            job_input=job_input, loader_version=loader_version, required_skill_ids=required_skill_ids,
        )

    def preflight(self, context: object, snapshot_ref: dict[str, Any], *, command_key: str,
                  job_input: dict[str, Any]) -> dict[str, Any]:
        """Verify immutable input/pins plus trusted CURRENT status on every call.

        Job metadata cannot choose another status reader or widen the supported
        loader set. This returns verified metadata, not Runtime authorization.
        """
        validated, task = self._context(context)
        self.locks.read(validated["task_lock_ref"], task=task)
        result = self.locks.preflight(
            snapshot_ref, task=task, command_key=command_key, job_input=job_input,
            supported_loaders=tuple(sorted(self.supported_loaders)),
            current_availability=self.availability.current_status,
        )
        if result["task_lock_ref"] != validated["task_lock_ref"]:
            raise SkillError("snapshot_scope_mismatch", "Job snapshot is detached from the carried task lock.")
        return result
