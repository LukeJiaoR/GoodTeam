# Cross-project attention (#397)

Projects now shows a read-only, all-project view of current workflow approvals,
workflow blockers, failed/input-needed preparation steps, and unstarted task
drafts explicitly requesting clarification. It is a bounded slice of #397,
not completion of the entire Work UX tracker or real-task acceptance.

## Truth and scope

`GET /api/projects` adds an optional `attention` projection to each project:
`project_attention.v1`, `items`, and exact category/total `counts`.

- Workflow attention reuses `build_team_workspace` and its current gate, current
  attempt and unresolved intent rules. A merely pending future gate is not an
  approval request. Completed/archived runs are not current attention. An active
  implementation attempt suppresses its old blocker, consistently with Work.
- Preparation items are the existing `needs_input`/`failed` steps, once per stable
  step identity. The existing `attention_count` stays readiness-only and is not
  added to the new item total.
- Clarification items come from the persisted draft's explicit
  `needs_clarification`, never inferred from natural-language conversation.
- IDs bind workflow run + attention identity, preparation execution + step, or
  draft identity. Counts describe actionable contexts, not blocker strings,
  unread events, individual job failures, or an estimate of urgency.
- Blocked contexts sort first, then approvals, then input requests. Ties use
  project key and item ID for a deterministic order. Search/filtering the project
  launcher does not hide the all-project attention list.
- Operator queue, Goal assessments, arbitrary assistant errors, and all possible
  pending Team decisions are outside this initial slice. The UI states this
  scope and does not promise a universal all-clear.

The scan reads persisted stores without calling the Work request's initializing
Team/session path, worker dispatch, recovery, approval decisions or model calls.
The existing project-discovery import lock may be created; no business records,
configs, sessions, approvals, job files or their modification times are changed.
An unreadable optional attention source yields `attention: null`, not zero;
ordinary project navigation remains available. A recording/executing workflow
transition also reports unknown instead of running recovery during the scan.
Older servers also display an
unavailable projection instead of fabricating an empty queue.

## Navigation and freshness

Links are built from validated structured identities and existing local routes,
not server-provided URLs. They identify the exact project/run and attention
context. The Work destination reads the project list again and compares it with
its current Work snapshot before focusing the existing decision, blocker or
clarification surface. Missing, superseded or unavailable identities produce an
explicit notice; they do not select a newer run/gate or automatically perform an
action. Approval/retry controls retain their existing exact-target safeguards.
The existing Work loader can initialize or reconcile its own persisted state;
this feature does not claim that entering Work is a write-free operation. Failed
preparation steps without a current Work input control remain an exact-item
status notice, rather than inventing an action or focusing a different step.

Refresh drops the old attention snapshot from current presentation. A failed or
aborted request cannot claim a current zero count, and superseded responses must
not repopulate or focus the page. There is no optimistic dismissal, automatic
approval, notification delivery, cross-project mutation or new inbox state.

## Acceptance boundary

Verification targets API/DOM state projection, read-only behavior, re-entry,
priority/counts, stale/deferred response handling, and exact deep links. Real
browser pixel, keyboard and mobile acceptance must be reported separately from
DOM tests. This slice does not start the deferred real-model acceptance #567,
resume parked #409/#448, deploy production, or change CI/security policy.
