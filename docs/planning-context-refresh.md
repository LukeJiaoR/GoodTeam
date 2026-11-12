# Reconsider changed planning Context without abandoning the bounded loop

Exact base: saved delivery-drain candidate, archive SHA256
`8d1e6db76efa08816b83f67ac90ab2150d855a3cb6431610591e3bf96c3f168b`.

This closes a concrete #686 item 4 path: an original Manager planning invocation
has returned a valid decision, but unrelated backlog or conversation facts changed
during it. Previously the result was discarded and the grant blocked with its
old planning reservation, preventing an ordinary next judgment.

The automatic continuation now retains the original input, validated selection,
invocation metadata and reason as a non-executable superseded event in its
existing session timeline. The same atomic mutation clears only that original
planning reservation. No queue entry or Worker action is created from the stale
choice. A later original tick rereads Context and may spend the next call under
the unchanged grant budget. There is no loop within one tick and no refund.

Settlement verifies original grant, active status, authority, task intent scope,
backlog reservation, call number and input version. Stop, replaced grants and
changed permissions or authorized task contracts do not become refreshes.
Unknown execution, thrown model calls and invalid responses retain the original
reservation; this is not generic recovery for unknown Manager jobs. A completed
atomic save with a lost acknowledgement is recognized only by its exact retained
event and cleared matching reservation. Previous waiting caches are cleared.

The manual planning entry, which has no continuation settlement callback, keeps
its original refresh-required error. Superseded events are never executable
iteration-choice recommendations. Existing scheduling, queue and Worker checks
still apply before any next dispatch.

Tests exercise the real authorization, task ledger, planning, timeline and queue
path with deterministic Manager output; they stop before Worker execution. No
real Provider/Pi or Git execution acceptance is claimed. Three representative
cases (new unrelated task, new conversation facts, committed save acknowledgement
loss) fail on the baseline and pass on this candidate. Nothing was pushed,
merged, installed or deployed; whole-project acceptance remains #419.

## Results

Baseline: 32 failed, 484 passed, 20 skipped, 5 errors in 82.91s (0:01:22)

Candidate: 32 failed, 487 passed, 20 skipped, 5 errors in 75.78s (0:01:15)

Same 37 existing failing/error IDs. Original new regression and independent boundary probe: 9 passed. Ruff passed; independent review found no blocker.
