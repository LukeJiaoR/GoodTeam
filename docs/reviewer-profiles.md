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

This registry does not read worker status files or enforce evidence. It only
declares what a later reviewer policy gate should expect.

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

## Non-Goals

- No runtime dispatch behavior changes.
- No worker invocation behavior changes.
- No execution orchestrator changes.
- No approval gate or policy enforcement.
- No Review Workbench UI changes.
- No parallel reviewer execution.
