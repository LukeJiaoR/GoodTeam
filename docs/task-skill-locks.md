# Task Skill locks (S4 / #444)

## Status: S4a foundation; production integration is tracked in #461 (S4b)

The user-approved delivery split is: review and merge the non-activated foundation
in PR #460, then implement production wiring in a separate PR for #461, and only
then proceed to #445. #444 remains the complete S4 acceptance contract and stays
open until both slices pass. Splitting delivery does not remove any acceptance,
security, identity, recovery or verification requirement.

S4a introduces `TaskSkillLockStore`, `SkillAvailabilityStore`, portable
`task_skill_context.v1` references, and `ProjectTaskSkillService`. Workflow state
can preserve the references, and the shared service composes real Team/Library
truth with current revocation checks. It is not yet a working end-to-end S4 slice.
Do not close #444 or enable Runtime loading on the strength of these tests.
Task-start producers and Worker dispatch still need to call the shared service.
Reviewer retries preserve original inputs, but Skill-backed retries remain
explicitly blocked until the Worker integration is complete.

S4a can merge only after reviewing its actual changed behavior (including state
serialization and legacy Reviewer retries) and passing exact-head CI. #461 owns
the remaining integration map below and must start from the merged main. No
subsequent Runtime capability is implied by a foundation merge; #440 remains the
separate live-conformance gate. Existing UI continues to distinguish saved-only
configuration from active execution.

## Contract

An authorized task-start command supplies an explicit `TaskSkillIdentity` and a
lazy, server-owned `TaskSkillPlan` resolver. The four identity kinds are
`workflow_run`, `issue_execution`, `direct_task`, and `manager_message`. An issue
execution is not assumed to be a workflow run. Manager messages must not use the
shared `task-assistant:<project>` run identifier as their logical identity.

`begin` invokes the resolver once, freezes the thin Role contracts and effective
methods for every declared subject, publishes an immutable resource, durably
reserves its task and command key, pins all available versions using deterministic
holders, and then commits readiness. A failed pin leaves a preparing record.
Explicitly retrying the original start command resumes that record without
consulting current project bindings. Reads do not recover, adopt new methods,
create pins, or migrate legacy in-flight tasks.

Unavailable methods are recorded per subject. A missing unused Builder method
need not prevent deriving a valid Reviewer snapshot. Empty custom bindings and
unresolved default presets remain blocked; a loader cannot silently fill them.
Disabled bindings are preserved in the captured configuration but not pinned or
promoted into the effective method set. Defaults require an explicitly resolved
preset at the capture boundary, not a late default lookup.

`snapshot` derives a subject's methods from the original task lock. It persists
`skill_execution_snapshot.v1` under a stable job command key. The full job request
must include its task/context evidence, immutable file references or byte digests,
output contract, runtime, execution policy, and task-required methods. A mutable
path alone is not immutable evidence. Changing those inputs under the same key
conflicts; callers must not put the changed payload hash into a new key and
accidentally schedule twice. Unknown subjects or newly required unbound Skills
need an explicit bounded change, not a query for the latest project binding.
Matching snapshot replays also sync the registry directory: a prior visible
replace is not proof that its directory entry was durably published.

`preflight` verifies the registered job reference, original task/owner, methods,
job request, loader version, package bytes, and retained pins. It requires a
trusted current availability/revocation reader with an exact scope, Skill/version,
boolean availability and revocation state, and a status revision. Missing or
unknown status fails closed. `SkillAvailabilityStore.current_status` implements
that reader and `ProjectTaskSkillService` supplies it as a trusted dependency.
A successful preflight does not authorize a provider, prove #440 conformance,
prevent a later revocation race on its own, or prove that a Skill entered a model's
context. Dispatch and each resource load must enforce their current checks at
the appropriate boundary.

## Portable workflow-state references

`task_skill_context.v1` contains exactly its schema version, an explicit bounded
task identity, and a versioned task-lock reference. It contains no filesystem path,
caller-selected status reader, or Runtime permission. Structural validation binds
the task-lock ID to the canonical owner scope and task identity, rejects unknown
schemas and detached references, and copies mutable input rather than aliasing it.
This validates the transport contract, not the existence or authenticity of the
referenced object; trusted-store reads and preflight are still mandatory.

`AgileDevState.task_skill_context` is an explicit supported field. Construction,
state writes, and checkpoint writes validate its workflow kind, project and run.
Current-state and archive readers preserve it, and a project-specific read rejects
a valid context stored under another project's path. An explicit project override
cannot rehome a task reference. The field survives current/checkpoint/archive
serialization without adding unknown dataclass fields. API consumers must still
carry it through any separately maintained projection whitelist.

Only an absent legacy field or the explicit empty object represents legacy state.
Malformed, null, unknown-version, cross-kind or cross-run references are not
silently converted to legacy. Reading or serializing state does not query Team,
open the Operator Skill store, acquire pins, or construct a replacement lock.
This field alone does not bind a new run: the authorized start producer must call
`begin` and save its returned context before dispatching the first job.

## Concrete project service

`ProjectTaskSkillService` derives the Operator root from server configuration and
uses the existing single-operator Library scope. The workspace and all declared
Worker roots are excluded from managed storage. A job cannot select another store
root, owner, availability reader or supported-loader allowlist in its payload.

`begin` requires the confirmation's Team revision, a bounded subject inventory,
exact thin Role contracts, confirmed task input and any explicitly supplied
default package references. Its lazy resolver takes one serialized Team snapshot;
all selected roles come from that revision. It does not perform one latest lookup
per role and does not lazily migrate Team state. An unknown subject or stale new
confirmation is rejected. The confirmed inventory/contracts/default references
and revision are fingerprinted with the task input; changing them under the same
logical start key conflicts. Replaying the original request bypasses that resolver
and preserves the original lock even after a project update or default restore.

Only the first-time authorized start initializes availability metadata. A replay,
read or preflight cannot reset missing revocation metadata. `snapshot` preserves
the caller's logical job key and delegates exact input comparison to the immutable
registry. `preflight` always supplies the real current-status reader; an availability
claim inside `job_input` has no authority. The server-owned metadata-loader
allowlist defaults to empty. Configuring a metadata loader does not establish
Runtime support or bypass #440/#445.

The common service is a production dependency available for the entrypoints below.
Its integration tests call real Team commands, Library installation, lock/pin
persistence and revocation services. They do not call a Worker or prove those
entrypoints have already been migrated.

## Current availability and security revocation

`SkillAvailabilityStore` is an Operator-owned service over the existing package
scope. It is not an HTTP authorization layer, a package-supplied permission flag,
or a replacement for Runtime conformance. The caller authorizes the root/actor
and keeps the directory outside Worker-writable roots.

Explicit `initialize()` publishes an initial registry directory with its complete
index through an atomic rename. Status reads never call initialization. An
existing registry directory whose metadata is missing, corrupt, from another
owner, or an unknown schema is rejected; it is not silently reset to unrevoked.
Within an initialized registry, a valid exact package version without a revocation
record is available. Missing or corrupt package bytes are unavailable.

`revoke()` serializes an exact-version decision against the current status
revision. Its receipt binds actor, reason, Skill and version to a stable command
key. Identical retries return the original receipt; different inputs conflict.
The CAS revision is a precondition, not part of the semantic replay fingerprint.
Another explicit command for an already revoked version records a no-op receipt
without replacing the original security event. Revocation is terminal here; no
un-revoke operation is provided.

The registry atomically stores the decision and receipt together. Partial writes
cannot corrupt the visible index. A failed directory sync does not produce a
successful replay until durability is re-established. Exact package ownership is
checked using metadata so corrupt package bytes cannot prevent their revocation.
The current-status reader does not open a revoked package's content.

Revocation does not delete immutable package bytes, rewrite a task lock, or release
retention pins. A previously valid task snapshot fails its next core preflight
when its version is revoked. This is distinct from ordinary uninstall, which
remains #448; the existing package store does not implement uninstall yet. The
lifecycle UI and authenticated mutation route remain separate integration work.

## Persistence and limits

The explicitly authorized Operator root must be outside declared Worker-writable
roots. Namespace ownership comes from the existing `SkillStore.scope`. Anchored
POSIX operations reject links and unsafe metadata files. Resource objects are
content-addressed, limited to 2 MiB, and immutable. The bounded 16 MiB task registry
atomically owns both task identities and command receipts; it does not replace
the Team control-plane ledger. The current-availability registry is bounded to
8 MiB and rejects detached/missing audit receipts. Registry replacement and
resource publication flush file bytes and their parent directory. Pin acquisition
uses the existing SkillStore idempotency protocol, then verifies the retained
holder before marking the task committed. Reads/preflight never recreate a
missing retention pin.

Immutable objects published before a failed registry commit can be unreferenced.
They are deliberately retained. No garbage collector or automatic pin release is
introduced here; #448 must preserve ordinary-uninstall vs security-revoke semantics.

## Mandatory S4b integration map (#461; before completing #444)

| Boundary | Required behavior | Current status |
| --- | --- | --- |
| `web.start_workflow` / confirmed task start | Capture one coherent Team revision before the first dispatch; persist the lock ref in versioned state. | Producer not connected |
| `AgileDevState` / state and checkpoint projections | Preserve the validated lock ref through current, archive, checkpoint, and API views. | State/current/archive/checkpoint implemented; API projection audit pending |
| shared Team/Library/status composition | One project-owned resolver and trusted current availability reader. | `ProjectTaskSkillService` implemented; dispatch callers pending |
| `execution/orchestrator.py` | Use explicit issue-execution identity and retain the lock across recovery. | Not connected |
| `WorkerJobStore.create_job/create_or_reuse_job` | Derive/register snapshot; compare complete immutable request on key replay; reject missing task scope. | Not connected |
| `WorkerJobStore.run_job` and internal retries | Preflight before every provider attempt; independently enforce adapter capability/conformance. | Not connected |
| `reviewer_dispatch.py` and reviewer retry | Original task lock; complete original task/context/output/read-only policy, not an abbreviated retry prompt. | Original-input/policy preservation implemented; Skill-backed retry explicitly blocked pending Worker integration |
| `manager_execution.py` | Logical message/intent lock, same-message retries stable, new messages capture new bindings. | Caller not connected; identity tested at shared-service level only |
| operator/direct dispatch | Explicit bounded scope or an explicit Skill-backed dispatch rejection. | Not connected |
| availability/revocation source | Trusted current versioned status and explicit security revocation. | Implemented and composed into the shared service |
| dispatch/load revocation enforcement | Recheck current status at every provider/resource boundary, not only at snapshot creation. | Provider/resource callers not connected |

#461 must not be marked complete until those paths either use the unified contract
or explicitly block unsupported Skill-backed work, with integration evidence.
S4a does not claim that coverage and does not activate the incomplete pipeline.
Do not add production monkey patches, weaken CI, or reclassify core-only tests as
proof that all production dispatch entry points are protected.

## Validation

Tier 3 control-plane core. `tests/test_task_skill_locks.py` covers version drift,
late Reviewer/retry derivation, Manager turn scope, kind/owner/project isolation,
command-input conflicts, empty/default/disabled methods, unavailable unused
subjects, concurrent capture, prepare/pin/final-commit interruptions, immutable
resource tampering, missing pins, and current availability failures.

`tests/test_skill_availability.py` connects the real persistent current-status
reader to core preflight. It covers terminal exact-version revocation, unchanged
history/pins, semantic replay and CAS conflict, scope/version ownership, concurrent
commands, missing/corrupt status, corrupted package content, partial writes,
pre/post-replace failure, repeated directory sync failure, and snapshot replay
durability. These are core-service integration tests, not Worker dispatch tests.

`tests/test_task_skill_context_state.py` covers real lock-reference roundtrips,
state/checkpoint/archive persistence, rejected cross-project/run/kind references,
malformed/unknown schemas, write-time revalidation, non-aliasing and legacy reads
without mutation or access to the Operator store.

`tests/test_project_task_skill_service.py` connects real Team changes and Library
versions to task locks, delayed snapshots and current-status preflight. It covers
old-task v1/new-task v2, default restoration without old-task downgrade, stale
confirmation, key/input conflicts, current revocation, untrusted status claims,
missing status metadata, Manager message identities, owner/project isolation,
unknown subjects and a closed metadata-loader allowlist. No provider is called.

### CI #991 failure investigation

Both attempts on `ef1fdd8` failed (2501 passed / 1 failed each). The first reported
a sanitized observation-persistence error. The second exposed ancestor-directory
mtime churn while opening an anchored repository: the device/inode/mode/size
matched, but mtime differed. The second trace establishes a false-positive path;
it does not prove the unexposed cause of the first failure.

The corrective change limits identity-only directory checks to ancestors outside
the repository. It still compares device/inode/type/mode/owner/group, uses
no-follow directory opens, and verifies the current entry after opening. Root,
repository-subdirectory and file-content checks retain their existing strict
metadata behavior. `tests/test_repository_ancestor_identity.py` injects unrelated
ancestor churn without sleeps, verifies snapshot and persisted-error behavior,
and rejects directory/symlink replacement before or after open, permission/owner
changes, and repository-internal directory churn. Existing file-mutation and
symlink tests remain unchanged. No retry-until-green or weakened assertions.

Tests execute in CI according to the existing CI-only agreement. Full backend,
Ruff, HTTP recovery, isolated browser acceptance and the final CI gate are still
required. An unchanged frontend does not imply a frontend Runtime integration.
No real provider call, Runtime Skill loading, deployment, or product acceptance
has been performed by these modules. Exact-head results are recorded in the PR,
not inferred from an earlier head or from this document.
