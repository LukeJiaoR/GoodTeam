"""Canonical, snapshot-bound project onboarding profiles.

``project_profile.v1`` has two deliberately different representations:

* a model draft contains only claim text, claim references, and provider
  ``observation_id`` citations; and
* the canonical profile contains system-derived claim and evidence identifiers
  plus the immutable evidence metadata copied from the observation ledger.

Keeping the two representations separate prevents a model from inventing a
path, line range, content hash, evidence identifier, or user confirmation.
This module does not publish profiles or decide readiness.  It only validates
and canonicalizes a draft so callers can fail closed before publication.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime


PROJECT_PROFILE_SCHEMA = "project_profile.v1"
REPOSITORY_OBSERVATION_SCHEMA = "repository_observation.v1"

_PROFILE_ID = re.compile(r"^profile_[0-9a-f]{64}$")
_SNAPSHOT_ID = re.compile(r"^snap_[0-9a-f]{64}$")
_JOB_ID = re.compile(r"^onboarding_[0-9a-f]{32}$")
_CLAIM_KIND = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OBSERVATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema",
        "profile_id",
        "project_key",
        "snapshot_id",
        "previous_profile_id",
        "generated_by",
        "project",
        "architecture",
        "operations",
        "constraints",
        "risk",
        "knowledge",
        "coverage",
        "claims",
        "created_at",
    }
)
_OPTIONAL_TOP_LEVEL_FIELDS = frozenset({"evidence"})
_GENERATED_BY_FIELDS = frozenset(
    {"job_id", "adapter", "provider", "model", "external_invocation"}
)
_PROJECT_FIELDS = frozenset({"purpose_claim_id", "current_stage_claim_id"})
_ARCHITECTURE_FIELDS = frozenset(
    {
        "overview_claim_ids",
        "modules",
        "entrypoints",
        "data_flows",
        "external_dependencies",
    }
)
_MODULE_REQUIRED_FIELDS = frozenset({"name", "purpose_claim_id"})
_MODULE_OPTIONAL_FIELDS = frozenset(
    {"path", "ownership_claim_ids", "entrypoint_claim_ids", "test_claim_ids"}
)
_OPERATIONS_FIELDS = frozenset({"development", "test", "lint", "build", "deploy"})
_CONSTRAINT_FIELDS = frozenset(
    {"coding_rules", "protected_areas", "prohibited_operations"}
)
_RISK_FIELDS = frozenset(
    {"high_risk_modules", "legacy_areas", "weak_test_coverage"}
)
_KNOWLEDGE_FIELDS = frozenset(
    {"confirmed_claim_ids", "inferred_claim_ids", "unknowns"}
)
_COVERAGE_FIELDS = frozenset(
    {"status", "dimensions", "limitations", "recommended_followups"}
)
_MINIMUM_DIMENSIONS = (
    "project_purpose",
    "repository_shape",
    "main_entrypoint",
    "core_module_map",
    "development_commands",
    "test_commands",
    "instruction_constraint_review",
    "high_risk_assessment",
    "explicit_unknowns",
)
_DIMENSION_FIELDS = frozenset({"status", "claim_refs"})
_CERTAINTIES = frozenset({"confirmed", "inferred", "user_confirmed"})
_OBSERVATION_STATUSES = frozenset({"ok", "invalid", "error", "denied"})

_MAX_CLAIMS = 200
_MAX_LIST_ITEMS = 200
_MAX_CLAIM_TEXT = 12_000
_MAX_RATIONALE_TEXT = 4_000
_MAX_METADATA_TEXT = 256


class ProjectProfileError(ValueError):
    """A stable profile-contract failure suitable for onboarding recovery."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def compute_project_profile_id(snapshot_id: str, job_id: str) -> str:
    """Return the deterministic profile identifier for one onboarding job.

    A profile version is owned by one job and one snapshot.  The content of a
    draft intentionally does not affect this identifier: a resumed job may
    refine its draft, but it cannot silently switch identity or snapshot.
    """

    snapshot = _validate_snapshot_identifier(snapshot_id, field="snapshot_id")
    job = _validate_job_identifier(job_id, field="generated_by.job_id")
    return "profile_" + _digest(
        {
            "schema": PROJECT_PROFILE_SCHEMA,
            "snapshot_id": snapshot,
            "job_id": job,
        }
    )


class ProjectProfileValidator:
    """Canonicalize a model profile draft against trusted snapshot observations.

    ``observations`` must be persisted ``repository_observation.v1`` envelopes,
    such as ``OnboardingObservationStore.read_all()`` returns.  The constructor
    indexes every observation up front and rejects mixed-snapshot ledgers; a
    cited observation must also carry a content or Git identity from which an
    evidence record can be derived.
    """

    def __init__(
        self,
        *,
        snapshot: object,
        observations: object,
        model_output: bool = True,
    ) -> None:
        if not isinstance(model_output, bool):
            raise _invalid("model_output")
        self._snapshot = _validate_snapshot(snapshot)
        self._snapshot_id = str(self._snapshot["snapshot_id"])
        self._project_key = str(self._snapshot["project_key"])
        self._scope_complete = bool(
            _expect_object(self._snapshot["content_scope"], "snapshot.content_scope")[
                "complete"
            ]
        )
        self._model_output = model_output
        self._observations = _index_authoritative_observations(
            observations,
            snapshot_id=self._snapshot_id,
        )

    def validate(self, profile: object) -> dict[str, object]:
        """Validate a draft and return its canonical profile representation."""

        return self.canonicalize(profile)

    def canonicalize(self, profile: object) -> dict[str, object]:
        """Return a deterministic canonical profile or raise ``ProjectProfileError``."""

        source = _expect_object(profile, "profile")
        _expect_exact_keys(
            source,
            _TOP_LEVEL_FIELDS,
            _OPTIONAL_TOP_LEVEL_FIELDS,
            "profile",
        )
        if source.get("schema") != PROJECT_PROFILE_SCHEMA:
            raise _invalid("schema")

        snapshot_id = _validate_snapshot_identifier(
            source.get("snapshot_id"), field="snapshot_id"
        )
        if snapshot_id != self._snapshot_id:
            raise _invalid("snapshot_id", reason="snapshot_mismatch")
        project_key = _clean_text(
            source.get("project_key"), field="project_key", limit=_MAX_METADATA_TEXT
        )
        if project_key != self._project_key:
            raise _invalid("project_key", reason="snapshot_mismatch")

        generated_by = _canonical_generated_by(source.get("generated_by"))
        profile_id = _validate_profile_identifier(
            source.get("profile_id"), field="profile_id"
        )
        expected_profile_id = compute_project_profile_id(
            snapshot_id,
            str(generated_by["job_id"]),
        )
        if profile_id != expected_profile_id:
            raise _invalid("profile_id", reason="identity_mismatch")
        previous_profile_id = _canonical_previous_profile_id(
            source.get("previous_profile_id"), profile_id
        )
        created_at = _canonical_timestamp(source.get("created_at"), field="created_at")

        supplied_evidence = source.get("evidence", [])
        if type(supplied_evidence) is not list or supplied_evidence:
            raise _invalid("evidence", reason="system_derived")

        source_claims = _expect_list(source.get("claims"), "claims", maximum=_MAX_CLAIMS)
        if not source_claims:
            raise _insufficient("claims")
        claims, claim_ids_by_ref, evidence = self._canonical_claims(
            source_claims,
            profile_id=profile_id,
        )
        evidence_paths_by_claim = _evidence_paths_by_claim(claims, evidence)

        project = _canonical_project(source.get("project"), claim_ids_by_ref)
        architecture = _canonical_architecture(
            source.get("architecture"),
            claim_ids_by_ref,
            evidence_paths_by_claim=evidence_paths_by_claim,
        )
        operations = _canonical_reference_mapping(
            source.get("operations"),
            fields=_OPERATIONS_FIELDS,
            field="operations",
            references=claim_ids_by_ref,
        )
        constraints = _canonical_reference_mapping(
            source.get("constraints"),
            fields=_CONSTRAINT_FIELDS,
            field="constraints",
            references=claim_ids_by_ref,
        )
        risk = _canonical_reference_mapping(
            source.get("risk"),
            fields=_RISK_FIELDS,
            field="risk",
            references=claim_ids_by_ref,
        )
        knowledge = _canonical_knowledge(source.get("knowledge"), claims)
        coverage = _canonical_coverage(
            source.get("coverage"),
            references=claim_ids_by_ref,
            unknowns=knowledge["unknowns"],
            scope_complete=self._scope_complete,
        )
        _validate_dimension_links(
            coverage=coverage,
            project=project,
            architecture=architecture,
            operations=operations,
            constraints=constraints,
            risk=risk,
            knowledge=knowledge,
        )

        return {
            "schema": PROJECT_PROFILE_SCHEMA,
            "profile_id": profile_id,
            "project_key": project_key,
            "snapshot_id": snapshot_id,
            "previous_profile_id": previous_profile_id,
            "generated_by": generated_by,
            "project": project,
            "architecture": architecture,
            "operations": operations,
            "constraints": constraints,
            "risk": risk,
            "knowledge": knowledge,
            "coverage": coverage,
            "claims": sorted(claims, key=lambda item: str(item["claim_id"])),
            "evidence": sorted(evidence, key=lambda item: str(item["evidence_id"])),
            "created_at": created_at,
        }

    def _canonical_claims(
        self,
        source_claims: list[object],
        *,
        profile_id: str,
    ) -> tuple[list[dict[str, object]], list[str], list[dict[str, object]]]:
        evidence_by_observation: dict[str, dict[str, object]] = {}
        claims: list[dict[str, object]] = []
        claim_ids_by_ref: list[str] = []
        seen_claim_ids: set[str] = set()

        for index, value in enumerate(source_claims):
            source = _expect_object(value, f"claims[{index}]")
            certainty = source.get("certainty")
            if certainty not in _CERTAINTIES:
                raise _invalid(f"claims[{index}].certainty")
            if certainty == "user_confirmed" and self._model_output:
                raise _invalid(f"claims[{index}].certainty", reason="model_user_confirmation")
            allowed_fields = {"kind", "text", "certainty", "observation_ids"}
            if certainty == "inferred":
                allowed_fields.add("inference_rationale")
            if certainty == "user_confirmed":
                allowed_fields.add("user_confirmation")
            _expect_exact_keys(source, frozenset(allowed_fields), frozenset(), f"claims[{index}]")

            kind = source.get("kind")
            if not isinstance(kind, str) or _CLAIM_KIND.fullmatch(kind) is None:
                raise _invalid(f"claims[{index}].kind")
            text = _clean_text(
                source.get("text"),
                field=f"claims[{index}].text",
                limit=_MAX_CLAIM_TEXT,
            )
            observation_ids = _canonical_observation_ids(
                source.get("observation_ids"),
                field=f"claims[{index}].observation_ids",
                required=certainty != "user_confirmed",
            )
            if certainty == "user_confirmed" and not observation_ids:
                # A user correction is allowed to be the explicit non-evidence
                # branch of the claim contract.  It still needs a durable actor
                # and timestamp, validated below.
                pass

            inference_rationale: str | None = None
            if certainty == "inferred":
                inference_rationale = _clean_text(
                    source.get("inference_rationale"),
                    field=f"claims[{index}].inference_rationale",
                    limit=_MAX_RATIONALE_TEXT,
                )
            user_confirmation: dict[str, object] | None = None
            if certainty == "user_confirmed":
                user_confirmation = _canonical_user_confirmation(
                    source.get("user_confirmation"),
                    field=f"claims[{index}].user_confirmation",
                )

            evidence_ids: list[str] = []
            for observation_id in observation_ids:
                evidence = evidence_by_observation.get(observation_id)
                if evidence is None:
                    evidence = self._evidence_for_observation(observation_id)
                    evidence_by_observation[observation_id] = evidence
                evidence_ids.append(str(evidence["evidence_id"]))
            evidence_ids.sort()

            identity = {
                "schema": "project_profile.v1.claim",
                "profile_id": profile_id,
                "snapshot_id": self._snapshot_id,
                "kind": kind,
                "text": text,
                "certainty": certainty,
                "evidence_ids": evidence_ids,
                "inference_rationale": inference_rationale,
                "user_confirmation": user_confirmation,
            }
            claim_id = "claim_" + _digest(identity)
            if claim_id in seen_claim_ids:
                raise _invalid(f"claims[{index}]", reason="duplicate_claim")
            seen_claim_ids.add(claim_id)
            claim: dict[str, object] = {
                "claim_id": claim_id,
                "kind": kind,
                "text": text,
                "certainty": certainty,
                "evidence_ids": evidence_ids,
            }
            if inference_rationale is not None:
                claim["inference_rationale"] = inference_rationale
            if user_confirmation is not None:
                claim["user_confirmation"] = user_confirmation
            claims.append(claim)
            claim_ids_by_ref.append(claim_id)

        return claims, claim_ids_by_ref, list(evidence_by_observation.values())

    def _evidence_for_observation(self, observation_id: str) -> dict[str, object]:
        observed = self._observations.get(observation_id)
        if observed is None:
            raise _invalid("claims.observation_ids", reason="unknown_observation")
        if observed.get("evidence_available") is not True:
            raise _invalid("claims.observation_ids", reason="observation_not_evidence")
        identity = {
            "schema": "project_profile.v1.evidence",
            "snapshot_id": self._snapshot_id,
            "observation_id": observation_id,
            "path": observed["path"],
            "start_line": observed["start_line"],
            "end_line": observed["end_line"],
            "content_hash": observed["content_hash"],
            "git_blob": observed["git_blob"],
            "git_commit": observed["git_commit"],
        }
        return {
            "evidence_id": "evidence_" + _digest(identity),
            "observation_id": observation_id,
            "snapshot_id": self._snapshot_id,
            "path": observed["path"],
            "start_line": observed["start_line"],
            "end_line": observed["end_line"],
            "content_hash": observed["content_hash"],
            "git_blob": observed["git_blob"],
            "git_commit": observed["git_commit"],
        }


def canonicalize_project_profile(
    profile: object,
    *,
    snapshot: object,
    observations: object,
    model_output: bool = True,
) -> dict[str, object]:
    """Canonicalize a raw onboarding profile draft.

    ``model_output`` must remain true for a model-produced draft.  The false
    branch exists only for a future authenticated human-review path, where the
    separate ``user_confirmation`` object is required for user-confirmed claims.
    """

    return ProjectProfileValidator(
        snapshot=snapshot,
        observations=observations,
        model_output=model_output,
    ).canonicalize(profile)


def validate_project_profile(
    profile: object,
    *,
    snapshot: object,
    observations: object,
    model_output: bool = True,
) -> dict[str, object]:
    """Compatibility spelling for :func:`canonicalize_project_profile`."""

    return canonicalize_project_profile(
        profile,
        snapshot=snapshot,
        observations=observations,
        model_output=model_output,
    )


def render_project_profile_projections(profile: object) -> dict[str, str]:
    """Render the five deterministic Markdown projections for a canonical profile."""

    canonical = _validate_renderable_profile(profile)
    claims = {
        str(item["claim_id"]): item
        for item in _expect_list(canonical["claims"], "claims", maximum=_MAX_CLAIMS)
    }
    metadata = _projection_metadata(canonical)
    architecture = _expect_object(canonical["architecture"], "architecture")
    operations = _expect_object(canonical["operations"], "operations")
    constraints = _expect_object(canonical["constraints"], "constraints")
    risk = _expect_object(canonical["risk"], "risk")
    knowledge = _expect_object(canonical["knowledge"], "knowledge")
    coverage = _expect_object(canonical["coverage"], "coverage")

    purpose = _render_claim(claims, str(_expect_object(canonical["project"], "project")["purpose_claim_id"]))
    stage = _render_claim(claims, str(_expect_object(canonical["project"], "project")["current_stage_claim_id"]))
    overview = _render_claim_list(claims, _string_list(architecture.get("overview_claim_ids")))
    unknowns = _markdown_bullets(_string_list(knowledge.get("unknowns")))
    limitations = _markdown_bullets(_string_list(coverage.get("limitations")))

    project_overview = "\n".join(
        [
            "# Project overview",
            "",
            metadata,
            "",
            "## Purpose",
            purpose,
            "",
            "## Current stage",
            stage,
            "",
            "## Architecture / stack findings",
            overview,
            "",
            "## Coverage limitations",
            limitations,
            "",
            "## Explicit unknowns",
            unknowns,
            "",
        ]
    )

    architecture_projection = "\n".join(
        [
            "# Architecture",
            "",
            metadata,
            "",
            "## Overview",
            overview,
            "",
            "## Entrypoints",
            _render_claim_list(claims, _string_list(architecture.get("entrypoints"))),
            "",
            "## Data flows",
            _render_claim_list(claims, _string_list(architecture.get("data_flows"))),
            "",
            "## External dependencies",
            _render_claim_list(
                claims, _string_list(architecture.get("external_dependencies"))
            ),
            "",
        ]
    )

    module_sections = _render_modules(claims, architecture.get("modules"))
    module_map = "\n".join(
        [
            "# Module map",
            "",
            metadata,
            "",
            "## Modules",
            module_sections,
            "",
        ]
    )

    development_sections = [
        "# Development guide",
        "",
        metadata,
    ]
    for name in ("development", "test", "lint", "build", "deploy"):
        development_sections.extend(
            [
                "",
                f"## {_heading(name)} findings",
                _render_claim_list(claims, _string_list(operations.get(name))),
            ]
        )
    development_sections.extend(
        [
            "",
            "## Constraints and protected operations",
            _render_named_claim_groups(claims, constraints),
            "",
        ]
    )
    development_guide = "\n".join(development_sections)

    risk_register = "\n".join(
        [
            "# Risk register",
            "",
            metadata,
            "",
            "## Risk findings",
            _render_named_claim_groups(claims, risk),
            "",
            "## Coverage limitations",
            limitations,
            "",
            "## Unknown areas",
            unknowns,
            "",
        ]
    )
    return {
        "project-overview.md": project_overview,
        "architecture.md": architecture_projection,
        "module-map.md": module_map,
        "development-guide.md": development_guide,
        "risk-register.md": risk_register,
    }


def _validate_snapshot(snapshot: object) -> dict[str, object]:
    if hasattr(snapshot, "as_dict") and callable(getattr(snapshot, "as_dict")):
        snapshot = snapshot.as_dict()
    value = _expect_object(snapshot, "snapshot")
    if value.get("schema") != "repository_snapshot.v1":
        raise _invalid("snapshot.schema")
    snapshot_id = _validate_snapshot_identifier(
        value.get("snapshot_id"), field="snapshot.snapshot_id"
    )
    project_key = _clean_text(
        value.get("project_key"), field="snapshot.project_key", limit=_MAX_METADATA_TEXT
    )
    content_scope = _expect_object(value.get("content_scope"), "snapshot.content_scope")
    if type(content_scope.get("complete")) is not bool:
        raise _invalid("snapshot.content_scope.complete")
    return {
        "snapshot_id": snapshot_id,
        "project_key": project_key,
        "content_scope": {"complete": content_scope["complete"]},
    }


def _index_authoritative_observations(
    observations: object,
    *,
    snapshot_id: str,
) -> dict[str, dict[str, object]]:
    envelopes = _expect_list(observations, "observations", maximum=10_000)
    indexed: dict[str, dict[str, object]] = {}
    for envelope_index, value in enumerate(envelopes):
        envelope = _expect_object(value, f"observations[{envelope_index}]")
        if envelope.get("schema") != REPOSITORY_OBSERVATION_SCHEMA:
            raise _invalid(f"observations[{envelope_index}].schema")
        if envelope.get("snapshot_id") != snapshot_id:
            raise _invalid(
                f"observations[{envelope_index}].snapshot_id",
                reason="snapshot_mismatch",
            )
        tool_call_id = envelope.get("tool_call_id")
        if (
            not isinstance(tool_call_id, str)
            or _OBSERVATION_ID.fullmatch(tool_call_id) is None
            or ":" in tool_call_id
        ):
            raise _invalid(f"observations[{envelope_index}].tool_call_id")
        status = envelope.get("status")
        if status not in _OBSERVATION_STATUSES:
            raise _invalid(f"observations[{envelope_index}].status")
        listed = _expect_list(
            envelope.get("observations"),
            f"observations[{envelope_index}].observations",
            maximum=10_000,
        )
        if status != "ok" and listed:
            raise _invalid(f"observations[{envelope_index}].observations")
        for observation_index, raw in enumerate(listed):
            observation = _expect_object(
                raw,
                f"observations[{envelope_index}].observations[{observation_index}]",
            )
            observation_id = observation.get("observation_id")
            if (
                not isinstance(observation_id, str)
                or _OBSERVATION_ID.fullmatch(observation_id) is None
            ):
                raise _invalid(
                    f"observations[{envelope_index}].observations[{observation_index}].observation_id"
                )
            if observation_id in indexed:
                raise _invalid("observations", reason="duplicate_observation")
            if observation_id != f"{tool_call_id}:{observation_index}":
                raise _invalid("observations", reason="observation_envelope_mismatch")
            if (
                "snapshot_id" in observation
                and observation.get("snapshot_id") != snapshot_id
            ):
                raise _invalid("observations", reason="snapshot_mismatch")
            indexed[observation_id] = _canonical_observation_metadata(observation)
    return indexed


def _canonical_observation_metadata(observation: dict[str, object]) -> dict[str, object]:
    path = observation.get("path")
    if path is not None and not isinstance(path, str):
        raise _invalid("observations.path")
    if isinstance(path, str):
        path = _clean_text(path, field="observations.path", limit=4_096)

    start_line = observation.get("start_line")
    end_line = observation.get("end_line")
    if start_line is None and end_line is None:
        normalized_start = None
        normalized_end = None
    elif (
        isinstance(start_line, int)
        and not isinstance(start_line, bool)
        and start_line > 0
        and isinstance(end_line, int)
        and not isinstance(end_line, bool)
        and end_line >= start_line
    ):
        normalized_start = start_line
        normalized_end = end_line
    else:
        raise _invalid("observations.lines")

    content_hash = _optional_sha256(observation.get("content_hash"), "observations.content_hash")
    diff_hash = _optional_sha256(observation.get("diff_hash"), "observations.diff_hash")
    if content_hash is not None and diff_hash is not None:
        raise _invalid("observations", reason="ambiguous_content_identity")
    content_identity = content_hash if content_hash is not None else diff_hash
    git_blob = _optional_git_object(observation.get("new_blob"), "observations.new_blob")
    if git_blob is None:
        git_blob = _optional_git_object(observation.get("old_blob"), "observations.old_blob")
    git_commit = _optional_git_object(
        observation.get("commit_sha"), "observations.commit_sha"
    )
    return {
        "path": path,
        "start_line": normalized_start,
        "end_line": normalized_end,
        "content_hash": content_identity,
        "git_blob": git_blob,
        "git_commit": git_commit,
        "evidence_available": bool(content_identity or git_blob or git_commit),
    }


def _canonical_generated_by(value: object) -> dict[str, object]:
    source = _expect_object(value, "generated_by")
    _expect_exact_keys(source, _GENERATED_BY_FIELDS, frozenset(), "generated_by")
    return {
        "job_id": _validate_job_identifier(
            source.get("job_id"), field="generated_by.job_id"
        ),
        "adapter": _clean_text(
            source.get("adapter"), field="generated_by.adapter", limit=_MAX_METADATA_TEXT
        ),
        "provider": _clean_text(
            source.get("provider"), field="generated_by.provider", limit=_MAX_METADATA_TEXT
        ),
        "model": _clean_text(
            source.get("model"), field="generated_by.model", limit=_MAX_METADATA_TEXT
        ),
        "external_invocation": _expect_bool(
            source.get("external_invocation"), "generated_by.external_invocation"
        ),
    }


def _canonical_previous_profile_id(value: object, profile_id: str) -> str | None:
    if value is None:
        return None
    previous = _validate_profile_identifier(value, field="previous_profile_id")
    if previous == profile_id:
        raise _invalid("previous_profile_id", reason="self_reference")
    return previous


def _canonical_project(value: object, references: list[str]) -> dict[str, object]:
    source = _expect_object(value, "project")
    _expect_exact_keys(source, _PROJECT_FIELDS, frozenset(), "project")
    return {
        "purpose_claim_id": _resolve_claim_reference(
            source.get("purpose_claim_id"), references, "project.purpose_claim_id"
        ),
        "current_stage_claim_id": _resolve_claim_reference(
            source.get("current_stage_claim_id"),
            references,
            "project.current_stage_claim_id",
        ),
    }


def _canonical_architecture(
    value: object,
    references: list[str],
    *,
    evidence_paths_by_claim: dict[str, set[str]],
) -> dict[str, object]:
    source = _expect_object(value, "architecture")
    _expect_exact_keys(source, _ARCHITECTURE_FIELDS, frozenset(), "architecture")
    modules_source = _expect_list(source.get("modules"), "architecture.modules", maximum=_MAX_LIST_ITEMS)
    modules = [
        _canonical_module(
            item,
            index=index,
            references=references,
            evidence_paths_by_claim=evidence_paths_by_claim,
        )
        for index, item in enumerate(modules_source)
    ]
    names = [(str(item["name"]), str(item.get("path") or "")) for item in modules]
    if len(set(names)) != len(names):
        raise _invalid("architecture.modules", reason="duplicate_module")
    modules.sort(key=lambda item: (str(item.get("path") or ""), str(item["name"])))
    return {
        "overview_claim_ids": _canonical_claim_reference_list(
            source.get("overview_claim_ids"), references, "architecture.overview_claim_ids"
        ),
        "modules": modules,
        "entrypoints": _canonical_claim_reference_list(
            source.get("entrypoints"), references, "architecture.entrypoints"
        ),
        "data_flows": _canonical_claim_reference_list(
            source.get("data_flows"), references, "architecture.data_flows"
        ),
        "external_dependencies": _canonical_claim_reference_list(
            source.get("external_dependencies"),
            references,
            "architecture.external_dependencies",
        ),
    }


def _canonical_module(
    value: object,
    *,
    index: int,
    references: list[str],
    evidence_paths_by_claim: dict[str, set[str]],
) -> dict[str, object]:
    field = f"architecture.modules[{index}]"
    source = _expect_object(value, field)
    _expect_exact_keys(source, _MODULE_REQUIRED_FIELDS, _MODULE_OPTIONAL_FIELDS, field)
    path = source.get("path")
    if path is not None:
        path = _canonical_module_path(path, field=f"{field}.path")
    purpose_claim_id = _resolve_claim_reference(
        source.get("purpose_claim_id"), references, f"{field}.purpose_claim_id"
    )
    ownership_claim_ids = _canonical_claim_reference_list(
        source.get("ownership_claim_ids", []),
        references,
        f"{field}.ownership_claim_ids",
    )
    entrypoint_claim_ids = _canonical_claim_reference_list(
        source.get("entrypoint_claim_ids", []),
        references,
        f"{field}.entrypoint_claim_ids",
    )
    test_claim_ids = _canonical_claim_reference_list(
        source.get("test_claim_ids", []), references, f"{field}.test_claim_ids"
    )
    if path is not None:
        referenced_claims = {
            purpose_claim_id,
            *ownership_claim_ids,
            *entrypoint_claim_ids,
            *test_claim_ids,
        }
        evidenced_paths = {
            evidence_path
            for claim_id in referenced_claims
            for evidence_path in evidence_paths_by_claim.get(claim_id, set())
        }
        if path not in evidenced_paths:
            raise _invalid(f"{field}.path", reason="path_not_evidenced")
    return {
        "name": _clean_text(source.get("name"), field=f"{field}.name", limit=_MAX_METADATA_TEXT),
        "path": path,
        "purpose_claim_id": purpose_claim_id,
        "ownership_claim_ids": ownership_claim_ids,
        "entrypoint_claim_ids": entrypoint_claim_ids,
        "test_claim_ids": test_claim_ids,
    }


def _evidence_paths_by_claim(
    claims: list[dict[str, object]],
    evidence: list[dict[str, object]],
) -> dict[str, set[str]]:
    evidence_paths = {
        str(item["evidence_id"]): item["path"]
        for item in evidence
        if isinstance(item.get("path"), str)
    }
    result: dict[str, set[str]] = {}
    for claim in claims:
        claim_id = str(claim["claim_id"])
        evidence_ids = claim.get("evidence_ids")
        if not isinstance(evidence_ids, list):  # pragma: no cover - canonical invariant
            raise _invalid("claims")
        result[claim_id] = {
            str(evidence_paths[evidence_id])
            for evidence_id in evidence_ids
            if isinstance(evidence_id, str) and evidence_id in evidence_paths
        }
    return result


def _canonical_reference_mapping(
    value: object,
    *,
    fields: frozenset[str],
    field: str,
    references: list[str],
) -> dict[str, list[str]]:
    source = _expect_object(value, field)
    _expect_exact_keys(source, fields, frozenset(), field)
    return {
        name: _canonical_claim_reference_list(source.get(name), references, f"{field}.{name}")
        for name in sorted(fields)
    }


def _canonical_knowledge(value: object, claims: list[dict[str, object]]) -> dict[str, object]:
    source = _expect_object(value, "knowledge")
    _expect_exact_keys(source, frozenset({"unknowns"}), frozenset({"confirmed_claim_ids", "inferred_claim_ids"}), "knowledge")
    for key in ("confirmed_claim_ids", "inferred_claim_ids"):
        if key in source and source[key] != []:
            raise _invalid(f"knowledge.{key}", reason="system_derived")
    unknowns = _canonical_text_list(source.get("unknowns"), "knowledge.unknowns")
    confirmed = sorted(
        str(item["claim_id"])
        for item in claims
        if item.get("certainty") == "confirmed"
    )
    inferred = sorted(
        str(item["claim_id"])
        for item in claims
        if item.get("certainty") == "inferred"
    )
    return {
        "confirmed_claim_ids": confirmed,
        "inferred_claim_ids": inferred,
        "unknowns": unknowns,
    }


def _canonical_coverage(
    value: object,
    *,
    references: list[str],
    unknowns: object,
    scope_complete: bool,
) -> dict[str, object]:
    source = _expect_object(value, "coverage")
    _expect_exact_keys(source, _COVERAGE_FIELDS, frozenset(), "coverage")
    status = source.get("status")
    if status == "insufficient":
        raise _insufficient("coverage.status")
    if status not in {"sufficient", "partial"}:
        raise _invalid("coverage.status")
    dimensions = _expect_object(source.get("dimensions"), "coverage.dimensions")
    if set(dimensions) != set(_MINIMUM_DIMENSIONS):
        raise _insufficient("coverage.dimensions")

    canonical_dimensions: dict[str, dict[str, object]] = {}
    for name in _MINIMUM_DIMENSIONS:
        item = _expect_object(dimensions.get(name), f"coverage.dimensions.{name}")
        _expect_exact_keys(item, _DIMENSION_FIELDS, frozenset(), f"coverage.dimensions.{name}")
        dimension_status = item.get("status")
        allowed_statuses = _dimension_statuses(name)
        if dimension_status not in allowed_statuses:
            raise _insufficient(f"coverage.dimensions.{name}.status")
        claim_ids = _canonical_claim_reference_list(
            item.get("claim_refs"),
            references,
            f"coverage.dimensions.{name}.claim_refs",
        )
        if name == "explicit_unknowns":
            if claim_ids or not isinstance(unknowns, list) or not unknowns:
                raise _insufficient(f"coverage.dimensions.{name}")
        elif not claim_ids:
            raise _insufficient(f"coverage.dimensions.{name}")
        canonical_dimensions[name] = {
            "status": str(dimension_status),
            "claim_ids": claim_ids,
        }

    limitations = _canonical_text_list(source.get("limitations"), "coverage.limitations")
    recommended_followups = _canonical_text_list(
        source.get("recommended_followups"), "coverage.recommended_followups"
    )
    if status == "sufficient" and limitations:
        raise _insufficient("coverage.limitations")
    if status == "partial" and not limitations:
        raise _insufficient("coverage.limitations")
    if not scope_complete and status != "partial":
        raise _insufficient("coverage.status", reason="snapshot_scope_incomplete")
    return {
        "status": status,
        "dimensions": canonical_dimensions,
        "limitations": limitations,
        "recommended_followups": recommended_followups,
    }


def _dimension_statuses(name: str) -> frozenset[str]:
    if name in {"main_entrypoint", "development_commands", "test_commands"}:
        return frozenset({"covered", "not_found"})
    if name == "explicit_unknowns":
        return frozenset({"documented"})
    return frozenset({"covered"})


def _validate_dimension_links(
    *,
    coverage: dict[str, object],
    project: dict[str, object],
    architecture: dict[str, object],
    operations: dict[str, list[str]],
    constraints: dict[str, list[str]],
    risk: dict[str, list[str]],
    knowledge: dict[str, object],
) -> None:
    dimensions = _expect_object(coverage["dimensions"], "coverage.dimensions")

    def claim_ids(name: str) -> set[str]:
        item = _expect_object(dimensions[name], f"coverage.dimensions.{name}")
        return set(_string_list(item.get("claim_ids")))

    if str(project["purpose_claim_id"]) not in claim_ids("project_purpose"):
        raise _insufficient("coverage.dimensions.project_purpose")
    if not claim_ids("repository_shape").intersection(
        _string_list(architecture.get("overview_claim_ids"))
    ):
        raise _insufficient("coverage.dimensions.repository_shape")

    entrypoint_dimension = _expect_object(
        dimensions["main_entrypoint"], "coverage.dimensions.main_entrypoint"
    )
    entrypoints = set(_string_list(architecture.get("entrypoints")))
    if entrypoint_dimension.get("status") == "covered":
        if not entrypoints or not claim_ids("main_entrypoint").intersection(entrypoints):
            raise _insufficient("coverage.dimensions.main_entrypoint")
    elif entrypoints:
        raise _insufficient("coverage.dimensions.main_entrypoint")

    modules = _expect_list(architecture.get("modules"), "architecture.modules", maximum=_MAX_LIST_ITEMS)
    module_purpose_ids = {
        str(_expect_object(module, "architecture.modules")["purpose_claim_id"])
        for module in modules
    }
    if not module_purpose_ids or not module_purpose_ids.issubset(claim_ids("core_module_map")):
        raise _insufficient("coverage.dimensions.core_module_map")

    for dimension, operation in (
        ("development_commands", "development"),
        ("test_commands", "test"),
    ):
        dimension_value = _expect_object(dimensions[dimension], f"coverage.dimensions.{dimension}")
        operation_ids = set(operations[operation])
        if dimension_value.get("status") == "covered":
            if not operation_ids or not claim_ids(dimension).intersection(operation_ids):
                raise _insufficient(f"coverage.dimensions.{dimension}")
        elif operation_ids:
            raise _insufficient(f"coverage.dimensions.{dimension}")

    constraint_ids = set().union(*[set(value) for value in constraints.values()])
    if not constraint_ids or not claim_ids("instruction_constraint_review").intersection(
        constraint_ids
    ):
        raise _insufficient("coverage.dimensions.instruction_constraint_review")
    risk_ids = set().union(*[set(value) for value in risk.values()])
    if not risk_ids or not claim_ids("high_risk_assessment").intersection(risk_ids):
        raise _insufficient("coverage.dimensions.high_risk_assessment")
    if not isinstance(knowledge.get("unknowns"), list) or not knowledge["unknowns"]:
        raise _insufficient("coverage.dimensions.explicit_unknowns")


def _canonical_observation_ids(value: object, *, field: str, required: bool) -> list[str]:
    items = _expect_list(value, field, maximum=_MAX_LIST_ITEMS)
    result: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str) or _OBSERVATION_ID.fullmatch(item) is None:
            raise _invalid(f"{field}[{index}]")
        result.append(item)
    if len(set(result)) != len(result):
        raise _invalid(field, reason="duplicate_observation")
    if required and not result:
        raise _invalid(field, reason="missing_evidence")
    return sorted(result)


def _canonical_user_confirmation(value: object, *, field: str) -> dict[str, object]:
    source = _expect_object(value, field)
    _expect_exact_keys(source, frozenset({"actor", "confirmed_at"}), frozenset(), field)
    return {
        "actor": _clean_text(source.get("actor"), field=f"{field}.actor", limit=_MAX_METADATA_TEXT),
        "confirmed_at": _canonical_timestamp(source.get("confirmed_at"), field=f"{field}.confirmed_at"),
    }


def _canonical_claim_reference_list(
    value: object,
    references: list[str],
    field: str,
) -> list[str]:
    items = _expect_list(value, field, maximum=_MAX_LIST_ITEMS)
    result = [_resolve_claim_reference(item, references, f"{field}[{index}]") for index, item in enumerate(items)]
    if len(set(result)) != len(result):
        raise _invalid(field, reason="duplicate_claim_reference")
    return sorted(result)


def _resolve_claim_reference(value: object, references: list[str], field: str) -> str:
    index: int | None = None
    if isinstance(value, int) and not isinstance(value, bool):
        index = value
    elif isinstance(value, str) and value.startswith("draft:"):
        suffix = value.removeprefix("draft:")
        if suffix.isdigit():
            index = int(suffix)
    if index is None or index < 0 or index >= len(references):
        raise _invalid(field, reason="unknown_claim_reference")
    return references[index]


def _canonical_text_list(value: object, field: str) -> list[str]:
    items = _expect_list(value, field, maximum=_MAX_LIST_ITEMS)
    result = [
        _clean_text(item, field=f"{field}[{index}]", limit=_MAX_CLAIM_TEXT)
        for index, item in enumerate(items)
    ]
    if len(set(result)) != len(result):
        raise _invalid(field, reason="duplicate_value")
    return sorted(result)


def _canonical_module_path(value: object, *, field: str) -> str:
    path = _clean_text(value, field=field, limit=4_096)
    if path.startswith("/") or "\\" in path:
        raise _invalid(field)
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise _invalid(field)
    return path


def _validate_renderable_profile(profile: object) -> dict[str, object]:
    value = _expect_object(profile, "profile")
    required = _TOP_LEVEL_FIELDS | {"evidence"}
    if set(value) != required:
        raise _invalid("profile")
    if value.get("schema") != PROJECT_PROFILE_SCHEMA:
        raise _invalid("schema")
    _validate_profile_identifier(value.get("profile_id"), field="profile_id")
    _validate_snapshot_identifier(value.get("snapshot_id"), field="snapshot_id")
    for field in ("project", "architecture", "operations", "constraints", "risk", "knowledge", "coverage"):
        _expect_object(value.get(field), field)
    claims = _expect_list(value.get("claims"), "claims", maximum=_MAX_CLAIMS)
    claim_ids: set[str] = set()
    for index, claim in enumerate(claims):
        item = _expect_object(claim, f"claims[{index}]")
        claim_id = item.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id.startswith("claim_"):
            raise _invalid(f"claims[{index}].claim_id")
        if claim_id in claim_ids:
            raise _invalid("claims", reason="duplicate_claim")
        claim_ids.add(claim_id)
    return value


def _projection_metadata(profile: dict[str, object]) -> str:
    coverage = _expect_object(profile["coverage"], "coverage")
    return "\n".join(
        [
            f"- Profile ID: `{_code(str(profile['profile_id']))}`",
            f"- Snapshot ID: `{_code(str(profile['snapshot_id']))}`",
            f"- Coverage: `{_code(str(coverage.get('status', 'unknown')))}`",
            f"- Created: `{_code(str(profile['created_at']))}`",
            "- Freshness: `snapshot-bound at publication`",
        ]
    )


def _render_claim_list(claims: dict[str, dict[str, object]], claim_ids: list[str]) -> str:
    if not claim_ids:
        return "- No evidence-backed finding was recorded."
    return "\n".join(_render_claim(claims, claim_id) for claim_id in sorted(claim_ids))


def _render_claim(claims: dict[str, dict[str, object]], claim_id: str) -> str:
    claim = claims.get(claim_id)
    if claim is None:
        raise _invalid("profile.claim_reference")
    text = _clean_text(claim.get("text"), field="claim.text", limit=_MAX_CLAIM_TEXT)
    certainty = _clean_text(
        claim.get("certainty"), field="claim.certainty", limit=_MAX_METADATA_TEXT
    )
    return f"- `{_code(claim_id)}` ({_inline(certainty)}): {_inline(text)}"


def _render_modules(claims: dict[str, dict[str, object]], value: object) -> str:
    modules = _expect_list(value, "architecture.modules", maximum=_MAX_LIST_ITEMS)
    if not modules:
        return "- No core module map was recorded."
    sections: list[str] = []
    for raw in modules:
        module = _expect_object(raw, "architecture.modules")
        name = _clean_text(module.get("name"), field="module.name", limit=_MAX_METADATA_TEXT)
        path = module.get("path")
        sections.extend([f"### {_inline(name)}", ""])
        if isinstance(path, str):
            sections.extend([f"- Path: `{_code(path)}`", ""])
        sections.extend(
            [
                "#### Purpose",
                _render_claim(claims, str(module.get("purpose_claim_id"))),
                "",
                "#### Ownership candidates",
                _render_claim_list(claims, _string_list(module.get("ownership_claim_ids"))),
                "",
                "#### Entrypoints",
                _render_claim_list(claims, _string_list(module.get("entrypoint_claim_ids"))),
                "",
                "#### Tests",
                _render_claim_list(claims, _string_list(module.get("test_claim_ids"))),
                "",
            ]
        )
    return "\n".join(sections).rstrip()


def _render_named_claim_groups(
    claims: dict[str, dict[str, object]], value: dict[str, object]
) -> str:
    sections: list[str] = []
    for name in sorted(value):
        sections.extend(
            [
                f"### {_heading(name)}",
                _render_claim_list(claims, _string_list(value[name])),
                "",
            ]
        )
    return "\n".join(sections).rstrip()


def _markdown_bullets(values: list[str]) -> str:
    if not values:
        return "- None recorded."
    return "\n".join(f"- {_inline(value)}" for value in sorted(values))


def _heading(value: str) -> str:
    return _inline(value.replace("_", " ").title())


def _inline(value: str) -> str:
    """Render untrusted profile text as a single safe Markdown line."""

    flattened = " ".join(value.splitlines())
    return (
        flattened.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _code(value: str) -> str:
    """Render a value inside a Markdown code span without changing its identity."""

    return " ".join(value.splitlines()).replace("`", "\\`")


def _expect_object(value: object, field: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _invalid(field)
    return dict(value)


def _expect_list(value: object, field: str, *, maximum: int) -> list[object]:
    if type(value) is not list or len(value) > maximum:
        raise _invalid(field)
    return list(value)


def _expect_exact_keys(
    value: dict[str, object],
    required: frozenset[str],
    optional: frozenset[str],
    field: str,
) -> None:
    keys = set(value)
    if not required.issubset(keys) or keys - required - optional:
        raise _invalid(field)


def _expect_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise _invalid(field)
    return value


def _clean_text(value: object, *, field: str, limit: int) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise _invalid(field)
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise _invalid(field)
    return normalized


def _canonical_timestamp(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise _invalid(field)
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise _invalid(field) from exc
    if parsed.tzinfo is None:
        raise _invalid(field)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_snapshot_identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SNAPSHOT_ID.fullmatch(value) is None:
        raise _invalid(field)
    return value


def _validate_profile_identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _PROFILE_ID.fullmatch(value) is None:
        raise _invalid(field)
    return value


def _validate_job_identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _JOB_ID.fullmatch(value) is None:
        raise _invalid(field)
    return value


def _optional_sha256(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _invalid(field)
    return value


def _optional_git_object(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _GIT_OBJECT.fullmatch(value) is None:
        raise _invalid(field)
    return value


def _string_list(value: object) -> list[str]:
    if type(value) is not list or not all(isinstance(item, str) for item in value):
        raise _invalid("profile")
    return list(value)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _invalid(field: str, *, reason: str | None = None) -> ProjectProfileError:
    details: dict[str, object] = {"field": field}
    if reason is not None:
        details["reason"] = reason
    return ProjectProfileError(
        "project_profile_invalid",
        "Project profile does not satisfy the onboarding contract.",
        details=details,
    )


def _insufficient(field: str, *, reason: str | None = None) -> ProjectProfileError:
    details: dict[str, object] = {"field": field}
    if reason is not None:
        details["reason"] = reason
    return ProjectProfileError(
        "project_profile_insufficient",
        "Project profile does not meet minimum evidence coverage.",
        details=details,
    )
