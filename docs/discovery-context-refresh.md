# Preserve completed discovery decisions and reconsider valid new facts

Exact base: saved planning-refresh candidate, archive SHA256
`ed7407faa52ed4e22f1738b9b152b15b6bae9f6b3bf59f80c4d5c9506ad2caaa`.

The delivery/discovery Manager path had a separate #686 Context interruption from
ordinary task planning: after a valid returned decision, changed dependency/task
facts or current HEAD caused the grant to block and lose that returned decision.
This change does not reuse a stale action. It retains the original frozen packet,
validated selection, original invocation metadata and reason in the existing
session timeline; the same atomic mutation clears only its original unpublished
reservation. No source input is consumed, no call refunded, no task, question or
queue entry published. A later original tick uses current Context and the same
finite budget.

Allowed fact drift is collected during revalidation, not accepted early. All
original input digest, authority, Human evidence, history, source receipt and task
checks run before drift can be classified. The latest session is fully rechecked
before settlement, and the atomic mutation requires that exact retained grant
state and reservation plus unchanged authorized task intents. HEAD drift is
accepted only after revalidating the original delivered outcome at current HEAD
and confirming all outcome fields except project_head are unchanged. Changed or
invalid receipts/report provenance remain errors, not permission to refresh.

This applies only immediately after this invocation returns valid output. Unknown
results, failed/invalid calls, prior saved-selection publication replay, already
published task effects, Stop and changed grants are not cleared or restarted.
A lost acknowledgement is reconciled only from the exact committed event,
original call count and cleared reservation.

Representative regression covers current HEAD advance, original task-status
change and committed-save acknowledgement loss. It proposes a valid new task
from the old Context and proves no stale task is published; the next tick judges
new Context once. The earlier two dependency-drift tests now expect deliberate
rejudgment instead of permanent blocking. Real authorization, ledger, receipt
consumer and Manager loop are exercised; Git verification and Manager output are
fixtures. No actual Provider/Pi or whole-product acceptance is claimed. No push,
merge, installation or deployment occurred; remaining acceptance is #419.

Independent review found and fixed a related late-result error path: the existing
continuation generic exception handler could overwrite a replacement grant with
the old blocked grant after correctly rejecting its stale result. It now returns
the current grant unchanged when IDs differ. The independent nine-case boundary
probe is retained as ordinary regression tests, including replacement grant,
Stop, exhausted budget, changed intent, receipt/packet/history tampering,
unknown results and invalid output. This fix does not broaden continuation
permission or retry unknown calls.

## Results

Baseline: 32 failed, 487 passed, 20 skipped, 5 errors in 75.78s (0:01:15)

Candidate: 32 failed, 499 passed, 20 skipped, 5 errors in 76.88s (0:01:16)

Same 37 existing failing/error IDs. Normal focused suite: 16 passed. Independent review rerun including separate duplicate probes: 25 passed. Ruff passed; independent review found no remaining blocker after fixing grant replacement. Final baseline reproduction: 5 failed, 7 passed.
