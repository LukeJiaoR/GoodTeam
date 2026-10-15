# Correction Worker host protocol

## Availability

This slice connects the existing supervised correction loop to a distinct Worker
input, retained Git worktree, one-invocation receipt and same-round recheck. The
production external-executor admission list is **closed**. Existing Codex
Skill/reviewer authorities do not yet admit this correction protocol. Codex,
Claude, Pi, unknown adapters and PTY transport cannot acquire permission from a
binary name, issue text, an argv safety flag or a cost acknowledgement.

Only built-in `fake` / `noop` simulations are admitted. They cannot implement a
real repair. The Workbench disables preparation for a blocked captured executor,
shows the reason, and keeps the existing manual fix/check path available. Changing
a captured task's adapter is not a supported way to unlock it. A compatible,
verified Codex execution envelope is the next integration step; this release
**does not deliver production automatic coding or autonomous task completion**.

## Connected path

After an exact candidate check and a bound `request_changes` record, start the
existing loop. The Workbench can prepare an admitted simulation; explicit CLI
preparation may also inspect a blocked runtime's input/worktree without invoking
it. Preparation reserves one round before publishing the Worker job:

```sh
goodteam execution prepare-correction-worker \
  --project-root /path/to/project --execution-id EXECUTION_ID \
  --loop-id LOOP_ID --validation-receipt RECEIPT --head FULL_COMMIT_ID \
  --operation-id correction-1 --max-seconds 120

goodteam execution run-correction-worker \
  --project-root /path/to/project --execution-id EXECUTION_ID --job-id JOB_ID
```

The default per-attempt time limit is 120 seconds, at most 600, and must fit the
original loop's remaining wall budget. Built-in simulation needs no cost flag.
`--acknowledge-external-cost` is a separate explicit operator acknowledgement for
one external invocation **if** a verified adapter is supported later; it currently
does not enable any external adapter. Cost is unknown, not a dollar/token cap.
No HTTP route starts the Worker or accepts an executable command. The JSON-only
prepare route has the same configured-authority/exact-origin protections as
start/stop/review. Localhost by itself does not establish user authorization.

The original canonical `TaskExecutionSnapshot` is handed directly alongside a
small immutable feedback delta. No summary/RAG round trip reconstructs the goal.
Each attempt gets a new `job-correction-…-N` identity; original run/job/snapshot
stay intact. Preparation retains all tracked source bytes in a new worktree,
without copying ignored dependencies. The narrow correction Git materializer uses
no-checkout plus raw object materialization and an index-only read-tree, with
hooks, filters, lazy fetch and inherited credential environment excluded. This
is artifact separation, not OS isolation; the Git object database is shared.
Protected workspace-ready evidence binds the linked admin gitdir, `.git` bytes,
backlink, common directory and exact non-aliased attempt branch ref. Dispatch and
recheck reject preexisting swaps to original/sibling metadata or branches. This
observation does not confine an admitted process that itself changes shared Git
control files; a future real executor must separately establish that boundary.

An existing original transport needs its protected host-return receipt before
preparation. A mutable `completed` status is insufficient. Older executions whose
transport may have run but lack that receipt require human inspection; this
feature never manufactures a return from their status or retries the old job.

After an admitted attempt returns, the retained worktree is shown in the same
Workbench. A person inspects its committed candidate and explicitly reruns the
original local check with the additional selector:

```sh
goodteam execution validate-candidate \
  --project-root /path/to/project --execution-id EXECUTION_ID \
  --correction-worker JOB_ID --head NEW_FULL_COMMIT_ID \
  --operation-id correction-check-1 --criterion 0 -- ORIGINAL_ARGV
```

The selector chooses only the protected attempt's worktree. The check preserves
the original argv/criteria and belongs to the **already reserved** round. A new
bound review is required before another attempt. The trusted
[automatic candidate review controller](automatic-candidate-review.md) can
consume that review without a manual attestation, while preserving this
protocol's closed external-executor admission boundary. Original round/time/no-progress
limits still apply. Repeated trees stop; successful exits and human labels do not
verify semantic acceptance or authorize merge, deployment or verified memory.

## Recovery and process boundaries

Correction prompts resolve the exact captured check and review receipt through
the retained loop history. Machine reviews deliver the complete protected report:
all findings, details, recommendations, checks and cited source evidence, including
items beyond the short decision summary. Public job projections and a newer
Reviewer report cannot replace this input. The existing immutable attempt and
review receipt formats are unchanged; human-recorded feedback remains supported.

The handoff also includes the recorded host-check observation. Its output fields
are hashes and byte counts, not raw stdout/stderr. Missing or inconsistent report
evidence blocks the handoff. Complete feedback is checked against the existing
task-context byte limit before reserving a new round and again when assembling
the Worker prompt; over-budget feedback is never silently truncated. This changes
neither live-runtime admission nor merge/deployment authority.

Input/required proof, round reservation, workspace-started/ready, job binding,
authorization, run-started, dispatch, receipt journal and host return live under
the existing Operator root and stable per-execution lock. This root is not a
strong boundary against unrestricted same-UID writers. Individual publications
are atomic; the sequence is not a multi-file transaction.

- Exact completed replay returns the same outcome. Two contenders cannot invoke
  the attempt twice. The low-level Worker boundary also checks authority, identity
  and dispatch history; a direct `run_job` cannot bypass the explicit invocation.
- Missing input, job/receipt proof or an observed run-started without its final
  result remains unavailable/unknown, never fresh history. A live start additionally
  needs a one-use, current-PID in-memory handoff lease. Exceptions revoke it;
  restart, a fresh Worker store and an inherited fork cannot reuse a saved
  authorization as a new start. The independent dispatch marker consumes the
  actual transport once, under the same lock. A preparation crash
  after workspace-started requires inspection; worktrees are not deleted or
  silently recreated. Full coordinated rollback of all proofs is out of scope.
- The new CLI transport path creates an owned POSIX process group, bounds combined
  output to 1 MiB, polls stop/current-loop validity, and uses the smaller of the
  attempt timeout and remaining loop time. It terminates the owned group even
  after parent exit. Failure to confirm group cleanup leaves outcome unknown.
  Existing same-UID/escaped-process limits are not a sandbox guarantee. PTY and
  unsupported platforms fail closed for this new transport.
- There is no automatic transient retry. A local timeout returns failed after
  cleanup; incomplete output, revoked authority, lost acknowledgement or unknown
  cleanup requires human inspection. Stop is observed during the new Worker
  process; an already running local validation check retains its preexisting
  bounded-lock behavior.
- Host return means the host observed a Worker result. Provider receipt, semantic
  acceptance and actual correctness remain unknown. The retained result digest
  binds what the host observed; it is not a signed provider attestation.

New attempts do not publish a second ExecutionRun or write SandboxService ledger
metadata merely to create their worktree. Their Worker job remains the consumer.
Before creating or admitting its project projection, anchored directory checks
reject linked parents and symlink/hardlinked/special leaf files. Retained loop,
reservation or attempt proof also blocks the original Worker if one correction
marker disappears. These are fail-closed observations, not an OS guarantee
against arbitrary same-UID writers racing directory replacement.

## Verification scope

Synthetic registered projects exercise actual Git worktrees, a real offline
Python CLI subprocess through Worker prompt/receipt transport, a committed fix,
actual recheck in that worktree and the bound review-result API/UI. Tests
inject a test-only runtime capability in code composition; there is no production
flag exposing that capability. They do not prove Codex admission, paid-provider
access or model coding quality. Additional tests cover single-dispatch contention,
partial proof loss, stop/timeout/output bounds, unchanged original task/worktree,
raw checkout hook/filter exclusion, explicit CLI simulations, blocked-production
UI, and a subsequent round's identity and source lineage.
