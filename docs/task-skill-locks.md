# Task Skill locks (S4 / #444)

## Status: core implementation; production wiring is not complete

This change introduces `TaskSkillLockStore`, not a working end-to-end S4 slice.
Do not close #444 or enable Runtime loading on the strength of these unit tests.
No current Worker, workflow, Manager, or operator call site is migrated by this
core module. Existing saved-only Skill configuration remains unchanged.

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

`preflight` verifies the registered job reference, original task/owner, methods,
job request, loader version, package bytes, and retained pins. It requires a
trusted current availability/revocation reader with an exact scope, Skill/version,
boolean availability and revocation state, and a status revision. Missing or
unknown status fails closed. The reader's implementation and Runtime capability
checks remain integration work. A successful core preflight does not authorize a
provider, prove #440 conformance, prevent a later revocation race on its own, or
prove that a Skill entered a model's context. Dispatch and each resource load
must enforce their own current checks at the appropriate boundary.

## Persistence and limits

The explicitly authorized Operator root must be outside declared Worker-writable
roots. Namespace ownership comes from the existing `SkillStore.scope`. Anchored
POSIX operations reject links and unsafe metadata files. Resource objects are
content-addressed, limited to 2 MiB, and immutable. The bounded 16 MiB registry
atomically owns both task identities and command receipts; it does not replace
the Team control-plane ledger. Registry replacement and resource publication
flush file bytes and their parent directory. Pin acquisition uses the existing
SkillStore idempotency protocol, then verifies the retained holder before marking
the task committed. Reads/preflight never recreate a missing retention pin.

Immutable objects published before a failed registry commit can be unreferenced.
They are deliberately retained. No garbage collector or automatic pin release is
introduced here; #448 must preserve ordinary-uninstall vs security-revoke semantics.

## Mandatory integration map before merge

| Boundary | Required behavior | Current status |
| --- | --- | --- |
| `web.start_workflow` / confirmed task start | Capture one coherent Team revision before the first dispatch; persist the lock ref in versioned state. | Not connected |
| `AgileDevState` / state and checkpoint projections | Preserve the validated lock ref through current, archive, checkpoint, and API views. | Not connected |
| `execution/orchestrator.py` | Use explicit issue-execution identity and retain the lock across recovery. | Not connected |
| `WorkerJobStore.create_job/create_or_reuse_job` | Derive/register snapshot; compare complete immutable request on key replay; reject missing task scope. | Not connected |
| `WorkerJobStore.run_job` and internal retries | Preflight before every provider attempt; independently enforce adapter capability/conformance. | Not connected |
| `reviewer_dispatch.py` and reviewer retry | Original task lock; complete original task/context/output/read-only policy, not an abbreviated retry prompt. | Not connected |
| `manager_execution.py` | Logical message/intent lock, same-message retries stable, new messages capture new bindings. | Not connected |
| operator/direct dispatch | Explicit bounded scope or an explicit Skill-backed dispatch rejection. | Not connected |
| availability/revocation source | Trusted, current, versioned control-plane status, including checks at resource loading time. | Reader contract only |

The PR must remain Draft until those paths either use the unified contract or
explicitly block unsupported Skill-backed work, with integration evidence.
Do not add monkey patches, weaken CI, or reclassify core-only tests as proof that
all production dispatch entry points are protected.

## Validation

Tier 3 control-plane core. `tests/test_task_skill_locks.py` covers version drift,
late Reviewer/retry derivation, Manager turn scope, kind/owner/project isolation,
command-input conflicts, empty/default/disabled methods, unavailable unused
subjects, concurrent capture, prepare/pin/final-commit interruptions, immutable
resource tampering, missing pins, and current availability failures.

Tests execute in CI according to the existing CI-only agreement. Full backend,
Ruff, HTTP recovery, isolated browser acceptance and the final CI gate are still
required. An unchanged frontend does not imply a frontend Runtime integration.
No real provider call, Runtime Skill loading, deployment, or product acceptance
has been performed by this module.
