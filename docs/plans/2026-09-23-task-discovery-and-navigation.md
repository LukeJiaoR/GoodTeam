# Task discovery and task-list disclosure

The Assistant previously treated a product category, audience and quality
aspiration as sufficient to propose executable work. New model turns must assess
missing decisions before proposing a draft, clarify consequential scope/flow/
acceptance questions, and select the relevant original user messages. The server
resolves those sources, preserving multi-turn requirements verbatim. Incomplete
existing drafts retain their text but cannot start until clarified or edited.

The task/history list now opens through **Manage tasks** instead of remaining
visible in the execution pane. Closing returns focus to the composer; selecting
a task or preparing a saved draft closes the list. Desktop and mobile retain the
same navigation and exact task-selection behavior.

Verification tier: **Tier 4**, including Tier 3 draft revision/confirmation and
source-identity contracts. No production project was mutated for smoke testing.

- Focused backend: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_assistant_intake.py tests/test_task_assistant.py tests/test_assistant_drafts.py tests/test_assistant_draft_actions.py tests/test_manager_execution.py tests/test_task_assistant_session.py tests/test_assistant_conversation.py tests/test_assistant_execution_logic_regressions.py -q` — **92 passed** after final source-boundary/cache refinements.
- Full backend: `PYTHONPATH=src .venv/bin/python -m pytest tests -q --tb=short` — latest full run **2646 passed, 2 failed**. Failures were `test_concurrent_identical_installs_have_one_effect` and `test_concurrent_same_task_start_captures_once`; both reproduced from an untouched `git archive HEAD` baseline. An earlier full run failed `test_concurrent_stale_retry_requests_queue_one_job`, also reproduced on that baseline. These existing concurrency failures are not fixed by this change.
- Frontend: `npm run lint --prefix frontend`, `npm run build --prefix frontend`, `npm test --prefix frontend` — lint/build passed; **223 Vitest + 6 Node tests passed**. After the final current-task hint adjustment, the 21 relevant context/draft tests and lint/build passed again. Existing chunk-size/dynamic-import build warnings remain.
- Browser: `npm run test:e2e --prefix frontend -- assistant-replies.spec.js project-manager.spec.js workspace-continuity.spec.js business-loop.spec.js` — **12 passed** against a unique temporary workspace with fake Workers. This includes desktop/mobile task-list disclosure, closing focus, saved-draft selection, history navigation, composer persistence and the complete business loop. Console/unhandled-error assertions passed.
- Additional browser/API smoke used Ego Browser and real HTTP/draft storage with only model output substituted. The screenshot's idea remained a clarification without a task; three user turns became one exact draft without starting a workflow. A legacy incomplete draft advanced from revision 1 to 2; both the old and incomplete-current confirmation returned HTTP 409, and the UI disabled Start.
- Real-model smoke attempted `codex / gpt-5.5 / high / read-only` in a separate temporary target. The CLI returned a usage-limit error before producing a response. This is **not evidence of real-model comprehension or question quality**; that validation remains unavailable.

Local evidence is retained under the temporary directories
`goodteam-intake-browser-pxv8onhl` (API snapshots and screenshot),
`goodteam-intake-live-aaqhdk95` (real CLI failure), and
`goodteam-intake-baseline-haqn8em2` (baseline concurrency reproductions).
Playwright screenshots and reports remain in `frontend/test-results` and
`frontend/playwright-report`. No branches or worktrees were created or removed.
