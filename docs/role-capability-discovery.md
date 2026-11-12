# Continuing Manager decisions use configured Role capability

## User outcome and implementation
A continuing Manager can select bounded follow-up work using a Role whose Runtime
source was explicitly configured, without requiring permanent members of that
Role. Ordinary chat already used this capability; project outcome discovery
recomputed it from same-Role membership and incorrectly hid ready capabilities.
`project_outcome_discovery._packet` now uses the existing Team capability view's
`enabled` and `ready` projection. Research and Planner views reuse that single
projection. No new runtime selector, scheduler, grant, or permission is created.
Original task-type opt-ins, source evidence, bounds, authorization revalidation,
actual dispatch checks, disabled Role and unavailable source behavior remain.
The exact source reference is configuration provenance, not an execution slot.

## Validation and limits
The existing Role-source representative now covers real ordinary chat intake,
continuing discovery packet construction/selection, target Role fields and fake
Worker job execution. It includes an explicit backlog-source fixture, not a real
delivery or product acceptance receipt. It confirms no investigation permission
is added, and disabled Role/unavailable source remain non-dispatchable.
Focused Role-source, Planner and investigation tests: 5 passed. The representative
against the prior full source fails at planning_capability.dispatchable=False,
reproducing the omission. Ruff passes. No full suite, live Pi/model, actual Git
execution or autonomous multi-iteration acceptance is claimed.

An independent read-only concurrency audit found no Worker-ID exclusivity:
individual tracks create distinct jobs; dispatch capacity and Pi execution locks
use job identity, not runtime_source_worker_id. desired_instances=1 does not
limit the existing dispatch pool. No lock removal or unproven concurrency change
was made. Existing project capacity (up to 10), authorization and write-domain
conflict protections remain.

Base: complete role-intake candidate, archive SHA256
972ef6ce194cf317cd9e20b2ca66aba6984b41e5aa676ed33918263dda7fc490.
Candidate is locally saved only; no push, PR, merge, deployment or model spend.

## Independent review
Reviewer found no blocker in the final projection/source/permission change and
reran the Role-source representative: 1 passed. The existing full discovery test
was also tried and stopped at its original seed with `Trusted Git executable was
not available`, before reaching this change. This is not green end-to-end evidence
and not established as a regression. No Git trust gate was bypassed.
