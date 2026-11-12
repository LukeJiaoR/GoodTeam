# Mixed Runtime batches keep per-task Role methods

## User outcome and previous gap
A Manager may use heterogeneous generic Workers in one batch without an unrelated
legacy Runtime disabling a supported Pi Worker's Role methods. The former
producer selected ordinary/default method capture only when every track met the
Pi contract. A mixed same-Role Pi + fake batch reproduced the omission: the Pi
job had no method_loading_mode and could not consume its default packages.

## Production change
New mixed batches persist dispatch_track_methods.v1 in the original immutable
request.json. It freezes the ordered track routes and Team revision. Each method
track uses the existing capture_task_start/TaskSkill stores with a deterministic
Skill capture identity derived from the original batch logical ID and track ID.
This is not another task/queue/dispatch ledger. Original Worker run_id,
idempotency_key, project_task_id and track_id stay unchanged.

Only supported standard Pi tracks receive ordinary method consumers/default
packages. Custom-empty remains custom; custom/strict siblings retain their
original captured route and isolation refusal. Legacy siblings remain legacy,
even if they have the same Role as a method-loaded sibling. No read-only task is
made writable, no custom binding is ignored, no new Runtime is enabled.
All captures and strict/isolation preflight finish before any Worker job is
created or run. Partial capture failures retain the original outer receipt;
uncaptured subjects must still match its Team revision, never latest substitution.
Original outer requests, old shared captures and already-created jobs do not
upgrade. Homogeneous existing capture/replay behavior remains unchanged.

## Evidence boundaries
Representative invokes the real dispatcher, original package/capture/job stores
and PiMethodInput; it stops immediately before retained Git workspace preparation.
It proves independent loading, exact attribution, private prompt separation,
original-job replay, old-request compatibility and strict/custom-empty refusal.
It is not live Git/Pi/model execution, concurrent Runtime acceptance or #419 PASS.
Old full source fails the same mixed representative with missing method mode.
Focused checks: 9 passed, 1 skipped (existing Trusted Git environment condition).
Additional final representative including old/strict boundaries: 1 passed.
No paid model request, install, permission change, push, merge or deployment.

Base full role-routing archive SHA256:
07da66d6198750b07e026eedc77b624c4ff85e71c140c87a073e653deec46c54.
Read-only Researcher/Planner Pi method execution remains separate unfinished
production work; this mixed-batch fix does not remove that boundary.

Independent review found no blocker. A separate custom-empty/strict mixed case
refused before workspace preparation with zero jobs. A separate three-track
partial-capture case interrupted the second capture, then changed Team revision:
replay retained the first original lock unchanged and refused the uncaptured
second subject with team_revision_changed, with zero jobs or workspace execution.
These independent scripts and the partial-capture output are retained as evidence.

The initial full run exposed five failures in older integration fixtures that
omitted the actual Team view's capability field following the earlier discovery
projection fix. Three test mocks now include capability=ready, matching their
existing enabled/dispatchable setup; no grant, output evidence or assertions were
weakened. Independent review confirmed this correction. Related regressions plus
the mixed representative: 12 passed. Initial full results are retained, not hidden.

Final full backend: 555 passed, 32 failed, 5 errors, 21 skipped. Exact 37 failure/error identities match the last full manager-result baseline (554 passed). Suite is not green; original environment/baseline failures remain.
