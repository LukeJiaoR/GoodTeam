# Project Onboarding Runner (Track 2A)

Track 2A adds an internal, non-promoting runner for exploring one already-captured
repository snapshot through the bounded Repository Context Provider. It is a
control-plane foundation for later onboarding work, not a new way to mark a
project ready.

The runner can drive a provider-neutral model-tool-loop contract and persist a
validated `project_profile.v1` bundle. It can resume from completed operation
boundaries; an interruption while a model or tool call is in flight is instead
marked for reconciliation and is never replayed automatically. It does not
itself select, configure, or invoke a production external model service.

For the snapshot and seven-tool retrieval boundary it builds on, see
[repository-context-provider.md](repository-context-provider.md). The intended
end-state, including review and readiness policy, remains documented in
[the existing-project onboarding design](superpowers/specs/2026-07-28-existing-project-onboarding-design.md).

## Scope and authority

Each job is bound to exactly one project key, imported root binding, onboarding
job ID, and `snapshot_id`. The runner starts only with a matching immutable
`repository_snapshot.v1` and `repository_inventory.v1` already published in the
GoodTeam workspace. It rechecks that binding before every adapter dispatch and
before publishing a profile.

The Track 2A runner may produce a canonical, evidence-backed profile artifact,
but that artifact has **no readiness authority**. It does not modify
`readiness.json`, change readiness schema 2, add schema-3 steps, unlock
`start_workflow`, or make any claim that onboarding is complete for production
use.

The target repository remains read-only throughout the job:

- it is not mounted or exposed directly to the adapter;
- no project command, shell command, package-manager command, or build/test
  command is run by onboarding; and
- job data, observations, checkpoints, and profiles are written only under the
  GoodTeam workspace, never into `<target>/.goodteam/context` or another target
  path.

## Model-loop boundary

`ModelToolLoopAdapter` is a narrow, provider-neutral protocol. The runner owns
the repository boundary and invokes the adapter one turn at a time. An adapter
receives a `ModelTurnRequest` containing only:

- the current turn and snapshot ID;
- a sanitized, allowlisted snapshot/inventory/budget context;
- schemas for exactly the seven public Context Provider tools; and
- authoritative results from prior tool calls.

The adapter never receives a target-root path, root device/inode, workspace path,
filesystem descriptor, shell, command runner, or a newly discovered provider
method. Repository bytes can reach it only inside persisted Context Provider
observation envelopes. Repository text is untrusted data and cannot expand this
protocol or override its policy.

For a turn, the adapter must return either bounded tool calls or one profile
draft, never both. The runner validates each requested tool against the fixed
tool contract, executes it through `RepositoryContextProvider`, persists the
result in the snapshot-locked observation ledger, and feeds that authoritative
envelope into a later turn. Model-supplied file paths, hashes, evidence IDs,
snapshot IDs, and invocation identity are not accepted as authority.

`ProjectProfileValidator` converts a draft into canonical `project_profile.v1`.
Claims cite only persisted `observation_id` values; the validator derives claim
and evidence identifiers and evidence metadata from the matching observation
ledger. A model draft cannot assert a user confirmation. Invalid, mixed-snapshot,
or uncited profile data fails closed rather than being published.

## Scripted adapter and production boundary

`ScriptedOnboardingAdapter` exists for deterministic unit/integration tests and
UI-preview fixtures. It is mechanically marked `test_only: true` and
`external_invocation: false`; its preflight report cannot satisfy a production
onboarding gate. Supplying a model-response invocation claim cannot change that
fact.

Track 2A ships no production external-adapter transport, credential discovery,
provider selection, or real model invocation. A future adapter must implement
the same protocol, supply server-owned identity and preflight evidence, preserve
the read-only boundary, and be validated with isolated live-path evidence before
it can participate in readiness.

## Workspace artifacts

All artifacts are descriptor-anchored under one project state directory:

```text
<workspace>/state/projects/<PROJECT_KEY>/onboarding/
  snapshots/<SNAPSHOT_ID>.json
  inventories/<SNAPSHOT_ID>.json
  jobs/<JOB_ID>/
    job.json
    prompt.json
    status.json
    observations.jsonl
    model-events.jsonl
    checkpoints/
      current.json
    lease.json
  profiles/<PROFILE_ID>/
    project-profile.json
    artifact-manifest.json
```

`job.json` is immutable metadata that binds every later record to the project,
root, job, and snapshot. `prompt.json` is a bounded, redacted record of the
model-facing setup. `observations.jsonl` is the existing append-only Context
Provider ledger; `model-events.jsonl` records bounded, redacted model-loop
events. Status and checkpoint records are atomically replaced only by the active
lease holder.

Profiles are immutable bundles. `project-profile.json` is written before
`artifact-manifest.json`; the manifest is the final completion marker and binds
the profile digest to its job and snapshot. Readers reject a missing, malformed,
or inconsistent manifest, so an interrupted publication is never treated as a
complete profile. A retry may add a missing final manifest only after proving
that the durable body exactly matches its canonical profile; missing, malformed,
or conflicting partial bodies remain fail-closed.

The human-readable projection renderer is deterministic, but Track 2A does not
expose profile projections through a CLI, HTTP API, or UI.

## Recovery, checkpoints, and leases

The runner checkpoints consumed model-turn state before invoking an adapter, and
checkpoints the completed state after every model response and tool result. It
also checkpoints a tool call as pending before the Context Provider reserves a
result. The checkpoint therefore includes the resumable budget state so a restart
cannot reset model turn, tool-call, observed-file, character, token, or cost
ceilings. Completed observations and redacted model events remain available to a
resumed job.

A lease has an owner and opaque token. A runner must acquire it before changing
status, checkpoints, model events, or publishing a profile; it must renew it
while work continues and release it when it stops. Expired or released leases
may be acquired by a new runner. A stale runner with an old token cannot resume
writing or publish after another runner owns the job.

Recovery is valid only from a completed checkpoint boundary while immutable job
metadata, the published snapshot, and the current imported-root binding still
agree. If a model or tool call was in flight, its result is uncertain and the
runner marks the job `recovery_required` rather than replaying it. Snapshot/root
drift also fails closed with the normal repository snapshot/root error family; it
requires a new capture and a new onboarding job rather than mixing old
observations into a new profile. Recovery does not broaden a failed tool request,
bypass a hard budget, or turn a test-only invocation into external-model evidence.

## Explicitly out of scope

Track 2A deliberately does **not** add:

- a readiness schema migration, onboarding readiness step, readiness promotion,
  or workflow capability change;
- profile-review UI or API endpoints;
- `accept`, `accept_limited`, `request_more`, `correct`, or user-confirmed claim
  handling;
- a production external model adapter, credential configuration, or live model
  invocation;
- project command execution, direct target filesystem access, or target writes;
- a public onboarding CLI, HTTP resource, browser route, or profile browsing
  surface; or
- downstream task-context promotion, freshness rules, or incremental profile
  refresh.

Those are later deliveries. In particular, a persisted Track 2A profile is
research evidence for development and tests, not an approval decision and not a
substitute for rereading current files before a delivery Worker changes them.

## Verification boundary

The runner, artifact binding, lease ownership, evidence canonicalization, and
adapter protocol are Tier 3 control-plane contracts. Their focused tests belong
alongside `tests/test_onboarding_store.py`, `tests/test_onboarding_backend.py`,
`tests/test_onboarding_profile.py`, and the runner contract tests, followed by
the full backend suite and Ruff as described in
[testing-strategy.md](testing-strategy.md).

A future real adapter, readiness mutation, or browser/API surface is Tier 4 and
needs an isolated-workspace live smoke. No mutating onboarding smoke may use an
active real project workspace.
