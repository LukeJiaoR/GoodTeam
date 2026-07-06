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
- Memory provenance refs are read from existing `provenance_written` ledger
  closure events and existing memory WAL events with source
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

## Consumers

For #216 Review Workbench, this manifest is the stable backend contract for a
future evidence panel: the UI can render source issue, PR, CI, review, merge,
memory provenance, and incomplete status without knowing ledger internals.

For audits, the manifest gives a deterministic replay artifact that can be
snapshot-tested and compared across regressions.

For memory provenance, the manifest references existing memory events and ledger
closure events, preserving the lifecycle/provenance closure established by #219
without reopening or patching #219 itself.

## Out Of Scope

This slice does not close #217. Remaining #217 work still includes broader
system adoption: all execution components writing the complete required event
chain, Review Workbench consuming the manifest, and memory references becoming a
first-class ledger-linked contract.

This slice also does not implement #216 UI work, #218 sandbox execution,
Telegram/TG flows, live GitHub synchronization, analytics, or dashboard
visualization.
