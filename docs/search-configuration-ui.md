# Explicit queued-task Web Search configuration (#701)

Work → Next project iteration shows search configuration on unconfirmed queued
project tasks, including Manager conversation assignments and iteration picks.
Search is disabled by default. Existing provider selection is pinned by revision;
the operator must name the model, request count (1–20), and per-request timeout
(1–120 seconds). Saving explicitly acknowledges the cost/data notice but starts
neither a Worker nor a provider test. A subsequent task confirmation uses the
existing queue → ordinary Pi Worker → result/Manager Context contract.

Configuration changes retain the original Role, scope, source provenance and
acceptance criteria. They increment the queue revision and update the exact task
digest, so old confirmations are rejected. Running/selected/confirmed or stale
tasks cannot be reconfigured. Read-only/Skill isolation is not relaxed. Historical
queue records without a policy projection show “unknown”, not “off”, and require
an explicit save before the UI can start them. Missing or revised providers fail
closed. An uncertain search outcome is not automatically retried.

The request allowance is not a monetary spending cap or a cap on provider-internal
subcalls. No provider is auto-created, no credentials are stored by this UI, and
no model-list request or paid search is sent while configuring it.

## Verification

- Control-plane tier: backend full suite 437 passed, 32 failed, 5 errors,
  20 skipped. All 37 failure/error IDs equal the existing baseline; this is not
  an all-green backend run.
- Focused queue binding/configuration: 2 passed; includes explicit opt-in,
  exact revision changes, disable, stale retry and read-only refusal.
- Frontend focused configuration/queue: 5 passed; defaults off, exact policy
  payload, no execution on save, and legacy batch refusal.
- Frontend lint and build passed (existing bundle-size warning).
- Independent review corrected nested file lock and legacy-policy display/start
  issues. No production deployment or paid provider request; GitHub CI remains
  paused. Real provider/live Worker end-to-end acceptance remains in #419/#701.
