# Informed Work and Review approval: #416 / T01

## Execution relationship

#411 selects real work and records the baseline, #416 fixes trustworthy decisions,
#418 follows with verifiable delivery, and #419 verification accompanies every
increment. Results go back to #411. Closing #411 is not an implementation
prerequisite. The persistent-Goal product target and independent acceptance in
#439 remain intact.

The T01 task contract and source baseline are recorded in
[#411 comment 5994449697](https://github.com/LukeJiaoR/GoodTeam/issues/411#issuecomment-5994449697).
The baseline is main `3b889b7485f8554ed27e2f5a1b0e487331bd2a49`; this increment
continues PR #486 from `b5879ecce74773c4fbbc010e0fa57bfc1d17f978`, without creating
a duplicate implementation PR. A source baseline is not a completed real run.

## Scope and implementation

Work and ReviewPage (including the embedded document panel) now use the same
explicit-positive-versus-incomplete recommendation semantics. Only an explicit
`approve` keeps the normal approval action. Every other/absent recommendation
uses manual continuation and requires an operator reason; Review does not offer
canned pass/test snippets for this choice. The existing evidence and risks remain
the basis for the decision, not Markdown keyword matching.

Review's button and Ctrl/Cmd+Enter share the same validation and synchronous
submission lock. The page handler also refuses blank informed approval, stale
page state, and non-actionable reviews. Existing full-artifact loading, unsaved
edits, version conflicts, and readback guards are preserved. The existing
run/target/artifact fields and typed note go through reviewApi; no backend
permission, ledger write, reviewer result or provenance is synthesized.

The decision draft is keyed to project/run/gate/artifact version and displayed
recommendation/evidence/provenance. Reordered object keys do not erase a draft;
actual context changes do. Failures retain the reason for an explicit retry.
Request-changes feedback, including whitespace, remains unmodified and still
uses the existing Manager handoff. An empty change-discussion draft is allowed;
it is not permission for an empty manual approval.

Work retains its existing exact-target validation, including the producer's
`missing` sentinel rejection and matching brief/action/target gates. Its key and
recommendation helpers now delegate to the shared confirmation module.

## Verification actually performed

Tier 4 browser approval path, with existing Tier 3 approval/identity contracts
requiring corresponding verification before merge.

On an isolated partial source copy, Node 22.16.0:

```sh
node --test frontend/src/lib/reviews/approvalConfirmation.test.js \
  frontend/src/features/runs/workspace/workApprovalContext.test.js
# 18 passed, 0 failed, 0 skipped
```

The unchanged Work test copy was verified against Git blob
`08d123cde88aecae7eeb000071dbf276226fd5cf`. The ReviewPage source was verified
against original blob `bef30c7e3171c55fe06de8b249ae93e18e5ffd89` before applying
bounded replacements. Twelve local JS/JSX files passed a TypeScript parser
syntax check; this was not a dependency check, typecheck, frontend build, Vitest
run, or browser test.

Added component and Review integration tests cover empty/whitespace reasons,
keyboard and duplicate submission, failed retry, changed context, exact API
identity, stale-page blocking, and the normal positive-evidence path. Existing
business-loop, embedded-panel and two-tab browser tests now explicitly enter a
fake-only reason where required. A new real-HTTP/ledger browser case compares
Work/Review semantics and verifies the bound reason reaches the existing ledger.
These browser tests use fake Workers, not real model acceptance. Their presence
is not a passing execution result; inspect the exact current PR head's CI.

Before merge, run frontend tests/lint/build, isolated Playwright and the required
backend/approval verification per docs/testing-strategy.md. No CI condition or
runner privilege was changed to obtain a green result.

## Evidence and remaining boundaries

CI #1093 and #1094 are historical results on older heads: recovery ran, evidence
upload failed, and downstream checks did not complete. Never assign those
results to a later head or treat skipped checks as passing.

Canvas/legacy quick actions, task-detail approval, non-Web entrypoints and
server-wide rationale enforcement are not completed by this increment.
DecisionActions' default remains compatible for callers not yet wired to the
informed context; #416 therefore remains open. #418 is not implemented here.

T01 has been selected and its source baseline recorded, but no real GoodTeam
run was started in this conversation: runtime/provider/cost/intervention and
product scorecard fields remain uncollected, not zero or PASS. Direct GitHub
coding and deterministic tests are engineering evidence only. No paid call,
deployment, production mutation, internal-state repair or paused #446/#476
bridge/network/Skill-runtime work was performed.
