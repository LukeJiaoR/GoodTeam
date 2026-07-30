"""Non-promoting, snapshot-bound runner for existing-project onboarding.

Track 2A connects Track 1's read-only Repository Context Provider to a bounded
model-tool-loop protocol.  It deliberately does *not* modify readiness, launch a
worker in the target repository, run project commands, or make an onboarding
profile actionable.  A production network adapter and every readiness/review
transition are separate deliveries.

The runner fails closed around interruption boundaries.  It checkpoints consumed
budget before an adapter or Context Provider call.  If the process stops while
one of those calls is in-flight, a later invocation refuses to replay it: doing so
could duplicate an external model charge or silently reset a tool/budget decision.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from goodteam_os.onboarding_backend import (
    ONBOARDING_INVOCATION_SCHEMA,
    ModelProtocolError,
    ModelToolLoopAdapter,
    OnboardingAdapterIdentity,
    OnboardingBackendError,
    OnboardingBackendPreflight,
    OnboardingInvocationEvidence,
    OnboardingToolCall,
    OnboardingToolResult,
    ProfileDraft,
    build_model_turn_request,
    build_sanitized_model_context,
    collect_observation_ids,
    make_tool_result,
    normalize_invocation_evidence,
    parse_model_turn_response,
    validate_adapter_identity,
    validate_backend_preflight,
    validate_profile_draft,
    validate_tool_call,
)
from goodteam_os.onboarding_profile import (
    ProjectProfileError,
    canonicalize_project_profile,
    compute_project_profile_id,
    render_project_profile_projections,
)
from goodteam_os.onboarding_store import (
    OnboardingArtifactStore,
    OnboardingJobStore,
    OnboardingObservationStore,
)
from goodteam_os.project_import import ProjectImportError
from goodteam_os.repository_context_provider import (
    ContextProviderError,
    OnboardingBudget,
    OnboardingBudgetLimits,
    RepositoryContextProvider,
)
from goodteam_os.repository_inventory import build_repository_inventory
from goodteam_os.repository_snapshot import (
    RepositorySnapshot,
    RepositorySnapshotError,
    capture_repository_snapshot,
    verify_repository_snapshot,
)
from goodteam_os.state import normalize_project_key


TRACK_ID = "existing_project_onboarding_track_2a"
RUNNER_CHECKPOINT_SCHEMA = "project_onboarding_runner_checkpoint.v1"
_JOB_ID = re.compile(r"^onboarding_[0-9a-f]{32}$")
_SNAPSHOT_ID = re.compile(r"^snap_[0-9a-f]{64}$")
_OWNER_ID = re.compile(r"^runner_[0-9a-f]{32}$")
_TOOL_CALL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_PROFILE_DRAFT_BYTES = 256 * 1024
_MAX_TOOL_REFERENCES = 80
_CHECKPOINT_PHASES = frozenset(
    {
        "ready",
        "model_response_pending",
        "tool_calls",
        "tool_call_pending",
        "profile_draft",
        "succeeded",
    }
)
_UNCERTAIN_CHECKPOINT_PHASES = frozenset(
    {"model_response_pending", "tool_call_pending"}
)
_RESUMABLE_CHECKPOINT_PHASES = frozenset(
    {"ready", "tool_calls", "profile_draft", "succeeded"}
)


class OnboardingRunnerError(ValueError):
    """A root-safe, stable failure returned by the onboarding runner."""

    def __init__(
        self,
        code: str,
        message: str = "Project onboarding could not continue.",
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class OnboardingJob:
    """A durable, non-promoting onboarding job identity."""

    job_id: str
    snapshot_id: str
    project_key: str
    non_promoting: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "snapshot_id": self.snapshot_id,
            "project_key": self.project_key,
            "non_promoting": self.non_promoting,
        }


@dataclass(frozen=True)
class OnboardingRunResult:
    """The terminal result of one canonical profile publication."""

    job_id: str
    snapshot_id: str
    profile_id: str
    profile: Mapping[str, object]
    non_promoting: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "snapshot_id": self.snapshot_id,
            "profile_id": self.profile_id,
            "profile": _json_copy(self.profile),
            "non_promoting": self.non_promoting,
        }


class ProjectOnboardingRunner:
    """Run a bounded read-only model loop for one imported project.

    The adapter receives only ``ModelTurnRequest`` instances constructed from a
    redacted snapshot/inventory projection.  It never receives ``root``,
    ``workspace``, a shell, or a raw snapshot object.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        project_key: str,
        root: Path,
        adapter: ModelToolLoopAdapter,
        artifact_store: OnboardingArtifactStore | None = None,
        job_store: OnboardingJobStore | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.project_key = normalize_project_key(project_key)
        self.root = Path(root).expanduser().resolve()
        self.adapter = adapter
        self.artifact_store = artifact_store or OnboardingArtifactStore(
            self.workspace,
            self.project_key,
            self.root,
        )
        self.job_store = job_store or OnboardingJobStore(
            self.workspace,
            self.project_key,
            self.root,
        )
        self._adapter_identity()

    def create_job(
        self,
        *,
        requested_limits: OnboardingBudgetLimits | Mapping[str, object] | None = None,
        job_id: str | None = None,
    ) -> OnboardingJob:
        """Capture and persist one immutable onboarding snapshot and job.

        This is intentionally a read-only target operation.  The resulting job is
        bound to the exact snapshot and can only publish workspace-side artifacts.
        """

        identifier = _new_job_id() if job_id is None else _validate_job_id(job_id)
        budget = OnboardingBudget(_copy_requested_limits(requested_limits))
        identity = self._adapter_identity()
        try:
            snapshot = capture_repository_snapshot(self.root, project_key=self.project_key)
            self.artifact_store.publish_snapshot(snapshot)
            inventory = build_repository_inventory(self.root, snapshot)
            self.artifact_store.publish_inventory(inventory)
            self.job_store.create_job(
                identifier,
                snapshot.snapshot_id,
                {
                    "track": TRACK_ID,
                    "non_promoting": True,
                    "adapter_identity": identity.as_dict(),
                    "budget_limits": budget.limits.as_dict(),
                    "inventory_digest": inventory.get("inventory_digest"),
                },
            )
            context = build_sanitized_model_context(
                snapshot=snapshot,
                inventory=inventory,
                budget=budget.as_dict(),
                forbidden_texts=self._model_forbidden_texts(),
            )
            self.job_store.publish_prompt(
                identifier,
                snapshot.snapshot_id,
                {
                    "runner_schema": "project_onboarding_prompt.v1",
                    "track": TRACK_ID,
                    "context": context,
                },
            )
            owner, lease_token = self._acquire_lease(identifier, snapshot.snapshot_id)
            try:
                checkpoint = _initial_checkpoint(budget)
                self._write_checkpoint(
                    identifier,
                    snapshot.snapshot_id,
                    checkpoint,
                    owner=owner,
                    lease_token=lease_token,
                )
                self._write_status(
                    identifier,
                    snapshot.snapshot_id,
                    state="created",
                    phase="ready",
                    budget=budget,
                    owner=owner,
                    lease_token=lease_token,
                )
            finally:
                self._release_lease(identifier, snapshot.snapshot_id, owner, lease_token)
        except Exception as exc:
            raise _runner_error(exc) from None
        return OnboardingJob(
            job_id=identifier,
            snapshot_id=snapshot.snapshot_id,
            project_key=self.project_key,
        )

    def run(self, job_id: str) -> OnboardingRunResult:
        """Advance a previously created job until it publishes one profile.

        Calls that were in flight at interruption time are not replayed.  Their
        checkpoint remains inspectable and the job is marked recovery-required so
        an operator can decide how to reconcile the external side effect.
        """

        identifier = _validate_job_id(job_id)
        job: dict[str, object] | None = None
        snapshot: RepositorySnapshot | None = None
        budget: OnboardingBudget | None = None
        checkpoint: dict[str, object] | None = None
        preflight: OnboardingBackendPreflight | None = None
        owner = ""
        lease_token = ""
        lease_acquired = False
        try:
            job = self.job_store.read_job(identifier)
            snapshot_id = _required_snapshot_id(job.get("snapshot_id"))
            self._validate_job_contract(job, snapshot_id)
            snapshot = self.artifact_store.verify_snapshot(snapshot_id)
            inventory = self._read_inventory(snapshot_id)
            self._validate_inventory_contract(job, inventory, snapshot_id)
            self._verify_live_snapshot(snapshot)

            status = self.job_store.read_status(identifier, snapshot_id)
            if status is not None and status.get("state") == "succeeded":
                return self._read_succeeded_result(identifier, snapshot_id, status)
            if status is not None and status.get("state") == "failed":
                failed_phase = self._persisted_checkpoint_phase(
                    identifier,
                    snapshot_id,
                    fallback=None,
                )
                if failed_phase not in _RESUMABLE_CHECKPOINT_PHASES:
                    raise OnboardingRunnerError(
                        "onboarding_job_terminal",
                        "This onboarding job is terminal and cannot be restarted.",
                    )

            owner, lease_token = self._acquire_lease(identifier, snapshot_id)
            lease_acquired = True
            checkpoint = self._read_checkpoint(identifier, snapshot_id, job)
            budget = checkpoint["budget"]
            if not isinstance(budget, OnboardingBudget):  # pragma: no cover - internal guard
                raise OnboardingRunnerError("onboarding_checkpoint_invalid")

            phase = str(checkpoint["phase"])
            if phase in _UNCERTAIN_CHECKPOINT_PHASES:
                self._write_status(
                    identifier,
                    snapshot_id,
                    state="recovery_required",
                    phase=phase,
                    budget=budget,
                    owner=owner,
                    lease_token=lease_token,
                    failure_code="onboarding_operation_uncertain",
                )
                raise OnboardingRunnerError(
                    "onboarding_operation_uncertain",
                    "An onboarding operation was interrupted after its budget was reserved.",
                    details={"phase": phase},
                )

            if phase in {"profile_draft", "succeeded"}:
                preflight = self._checkpoint_preflight(checkpoint.get("preflight"))
            else:
                preflight = self._preflight()

            if phase == "succeeded":
                return self._repair_succeeded_result(
                    identifier,
                    snapshot_id,
                    budget=budget,
                    owner=owner,
                    lease_token=lease_token,
                    preflight=preflight,
                )

            observation_store = OnboardingObservationStore(
                self.workspace,
                self.project_key,
                self.root,
                identifier,
                snapshot_id,
            )
            provider = RepositoryContextProvider(
                workspace=self.workspace,
                project_key=self.project_key,
                root=self.root,
                snapshot=snapshot,
                observation_store=observation_store,
                budget=budget,
            )
            self._write_status(
                identifier,
                snapshot_id,
                state="running",
                phase=phase,
                budget=budget,
                owner=owner,
                lease_token=lease_token,
                preflight=preflight,
            )
            return self._advance(
                job_id=identifier,
                snapshot=snapshot,
                inventory=inventory,
                budget=budget,
                checkpoint=checkpoint,
                observation_store=observation_store,
                provider=provider,
                owner=owner,
                lease_token=lease_token,
                preflight=preflight,
            )
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            failure = _runner_error(exc)
            if (
                lease_acquired
                and job is not None
                and snapshot is not None
                and owner
                and lease_token
            ):
                try:
                    persisted_phase = self._persisted_checkpoint_phase(
                        identifier,
                        snapshot.snapshot_id,
                        fallback=checkpoint,
                    )
                    recovery_required = persisted_phase in _UNCERTAIN_CHECKPOINT_PHASES
                    self._write_status(
                        identifier,
                        snapshot.snapshot_id,
                        state="recovery_required" if recovery_required else "failed",
                        phase=persisted_phase or "failed",
                        budget=budget,
                        owner=owner,
                        lease_token=lease_token,
                        preflight=preflight,
                        failure_code=failure.code,
                    )
                except Exception:
                    # A changed target binding can make workspace records unsafe to
                    # update.  Preserve the original, root-safe failure instead.
                    pass
            raise failure from None
        finally:
            if lease_acquired and snapshot is not None and owner and lease_token:
                self._release_lease(
                    identifier,
                    snapshot.snapshot_id,
                    owner,
                    lease_token,
                )

    def _advance(
        self,
        *,
        job_id: str,
        snapshot: RepositorySnapshot,
        inventory: Mapping[str, object],
        budget: OnboardingBudget,
        checkpoint: dict[str, object],
        observation_store: OnboardingObservationStore,
        provider: RepositoryContextProvider,
        owner: str,
        lease_token: str,
        preflight: OnboardingBackendPreflight,
    ) -> OnboardingRunResult:
        snapshot_id = snapshot.snapshot_id
        while True:
            phase = str(checkpoint["phase"])
            if phase == "succeeded":
                return self._repair_succeeded_result(
                    job_id,
                    snapshot_id,
                    budget=budget,
                    owner=owner,
                    lease_token=lease_token,
                    preflight=preflight,
                )
            if phase == "profile_draft":
                return self._publish_profile(
                    job_id=job_id,
                    snapshot=snapshot,
                    observation_store=observation_store,
                    budget=budget,
                    checkpoint=checkpoint,
                    owner=owner,
                    lease_token=lease_token,
                    preflight=preflight,
                )
            if phase == "tool_calls":
                checkpoint = self._complete_pending_tool_calls(
                    job_id=job_id,
                    snapshot=snapshot,
                    budget=budget,
                    checkpoint=checkpoint,
                    observation_store=observation_store,
                    provider=provider,
                    owner=owner,
                    lease_token=lease_token,
                    preflight=preflight,
                )
                continue
            if phase != "ready":
                raise OnboardingRunnerError(
                    "onboarding_checkpoint_invalid",
                    "Onboarding checkpoint has an unsupported state.",
                )

            turn = _required_positive_integer(checkpoint.get("next_turn"), "next_turn")
            prior_results = self._load_prior_tool_results(
                checkpoint["prior_tool_refs"],
                observation_store=observation_store,
                snapshot_id=snapshot_id,
            )
            budget.begin_model_turn(turn)
            checkpoint = {
                **checkpoint,
                "phase": "model_response_pending",
                "active_turn": turn,
                "budget": budget.checkpoint(),
            }
            self._write_checkpoint(
                job_id,
                snapshot_id,
                checkpoint,
                owner=owner,
                lease_token=lease_token,
            )
            self._write_status(
                job_id,
                snapshot_id,
                state="running",
                phase="model_response_pending",
                budget=budget,
                owner=owner,
                lease_token=lease_token,
                preflight=preflight,
            )
            context = build_sanitized_model_context(
                snapshot=snapshot,
                inventory=inventory,
                budget=budget.as_dict(),
                forbidden_texts=self._model_forbidden_texts(),
            )
            request = build_model_turn_request(
                turn=turn,
                context=context,
                prior_tool_results=prior_results,
            )
            self._renew_lease(job_id, snapshot_id, owner, lease_token)
            self._verify_live_snapshot(snapshot)
            raw_response = self.adapter.next_turn(request)
            allowed_observation_ids = collect_observation_ids(
                prior_results,
                snapshot_id=snapshot_id,
            )
            response = parse_model_turn_response(
                raw_response,
                identity=self._adapter_identity(),
                snapshot_id=snapshot_id,
                allowed_observation_ids=allowed_observation_ids,
                forbidden_texts=self._model_forbidden_texts(),
            )
            self._record_model_usage(budget, turn, response.usage)
            self._append_event(
                job_id,
                snapshot_id,
                {
                    "event": "model_response",
                    "turn": turn,
                    "response": _response_event_summary(response),
                },
                owner=owner,
                lease_token=lease_token,
            )
            if response.tool_calls:
                checkpoint = {
                    **checkpoint,
                    "phase": "tool_calls",
                    "active_turn": turn,
                    "pending_tool_calls": [call.as_dict() for call in response.tool_calls],
                    "profile_draft": None,
                    "invocation": None,
                    "preflight": None,
                    "profile_created_at": None,
                    "profile_id": None,
                    "budget": budget.checkpoint(),
                }
                self._write_checkpoint(
                    job_id,
                    snapshot_id,
                    checkpoint,
                    owner=owner,
                    lease_token=lease_token,
                )
                self._write_status(
                    job_id,
                    snapshot_id,
                    state="running",
                    phase="tool_calls",
                    budget=budget,
                    owner=owner,
                    lease_token=lease_token,
                    preflight=preflight,
                )
                continue

            if response.profile_draft is None:  # pragma: no cover - protocol guard
                raise OnboardingRunnerError("model_response_invalid")
            draft_payload = response.profile_draft.as_dict()
            _assert_bounded_json(draft_payload, maximum=_MAX_PROFILE_DRAFT_BYTES)
            checkpoint = {
                **checkpoint,
                "phase": "profile_draft",
                "active_turn": turn,
                "pending_tool_calls": [],
                "profile_draft": draft_payload,
                "invocation": response.invocation.as_dict(),
                "preflight": preflight.as_dict(),
                "profile_created_at": _now(),
                "budget": budget.checkpoint(),
            }
            self._write_checkpoint(
                job_id,
                snapshot_id,
                checkpoint,
                owner=owner,
                lease_token=lease_token,
            )
            self._write_status(
                job_id,
                snapshot_id,
                state="running",
                phase="profile_draft",
                budget=budget,
                owner=owner,
                lease_token=lease_token,
                preflight=preflight,
            )

    def _complete_pending_tool_calls(
        self,
        *,
        job_id: str,
        snapshot: RepositorySnapshot,
        budget: OnboardingBudget,
        checkpoint: dict[str, object],
        observation_store: OnboardingObservationStore,
        provider: RepositoryContextProvider,
        owner: str,
        lease_token: str,
        preflight: OnboardingBackendPreflight,
    ) -> dict[str, object]:
        snapshot_id = snapshot.snapshot_id
        active_turn = _required_positive_integer(checkpoint.get("active_turn"), "active_turn")
        pending = _validated_tool_calls(checkpoint.get("pending_tool_calls"))
        prior_refs = _validated_tool_references(checkpoint.get("prior_tool_refs"))
        while pending:
            call = pending[0]
            in_flight = {
                **checkpoint,
                "phase": "tool_call_pending",
                "active_tool_call": call.as_dict(),
                "pending_tool_calls": [item.as_dict() for item in pending],
                "prior_tool_refs": prior_refs,
                "budget": budget.checkpoint(),
            }
            self._write_checkpoint(
                job_id,
                snapshot_id,
                in_flight,
                owner=owner,
                lease_token=lease_token,
            )
            self._write_status(
                job_id,
                snapshot_id,
                state="running",
                phase="tool_call_pending",
                budget=budget,
                owner=owner,
                lease_token=lease_token,
                preflight=preflight,
            )
            self._renew_lease(job_id, snapshot_id, owner, lease_token)
            envelope = provider.call(
                call.name,
                dict(call.arguments),
                model_turn=active_turn,
            )
            result = make_tool_result(
                call,
                envelope,
                snapshot_id=snapshot_id,
                forbidden_texts=self._model_forbidden_texts(),
            )
            provider_call_id = envelope.get("tool_call_id")
            if not isinstance(provider_call_id, str) or _TOOL_CALL_ID.fullmatch(provider_call_id) is None:
                raise OnboardingRunnerError("model_tool_result_invalid")
            prior_refs = [
                *prior_refs,
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": _json_copy(call.arguments),
                    "provider_tool_call_id": provider_call_id,
                },
            ]
            if len(prior_refs) > _MAX_TOOL_REFERENCES:
                raise OnboardingRunnerError("onboarding_checkpoint_invalid")
            pending = pending[1:]
            self._append_event(
                job_id,
                snapshot_id,
                {
                    "event": "tool_result",
                    "turn": active_turn,
                    "call_id": call.call_id,
                    "name": call.name,
                    "provider_tool_call_id": provider_call_id,
                    "status": result.result.get("status"),
                    "observation_count": len(result.result.get("observations", [])),
                },
                owner=owner,
                lease_token=lease_token,
            )
            checkpoint = {
                **checkpoint,
                "phase": "tool_calls" if pending else "ready",
                "active_turn": active_turn if pending else None,
                "active_tool_call": None,
                "pending_tool_calls": [item.as_dict() for item in pending],
                "prior_tool_refs": prior_refs,
                "next_turn": active_turn if pending else active_turn + 1,
                "budget": budget.checkpoint(),
            }
            self._write_checkpoint(
                job_id,
                snapshot_id,
                checkpoint,
                owner=owner,
                lease_token=lease_token,
            )
            self._write_status(
                job_id,
                snapshot_id,
                state="running",
                phase=str(checkpoint["phase"]),
                budget=budget,
                owner=owner,
                lease_token=lease_token,
                preflight=preflight,
            )
        return checkpoint

    def _publish_profile(
        self,
        *,
        job_id: str,
        snapshot: RepositorySnapshot,
        observation_store: OnboardingObservationStore,
        budget: OnboardingBudget,
        checkpoint: dict[str, object],
        owner: str,
        lease_token: str,
        preflight: OnboardingBackendPreflight,
    ) -> OnboardingRunResult:
        snapshot_id = snapshot.snapshot_id
        raw_draft = checkpoint.get("profile_draft")
        invocation = self._checkpoint_invocation(checkpoint.get("invocation"))
        preflight = self._checkpoint_preflight(checkpoint.get("preflight"))
        prior_results = self._load_prior_tool_results(
            checkpoint.get("prior_tool_refs"),
            observation_store=observation_store,
            snapshot_id=snapshot_id,
        )
        draft = validate_profile_draft(
            raw_draft,
            snapshot_id=snapshot_id,
            allowed_observation_ids=collect_observation_ids(
                prior_results,
                snapshot_id=snapshot_id,
            ),
            forbidden_texts=self._model_forbidden_texts(),
        )
        self._verify_live_snapshot(snapshot)
        profile_id = compute_project_profile_id(snapshot_id, job_id)
        profile_source = _enrich_profile_draft(
            draft,
            profile_id=profile_id,
            project_key=self.project_key,
            job_id=job_id,
            invocation=invocation,
            created_at=_required_profile_timestamp(
                checkpoint.get("profile_created_at")
            ),
        )
        canonical = canonicalize_project_profile(
            profile_source,
            snapshot=snapshot,
            observations=observation_store.read_all(),
            model_output=True,
        )
        projections = render_project_profile_projections(canonical)
        self._renew_lease(job_id, snapshot_id, owner, lease_token)
        self.job_store.publish_profile(
            profile_id,
            job_id,
            snapshot_id,
            canonical,
            {
                "track": TRACK_ID,
                "non_promoting": True,
                "readiness_promotion": "not_available",
                "adapter_identity": self._adapter_identity().as_dict(),
                "invocation": invocation.as_dict(),
                "preflight": preflight.as_dict(),
                "projection_digests": {
                    name: _sha256_text(value) for name, value in projections.items()
                },
            },
            owner=owner,
            lease_token=lease_token,
        )
        checkpoint = {
            **checkpoint,
            "phase": "succeeded",
            "profile_id": profile_id,
            "budget": budget.checkpoint(),
        }
        self._write_checkpoint(
            job_id,
            snapshot_id,
            checkpoint,
            owner=owner,
            lease_token=lease_token,
        )
        self._write_status(
            job_id,
            snapshot_id,
            state="succeeded",
            phase="succeeded",
            budget=budget,
            owner=owner,
            lease_token=lease_token,
            preflight=preflight,
            profile_id=profile_id,
        )
        return OnboardingRunResult(
            job_id=job_id,
            snapshot_id=snapshot_id,
            profile_id=profile_id,
            profile=canonical,
        )

    def _adapter_identity(self) -> OnboardingAdapterIdentity:
        try:
            return validate_adapter_identity(getattr(self.adapter, "identity"))
        except Exception as exc:
            raise _runner_error(exc) from None

    def _preflight(self) -> OnboardingBackendPreflight:
        try:
            report = validate_backend_preflight(
                self.adapter.preflight(),
                identity=self._adapter_identity(),
            )
        except Exception as exc:
            raise _runner_error(exc) from None
        if not (
            report.available
            and report.tool_loop_capable
            and report.read_only_runtime
        ):
            raise OnboardingRunnerError(
                "onboarding_backend_unavailable",
                "The configured onboarding backend is not available for a read-only tool loop.",
            )
        return report

    def _validate_job_contract(
        self,
        job: Mapping[str, object],
        snapshot_id: str,
    ) -> None:
        if job.get("track") != TRACK_ID or job.get("non_promoting") is not True:
            raise OnboardingRunnerError("onboarding_job_invalid")
        identity = job.get("adapter_identity")
        if not isinstance(identity, Mapping) or dict(identity) != self._adapter_identity().as_dict():
            raise OnboardingRunnerError(
                "onboarding_adapter_mismatch",
                "The configured onboarding adapter does not match this job.",
            )
        _validate_job_id(job.get("job_id"))
        limits = job.get("budget_limits")
        if not isinstance(limits, Mapping):
            raise OnboardingRunnerError("onboarding_job_invalid")
        # Constructing a budget checks every requested value against server ceilings.
        OnboardingBudget(dict(limits))

    def _validate_inventory_contract(
        self,
        job: Mapping[str, object],
        inventory: Mapping[str, object],
        snapshot_id: str,
    ) -> None:
        if inventory.get("snapshot_id") != snapshot_id:
            raise OnboardingRunnerError("onboarding_snapshot_mismatch")
        expected = job.get("inventory_digest")
        if not isinstance(expected, str) or inventory.get("inventory_digest") != expected:
            raise OnboardingRunnerError("onboarding_inventory_mismatch")

    def _read_inventory(self, snapshot_id: str) -> dict[str, object]:
        try:
            inventory = self.artifact_store.read_inventory(snapshot_id)
        except AttributeError as exc:  # pragma: no cover - protects incompatible stores
            raise OnboardingRunnerError("onboarding_inventory_missing") from exc
        if not isinstance(inventory, dict):
            raise OnboardingRunnerError("onboarding_inventory_invalid")
        return inventory

    def _read_checkpoint(
        self,
        job_id: str,
        snapshot_id: str,
        job: Mapping[str, object],
    ) -> dict[str, object]:
        stored = self.job_store.read_checkpoint(job_id, snapshot_id)
        if stored is None:
            raise OnboardingRunnerError("onboarding_checkpoint_missing")
        if stored.get("runner_schema") != RUNNER_CHECKPOINT_SCHEMA:
            raise OnboardingRunnerError("onboarding_checkpoint_invalid")
        phase = stored.get("phase")
        if phase not in _CHECKPOINT_PHASES:
            raise OnboardingRunnerError("onboarding_checkpoint_invalid")
        budget_payload = stored.get("budget")
        try:
            budget = OnboardingBudget.from_checkpoint(budget_payload)
        except ContextProviderError as exc:
            raise OnboardingRunnerError(exc.code) from None
        limits = job.get("budget_limits")
        if not isinstance(limits, Mapping) or budget.limits.as_dict() != dict(limits):
            raise OnboardingRunnerError("onboarding_checkpoint_invalid")
        checkpoint = {
            "runner_schema": RUNNER_CHECKPOINT_SCHEMA,
            "phase": str(phase),
            "next_turn": _required_positive_integer(stored.get("next_turn"), "next_turn"),
            "active_turn": _optional_positive_integer(stored.get("active_turn"), "active_turn"),
            "active_tool_call": stored.get("active_tool_call"),
            "pending_tool_calls": stored.get("pending_tool_calls", []),
            "prior_tool_refs": stored.get("prior_tool_refs", []),
            "profile_draft": stored.get("profile_draft"),
            "invocation": stored.get("invocation"),
            "preflight": stored.get("preflight"),
            "profile_created_at": stored.get("profile_created_at"),
            "profile_id": stored.get("profile_id"),
            "budget": budget,
        }
        _validated_tool_references(checkpoint["prior_tool_refs"])
        if checkpoint["phase"] == "tool_calls":
            _validated_tool_calls(checkpoint["pending_tool_calls"])
            if checkpoint["active_turn"] is None:
                raise OnboardingRunnerError("onboarding_checkpoint_invalid")
        elif checkpoint["phase"] == "tool_call_pending":
            _validated_tool_calls(checkpoint["pending_tool_calls"])
            validate_tool_call(checkpoint["active_tool_call"])
        elif checkpoint["phase"] == "profile_draft":
            if (
                checkpoint["profile_draft"] is None
                or checkpoint["invocation"] is None
                or checkpoint["preflight"] is None
                or checkpoint["profile_created_at"] is None
            ):
                raise OnboardingRunnerError("onboarding_checkpoint_invalid")
            _required_profile_timestamp(checkpoint["profile_created_at"])
            self._checkpoint_preflight(checkpoint["preflight"])
        elif checkpoint["phase"] == "succeeded":
            profile_id = checkpoint["profile_id"]
            expected = compute_project_profile_id(snapshot_id, job_id)
            if profile_id != expected:
                raise OnboardingRunnerError("onboarding_checkpoint_invalid")
            _required_profile_timestamp(checkpoint["profile_created_at"])
            self._checkpoint_preflight(checkpoint["preflight"])
        return checkpoint

    def _checkpoint_invocation(self, value: object) -> OnboardingInvocationEvidence:
        try:
            if isinstance(value, Mapping):
                payload = dict(value)
                if payload.pop("schema", None) != ONBOARDING_INVOCATION_SCHEMA:
                    raise OnboardingRunnerError("onboarding_checkpoint_invalid")
                value = payload
            return normalize_invocation_evidence(
                value,
                identity=self._adapter_identity(),
            )
        except Exception as exc:
            raise _runner_error(exc) from None

    def _checkpoint_preflight(self, value: object) -> OnboardingBackendPreflight:
        try:
            return validate_backend_preflight(
                value,
                identity=self._adapter_identity(),
            )
        except Exception as exc:
            raise _runner_error(exc) from None

    def _model_forbidden_texts(self) -> tuple[str, str]:
        return (str(self.root), str(self.workspace))

    def _verify_live_snapshot(self, snapshot: RepositorySnapshot) -> None:
        verify_repository_snapshot(self.root, snapshot)
        published_snapshot = self.artifact_store.verify_snapshot(snapshot.snapshot_id)
        if published_snapshot.as_dict() != snapshot.as_dict():
            raise OnboardingRunnerError(
                "onboarding_snapshot_mismatch",
                "Published onboarding snapshot does not match this job.",
            )

    def _load_prior_tool_results(
        self,
        refs: object,
        *,
        observation_store: OnboardingObservationStore,
        snapshot_id: str,
    ) -> list[OnboardingToolResult]:
        references = _validated_tool_references(refs)
        envelopes = observation_store.read_all()
        by_provider_id: dict[str, dict[str, object]] = {}
        for envelope in envelopes:
            provider_id = envelope.get("tool_call_id")
            if not isinstance(provider_id, str) or provider_id in by_provider_id:
                raise OnboardingRunnerError("onboarding_observation_invalid")
            by_provider_id[provider_id] = envelope
        results: list[OnboardingToolResult] = []
        for ref in references:
            provider_id = str(ref["provider_tool_call_id"])
            envelope = by_provider_id.get(provider_id)
            if envelope is None:
                raise OnboardingRunnerError("onboarding_checkpoint_invalid")
            call = validate_tool_call(
                {
                    "id": ref["call_id"],
                    "name": ref["name"],
                    "arguments": ref["arguments"],
                }
            )
            result = make_tool_result(
                call,
                envelope,
                snapshot_id=snapshot_id,
                forbidden_texts=self._model_forbidden_texts(),
            )
            results.append(result)
        return results

    def _record_model_usage(self, budget: OnboardingBudget, turn: int, usage) -> None:
        arguments: dict[str, object] = {"turn_number": turn}
        if usage.authoritative:
            arguments["actual_input_tokens"] = usage.input_tokens
            arguments["actual_output_tokens"] = usage.output_tokens
        else:
            arguments["estimated_input_tokens"] = usage.input_tokens
            arguments["estimated_output_tokens"] = usage.output_tokens
        arguments["cost_usd"] = usage.cost_usd
        budget.record_model_usage(**arguments)  # type: ignore[arg-type]

    def _append_event(
        self,
        job_id: str,
        snapshot_id: str,
        event: dict[str, object],
        *,
        owner: str,
        lease_token: str,
    ) -> None:
        self.job_store.append_model_event(
            job_id,
            snapshot_id,
            event,
            owner=owner,
            lease_token=lease_token,
        )

    def _write_checkpoint(
        self,
        job_id: str,
        snapshot_id: str,
        checkpoint: Mapping[str, object],
        *,
        owner: str,
        lease_token: str,
    ) -> None:
        payload = _checkpoint_payload(checkpoint)
        self.job_store.write_checkpoint(
            job_id,
            snapshot_id,
            payload,
            owner=owner,
            lease_token=lease_token,
        )

    def _write_status(
        self,
        job_id: str,
        snapshot_id: str,
        *,
        state: str,
        phase: str,
        budget: OnboardingBudget | None,
        owner: str,
        lease_token: str,
        preflight: OnboardingBackendPreflight | None = None,
        failure_code: str | None = None,
        profile_id: str | None = None,
    ) -> None:
        if state not in {"created", "running", "succeeded", "failed", "recovery_required"}:
            raise OnboardingRunnerError("onboarding_status_invalid")
        payload: dict[str, object] = {
            "runner_schema": "project_onboarding_status.v1",
            "track": TRACK_ID,
            "non_promoting": True,
            "state": state,
            "phase": phase,
        }
        if budget is not None:
            payload["budget"] = budget.as_dict()
        if preflight is not None:
            payload["preflight"] = preflight.as_dict()
        if failure_code is not None:
            payload["failure_code"] = failure_code
        if profile_id is not None:
            payload["profile_id"] = profile_id
        self.job_store.write_status(
            job_id,
            snapshot_id,
            payload,
            owner=owner,
            lease_token=lease_token,
        )

    def _acquire_lease(self, job_id: str, snapshot_id: str) -> tuple[str, str]:
        owner = "runner_" + uuid.uuid4().hex
        if _OWNER_ID.fullmatch(owner) is None:  # pragma: no cover - UUID invariant
            raise OnboardingRunnerError("onboarding_lease_invalid")
        lease = self.job_store.acquire_lease(job_id, snapshot_id, owner)
        token = lease.get("lease_token")
        if not isinstance(token, str):
            raise OnboardingRunnerError("onboarding_lease_invalid")
        return owner, token

    def _release_lease(
        self,
        job_id: str,
        snapshot_id: str,
        owner: str,
        lease_token: str,
    ) -> None:
        try:
            self.job_store.release_lease(job_id, snapshot_id, owner, lease_token)
        except Exception:
            # Lease expiry/replacement must not overwrite the primary outcome.
            return

    def _renew_lease(
        self,
        job_id: str,
        snapshot_id: str,
        owner: str,
        lease_token: str,
    ) -> None:
        self.job_store.renew_lease(job_id, snapshot_id, owner, lease_token)

    def _read_succeeded_result(
        self,
        job_id: str,
        snapshot_id: str,
        status: Mapping[str, object],
    ) -> OnboardingRunResult:
        profile_id = status.get("profile_id")
        expected = compute_project_profile_id(snapshot_id, job_id)
        if profile_id != expected:
            raise OnboardingRunnerError("onboarding_status_invalid")
        bundle = self.job_store.read_profile(expected, job_id, snapshot_id)
        profile = bundle.get("profile")
        if not isinstance(profile, dict):
            raise OnboardingRunnerError("onboarding_profile_invalid")
        return OnboardingRunResult(
            job_id=job_id,
            snapshot_id=snapshot_id,
            profile_id=expected,
            profile=profile,
        )

    def _repair_succeeded_result(
        self,
        job_id: str,
        snapshot_id: str,
        *,
        budget: OnboardingBudget,
        owner: str,
        lease_token: str,
        preflight: OnboardingBackendPreflight,
    ) -> OnboardingRunResult:
        expected = compute_project_profile_id(snapshot_id, job_id)
        bundle = self.job_store.read_profile(expected, job_id, snapshot_id)
        profile = bundle.get("profile")
        if not isinstance(profile, dict):
            raise OnboardingRunnerError("onboarding_profile_invalid")
        self._write_status(
            job_id,
            snapshot_id,
            state="succeeded",
            phase="succeeded",
            budget=budget,
            owner=owner,
            lease_token=lease_token,
            preflight=preflight,
            profile_id=expected,
        )
        return OnboardingRunResult(
            job_id=job_id,
            snapshot_id=snapshot_id,
            profile_id=expected,
            profile=profile,
        )

    def _persisted_checkpoint_phase(
        self,
        job_id: str,
        snapshot_id: str,
        *,
        fallback: Mapping[str, object] | None,
    ) -> str | None:
        try:
            stored = self.job_store.read_checkpoint(job_id, snapshot_id)
            phase = stored.get("phase") if stored is not None else None
            if phase in _CHECKPOINT_PHASES:
                return str(phase)
        except Exception:
            pass
        if fallback is not None and fallback.get("phase") in _CHECKPOINT_PHASES:
            return str(fallback["phase"])
        return None


def _initial_checkpoint(budget: OnboardingBudget) -> dict[str, object]:
    return {
        "runner_schema": RUNNER_CHECKPOINT_SCHEMA,
        "phase": "ready",
        "next_turn": 1,
        "active_turn": None,
        "active_tool_call": None,
        "pending_tool_calls": [],
        "prior_tool_refs": [],
        "profile_draft": None,
        "invocation": None,
        "preflight": None,
        "profile_created_at": None,
        "profile_id": None,
        "budget": budget.checkpoint(),
    }


def _checkpoint_payload(checkpoint: Mapping[str, object]) -> dict[str, object]:
    budget = checkpoint.get("budget")
    budget_payload = budget.checkpoint() if isinstance(budget, OnboardingBudget) else budget
    payload = {
        "runner_schema": checkpoint.get("runner_schema"),
        "phase": checkpoint.get("phase"),
        "next_turn": checkpoint.get("next_turn"),
        "active_turn": checkpoint.get("active_turn"),
        "active_tool_call": _json_copy(checkpoint.get("active_tool_call")),
        "pending_tool_calls": _json_copy(checkpoint.get("pending_tool_calls", [])),
        "prior_tool_refs": _json_copy(checkpoint.get("prior_tool_refs", [])),
        "profile_draft": _json_copy(checkpoint.get("profile_draft")),
        "invocation": _json_copy(checkpoint.get("invocation")),
        "preflight": _json_copy(checkpoint.get("preflight")),
        "profile_created_at": checkpoint.get("profile_created_at"),
        "profile_id": checkpoint.get("profile_id"),
        "budget": _json_copy(budget_payload),
    }
    _assert_bounded_json(payload, maximum=480 * 1024)
    return payload


def _enrich_profile_draft(
    draft: ProfileDraft,
    *,
    profile_id: str,
    project_key: str,
    job_id: str,
    invocation: OnboardingInvocationEvidence,
    created_at: str,
) -> dict[str, object]:
    source = draft.as_dict()
    source.update(
        {
            "profile_id": profile_id,
            "project_key": project_key,
            "snapshot_id": draft.snapshot_id,
            "previous_profile_id": None,
            "generated_by": {
                "job_id": job_id,
                "adapter": invocation.identity.adapter,
                "provider": invocation.identity.provider,
                "model": invocation.identity.model,
                "external_invocation": invocation.identity.external_invocation,
            },
            "created_at": created_at,
        }
    )
    return source


def _response_event_summary(response) -> dict[str, object]:
    if response.tool_calls:
        payload: dict[str, object] = {
            "kind": "tool_calls",
            "tool_calls": [
                {"call_id": call.call_id, "name": call.name}
                for call in response.tool_calls
            ],
        }
    else:
        draft = response.profile_draft
        claims = draft.payload.get("claims", []) if draft is not None else []
        payload = {"kind": "profile_draft", "claim_count": len(claims)}
    payload["usage"] = response.usage.as_dict()
    payload["invocation"] = response.invocation.as_dict()
    return payload


def _validated_tool_calls(value: object) -> list[OnboardingToolCall]:
    if not isinstance(value, list) or len(value) > _MAX_TOOL_REFERENCES:
        raise OnboardingRunnerError("onboarding_checkpoint_invalid")
    calls = [validate_tool_call(item) for item in value]
    if len({call.call_id for call in calls}) != len(calls):
        raise OnboardingRunnerError("onboarding_checkpoint_invalid")
    return calls


def _validated_tool_references(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > _MAX_TOOL_REFERENCES:
        raise OnboardingRunnerError("onboarding_checkpoint_invalid")
    refs: list[dict[str, object]] = []
    call_ids: set[str] = set()
    provider_ids: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "call_id",
            "name",
            "arguments",
            "provider_tool_call_id",
        }:
            raise OnboardingRunnerError("onboarding_checkpoint_invalid")
        call_id = item.get("call_id")
        name = item.get("name")
        arguments = item.get("arguments")
        provider_id = item.get("provider_tool_call_id")
        if (
            not isinstance(call_id, str)
            or _TOOL_CALL_ID.fullmatch(call_id) is None
            or not isinstance(provider_id, str)
            or _TOOL_CALL_ID.fullmatch(provider_id) is None
        ):
            raise OnboardingRunnerError("onboarding_checkpoint_invalid")
        try:
            call = validate_tool_call(
                {"id": call_id, "name": name, "arguments": arguments}
            )
        except (ModelProtocolError, OnboardingBackendError) as exc:
            raise OnboardingRunnerError("onboarding_checkpoint_invalid") from exc
        if call_id in call_ids or provider_id in provider_ids:
            raise OnboardingRunnerError("onboarding_checkpoint_invalid")
        call_ids.add(call_id)
        provider_ids.add(provider_id)
        refs.append(
            {
                "call_id": call_id,
                "name": call.name,
                "arguments": _json_copy(call.arguments),
                "provider_tool_call_id": provider_id,
            }
        )
    return refs


def _copy_requested_limits(
    value: OnboardingBudgetLimits | Mapping[str, object] | None,
) -> OnboardingBudgetLimits | dict[str, object] | None:
    if value is None or isinstance(value, OnboardingBudgetLimits):
        return value
    if not isinstance(value, Mapping):
        raise OnboardingRunnerError("onboarding_budget_invalid")
    return {str(key): item for key, item in value.items()}


def _required_snapshot_id(value: object) -> str:
    if not isinstance(value, str) or _SNAPSHOT_ID.fullmatch(value) is None:
        raise OnboardingRunnerError("onboarding_job_invalid")
    return value


def _new_job_id() -> str:
    return "onboarding_" + uuid.uuid4().hex


def _validate_job_id(value: object) -> str:
    if not isinstance(value, str) or _JOB_ID.fullmatch(value) is None:
        raise OnboardingRunnerError("onboarding_job_invalid")
    return value


def _required_positive_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OnboardingRunnerError("onboarding_checkpoint_invalid", details={"field": field})
    return value


def _optional_positive_integer(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _required_positive_integer(value, field)


def _required_profile_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise OnboardingRunnerError("onboarding_checkpoint_invalid")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OnboardingRunnerError("onboarding_checkpoint_invalid") from exc
    return value


def _assert_bounded_json(value: object, *, maximum: int) -> None:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OnboardingRunnerError("onboarding_checkpoint_invalid") from exc
    if len(encoded) > maximum:
        raise OnboardingRunnerError(
            "project_profile_too_large",
            "The onboarding profile draft exceeds the durable safety limit.",
        )


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_copy(value: object) -> object:
    try:
        return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True))
    except (TypeError, ValueError) as exc:
        raise OnboardingRunnerError("onboarding_checkpoint_invalid") from exc


def _runner_error(exc: Exception) -> OnboardingRunnerError:
    if isinstance(exc, OnboardingRunnerError):
        return exc
    if isinstance(
        exc,
        (
            OnboardingBackendError,
            ModelProtocolError,
            ProjectProfileError,
            ContextProviderError,
            RepositorySnapshotError,
            ProjectImportError,
        ),
    ):
        return OnboardingRunnerError(str(getattr(exc, "code", "onboarding_runner_failed")))
    return OnboardingRunnerError("onboarding_runner_failed")
