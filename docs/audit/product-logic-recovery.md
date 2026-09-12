# Product Logic Recovery

This change addresses the ten product-logic findings from the 2026-09-12 audit
of `3a20a7b`.

| Finding | Corrected contract |
| --- | --- |
| Truncated review documents | Preview content is identified explicitly. Editing uses complete content, and saving can bind the run and artifact version. |
| Assistant cannot accept a second task | A proposal with proven terminal-run evidence moves to history before a new proposal is accepted. Completed runs are archived before replacement. |
| Concurrent approval dispatch | Project workflow mutations share an in-process and cross-process lock. Duplicate bound approvals cannot dispatch another worker. |
| Missing revision paths | Security revisions return to RFC review; staging/release revisions rerun delivery evidence; fix revisions return to planning or implementation review. |
| Lost approval after execution failure | The human decision and pending transition are persisted before executing. Interrupted transitions resume through explicit retry with the original decision. |
| Failed fix worker treated as reviewable | Failed or unsuccessful QA results block the fix. Retrying restores the review path and carries the actual QA evidence forward. |
| Polling destroys edits | Review drafts remain separate from refreshed server data. Dirty drafts are preserved when evidence changes. |
| Previous project shown after navigation | Project state and asynchronous responses remain bound to their project identity. |
| Fix ignored by topology runner | Topology plans and gate lookups use the run's load profile. |
| Reapproval deduplicated against old decisions | Review deduplication distinguishes a retry from a later decision or changed evidence. |

## Durable Approval Recovery

`pending_transition` is an additive state field. Older states default to an empty
transition. A new decision carries a stable operation ID and its original audit
payload. The state is atomically replaced before the decision is published and
the downstream operation starts.

The decision's operation ID makes the action append recoverable across an
interruption. A live holder prevents concurrent progression and prevents a GET
from classifying the running operation as interrupted. After an exception or
process exit, the project exposes a blocked transition. The operator inspects
existing worker/artifact evidence and explicitly retries; the original approval
is retained. External worker side effects cannot be assumed to be rolled back
by a process failure.

Existing `revise_*` states can recover through Retry when their original change
request is available in state or the action ledger. Revision instructions are
included in worker context. Editing an artifact with an outdated run or version
is rejected instead of overwriting newer evidence.

## Verification Ownership

Risk classification: Tier 4 live/user-facing workflows with Tier 3 control-plane
contracts (approval identity, ledger writes, recovery, execution evidence).

The user explicitly requested that local tests not run and that the complete
change be submitted as one PR for CI validation. Regression tests are included
for CI. No local pytest, Vitest, Playwright, build, or live smoke is claimed for
this implementation. Static source and diff inspection do not establish runtime
correctness. The earlier audit's passing tests and defect reproductions are not
verification of this patch.

The repository CI owns the backend/frontend checks selected by its risk
classifier. Its frontend job now runs `npm test` before build and lint, so the
new React regression cases execute remotely. Live worker/Telegram/GitHub smoke remains unexecuted; this PR does
not change remote production projects or merge itself.

## Follow-up: CI Business-loop Acceptance

The follow-up adds `frontend/e2e/business-loop.spec.js` and a required CI browser
job. It uses the real UI/API and durable state with isolated fake Workers to
cover import, request, explicit confirmation, preparation, execution, security
revision back to RFC, reapproval, completion, and a second task with preserved
history. See [the testing strategy](../testing-strategy.md#business-loop-browser-acceptance)
for evidence locations and the limits of this fixture.

Static review of that path found that change discussions still tried to parse
model JSON from built-in fake/noop/human runtimes. They now use the existing
explicit-request interpretation, consistent with Task Assistant chat. The
discussion does not decide an approval; separate confirmation remains required.
The builtin regression and full browser loop are executed only in CI, as
requested. The implementation retains PR #427 as its dependency until that PR
is merged; neither PR is automatically merged.
