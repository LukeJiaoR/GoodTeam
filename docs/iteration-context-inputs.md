# Iteration planning and bounded context

Date: 2026-11-07. Verification tier: Tier 3 (planning identity and Worker input).

The project-level outcome is that Manager can select a task, observe its accepted
delivery and use that evidence to select the next distinct iteration while
retaining explicit project goals and constraints across long conversations.
The existing Task Assistant queue, continuation reservation, isolated Workers
and delivery checks remain the execution path.

## Changes

Ordinary planning now reuses the delivery verifier already used by outcome
discovery. Its input carries the latest batch whose receipt, original task
intent, landed Git content and configured quality evidence can still be
verified. Missing or unverifiable delivery is explicitly unavailable. Human
delivery attestation does not become a claim of machine tests or machine review.
Outcome discovery still requires its existing grant and scope checks.

Recommendations and ordinary planning jobs bind the complete packet, including
current Git HEAD, verified delivery, saved repository observations and bounded
conversation. A fresh planning request cannot reuse a recommendation for changed
inputs. Inputs are checked again after the model returns. Recommendation events
are excluded from their own input projection without renumbering source events.
When an earlier input recurs, its Worker result is reused and a fresh advisory
event makes it current for both display and queue handoff. Repeating that current
request remains idempotent and does not spend another model call.

Automatic continuation passes its existing reservation identity (continuation
ID and consumed planning slot). A lost reply can reuse that slot's original
job; changed input cannot invoke another model call under the same slot. This
does not add a dispatcher, ledger or new budget grant.
An unfinished reservation from the earlier backlog-only identity protocol is
blocked for reconciliation instead of being replayed under a new job identity.

Scoped planning sends its selectable inventory once, with only the other tasks
in the supplemental project inventory. It retains all candidates; the UI preview
limit is not a planning limit.

Conversation continuity separately retains the latest 12 explicit user project
goal/constraint declarations, independent of query matches and recent-message
windows. Supported labels include `项目目标：`, `项目长期约束：`, `Project goal:`
and `Project constraints:`. Entries retain original message IDs, source indices,
digest and excerpt offsets. Omission/truncation is reported. These are historical
source excerpts, not inferred current requirements, task-intake sources or
execution authority. Scoped task chat and assistant-authored guesses do not
become project declarations; contradictions remain visible in source order.

The complete ordinary Worker prompt has a 192 KiB UTF-8 byte limit, including
role instructions, task, context, constraints, artifacts and memory. Oversized
input raises `worker_prompt_size_limit` before sending a prompt to the provider.
No requirements, candidate inventory or memory receipts are silently truncated.
Protected task, correction and Skill inputs keep their existing input protocol
and limits. This is a byte boundary, not a model token estimate or a dollar cap.

## Representative evidence

- `tests/test_iteration_context_inputs.py`: long-conversation source retention
  without widening intake, complete-prompt rejection before CLI invocation, and
  real offline planning processes that replan on HEAD/goal changes while a
  reserved attempt refuses changed input without another provider call.
  Returning from HEAD B to A restores A's recommendation in both the latest
  projection and queue, without repeating A's Worker invocation.
  A retained legacy reservation likewise cannot spend its slot again on upgrade.
- `tests/test_project_iteration_continuation.py`: the existing two-batch isolated
  execution now asserts that the second selection receives the first batch's
  verified delivery and its current HEAD. Both actual Worker subprocesses,
  candidate landing and delivery receipts remain part of that fixture.
- Existing planner inventory, queue handoff and outcome-discovery checks remain
  the regression boundary. Offline fixtures do not prove real-model quality.

GitHub CI remains paused. PostgreSQL integration requires a dedicated disposable
test database; production database configuration is never used for these checks.

## Local verification

Pinned base: `1d653a30cbaca668749d019043ccaec0bfa63a05` (includes #675,
#678, #680 and #681). Full-backend source: `a2710110ed58af40d4c9115bc89db3996d2a205e`.
Final integrated source: `a02ee366fa70a7441db98d86cffd8cb1d5911f84`.
The follow-up commit changes this verification record only.

- `env -u GOODTEAM_DATABASE_URL -u GOODTEAM_TEST_DATABASE_URL PYTHONPATH=src python -m pytest tests -q --tb=short`:
  **414 passed, 16 skipped**, 8 existing fork deprecation warnings, 591.52 seconds
  at `a271011`. This includes the legacy reservation guard, historical-input
  reactivation fix and both distinct project-iteration execution/learning scenarios.
- The automated review's A-to-B-to-A finding reproduced before the fix. The
  expanded existing regression and planner checks then passed all 13 cases.
- While that full run was executing, main advanced with #680's Provider retry
  binding fix. It merged without conflict. At integrated source `736f45b`, the
  same pytest command targeting `test_iteration_context_inputs.py`,
  `test_project_iteration_planner.py`, `test_project_iteration_continuation.py`,
  `test_project_outcome_discovery.py`, `test_retry_provider_binding.py`,
  `test_manager_execution.py`, `test_worker_retry_evidence_process.py`,
  `test_review_inputs.py`, `test_skill_library.py` and
  `test_project_iteration_execution.py` passed **42 cases in 165.68 seconds**.
  This checks the merged Worker/planner/retry paths.
- Main then advanced with #681's memory retrieval integration. It also merged
  without conflict. At `a02ee36`, the same pytest command targeting
  `test_iteration_context_inputs.py`, `test_project_outcome_discovery.py`,
  `test_project_iteration_continuation.py`, `test_memory_core.py`,
  `test_memory_embeddings.py`, `test_retry_provider_binding.py`,
  `test_review_inputs.py` and `test_skill_library.py` passed **39 cases** with
  **3 PostgreSQL cases skipped**, in 122.67 seconds. These checks cover the
  intersecting runtime memory, planning, delivery and retry paths. Neither
  integration run is a claim of a full-suite run on the final integrated source.
- Node 24.22.1 at `a02ee36`: `npm --prefix frontend test` passed
  (12 Node + 315 Vitest checks). `npm --prefix frontend run lint` and
  `npm --prefix frontend run build` passed, with existing bundle-size and
  dynamic-import warnings.
- `ruff check src/goodteam_os tests/` and `git diff --check`: passed on the
  integrated source.

The skipped cases need a dedicated disposable PostgreSQL database. No paid
provider, real embedding service or deployed-browser smoke was run. These local
results are not remote CI or independent review evidence.
