# Keep the original grant alive while sibling execution remains

This integrates the shared Worker pool with existing Manager outcome judgments.
A completed batch B can supply its original feedback while batch A is still
claimed or running. A Manager decision to create no follow-up for B does not end
A's existing authorization or cancel Workers waiting for the shared pool.

The no-follow-up settlement reads the same session's original batch ledger and
reuses `pending_batches`, so already verified delivery and already observed
results do not count as live execution even if an older batch status projection
lags. Only outstanding claimed/running batches of the current grant keep it
active (`sibling_execution_pending`). No role capability, billing budget,
publication authority, task completion, queue, or state machine is added.

The regression exercises the existing Researcher + Planner feedback path with
no backlog-judgment permission, an in-progress sibling, and the real Manager
judgment settlement. Runtime/Provider and Git evidence are offline fixtures.
This is not actual Pi execution or whole-project acceptance. The original
terminal-result resolution, delivery, permissions, Stop and conflict checks
remain in force. No push, merge, install or deployment occurred.

Final verification: 16 focused checks passed, including the independent probes;
final independent review reported 9 passes and no blocker. Ruff passed.
Full backend: 520 passed / 32 failed / 5 errors / 21 skipped. The 37 failure/error
identities exactly match the preceding 519-pass live-pool baseline. The full
suite is not green; unavailable trusted Git and real runtime acceptance are not
claimed as completed. No frontend files changed.
