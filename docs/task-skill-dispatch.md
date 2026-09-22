# Task Skill production boundaries (S4b / #461)

This change continues #460 without adding a new Agent runtime. #444 remains the
aggregate S4 acceptance contract; #445 is content loading, #440 is separately
authorized live Runtime conformance, and #446 is actual custom-method review.

## Captured is not executed

A custom-method task obtains its method lock at a real bounded start. It does not
obtain provider permission. `WorkerJobStore.run_job` validates the registered
snapshot, original bytes, scope, pins, digest, metadata-loader version and current
revocation. Every Skill-backed adapter currently stops at that boundary with
`skill_runtime_not_connected`, including fake/noop/human. No fake result may
stand in for content delivery, a resource read, or successful Skill execution.
The existing **Saved only · execution not connected** disclosure stays intact.

The task can preserve a nonempty context and a blocked Worker result. It cannot
fall back to a legacy template when Team/runtime resolution fails. Error states
remain inspectable and are not reported as a completed artifact.

## Real producers and callers

| Boundary | Behavior in this PR |
| --- | --- |
| Task Assistant confirmation | Saves exact draft identity/revision/text and `task_skill_context` before preparation or first dispatch. Old confirmed legacy tasks remain legacy. Unsupported Skill-backed readiness preparation stops explicitly. |
| `web.start_workflow` | Uses explicit `workflow_run` identity, persists current/checkpoint context before graph calls, and verifies carried confirmation/root identity. |
| Graph initial/adoption/fix/architecture and Worker attempts | Carries the original lock, never resolves current methods for a late job. Loss of a registered task reference blocks both Worker and template routes. |
| Issue execution intake | Resolves an actual server-registered project root, captures explicit `issue_execution` identity, and carries the lock in the Worker and ledger event. Blocked Skill runs do not create a sandbox first. |
| Bounded CLI/direct dispatch | Uses an explicit `direct_task` identity distinct from its diagnostic run ID. `dispatch --task-id` permits a deliberate same-task replay. A registered project captures its Task lock; an unregistered standalone project remains legacy only before Skill authority exists; otherwise missing scope blocks. |
| Operator Review/Fix | Inherits the original issue execution's context; new review requests carry `read_only`. It does not create a new method epoch at a later PR head. |
| Parallel Reviewer and Reviewer retry | Original and late dispatch derive from the task lock. Explicit retry copies original task/context/constraints/research/output/Role bytes and runtime policy, with private failed-attempt and artifact checks. |
| Manager chat/interpretation | Uses logical message/intent identity, not the shared project conversation run. Physical request attempts remain distinct accounting data. Explicit message retries use the original server-derived context and captured runtime. New discussion events/messages may capture new methods. |
| Workflow debate | Skill-backed debate is explicitly unsupported and rejected; an empty discussion is not presented as a successful method run. Legacy debates retain their existing route. |
| Web worktree helper | Rejects carried Skill references or a method-bound workflow before creating a worktree. This helper is not the bounded direct-task producer. |
| Lower-level CLI/session/fake helpers | Reject Skill-backed identities before a probe, session continuation, or fake execution can bypass public `run_job`. |
| State/status/provenance | Context survives current/checkpoint/archive, task proposals, run history, Worker status, Manager activity and Reviewer/issue provenance. Only references and diagnostics are projected, not full private method/input bytes. |

`project_intake` remains `workflow_role:project_intake` with Research supplying the
Worker. Manager remains `manager:orchestrator`, not an ordinary TeamRole.

## Identity, exact input and interruption

The immutable task object includes a versioned `task_skill_start_input.v1`
confirmation extension. `find_task` validates it against the original command
fingerprint. Replaying a preparing or completed start uses that saved confirmation
and Role inventory, even after Team changes. Old S4a objects remain readable;
missing historical confirmation data cannot be synthesized from today's Team.

Worker creation uses a stable logical key plus the server-owned Worker namespace.
Changing task, context, output, runtime, policy, loader, subject or required
methods conflicts. An input hash is never appended to create a different job.
Legacy/Skill creation for the same key shares a process/thread-safe serialization
boundary, while the Operator-owned registry remains the Skill authority.

Full request bytes are registered before atomic complete-job directory publication.
An Operator publication receipt distinguishes an interrupted first publication
from a previously published job that has been lost. A lost published job is not
recreated as queued with erased history. Recovery never reopens a changed research
source or repairs a corrupted job input in place. Preflight attempts retain their
own immutable private records before changing the mutable status view.

The protected registry identifies a Skill job before its Worker metadata is read.
Removing Skill fields from that job does not route it through legacy. Removing a
workflow/proposal context also cannot downgrade a still-registered explicit task.
Unknown or missing authority is not a permit to read latest or select defaults.

## Verification boundary

Product tests run in the agreed CI channel. The new Worker tests exercise real
create/reuse/run APIs with every external/fake Runtime completion forbidden. The
entrypoint tests use real Team changes, installed packages, task/Worker persistence,
Manager callers, graph starts, issue intake with a fixture GitHub client, direct
execution, Reviewer retry, current revocation and public evidence projection.

`frontend/e2e/task-skill-locks.spec.js` starts a separate custom-method task through
the real UI after ordinary legacy preparation. It checks the nonempty persisted
context, immutable package version, blocked Worker with no external invocation,
retention after default restoration and reload, and current JSON versus API.
This is blocked-dispatch evidence, not live model or custom-method output evidence.

Final-head CI results, artifact identities, failures and review findings belong
in PR #462. No supporting/transport workflow success substitutes for standard
CI. No deployment, package execution, live model call or S9 acceptance is implied.
