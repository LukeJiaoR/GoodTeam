# Bounded Role task delegation and entry consistency

This increment implements #686 items 5–6, not the complete project Loop.

A new Manager queue action must include a bounded `delegation` with purpose,
relevant context, and scope, alongside Role, execution policy, declared write
paths, and observable acceptance criteria. The server attaches exact source
message IDs and contents. Original request text remains provenance and visible
in the card; it is not used as a substitute for the assignment. New Manager
responses missing the contract are rejected. Replays of historical Manager jobs
use their captured prompt contract; old persisted tasks remain readable.

The task ledger and its evidence digest bind the delegation. All project task
batch execution, including authorized continuation, renders that same persisted
task into the original Worker job. The confirmation UI shows purpose, Context,
scope, and existing policy/criteria. Selected single project tasks use the same
batch admission, Role/runtime selection, isolation and result path as queued
batches. Read-only work stays read-only. Historical named assignments retain the
named Worker. They cannot silently enter an unrelated whole Workflow.

Ordinary human-authored whole-Workflow drafts retain their existing route.
Editing a selected task explicitly unbinds it as a new human draft. Preparation
and retries retain their original selected identity and supplied Team revision;
stale revisions fail closed. No new scheduler, execution permission, paid call,
CI dispatch, production deployment or automatic result approval is introduced.

## Evidence and remaining acceptance

- Representative contract test covers Manager action -> ledger/queue -> runtime
  track -> persisted Worker `task.md`, without a live provider.
- Selected-entry HTTP tests cover ready/preparation paths, replay identity,
  read-only Role preservation, edited draft fallback and stale Team confirmation.
  Readiness/execution boundaries are simulated; these are not live E2E evidence.
- Frontend unit suite: 324 passed; frontend lint and production build passed.
- Full backend suite is compared against the exact main baseline; the environment
  cannot satisfy trusted system Git ownership validation. Existing failures also
  include missing SOCKS support and timing-sensitive fixtures. These limitations
  are not bypassed or reported as passing real isolated execution.
- #419 retains real provider/isolation and continuous-project acceptance. Role
  Skills/search, generic feedback and Manager/Planner continuation remain later
  increments in the user's specified order.
