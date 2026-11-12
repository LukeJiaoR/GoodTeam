# Drain every authorized delivered outcome through the existing Manager loop

Exact base: saved recovery-board candidate, archive SHA256
`7d6addc55a8d817e34e1917437d87e7b3c12d9efa37d996a07fff8197fc1892b`.

Previously the default verified outcome selected the newest-created batch, even
when its receipt was already consumed. An older batch delivered later could not
supply its own verified evidence to Manager. When multiple deliveries were
already available, the first no-follow-up decision could complete the grant
before another original receipt was processed.

Discovery now prioritizes the original grant's eligible unconsumed receipts,
using the existing discovery_consumed ledger. When none remain it falls back to
the original latest-created selection. Ordinary planning without a grant and an
explicit run_id retain their old selection semantics. Every chosen receipt still
passes the original full evidence verification; invalid receipts are not skipped.

The existing atomic null-decision commit keeps an active grant active when an
additional original delivery receipt remains. It never restarts stopped/ended
grants, grants new authority, creates another queue, or loops extra calls inside
one tick. Subsequent original ticks retain the existing Manager budget and all
other guards. Waiting questions retain priority; no new Worker is dispatched by
this change.

Regression tests use real authorization, task ledger, receipt selection,
continuation and discovery consumers. Git verification, known terminal recovery
and Manager output are explicit fixtures, not live acceptance. Cases cover two
already-delivered batches and an older batch delivered later. Both fail against
the exact baseline and pass with this change. No provider calls, push, merge or
production deployment occurred. Whole-project acceptance remains #419.

## Results

Baseline: 32 failed, 482 passed, 20 skipped, 5 errors in 74.34s (0:01:14)

Candidate: 32 failed, 484 passed, 20 skipped, 5 errors in 82.91s (0:01:22)

Same 37 existing failing/error IDs. Original new regression and independent boundary probe: 6 passed. Ruff passed; independent review found no blocker.
