# Ordinary Manager intake follows the configured Role capability

Base: full compact-pages candidate archive SHA256
df0e95f6e0bfcd75ecf857a3165c705ba939c8f9a5f78637b415988d0c27bb86.

User outcome (#686 mechanism 8 / #445): a Manager can delegate an ordinary
conversation task by Role after the user explicitly chooses an existing Runtime
configuration source, without also creating a permanently rostered member for
that Role. The task owns the Role and method/context identity; a source member is
configuration provenance, not the new execution identity.

Existing build_team_capability_view and resolve_worker_from_state already support
this arrangement. But queue_assignment still inspected the target Role's roster
for a dispatchable member. Thus a ready Planner with an explicitly selected
Researcher configuration source was rejected before queueing. Conversely an
explicit unavailable source could be overlooked by that roster-only intake check.

The fix reuses the existing capability == ready projection, with the existing
Role enabled check. It neither adds a Runtime/source nor changes a Role policy.
The ordinary task card/proposal, confirmation, dispatch admission, Skill capture,
shared capacity and task/context identities remain owned by their original paths.
An unconfirmed proposal starts no Worker. Disabled or unavailable source remains
unavailable; no fallback or authorization is invented.

## Lean verification

Extended the existing Role-source representative case rather than adding a
permutation suite: original TaskAssistant discussion -> Planner card/queue ->
existing configuration resolution -> original fake Worker job/result. Same chat
message creates only one proposal; no fixed worker_id is added to the task;
source Role private prompt is not inherited. Disabled Role and inactive explicit
source cannot create another card. The existing active-Goal intake fixture now
includes the real capability projection field.

Two relevant tests passed; Ruff passed. Runtime is explicitly fake and Manager
output is injected; this is actual product routing under deterministic inputs,
not real Pi/model behavior, isolated Git execution or full Loop acceptance.
No full backend run, CI, install, paid call, publication or permission change.
