# #486 repair: server rationale boundary and remaining desktop entrypoints

## Source and authority

This repair incorporates main `2b93754cb08c0d15cfc88813ec64401e7f96027b`
(including #487 and #488) with a real two-parent merge, not a tree-only replay.
It continues #486; no duplicate implementation PR or deployment is created.

A new approved decision is checked inside the existing workflow guard, before
`begin_transition` assigns its operation ID, persists state, or publishes the
approval. The validator reloads the durable pre-decision run, because the caller
has already updated its in-memory state. Its server-built actionable review,
exact run/gate/artifact/version, and recommendation determine the requirement.
Every non-positive/unknown recommendation requires a nonempty operator reason.
Client origins, client verdicts and fake/test flags cannot waive it. Positive
planning recommendations do not acquire an unrelated CI requirement.

This does not change strict policy, reviewer provenance, or the decision ledger
format. Completed replay and recovery do not invent a second decision and retain
the original reason. Tests cover that distinction without a production bypass.

## Desktop entrypoints

Work and Review retain the earlier version-bound controls. Canvas now validates
the same rationale and exact target, displays recommendation reasons, and keys
its confirmation to the displayed context. Canvas hotkeys ignore editable input,
so typing `f`, arrows, or +/- cannot navigate/zoom instead of editing the reason.
The legacy Work banner routes non-positive recommendations to full Review.
Task detail remains a read-only projection with its existing full-Review entry;
its separate unbound decision form is removed. Review continues to own editing,
feedback handoff, and approval mutations.

The HTTP and recovery fixtures explicitly describe simulated evidence when they
continue. Negative boundary tests call the real decision/API with an empty
reason and a spoofed client verdict and assert no durable decision/dispatch.
Do not invent automatic reasons in production callers to pass this requirement.

## Actual local validation and limits

An isolated partial source copy, not a full checkout, was used locally:

- Python 3.13.5: 48 tests in `test_approval_rationale_contract.py` passed.
- Node 22.16.0: 4 tests in `approvalTarget.test.js` passed.
- JavaScript/JSX parser and Python syntax checks performed on the prepared files.

The new real-API boundary, isolated HTTP, React tests and updated recovery tests
are committed but have NOT been executed here. Full backend/frontend/build/lint
and isolated browser verification remain mandatory on the exact final SHA.
In particular, existing fake approval callers outside the updated helpers need
the full-suite check; do not exempt them or claim compatibility from pure tests.

Current CI is exact-SHA candidate validation on reviewed main. #488 removed
artifact uploads, so the old quota failures are history, not this head's known
blocker. Do not re-enable heavy per-push tests, forge validation status, or reuse
an old merge candidate to obtain a green result. The available connector has no
workflow-dispatch action; actual candidate validation must run before merge.

#411 remains the source-baseline/result owner, #419 accompanies verification,
and #439's persistent-Goal contract is unchanged. This repair is not a real
GoodTeam task PASS and does not start #418 or any model/production deployment.
