# Explicit continuation Role and feedback permissions

Base: `602ad62674d3c8d66e0235db56eabdf62f0b8f0b` (#725).
Related work: #686, #696. These issues and #419 remain open.

## Gap and change

The existing continuation API already supports `allow_investigation`,
`allow_planning`, `allow_undelivered_feedback` and
`allow_initial_readonly_feedback`. The normal authorization form did not expose
these options, so users could not opt into the implemented Planner/read-only
feedback path without manually calling the API.

The existing form now exposes these permissions, default off. Unselected keys
remain omitted, preserving old grant digests. Every selection participates in the
existing exact-input confirmation/idempotency identity. Disabling undelivered
feedback or the last matching Role also clears initial read-only continuation.
Active grant capabilities are shown from the saved grant, never upgraded by UI
rendering. The initial option is unavailable without investigation or Planner.

This is not a new dispatch path, Worker Loop, runtime admission or automatic
search permission. Backend checks, task/Manager budgets, Stop and known-result
checks remain authoritative. No runtime is called by rendering or selecting an
option. Model charges apply when the authorized continuation actually runs.

## Verification

- 6 component tests passed, including explicit opt-in, consent invalidation,
  identical replay, dependent opt-out and legacy grant display.
- Full frontend unit suite: 328 passed. ESLint and Vite build passed.
- Existing backend Planner/investigation/feedback tests: 7 passed.
- Full backend: 448 passed, 32 failed, 5 errors, 20 skipped. The same
  environment baseline is retained for failure-ID comparison; no backend files
  changed in this increment.
- Independent source review completed with no blocking finding.
- No real browser/provider/Pi/complete project Loop test was run. CI remains
  paused. No deployment, credentials or security settings changed.

The npm mirror in the repository lockfile failed in this environment. Validation
used an ephemeral lockfile with only the mirror hostname changed to npmjs.org;
package versions and integrity hashes were unchanged. Repository lockfiles were
not modified.
