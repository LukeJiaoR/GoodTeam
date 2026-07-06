# Canonical Evidence Query Surface

This document defines the first canonical read surface for execution evidence.
It belongs to #217 and turns the append-only execution ledger into a stable
manifest for audit, Review Workbench, and memory provenance consumers.

## Source Of Truth

The execution ledger is the source of truth. Evidence queries do not call
GitHub, run workers, refresh CI, write memory, or append ledger events. The
query surface reads:

- `.goodteam/executions/<execution_id>/task.json`
- `.goodteam/executions/<execution_id>/run.json`
- `.goodteam/executions/<execution_id>/events.jsonl`
- existing role memory events that already reference the execution provenance

The manifest is derived from `ExecutionLedger.replay(execution_id)`. Repeated
queries over unchanged disk state must return equivalent results.

## Query API

`ExecutionAPI.get_evidence(execution_id)` returns an
`execution_evidence_manifest.v1` payload. The same read-only builder is exposed
through `ExecutionEvidenceService.get_evidence(execution_id)`.

The manifest contains:

- `execution_id`
- `task_id`
- `source`, `source_issue`, and `github_ref`
- `pr` with repo, number, ref, branch, and commit SHA when known
- `ledger_events` in replay/file order
- `ci_observations` and latest `ci_summary`
- `review_observations` and `review_summary`
- `merge_summary`
- `memory_provenance_refs`
- `memory_provenance_integrity`
- `final_status`
- `incomplete_reason`

The payload is JSON-ready and deterministic. It intentionally does not include a
query timestamp or other runtime-generated data.

## Replay Semantics

`ledger_events` preserve append order from `events.jsonl`. Higher-level sections
are projections from those events:

- CI observations come from `ci_observed` ledger events.
- Review observations come from `review_recorded` and `pr_comment_recorded`
  events.
- Merge summary comes from the latest `merge_completed` event.
- PR identity is reconstructed from the stored run and PR lifecycle events.
- Memory provenance refs are rebuilt from existing `provenance_written` ledger
  closure events plus existing memory WAL events with source
  `execution:<execution_id>:final_provenance`.

Incomplete executions return a manifest instead of an unexplained exception.
Runs with no PR are `pending` with `incomplete_reason="pr_missing"`. Replays
with events but no stored run are `incomplete` with
`incomplete_reason="execution_run_missing"`.

## Append-Only Guarantees

Evidence query is read-only by design:

- It does not append ledger events.
- It does not create or update memory.
- It does not finalize provenance.
- It does not fetch live GitHub state.

Final provenance closure remains the responsibility of
`ExecutionEvidenceService.finalize_provenance(execution_id)`. That path may
write memory and a closure event. The query path can later read those refs but
must not create them.

## Memory Provenance Refs

The ledger is the source of truth for memory provenance closure. A completed
execution may write one role memory event and one `provenance_written` ledger
event. The ledger closure records the stable `memory_event_id`, the execution
evidence hash, and the memory placement fields needed to audit the projection.

`memory_provenance_refs` is the first-class contract that joins those surfaces:

- `execution_id`
- `memory_event_id`
- `ledger_event_id`
- `source`
- `role`
- `horizon`
- `evidence_hash`
- `created_at` from the ledger closure event
- `timestamp` from the memory WAL event
- `supersedes`
- `is_superseded`
- `status`
- `issues`

The expected complete state is one ref with `status="complete"`, a non-empty
`memory_event_id`, and a non-empty `ledger_event_id`. The `source` remains
`execution:<execution_id>:final_provenance`, but consumers should use the IDs as
the durable link rather than treating the source string as the relationship.

The `provenance_written` event points back to the evidence/provenance surface
with `execution_id`, `source`, `role`, `horizon`, `evidence_ref`, and
`evidence_hash`. The memory WAL event points back through its `source` and is
joined to the closure by the deterministic `memory_event_id`.

Repeated `finalize_provenance(execution_id)` calls are idempotent. If a
`provenance_written` event already exists, finalize reuses it and does not write
another memory event or ledger event. If a matching memory WAL event exists but
the ledger closure is missing, finalize can safely append the missing ledger
closure and link it to the existing memory event.

Pending, incomplete, and failed executions do not write memory provenance. Their
evidence manifests still report a pending integrity reason such as `pr_missing`,
`merge_missing`, or `execution_failed`.

## Memory Provenance Integrity

`memory_provenance_integrity` is a read-only audit summary for callers that need
to decide whether provenance is complete:

- `complete`: true only when exactly one ledger closure and one matching memory
  WAL event form a clean ref.
- `pending` and `pending_reason`: true when no provenance exists yet because the
  execution is not eligible or finalize has not run.
- `missing_memory_event_ids`: ledger closures whose `memory_event_id` no longer
  exists in memory WAL.
- `orphan_memory_event_ids`: memory WAL events with the execution provenance
  source but no ledger closure link.
- `duplicate_ledger_event_ids`: extra `provenance_written` ledger events beyond
  the first closure.

Broken and orphan refs are returned in `memory_provenance_refs` instead of being
silently dropped. This keeps drift auditable and gives future repair tooling a
stable target.

## Consumers

For #216 Review Workbench, this manifest is the stable backend contract for a
future evidence panel: the UI can render source issue, PR, CI, review, merge,
memory provenance, integrity warnings, and incomplete status without knowing
ledger internals.

For audits, the manifest gives a deterministic replay artifact that can be
snapshot-tested and compared across regressions.

For memory provenance, the manifest links existing memory events and ledger
closure events by ID, preserving the lifecycle/provenance closure established by
#219 without reopening or patching #219 itself.

## Out Of Scope

This slice does not close #217. Remaining #217 work still includes broader
system adoption: all execution components writing the complete required event
chain, Review Workbench consuming the manifest, and memory references becoming a
first-class ledger-linked contract.

This slice also does not implement #216 Review Workbench UI work, #218 sandbox
execution, Telegram/TG flows, live GitHub synchronization, analytics, or
dashboard visualization.
