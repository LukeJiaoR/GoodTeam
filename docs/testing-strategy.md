# Lean Testing Strategy

## Current decision (2026-10-31)

The owner asked GoodTeam to remove approximately **90% of test code** and keep
**one representative check per distinct feature behavior**, rather than keeping
large overlapping suites for every edge-case variant. This branch reduces the
combined backend/frontend test footprint by approximately 90.5% relative to
the GoodTeam project-iteration development branch.

This is a deliberate **reduction in regression coverage**, not a claim that
the remaining checks prove every safety property. All removed tests remain
recoverable from Git history. Production code and permission boundaries are
not weakened by this test-only cleanup.

GitHub Actions automatic CI was already **paused by the owner**. Do not turn
it back on, publish candidate refs, or dispatch manual GitHub Actions just
because this document changed.

## Verification when developing

Run the narrow test that directly exercises the touched behavior. There is
no requirement to add multiple near-duplicate tests for one contract point.
Prefer a single **observable end-to-end or integration assertion** over many
tests of private helper permutations. A new feature that has no representative
check should get one focused, meaningful test rather than an entire new suite.

Local commands, where dependencies and a disposable test workspace exist:

```bash
PYTHONPATH=src python -m pytest tests -q
PYTHONPATH=src ruff check src/goodteam_os tests/
cd frontend && npm run test:unit && npm run lint && npm run build
cd frontend && npm run test:e2e
```

These commands have **not** been executed against the pruned branch in this
conversation. Browser, real-provider, live Git, crash recovery, external
costs and production deployment require their own explicitly scoped evidence.

## Representative feature checks

| Feature contract | Retained evidence |
| --- | --- |
| Project import and readiness | `tests/test_init.py`, `test_project_readiness_web.py` |
| Repository trust and inventory | `tests/test_repository_access.py`, `test_repository_inventory.py` |
| Manager and task history | `tests/test_manager_execution.py`, `test_task_assistant_session.py` |
| Whole-project iteration and parallel planning | `tests/test_project_iteration.py`, `test_project_iteration_planner.py` |
| Real Worker dispatch and isolation | `tests/test_dispatch_isolation.py`, `test_dispatch_capacity.py` |
| Workflow graph and gates | `tests/test_workflow_table.py`, `test_review_gate.py` |
| Goal continuation and HTTP recovery | `tests/test_goal_continuation.py`, `test_workflow_http_recovery.py` |
| Execution state and safe paths | `tests/test_execution_idempotency.py`, `test_execution_path_claims.py` |
| Independent review and delivery | `tests/test_review_policy.py`, `test_delivery_review_truth.py` |
| Project memory | `tests/test_memory_core.py` |
| Existing Skill, provider and Telegram bridges | `tests/test_skill_library.py`, `test_codex_bridge.py`, `test_telegram_review.py` |
| Frontend project and Work surfaces | `frontend/src/features/projects/ProjectsPage.search.test.jsx` and retained Work/Review tests |
| Real browser business path | `frontend/e2e/business-loop.spec.js`, `project-manager.spec.js` |

Test helper modules and fixtures referenced by these representative cases
remain available. The change classifier routes focused targets to files that
still exist. Historical design documents may refer to removed test paths;
those references describe past revisions, not the current test inventory.

## Reporting rules

State which exact tests actually ran, on which source revision, and their
real outcomes. **Do not** call a deleted suite passing, reinterpret an old
green CI run as evidence for a new commit, or treat one retained test as
comprehensive security certification. For high-risk work, preserve genuine
human authorization, isolated workspaces, independent review, real evidence
and fail-closed recovery even where automated coverage was removed.

If a specific regression is observed, repair its minimal representative case;
do not restore the old broad test matrix automatically.
