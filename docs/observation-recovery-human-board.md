# Integrate existing observation recovery with Human board questions

This is a compatibility increment, not a new completed-job recovery mechanism.
The existing recovery was already pushed in
`d7b4e7e144d0f2db6d286cce3b7a256b55346418`
(`codex/700-observation-recovery-20261112`). The local feedback chain had not yet
included that branch. Its original reader and tests were fetched at that exact
commit and composed with the saved mixed-feedback candidate.

The real integration gap: that earlier recovery reader knew follow_up but not
the subsequently added authorized human_question output. A completed original
Manager job containing a valid question would therefore be marked invalid on
recovery, or never publish its question to the board.

The adaptation only passes the existing Human-question grant flag into the
existing output validator and, for a recorded still-current result, uses the
same atomic retain_observation_question adapter as normal completion. It does
not create another reader, dispatcher or state machine. Existing original-job,
input, phase, status, grant and current-snapshot checks remain unchanged.

Repeated reconciliation produces one original question. Old advice-only grants
still reject a question. Stop and stale snapshots archive the result without
publishing a board question or executing follow_up. No run_job invocation,
extra Manager allowance, refund, or transport retry is added.

## Verification and exact base

Integration base = saved mixed-feedback candidate (archive SHA256
`aa14f89787e17e782c27fac4366a7f72c60c3a285422c6d51144fc38da546345`)
plus the original #700 recovery code/tests at the fixed commit above. The
pre-adaptation composed runtime is included as the exact patch base; the upstream
reference files are evidence, not newly developed features.

The regression uses real WorkerJobStore and original production consumer with a
simulated transport: the first response is unknown, then the original local job
is observed completed. Current/new grant, old grant, Stop and stale-result cases
are checked with no second execution. No actual Provider/Pi/model or end-to-end
product acceptance is claimed. Nothing was pushed, merged or deployed; #419
remains the true acceptance record.

## Results

Baseline: 32 failed, 478 passed, 20 skipped, 5 errors in 75.42s (0:01:15)

Candidate: 32 failed, 482 passed, 20 skipped, 5 errors in 74.34s (0:01:14)

The same 37 existing failure/error IDs remain; no new failing ID. Focused tests and independent question-plus-follow-up probe: 15 passed. Ruff passed. Independent review found no blocker.
