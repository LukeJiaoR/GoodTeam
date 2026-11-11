# Continuation authority semantics

Issue #686 item 3 uses `execution_authority.v2` for newly confirmed Goal and
project-continuation grants. All existing admission, Human-answer, discovery
and batch-dispatch comparisons still require exact snapshot equality.

The snapshot binds resolved project/configuration paths, semantic project YAML,
Team role availability, Worker identities/lifecycle/runtime and selection order,
Manager runtime, Skill bindings, both workflow/Manager selected model profiles,
and the active provider's endpoint/account/model/credential fingerprint. Only
hashes of provider configuration are persisted, not credentials.

Project display `name`, YAML formatting/comments, Team Worker display names,
non-execution timestamps, revision/audit bookkeeping, provider display name and
unselected model profiles no longer invalidate the grant. Worker `created_at`
stays bound because it affects selection order. Unknown project configuration
keys and legacy runtime fallback blocks remain bound conservatively. This is
not an exhaustive per-task dependency analyser: unrelated roles within the
selected model profile remain bound too.

Display changes still appear through the existing fresh Context readers. The
change does not modify permissions, widen a grant, increase budget, restart
stopped work or replay unknown executions. Provider jobs retain their existing
revision checks. Malformed selected model data remains fail-closed.

Old snapshots are deliberately not translated from opaque hashes. Their next
action still fails the existing comparison and requires a fresh explicit grant;
no legacy authority is silently promoted to the new semantics.

## Verification

Model-free tests cover persisted Goal admission after cosmetic edits, unchanged
Human-question attribution and answer receipt after a Team rename, and rejection
of effective runtime, resource, model, endpoint and account changes. These tests
are not real-provider, browser or complete Loop acceptance; that remains #419.
CI remains paused and no provider calls or deployment are required for this fix.
