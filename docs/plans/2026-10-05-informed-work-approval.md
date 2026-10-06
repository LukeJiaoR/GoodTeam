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

## Initial verification (2026-10-05)

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

## Merge-candidate verification (2026-10-06)

Synced the existing PR with main `7c6e5db0d8ce943d2b9f68ff876e51cbf39e4df3`
in a separate managed worktree. The net application change remains the bounded
Work/Review confirmation slice; the CI policy comes from main. Verification tier
remains Tier 4 with Tier 3 approval, ledger and execution-identity coverage.

Executed locally with Python 3.12.13 and Node 25.8.0:

| Command | Observed result |
| --- | --- |
| `PYTHONPATH=src .venv/bin/python -m pytest tests/test_review_gate.py tests/test_review_policy.py tests/test_reviewer_evidence_snapshot.py tests/test_execution_api.py tests/test_execution_idempotency.py tests/test_web.py -q` | 216 passed |
| `PYTHONPATH=src .venv/bin/python -m pytest tests -q` | 2997 passed, 17 skipped in 539.80s |
| `.venv/bin/ruff check src/goodteam_os/ tests/` | Passed |
| `npm test --prefix frontend` | 24 Node tests and 307 React tests passed |
| Focused Vitest: DecisionActions, informed ReviewPage, existing ReviewPage regressions, WorkApprovalAction | 30 passed |
| `npm run lint --prefix frontend` and `npm run build --prefix frontend` | Passed |
| `npm run test:e2e --prefix frontend -- informed-review-approval.spec.js` | 1 passed after correcting its task setup |
| `npm run test:e2e --prefix frontend` | Final run: 37 passed, retries disabled |
| `git diff --check` | Passed |

The 17 backend skips require Linux `/proc` for the dedicated runner lifecycle;
they are inapplicable to this macOS host, not skipped approval tests. All live
HTTP/browser work used freshly owned temporary roots, isolated registry/home and
fake Workers, with Telegram networking disabled; no active project config or real
model was used. Browser assertions verified blank mouse/keyboard rejection,
matching Work/Review advice, exact submitted identity, the typed reason in the
durable ledger, and one decision/execution across two tabs. Existing embedded
editing, feedback handoff, restart/re-entry and archived-run isolation also passed.

The first browser run was 36 passed / 1 failed: its new Review case asked to
"Verify an informed planning decision", correctly triggering clarification before
a task existed. The case now requests a concrete project-list implementation;
its approval and ledger assertions are unchanged. The corrected focused case and
the subsequent full run both passed. The final macOS run reported `ENOTEMPTY`
during removal of its own temporary server root after all assertions completed;
retain that teardown diagnostic separately from the passing browser assertions.
This is not a claim that the local fixture teardown was clean.

Local execution does not replace the new exact-SHA remote gate. Publish a clean
candidate containing current main, dispatch the reviewed `main` workflow for that
SHA, and require `merge-candidate/validated` plus the PR-side `CI / gate` before
merging. Final SHA/run links belong to the PR evidence record. Historical artifact
quota failures remain historical until the candidate's actual run is observed.

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
