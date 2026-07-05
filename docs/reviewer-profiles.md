# Reviewer Profiles

Reviewer profiles are GoodTeam's declarative contracts for review roles. A
profile says what kind of review is needed, which worker adapter is the current
default example for that role, what evidence the report must cite, and what
fallback posture later policy gates should apply.

Profiles are project-neutral. The profile identity is the review responsibility,
not the vendor or tool that may perform it. For example,
`correctness-security` is a reusable contract; `codex-reviewer` would couple the
contract to one provider and make routing harder to evolve.

## Metadata Shape

Each built-in profile includes:

- `profile_id`: stable project-neutral identifier.
- `label`: operator-facing display label.
- `adapter`: worker adapter name used as the current routing example.
- `model`: optional model hint. `None` means the adapter default can decide.
- `focus_areas`: review priorities for the worker prompt/report contract.
- `required_report_sections`: sections a conforming review report should include.
- `required_evidence`: evidence keys the review report should cite.
- `fallback_policy`: declarative fallback posture for later policy gates.
- `timeout_seconds`: expected upper bound for the review worker.

The adapter field maps profile metadata to worker adapters, but it does not make
the adapter the profile identity. Future project config can opt into different
profiles or override adapters without changing the registry contract.

## Evidence

Issue #237 added `invocation_evidence` to worker status records. Reviewer
profiles reference that vocabulary as metadata so future reviewer reports can
cite how a worker was invoked and where its artifacts were written.

Current built-ins require:

- `invocation_evidence`
- `result_path`
- `stdout_path`
- `stderr_path`
- `changed_files`
- `verification_summary`

The registry itself does not enforce evidence. Review Workbench reads worker
status records only to display provenance for human operators; later policy
gates can reuse the same vocabulary without changing reviewer profile identity.

## Built-In Profiles

| Profile | Label | Adapter | Model | Focus | Fallback |
| --- | --- | --- | --- | --- | --- |
| `correctness-security` | Correctness and security | `codex` | adapter default | Behavioral correctness, security regressions, data handling, tests | `manual_review_required_on_missing_evidence` |
| `architecture-maintainability` | Architecture and maintainability | `claude` | adapter default | Boundaries, contracts, maintainability, complexity | `warn_on_unavailable` |
| `product-operator` | Product and operator review | `gemini` | adapter default | Workflow impact, operator ergonomics, release readiness, handoff clarity | `allow_fake_only_in_tests` |

The listed adapters are examples for the built-in defaults. They are not the
only supported reviewer shapes, and the profile IDs intentionally avoid vendor
names.

## Project Config Opt-In

Reviewer profiles are optional project routing metadata. A project can configure
zero profiles by omitting the block, one profile for a review moment, or many
profiles in order:

```yaml
reviewer_profiles:
  implementation:
    - correctness-security
    - architecture-maintainability
  release:
    - product-operator
```

Both `implementation` and `release` are optional. Empty phases are valid:

```yaml
reviewer_profiles:
  implementation: []
  release: []
```

Unknown profile IDs are validation errors. Configured profiles are checked
against the worker backend doctor for their adapter, but this remains separate
from `worker_backend.mode` and per-role worker routing. If an adapter is missing
or not ready, the project check reports an explicit warning with the profile ID,
adapter, command evidence when available, readiness, fallback policy, and
message. For now that warning is evidence only; policy enforcement belongs to a
later gate.

## Parallel Reviewer Dispatch Rollout

Reviewer profiles do not fan out by themselves. The default remains
single-reviewer/no-fan-out when `reviewer_dispatch` is omitted or set to
`mode: single`.

Parallel reviewer dispatch requires an explicit dispatch mode, phase opt-in,
and rollout acknowledgement:

```yaml
reviewer_profiles:
  implementation:
    - correctness-security
    - architecture-maintainability

reviewer_dispatch:
  mode: parallel
  rollout:
    enabled: true
    acknowledge: "parallel-reviewer-dispatch-is-advisory"
  phases:
    implementation: parallel
```

If `mode: parallel` is present without the rollout acknowledgement, GoodTeam
does not create parallel reviewer jobs. This makes accidental enablement easier
to audit while preserving single-reviewer compatibility.

To validate a rollout, move a real implementation-side gate such as staging,
code review, or QA to its pending approval state and inspect
`.goodteam/workers/jobs/*/job.json` under the project runtime root. Reviewer jobs
should carry `profile_id`, `approval` / `gate`, `phase`, `artifact_key`,
`artifact_path`, `artifact_version`, `dispatch_group_id`, and
`idempotency_key`.

Rollback is immediate for future gate transitions:

```yaml
reviewer_dispatch:
  mode: single
```

or remove `reviewer_dispatch`. Workbench may still show previously queued
reviewer evidence for the current run when it matches the current gate and
artifact identity. That evidence remains advisory only.

## Review Workbench Provenance

Review Workbench shows reviewer provenance as an advisory signal for each
approval review. It does not make any reviewer authoritative and does not
approve, reject, dispatch, or create reviewer jobs.

The workbench displays a badge derived from the worker status
`invocation_evidence`:

- `provider`: a provider-backed adapter invocation completed, such as `codex`
  with provider `openai`.
- `fallback`: adapter evidence says the command was unavailable or a configured
  reviewer fell back to non-provider evidence.
- `builtin`: local `fake` or `noop` adapter output, with no external
  invocation.
- `manual`: `human` adapter output supplied or awaited by the operator.
- `missing`: no `invocation_evidence` was available for the review.
- `failed`: the reviewer status failed; result and stderr handles are surfaced
  when present.

When a project opts into reviewer profiles, the workbench includes the selected
profile label and focus areas for the matching phase. `implementation` profiles
are shown for implementation-side approval reviews such as staging, security,
QA, code review, and fix-plan review. `release` profiles are shown for release
approval reviews. If profile metadata is missing or invalid, provenance still
renders with the available worker evidence.

Evidence handles are shown locally when present:

- `result_path`
- `stdout_path`
- `stderr_path`
- `changed_files`

Every provenance payload includes `advisory_only: true` and the operator message
`Human operator decides; this reviewer is advisory only.` Approval actions remain
human decisions.

For parallel reviewer dispatch, Workbench displays each configured reviewer in
profile order plus an aggregate severity. Missing evidence, fallback evidence,
non-provider evidence, and failed invocations remain visible to the operator.
They do not approve, reject, dispatch, or disable human approval actions.

## Review Evidence Policy Gates

Review Workbench also projects reviewer provenance into
`review_evidence_policy`. This is a visible gate-quality signal, not an
approval authority. The policy always includes a rationale, sets
`visible_to_operator: true`, keeps `can_human_override: true`, and repeats that
the human operator decides.

Default behavior is compatible and advisory. If a project omits
`review_evidence_policy`, existing approval paths are not blocked or hardened.
The default severities are:

- `ok`: provider-backed evidence completed; severity `ok`.
- `missing_evidence`: no invocation evidence is available; severity `warning`.
- `fallback_evidence`: evidence came from fallback or incomplete provider
  evidence; severity `warning`.
- `non_provider_evidence`: builtin `fake` / `noop` or manual human evidence;
  severity `warning`.
- `failed_invocation`: provider invocation failed; severity `warning` by
  default with visible failure rationale.

Projects can opt into strict projection for selected states:

```yaml
review_evidence_policy:
  mode: strict
  blockers:
    - failed_invocation
    - missing_evidence
```

Strict mode marks only the configured states as `blocker`, and the Workbench
payload/UI explains the strict rationale. This still does not automatically
approve, reject, dispatch, or disable human approval actions. The operator can
override after reviewing the visible evidence.

## Non-Goals

- No runtime dispatch behavior changes.
- No worker invocation behavior changes.
- No execution orchestrator changes.
- No autonomous approval gate enforcement.
- No approval action behavior changes.
- No parallel reviewer execution.
