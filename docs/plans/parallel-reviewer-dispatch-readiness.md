# Parallel Reviewer Dispatch Readiness

Issue: #242 Research parallel reviewer dispatch readiness

Decision: **NO-GO for enabling parallel reviewer dispatch immediately.**

Parallel reviewer dispatch is a good follow-up direction, but the current
system still has unresolved identity, ordering, aggregation, duplicate-dispatch,
and stale-result semantics. The safe path is to keep the current single-reviewer
projection, document the multi-reviewer contract first, then implement opt-in
fan-out only after the data model, Workbench display, and tests below exist.

## Current System Summary

The reviewer/provenance/policy chain after #241 is advisory and evidence-first:

- Reviewer profiles are declared in `src/goodteam_os/reviewer_profiles.py` as
  project-neutral review contracts. Each profile carries adapter metadata,
  focus areas, required report sections, required evidence handles, fallback
  posture, and timeout expectations.
- Project config may list zero, one, or many reviewer profiles per phase through
  `reviewer_profiles.implementation` and `reviewer_profiles.release`.
  `normalize_reviewer_profile_config()` validates and dedupes those profile ids
  in operator-configured order.
- Review Workbench currently projects only one reviewer/profile per approval
  gate. `reviewer_profile_for_approval()` selects the first profile configured
  for the gate's phase, and `latest_reviewer_status_for_approval()` returns one
  latest matching worker status.
- Worker status carries invocation evidence from `WorkerJobStore`. The status
  can include provider/fallback/manual/builtin/missing/failed evidence, local
  result/stdout/stderr paths, changed files, exit code, failure class, and
  timestamps.
- `build_reviewer_provenance()` converts one worker status plus one optional
  profile into one advisory provenance payload for Workbench.
- `evaluate_review_evidence_policy()` converts that one provenance payload into
  one visible policy state and severity. The policy is advisory by default; even
  strict `blocker` severity remains visible, human-overridable, and does not
  auto-approve or auto-reject.
- Approval actions are still human decisions. `decide_approval()` writes the
  human approval event and advances or revises workflow state; reviewer evidence
  remains context for the operator.

The important asymmetry is that config already accepts multiple profiles, while
runtime projection still collapses to one profile/status/policy payload.

## Readiness Assessment

Recommendation: **NO-GO.**

The system is not ready to enable parallel reviewer dispatch today because:

- A worker job does not have a stable reviewer identity beyond broad fields like
  `run_id`, `role`, and `adapter`. It does not record `profile_id`, `gate`,
  `artifact`, `artifact_version`, `attempt`, or a dispatch-group identity.
- Workbench selects a single "latest" reviewer status per gate. With multiple
  reviewers this would make completion order, timestamp quality, and role
  aliases decide which evidence survives.
- Provenance and policy are one-object projections. Enabling fan-out before
  changing the projection model would overwrite, hide, or collapse evidence.
- There is no documented duplicate-dispatch guard for repeated render/refresh,
  retried dispatch, or racey scheduler calls.
- There is no stale-result rule beyond filtering worker jobs by `run_id`.
  Multiple review attempts for the same gate/artifact could mix old and current
  evidence.
- Ledger/action semantics do not distinguish reviewer-job lifecycle events from
  human approval decisions. Adding fan-out without a clear event model risks
  duplicate or misleading action history.

The system is ready for a design and test-first follow-up. It is not ready for
runtime fan-out.

## Ledger Semantics

Future multi-reviewer dispatch should model these relationships explicitly:

- One artifact can be reviewed by many reviewer jobs.
- One approval gate can aggregate many reviewer results for the same artifact.
- One run can contain multiple review groups across gates and attempts.
- One approval decision remains one human decision for one gate.
- One reviewer profile should create at most one active reviewer job per
  `(run_id, gate, artifact_ref, artifact_version, profile_id, attempt)` identity.
- Provenance refs should be per reviewer job, not a single collapsed field.
- Action or ledger events should preserve reviewer-job lifecycle separately from
  approval decision events.

Recommended identity fields for future reviewer jobs:

- `run_id`
- `gate`
- `phase`
- `artifact_key`
- `artifact_path`
- `artifact_version` or content digest
- `profile_id`
- `adapter`
- `dispatch_group_id`
- `attempt`
- `created_at`

Recommended event model:

- `reviewer_dispatch_group_created`
- `reviewer_job_queued`
- `reviewer_job_started`
- `reviewer_job_completed`
- `reviewer_job_failed`
- `reviewer_job_timed_out`
- `reviewer_projection_viewed` should not be written during normal render
- `approval_decided` remains human-authored and unchanged

Risks to avoid:

- Duplicate events when Workbench refreshes.
- A "latest reviewer status" replacing other reviewers.
- Collapsing many evidence handles into one result path.
- Treating reviewer failure as approval rejection.
- Mutating ledger events after policy projection changes.
- Using adapter or role as reviewer identity. Profile id must be preserved.

## Ordering Model

Recommended ordering: **deterministic profile-config order for operator-facing
display, with completion time shown only as metadata.**

Rationale:

- Profile config order is explicit operator intent.
- It stays stable across refreshes and rerenders.
- It does not depend on scheduler timing or provider latency.
- It lets operators learn a predictable Workbench layout.
- It can still show `completed_at`, `duration`, or timeout metadata inside each
  reviewer card.

Rejected ordering models:

- Completion order: useful operational metadata, but unstable and can move cards
  around while the operator is reading.
- Severity order: helpful for an aggregate badge, but it hides the configured
  review contract and may create the impression that a reviewer is authoritative.
- Job id or filesystem order: deterministic but not meaningful to operators.

Future projection should sort reviewers by:

1. Configured `reviewer_profiles.<phase>` order.
2. Known matching `profile_id`.
3. Stable fallback key for unconfigured or legacy statuses, such as
   `(role, adapter, job_id)`.

## Aggregation Model

Workbench should display:

- One per-reviewer card for every configured reviewer profile.
- One aggregate badge per approval gate.
- The aggregate badge severity as the maximum individual severity.
- Individual provenance retained on each reviewer card.
- Individual evidence handles retained on each reviewer card.
- A visible pending/failed/missing state per reviewer.
- A clear statement that reviewers are advisory and the human operator decides.

Policy aggregation should not collapse individual states into one lossy state.
Recommended shape:

```json
{
  "aggregate": {
    "severity": "blocker",
    "state_counts": {
      "ok": 1,
      "failed_invocation": 1
    },
    "can_human_override": true,
    "advisory_only": true
  },
  "reviewers": [
    {
      "profile_id": "correctness-security",
      "policy": {"state": "ok", "severity": "ok"}
    },
    {
      "profile_id": "architecture-maintainability",
      "policy": {"state": "failed_invocation", "severity": "blocker"}
    }
  ]
}
```

Severity order:

1. `ok`
2. `warning`
3. `blocker`

Suggested aggregation rule:

- Aggregate severity is the max individual severity.
- Aggregate state keeps all individual states and counts.
- A strict blocker remains human-overridable unless a future explicit
  enforcement issue changes approval authority.
- Missing reviewer evidence should remain visible as that reviewer card's state,
  not disappear behind a successful reviewer.
- No reviewer result should become authoritative over the human operator.

## Failure Behavior

Future implementation should use these semantics:

- One reviewer succeeds and one fails: show both reviewer cards; aggregate
  severity reflects the failed reviewer; approval action remains available to
  the human operator.
- One reviewer unavailable or fallback: show fallback evidence with adapter,
  command availability, fallback policy, and rationale; do not auto-dispatch a
  different reviewer unless explicitly configured.
- One reviewer missing evidence: show a missing-evidence reviewer card and keep
  the evidence handles empty for that reviewer only.
- All reviewers fail: aggregate severity is warning or blocker according to
  configured policy; no automatic rejection.
- Partial completion or timeout: show completed cards plus pending/timed-out
  cards; completion time is metadata, not display ordering.
- Duplicate jobs: collapse by reviewer-job identity only after surfacing a clear
  duplicate warning, or reject duplicate creation before queuing. Do not pick an
  arbitrary latest job silently.
- Stale jobs from a previous run: ignore different `run_id`; for same `run_id`,
  ignore jobs whose artifact version or dispatch group does not match the
  current gate attempt.
- Mixed provider and non-provider evidence: evaluate policy per reviewer, keep
  each evidence source visible, and aggregate severity deterministically.

All cases should preserve evidence and keep the approval decision human-owned.

## Config Model Recommendation

Compared options:

- Global default fan-out: unsafe first step. It would change behavior for
  projects that only opted into advisory metadata.
- Gate-driven fan-out: precise, but introduces a new config surface before the
  phase/profile contract is fully exercised.
- Project opt-in boolean: safer than global default, but too coarse unless tied
  to explicit profile identities.
- Profile-driven fan-out per phase/gate: safest first implementation because it
  reuses the existing `reviewer_profiles` order and keeps reviewer identity
  project-neutral.

Recommended first implementation path:

- Default remains single-reviewer/current behavior.
- Parallel review is explicit opt-in.
- Opt-in is profile-driven using existing `reviewer_profiles`.
- Fan-out applies per phase/gate only after the gate has a current artifact and
  dispatch identity.
- No global fan-out default.
- No automatic dispatch unless explicitly configured.
- Missing or omitted `reviewer_profiles` remains backward-compatible and
  produces no reviewer jobs.

One possible future config shape:

```yaml
reviewer_profiles:
  implementation:
    - correctness-security
    - architecture-maintainability

reviewer_dispatch:
  mode: single # single | parallel
  phases:
    implementation: parallel
```

The extra `reviewer_dispatch` opt-in avoids changing the meaning of existing
multi-profile metadata before duplicate and aggregation safeguards exist.

## Required Tests Before Implementation

A future implementation PR must add tests before enabling dispatch:

- Multiple profiles configured for the implementation phase are preserved in
  configured order.
- Parallel dispatch opt-in creates one reviewer job per configured profile.
- No duplicate jobs are created on repeated render, refresh, or idempotent
  dispatch calls.
- Worker job identity includes `profile_id`, `gate`, `artifact`, run identity,
  and attempt or dispatch group.
- Provenance preserves every reviewer separately.
- Each reviewer keeps its own result/stdout/stderr/changed-file evidence
  handles.
- Policy projection runs per reviewer.
- Aggregate policy severity is deterministic and uses max severity.
- Aggregate state preserves individual states and counts.
- Failed reviewer does not auto-reject.
- Strict blocker does not remove human override unless a later explicit
  enforcement feature says so.
- Approval action shape and behavior remain unchanged.
- Workbench projection does not write ledger/action events.
- Workbench projection does not dispatch reviewer jobs.
- Stale run statuses are ignored.
- Stale same-run gate/artifact attempts are ignored.
- Duplicate jobs are either prevented or surfaced explicitly, never silently
  hidden by latest-status selection.
- UI degrades cleanly with missing evidence.
- UI degrades cleanly with partial completion.
- Ordering is stable across refreshes.
- Completion time is displayed as metadata, not used as primary order.
- Backward compatibility holds when only one profile is configured.
- Backward compatibility holds when `reviewer_profiles` is omitted.
- Mixed provider and non-provider evidence aggregates deterministically.
- Unavailable adapter/fallback evidence remains visible and advisory.
- All-reviewers-failed state remains human-decidable.

## Proposed Future Implementation Plan

Break follow-up work into separate issues:

- #242A: Document semantics and required tests only. This document satisfies
  that phase.
- #242B / #257: Add multi-reviewer projection data model with no runtime
  fan-out. This phase adds backend projection payload
  `multi_reviewer_projection` with per-reviewer provenance, per-reviewer
  policy, and aggregate policy counts while preserving the existing
  single-reviewer fields.
- #242C: Update Workbench UI to render multiple reviewer cards and aggregate
  badge from existing synthetic statuses only.
- #242D: Add opt-in profile-driven dispatch fan-out with idempotent job
  identity, duplicate protection, and no approval authority changes.
- #242E: Add rollout guard, strict aggregation tests, and operator-facing
  documentation for parallel reviewer dispatch.

Each phase should preserve the existing evidence-first workflow and should be
mergeable without enabling global parallel dispatch.

## Final Recommendation

**NO-GO for enabling parallel dispatch immediately.**

**GO for a follow-up implementation only after multi-reviewer ledger identity,
ordering, aggregation, stale-result handling, and duplicate-dispatch protections
are explicitly designed and tested.**

Human approval authority must remain intact. Reviewer evidence remains advisory.
Policy gates from #241 remain a visible projection, not autonomous enforcement.
