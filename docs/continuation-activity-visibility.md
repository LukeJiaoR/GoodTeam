# Show the human the actual continuation situation

Base: full role-loop combined source archive SHA256
`673d12fda11de4a16387c2b9a0aad6b290c74f503b23d4e7cdea660b8e9c9a8f`.

Previously every active grant was labeled Running automatically and active
reasons were hidden. A reserved batch, an unknown original call, an explicit
Manager wait and a pending new judgment could therefore look like execution.

The existing continuation controls now derive bilingual presentation from the
existing grant, its own batch and original waiting attempt. It distinguishes
reserved/not started, awaiting Worker results (not proof of a live process),
review/attribution checks, original calls awaiting result, retained decisions,
new-fact judgments, resources, Human decisions and waiting for project facts.
The original waiting rationale is displayed only from that grant's retained
waiting attempt. It is labeled Last Manager rationale, not a claim that its
Context is still current. Ended grants are labeled ended, not accepted delivery.

This is a read-only view helper. It creates no backend status, scheduling rules,
permission, budget, polling, side effect or automatic action. Existing Stop and
review controls retain their behavior. It does not replace the invocation view
as proof of real activity, or turn a recorded batch into a live executor claim.

Verification: 10 related UI tests passed; independent final review ran 9 tests
and found no blocker after correcting Current rationale to Last. Full frontend
unit suite: baseline 772 passed / 50 failed; candidate 774 passed / 50 failed,
with the exact same 50 failing test IDs. These are retained failures, not a green
full suite. Targeted ESLint and production build passed (existing chunk-size
warning remains). Backend was unchanged and not re-run for this display-only
slice. No real browser/Provider/Pi or whole-project acceptance is claimed.

The package contains the updated full source tree and exact four-file delta.
No branch push, merge, deployment, installation or permission change occurred.
Remaining complete project acceptance stays #419.
