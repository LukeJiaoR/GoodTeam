# Captured issue task inputs

New GitHub issue intake for a server-registered project with default methods now
freezes its existing `task_contract.v1` in `task_execution_snapshot.v1`. The
snapshot includes the original task, source revision (or `unknown`), trusted
scope namespace, first execution identity, role/backend, capture operation,
contract revision 1, and deterministic context policy. The legacy TaskContract
DTO remains compatible and is not advertised as deeply immutable.

## Input and handoff

The application snapshot owns canonical finite JSON bytes. Original nested
containers and decoded views cannot mutate it. The producing orchestrator passes
the committed object directly to the Worker store; same-process consumers keep
that object. Recovery decodes the original authorized capture. Disk admission
checks are not RAG retrieval or an internal-agent memory handoff mechanism.
The full contract is rendered deterministically. A mandatory contract larger
than the context limit is rejected rather than silently shortened. Existing
role memory remains supplemental external context and cannot change the fixed
contract. This is a first task-input vertical slice, not the complete evolving
TaskState, compactor, or cross-process handoff platform.

GitHub issue content is `captured_external_source`, **not user confirmation or
permission**. Commands mentioned in a contract are proposed task data, not
permission to execute validation. Existing project-local task files are not
promoted to trusted captures. Unregistered/old executions remain explicitly
`legacy_unverified` in the new evidence projection.

## Commit and recovery

The control plane resolves its existing registered workspace/project scope and
places the store under its configured Operator state directory, outside the
workspace and project root. A project-root-derived enrollment bucket remains
identifiable if that project's registry entry is lost; dispatch then requires
restoring its original registration. Other never-enrolled standalone projects
remain legacy. The snapshot scope still binds workspace, project key and root.
A new snapshot is committed before the issue
binding is published. Each execution uses a stable cross-process lock and the
existing anchored, bounded, atomic-replace/fsync file primitives (POSIX; no
unlocked fallback on unsupported platforms).

1. `snapshot.json` is atomically written once. If interrupted before the next
   step, intake resumes this exact pending capture, without fetching the current
   issue or trusting a mutable task ledger.
2. `required.json` binds the digest and deterministic `job-input-*` identity.
   Once this marker is durable, intake may publish its project binding.
3. Compatibility Worker files are materialized. `job-ready.json` binds the
   original Worker request after materialization and initialization of its empty
   receipt journal. Without it, dispatch is
   blocked; intake may finish interrupted initial materialization with the same
   identity. It cannot alter a committed Worker request on reuse.
4. The Worker checks scope, identity, digest, request binding, and readiness
   before adapter routing and again at transport boundaries. Removing a job
   reference, changing its run ID/backend, losing the snapshot, or losing the
   project registration cannot turn its protected job ID into a legacy job.
   The orchestrator also checks its caller-supplied execution identity against
   the capture before trusting the project's run-to-job mapping. Legacy Worker
   entries/creation reject run or execution identities already captured here.

The capture operation is commit-once. Repeating the same canonical request
reuses the original bytes and host timestamps; different content or role/backend
conflicts. New task revisions/attempt admission are deliberately not implemented
by this initial-input contract. Capture, binding, job projection and Execution
Ledger are not a single transaction. A loss of all original authority and every
external marker cannot be distinguished from legacy history; hashes are not
signatures or protection against an attacker rewriting all evidence.

Operator-root separation is an application trust boundary, not an OS sandbox.
Existing legacy execution backends have their existing permission characteristics;
this change does not establish isolation against an unrestricted same-user
process that can modify Operator state. The existing custom Skill/runtime gates
remain in force. Custom-method issue executions keep their separate method
protocol; this snapshot path neither activates them nor appends legacy methods
to them. A method-protocol change during new default intake is blocked.

## Observations and outcomes

The evidence manifest reports the protected snapshot, source trust/revision,
input preparation receipts and per-position acceptance criterion IDs. Duplicate
criterion text keeps separate IDs. All acceptance remains `unknown` without a
bound validation receipt. Legacy workflow `final_status` is not changed into an
acceptance decision by this feature.

- `snapshot_committed`: original task input has a durable capture.
- `worker_input_prepared`: the host rendered exact prompt bytes; their SHA-256 is
  linked to the same snapshot and context policy. Repeated identical preparation
  is idempotent.
- `transport_attempt`: the host entered the call boundary with those bytes. It
  does **not** prove the process/provider received them. Provider receipt remains
  `unknown`, including a crash immediately after recording this marker.

A previously entered transport boundary blocks an automatic second invocation
with `task_input_transport_indeterminate`. Missing results/acks are not proof
that no effects occurred. A commit-once `dispatch.json` marker is durably written
before its receipt row and before the external call. A crash between those two
file writes is explicitly indeterminate, even if the call never happened. Loss
of either the marker or the journal alone cannot permit a new invocation: the
other records the attempt. A ready job with a missing journal is unavailable;
read/dispatch never repairs it as an empty history. Removing or rolling back
both independent proofs is outside this file backend's tamper-detection claim.
Existing completed orchestrator runs return their
record; this is not new acceptance evidence. The old legacy retry behavior is
unchanged. An explicit new-attempt/reconciliation API is subsequent work; this
slice does not promise external exactly-once execution or safe automatic retry.
Explicit Pi `steer`/`follow_up` controls recheck the original input at enqueue
and immediately before RPC consumption, without treating them as a second
initial dispatch. They keep the existing control authorization/ack protocol;
they do not revise the frozen task contract or prove new acceptance. Abort and
read-only session status remain available when task input is lost.

When authority is unreadable/missing, the API projection retains readable ledger
execution evidence but marks captured input unavailable. It does not substitute
an editable ledger contract. Source contents and absolute Operator paths are not
included in error codes.
If only handoff history is damaged, the original verified capture remains
readable with explicit `partial` / `handoff_status=unavailable`; acceptance stays
unknown, and automatic dispatch is still blocked.

## Verification limits

Focused tests use synthetic issues, real temporary-directory persistence and
process concurrency, an actual local Python stdin/stdout subprocess, and offline
transport doubles. They prove input byte binding, conservative receipts,
recovery/reuse and admission behavior. They do not constitute a live model task,
provider acknowledgment, exact-head acceptance/review proof, or Runtime isolation
validation. No provider, new embedding dependency, or credential setup is needed.
