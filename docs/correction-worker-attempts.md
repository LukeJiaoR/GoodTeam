# Correction Worker host protocol

## Availability

This slice connects the existing supervised correction loop to a distinct Worker
input, retained Git worktree, one-invocation receipt and same-round recheck. The
default external-executor admission list is **closed**. An optional exact-attempt
trusted native Codex authority connects the edit-only correction protocol to
dispatch, host sealing, the original check and independent new-head review.
It is separate from Codex Skill/reviewer authority. Claude, Pi, unknown adapters
and PTY remain unavailable. No adapter acquires permission from a binary name,
issue text, an argv safety flag or a cost acknowledgement.

Without explicit native startup authority, only built-in `fake` / `noop`
simulations are admitted. They cannot implement a real repair. The Workbench
disables preparation for a blocked captured executor,
shows the reason, and keeps the existing manual fix/check path available. Changing
a captured task's adapter is not a supported way to unlock it. The native route
is code-integrated, with **real Codex acceptance still pending**. It does not
establish verified OS confinement or autonomous delivery.

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
one external invocation; it does not create native runtime authority. Cost is
unknown, not a dollar/token cap.
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

## Codex edit-only contract and host sealing

The default admission whitelist is **unchanged**. Only the distinct typed
`TrustedCodexCorrectionAdmission` can select `correction_codex` and
`correction_seal` through the Worker path. There is no generic CLI backend
fallback, native credential projection, implicit model selection or parent retry.

The command proposal is explicitly scoped to the locally observed 0.159.2 CLI
shape, independently of the older pinned-container and 0.160.0 Reviewer routes.
Its construction performs no binary/version/login/config probe or model call.
It requests a fresh JSON turn with `workspace-write` sandbox and `never` approval
policy, without a sandbox/approval bypass or additional writable directory. Its
environment is a fixed allowlist; login/config stay in the selected native home,
are not read or copied by the helper, and remain unverified native authority.
Generating argv is not proof of confinement, effective configuration, route
availability, request limits or cost limits. Production activation is separate.

The edit-only prompt preserves the original task, approved-plan binding when
present, and complete bound feedback, but replaces the instruction to commit with
a host-owned seal. Existing tracked regular files may be edited or deleted. The
first seal also admits new regular files within the assigned worktree and task
scope, including nested paths, raw binary bytes and executable files. Tracked and
new files share the existing 10,000-file / 32 MiB capture limits.
An anchored metadata walk also caps the worktree at 20,000 directory/file entries
and 64 directory levels; it rejects special nodes that Git's listing may omit.

New-file admission is deliberately conservative: hidden path components,
casefold path collisions, file/directory replacements, nested repositories,
known sensitive filenames (including the shared repository content policy and
auth/credential/secret state), links and special modes reject. All ignored files
reject the entire seal rather than being silently omitted. When additions are
present, every existing `.gitignore` must retain its exact original bytes and
mode; new `.gitignore` files are rejected with other hidden paths. This prevents
worker edits to ignore rules from declassifying files in the same attempt.
New hidden configuration files and simultaneous ignore-rule changes require a
future separately designed expansion. Filename policy is not a secret-content
scanner or proof of runtime confinement.

Missing/linked parent directories, symlinks, hardlinks, changed index, changed
HEAD, redirected worktrees and out-of-scope output identities still reject.

Sealing requires the existing protected run-started, dispatch, host-return and
completed run-result chain. For this exact one-shot correction path, host-return
can only follow confirmed process-group cleanup; failed/unknown cleanup does not
produce a sealable result. A model's final JSON is bound to that return digest and
the original attempt/base, but its summary is only untrusted commentary. It cannot
select files, a workspace or a candidate commit.

The host captures complete raw admitted bytes, uses hook/filter-free bounded Git
plumbing to write objects and a fixed-message commit, then compare-and-swaps only
the dedicated attempt ref and resets only that worktree's index. No hook, filter,
fetch, signing command or inherited credential environment is used. Unchanged
trees do not create a new candidate. The original checkout, branch and index are
not changed. A seal-started record precedes object/ref/index mutation; interrupted
sealing stays unknown and is never automatically retried or repaired. A completed
seal is replayable only while its exact clean candidate is still present. New
files become tracked in that sealed commit. Replay never admits more files:
any later untracked file, including ignored output from a check, blocks retained
seal validation. The manifest is recaptured before ref publication and strictly
verified after the dedicated index update, before recording a successful seal.

Offline contract tests use the existing explicit test-only runtime composition,
a real Python subprocess and real isolated Git worktrees. They exercise sealing,
ordinary same-round recheck, negative provenance/filesystem cases, cleanup failure,
and interrupted publication. They do not demonstrate Codex compatibility, actual
sandbox isolation, model coding or live correction acceptance. The code milestone
in #347 is complete; its deferred real acceptance is tracked separately in #567.
The native integration additionally exercises a typed per-attempt authority,
dedicated dispatch, retained seal and the full original-check/new-head-review
chain using an explicit offline Python subclass. Its receipts say
`contract_fixture`; they are never evidence of a real Codex call.

## Explicit native continuation (default disabled)

This is a trusted-self-use route, not strict third-party Skill admission. Before
activation, the operator must select and authorize the exact task, native binary,
existing login home, model, native-managed configuration/tool exposure and unknown
cost. The host checks native login/config file metadata without reading their
contents. It never copies credentials or overrides native provider/tool settings.
Requesting `workspace-write` and `never` is not proof that native managed external
tools, shared Git administration or arbitrary same-UID writers are confined.
No native configuration, account or permissions are activated by installation.

An already prepared attempt supplies its immutable `input` reference and retained
workspace path. A separately created, private canonical-JSON policy outside the
project must have exactly these fields (placeholder values below are not a grant):

```json
{
  "schema_version": "goodteam.trusted-codex-correction-policy.v1",
  "project_root": "/absolute/registered/project",
  "input": {"schema_version": "correction_worker_input.v1", "original": "EXACT ORIGINAL REFERENCE OBJECT", "job_id": "EXACT PREPARED JOB ID", "payload_digest": "EXACT DIGEST"},
  "workspace": "/absolute/registered/project/.goodteam/sandboxes/EXACT_JOB/worktree",
  "codex": "/absolute/existing/native/codex",
  "codex_home": "/absolute/existing/native/home",
  "version": "0.159.2",
  "binary_digest": "sha256:EXACT_BINARY_DIGEST",
  "model": "EXPLICIT_AUTHORIZED_MODEL",
  "expires_at": 0,
  "max_seconds": 120,
  "max_invocations": 1,
  "trusted_task_inputs": true,
  "accept_native_managed_configuration": true,
  "accept_unknown_cost": true,
  "provider_request_limit": null,
  "usd_limit": null
}
```

`input` must be copied as the complete protected reference, including the original
reference object, not reconstructed from issue text. `expires_at` must be an
integer Unix timestamp in the next 24 hours; `max_seconds` is 1–600 and no greater
than the captured attempt. The private policy must have mode 0600. Binary digest,
safe file metadata and exact version are checked; version probing is bounded and
owns its process group. Missing/mismatched native files or a different CLI version
reject; do not fix this by weakening permissions or silently choosing another route.

Only these CLI entries accept the existing policy:

```sh
goodteam execution run-correction-worker \
  --project-root /absolute/registered/project --execution-id EXECUTION_ID \
  --job-id JOB_ID --trusted-codex-correction-policy /private/existing-policy.json \
  --acknowledge-external-cost

goodteam execution continue-automatic-review \
  --project-root /absolute/registered/project --execution-id EXECUTION_ID \
  --trusted-codex-correction-policy /private/existing-policy.json \
  --trusted-codex-review-policy /private/existing-reviewer-policy.json \
  --acknowledge-external-cost
```

The second entry uses the frozen automatic start and a separate existing Reviewer
authority. It seals the returned edit, reruns the exact original argv/criteria/time
selection in that worktree, then requests a fresh independent report for the new
head. It neither creates a delivery plan nor gives a Builder, HTTP server or
another attempt native authority. No policy is loaded from project/job JSON or an
environment switch. Embedded trusted callers may pass the typed correction
dependency separately; defaults supply neither authority.
Captured `read_only` or unknown original execution policies reject rather than
being upgraded to writable execution by correction authority. An explicitly
selected original model must match the native policy model.

Policy content/expiry, process identity and binary identity are checked again at
dispatch and during polling. Native required proof precedes execution and remains
bound through dispatch and host return. Candidate validation, including explicit
`--correction-worker` and completed-check replay, requires the exact host-sealed
candidate. Removing a native/seal proof never changes this to the manual path.

A completed return interrupted **before** seal-started can finish sealing on an
explicit continuation without a new native policy or model call. Once sealing
started, missing seal-result is unknown and cannot be retried. Terminal checked
review can be read/replayed without credentials. The original process, check and
Reviewer are not rerun on successful replay. The check must keep the retained
worktree clean, including ignored files required by strict seal replay; select
commands that do not leave caches/artifacts there.

Milestone status: code-integrated and offline-contract-tested; real native CLI,
model quality, managed-tool behavior and end-to-end #347 acceptance remain pending.
