# Let Manager judge ready work alongside pending Human questions

Outcome: a question about known review results no longer globally prevents
another already-authorized ready task from reaching the original Manager
planner. Manager still decides whether that task actually depends on the answer;
the routing change never dispatches a task by itself or answers for the owner.

The previous bypass recognized only the backlog-judgment source marker. A grant
with known-undelivered feedback could publish a Human question from review
results but then repeatedly return the already-consumed discovery result instead
of considering independent ready work. The grant did not need broader backlog
judgment authority just to select its own ready tasks.

The bypass now depends on real authorized ready candidates rather than the
question's source. Reserved discovery, new Human answers and pending feedback
still take precedence. Unknown invocations, budget limits, scope, review and
execution checks remain unchanged.

The original planning Context previously carried timeline excerpts, not the
question board itself. New continuation planning reservations use input version
4 and capture the existing board as read-only Context. Its question/answer facts
participate in the existing input digest and before-publication revalidation.
Manager may choose eligible independent work, or return an empty selection when
it depends on an unresolved decision. No question is auto-resolved.

Already-reserved v2/v3 plans retain their original Context and prompt contract;
the new field is not silently inserted into unknown or retained invocations.
The existing empty-selection wait/cache and task queue remain the only paths.

## Evidence boundary

The regression starts with real local task/grant/session state, two tasks in
review and one already-authorized ready task. Terminal/recovery evidence and
Manager responses are fixtures. It checks both original planning/queue entry
with the board visible and an explicit wait that does not purchase another
judgment on unchanged facts. Prior review tasks remain unaccepted and the Human
question remains answerable. No actual Provider/Pi or Worker execution is claimed.

Base: saved all-review-feedback candidate, archive SHA256
`5d7402f9aaa0d46d9f7919eac3f11296afb5ccadcbb999fb50effd643c430068`.
No push, merge, deployment or security change. Full product acceptance stays #419.

Full backend: 470 passed /32 failed /5 errors /20 skipped. All 37 failure/error IDs exactly match base469. Final focused run: 3 passed (new ready/wait parameters plus blocked judgment); explicit-wait parameter was added after full collection and is excluded from470. Independent review: 12 targeted passes and old-v3 replay probe passed, no blocker. Ruff passed; frontend unchanged.
