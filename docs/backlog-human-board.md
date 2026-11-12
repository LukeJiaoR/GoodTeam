# Backlog judgment through the existing Human board

Project outcome: an authorized project loop can ask the owner about a blocked
initial task and, after an answer, choose an already-authorized investigation
without manufacturing a successful engineering delivery.

This increment adds a distinct `backlog_judgment_source.v1` receipt from the
original authorized seed tasks and their dependency statuses. It binds the
continuation ID, authorization digest, unchanged task intent and current ledger
facts. It is neither a verified delivery nor a terminal Worker outcome. Manager
inputs, retained reservations, source revalidation, Human question/answer
receipts, budgets, task publication and the queue/outbox use existing services.
No second ledger or dispatcher is created.

The existing discovery form explicitly describes judgment from verified delivery
or current authorized task facts. Its existing exact-input confirmation captures
`allow_backlog_judgment: true` for newly authorized discovery. There is no extra
checkbox. Old grants omit the marker and retain their existing meaning. Blocked
seed tasks can be selected only through this newly described discovery consent;
ordinary execution-only authorization still selects todo tasks only.
Investigation and Planner delegation retain their existing separate permissions,
Runtime admission and original budgets. An answer never grants extra scope.

A null backlog judgment remains active/waiting, not completed. Identical consumed
facts do not cause another Manager call. Unknown executions keep their original
reservation. A pending question does not prevent original tasks that become
eligible from entering the existing selection and execution path.

## Stacking and verification boundary

Backend base: local blocked-judgment candidate, which depends on the saved
multiple-followup candidate on main `602ad62674d3c8d66e0235db56eabdf62f0b8f0b`.
Frontend base: PR #726 head `53cb03952719d89275f51f04ba3c75131e48ffea`.
Apply the separately recorded delta only to those exact bases.

The representative regression uses real local task/session storage, grants,
question creation, `/answer`, source receipts and task queue publication. Manager
responses and capability availability are fixtures; execution stops at the
original batch runner boundary. This is not real Provider/Pi/Worker execution,
trusted Git validation, production deployment, full project acceptance or a
claim that all GoodTeam development is complete. #419 remains the real acceptance
record. No model/search call, Pi install or remote publication is performed.

Focused backend: 3 passed. UI component: 7 passed. Independent reviewer found no remaining blocking issue after the seed-intent digest correction. Ruff, ESLint and Vite build pass (pre-existing bundle-size warning). Full-suite results are in validation.

Full backend: 453 passed, 32 failed, 5 errors, 20 skipped. All 37 failure/error IDs exactly match the base (452 passed). Two additional null/ready probe parameters were incorporated after full collection and passed in the final focused three-case run; they are not included in the full-suite count.
