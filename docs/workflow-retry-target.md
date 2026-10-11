# Retrying the displayed workflow attempt

The Work page's retry control is bound to the current workflow task and the
failure snapshot the user is viewing. Project identity alone is insufficient:
an older tab must never retry a replacement task or a newer attempt.

`GET /api/state?project=...` includes `retry_target` with schema
`workflow-retry-target.v1`. Its version covers the persisted workflow state,
same-run Worker attempt metadata, selected execution configuration and recorded
retry starts. The projection includes the recovery mode, attempt identities and
whether retry is currently available. Active or indeterminate attempts disable
retry; unrelated runs and Manager conversation jobs are excluded.

Both `POST /api/retry` and its existing `/retry` alias require `project_key`,
`expected_run_id` and `expected_retry_version`. The same project workflow guard
protects comparison and execution. Binding rejection happens before retry-start
events, recovery handlers, checkpoints or dispatch. In particular, rejecting an
old tab cannot mark the replacement task's executing transition interrupted.

An admitted request records `blocked_workflow_retry_started` in the existing
action log before execution. This is evidence of an attempted retry, not its
success. It consumes the old version even when execution fails and leaves the
same workflow fields behind. The user must inspect the new failure before
trying again. It does not add an approval, provider permission or new runtime.

Retry continues the existing recovery behavior: resume the recorded transition,
repeat a revision/fix phase, rerun failed staging, or resume implementation.
Attempt IDs explain which observations justified that phase-level action; they
do not promise that only a single Worker will rerun.

Work freezes the clicked task's evidence while retry is pending and on a
conflict. Polling cannot silently replace it with another task's evidence. An
explicit successful refresh releases that snapshot and allows a newly bound
request. Existing conversation drafts remain mounted. No automatic replay is
performed after a conflict or uncertain request result.

## Verification scope

The regression was reproduced through the real product HTTP server with
isolated persistent runs A and B: a request from A previously advanced B and
returned 200. The fixed path returns 409 without modifying B. Tests also cover
both route aliases, valid recorded-transition recovery, missing bindings,
same-run attempt changes, active/unknown attempts, repeated requests and the
existing cross-request workflow guard. These controlled paths make no model
calls and do not prove native correction execution or browser visual quality.
No CI workflow or additional user approval is introduced.
