# Current result handoff in Review Workbench

The first summary now uses the same protected candidate-check and supervised
correction observations as the detailed sections. It shows the latest checked
head and receipt, its bound human record, simulated/unverified execution mode,
the existing correction state and the next action. Semantic acceptance stays
unknown. This is a read-only presentation change, not a new completion decision.

Previously, after a real local fix and recheck, the top summary could still show
the old ledger status and an earlier unbound `approve` while omitting the new
candidate and receipt. Those original execution/PR/CI/review fields remain
available in a collapsed **Recorded execution metadata** section, explicitly
labelled as potentially earlier observations. The text report uses the same
current-result presentation before its labelled historical metadata.

Reopening reconstructs the handoff from persisted evidence. A new receipt does
not inherit an older review. Missing input/receipt proof and candidate drift
leave checks unknown. A stopped or indeterminate correction keeps its existing
inspection instruction. If separately read check/loop observations disagree on
candidate or human-review state, the summary asks for refresh and inspection
instead of presenting a mixed-version handoff. A failed check plus an approval
record remains unresolved. No displayed action runs a command or Worker.

## Verification and limits

Tier 2, read-only existing-evidence presentation. Regression fixtures use actual
temporary Git candidates, local Python checks and protected review/loop records.
They exercise replacement receipts, stale legacy decisions, reopening, later
uncommitted edits, stopped loops, missing proof and mixed-version observations.
The change does not mutate the ledger, validation/loop state, approval policy or
runtime admission. It adds no provider call, automatic retry, merge or deployment.

This closes one re-entry clarity gap in #418. It does not establish the real
model delivery, usable artifact/PR, or multi-project product-effect acceptance
required by #418 and #411. Native correction runtime admission remains separate.
