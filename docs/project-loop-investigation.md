# Project-loop investigation: L1 protocol slice (#686)

This is an incremental extension of outcome discovery, not a second scheduler.
An explicitly authorized discovery turn may propose a read-only investigation
instead of inventing an implementation task when a material fact is missing.
The host chooses `research` / `read_only`; Manager still only decides and delegates.

## Explicit opt-in, unchanged old grants

The existing `start_project_continuation` action accepts the optional boolean
`discovery.allow_investigation`. It is omitted by default. An example discovery
object, inside the existing confirmed request, is:

```json
{
  "objective": "Use verified import results to identify the next useful improvement.",
  "allowed_write_domains": ["src/import", "tests/import"],
  "max_new_tasks": 2,
  "max_planning_calls": 3,
  "allow_investigation": true
}
```

This authorizes read-only investigation of the same registered repository using
its configured Researcher and existing runtime access limits. It does not grant
external network, credential, test-execution, merge or deployment permission.
Read-only still may cost model calls; ordinary privacy/runtime constraints apply.
Both task types consume the original new-task, batch and planning limits.

Omitting the flag preserves the exact old normalized grant. Adding it changes
existing scope identity and cannot modify or revive a stopped or active old grant
through replay. Use a new, explicitly confirmed grant through the normal controls
when its scope needs to change. No new grant is created by this patch itself.
The existing UI does not expose this experimental flag yet.

## Model output and the existing queue

The ordinary output remains `new_task` plus `rationale`. In the explicitly opted-in
packet, a task may additionally declare `task_type: "investigation"`. It must use
`write_domains: []`, concrete criteria for the factual question, already delivered
authorized dependencies and allowed source references including the prior receipt.
The input also states whether the configured Research role is dispatchable.
An unavailable Researcher is not silently replaced with a writer or the Manager.

Implementation remains the default, with its original nonempty allowed write
scope. The model cannot supply role, runtime, model, argv or execution permissions.
The host validates the selection again when building its task intent, then calls
the existing create-once task publication and Task Assistant queue. Existing
Worker selection, readiness, source/authority revalidation, Stop, reservation,
isolation and output-capture checks retain their owners. No parallel task ledger
or research dispatcher is added. Prompt instructions are not proof of runtime
confinement or that the Manager cannot inspect files.

## Deliberate boundary: L2 is not implemented here

This slice ends at ordinary Worker assignment and report return. A research
report remains review evidence. It is not automatically accepted, marked done,
converted to a Git delivery receipt or counted as a second engineering delivery.
The existing continuation may therefore wait at review after the investigation.
No background report-to-Manager continuation is added in this patch.

L2 must provide the separately bound report-feedback / re-decision path without
requiring fake Git commits or weakening review and delivery rules. Failure and
partial-result handling (L3), Human wait/resume (L4) and real two-delivery product
acceptance (L5 / #419) remain open. This first slice must not be described as
completed autonomous research or a completed #686 Loop.

## Validation scope

`tests/test_project_discovery_investigation.py` is one representative pure
contract case covering explicit opt-in, unchanged legacy identity, no mutation
of old authority, host-fixed read-only routing, unavailable capability, source
and dependency checks, malformed flags, forbidden role/permission overrides,
legacy writing and null behavior, and existing stopped-grant refusal.

The existing outcome-discovery and Manager-assignment integration cases must
still run against the complete checkout. The isolated pure checks do not exercise
actual database storage, queue publication, Worker dispatch, runtime read-only
confinement or real Manager judgment. Those remain validation requirements, not
claims supplied by this document. CI remains paused; do not start GitHub Actions
or real project/paid-model runs to fill the gap without their required authority.
