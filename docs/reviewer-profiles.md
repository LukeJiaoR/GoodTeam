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

Review Workbench shows `Reviewer dispatch diagnostics` in the System tab. Use
that section to interpret rollout state:

- Default single: `mode` is `single`, parallel is not requested, and no warning
  is expected.
- Guarded parallel: `mode` is `parallel`, parallel is requested, but parallel is
  not enabled because the rollout acknowledgement is missing or incomplete.
- Enabled parallel: `mode` is `parallel`, parallel is requested and enabled, and
  configured phase/profile counts show what can fan out on future gate
  transitions.
- Invalid config: diagnostics render errors instead of crashing Workbench.

Queued reviewer job counts are informational and grouped by gate/profile when
the existing worker job store is available. Failed reviewer job counts are also
informational and grouped by gate/profile when a failed job still carries strict
reviewer-dispatch identity. Diagnostics are read-only: they do not dispatch
jobs, run workers, write ledger/action events, approve, reject, retry, or
disable approval controls. Reviewer evidence remains advisory, and the human
operator remains authoritative.

Queued reviewer jobs are executed only by an explicit operator action. When the
current pending approval gate has matching queued reviewer jobs, Review
Workbench shows `Run queued reviewer jobs` on that approval review card. The
button runs only existing queued reviewer jobs for the active run, current
actionable gate, and current artifact identity, including the current artifact
content version. A queued job must carry reviewer dispatch identity such as
`profile_id`, reviewer `phase`, `dispatch_group_id`, and `idempotency_key` before
the execution action will run it. The button does not create reviewer jobs,
dispatch missing reviewers, run on page refresh, write approval ledger events,
approve, reject, or disable the human approval controls.

To validate a rollout, move a real implementation-side gate such as staging,
code review, or QA to its pending approval state and inspect
`.goodteam/workers/jobs/*/job.json` under the project runtime root. Reviewer jobs
should carry `profile_id`, `approval` / `gate`, `phase`, `artifact_key`,
`artifact_path`, `artifact_version`, `dispatch_group_id`, and
`idempotency_key`.

After running queued reviewer jobs, refresh Workbench and inspect the existing
provenance, review evidence policy, and multi-reviewer projection sections on
the same approval card. Result handles such as `result_path`, `stdout_path`, and
`stderr_path` remain advisory evidence. If reviewer execution fails, inspect the
failed job's stderr/result handles, fix adapter availability or project
preconditions, and then use the explicit `Retry failed reviewer jobs` action for
the current pending gate if a matching failed reviewer-dispatch job is shown.
Retry is scoped server-side to the active run, current actionable gate, current
artifact key/path/content version, and optional `profile_id`. Stale failures,
wrong-gate jobs, wrong-run jobs, queued/running/completed jobs, bare legacy
`code_review` jobs without reviewer-dispatch identity, and non-reviewer jobs are
not retried. Retry creates a new queued retry job with lineage back to the
original failed job (`original_failed_job_id`, `retry_attempt`, and
`retry_of_idempotency_key`); it does not run the job, approve, reject, dispatch
unrelated reviewers, or mutate approval state.

Refresh Workbench after a retry. The newer valid retry attempt is preferred for
the current profile status, while earlier failed attempts remain visible as
previous failure evidence when available. Partial success is expected: completed
reviewers and failed reviewers can appear side by side in the multi-reviewer
projection. Failed reviewer evidence does not auto-reject; the human operator
still decides the approval.

When the operator explicitly approves, rejects, or requests changes on an
approval gate, GoodTeam records a `reviewer_evidence_snapshot.v1` payload on the
existing approval decision action. The snapshot captures the advisory reviewer
state visible at decision time for the active run, current gate, and current
artifact identity, including completed, failed, queued, missing, and retry
lineage states. It stores summaries and local handles such as result/stdout/
stderr paths instead of large raw outputs. Snapshot creation is audit-only: it
does not run, retry, dispatch, approve, reject, enforce policy, or block the
human decision. Workbench render remains read-only and does not create these
snapshots.

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

- No automatic reviewer execution.
- No automatic reviewer retry.
- No background polling or scheduler rewrite.
- No dispatch or retry from Workbench render.
- No unrelated dispatch from the explicit execution or retry actions.
- No execution orchestrator changes.
- No autonomous approval gate enforcement.
- No approval action behavior changes.
- No approval auto-approve, auto-reject, or button disabling.
