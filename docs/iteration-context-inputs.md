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

Automatic continuation passes its existing reservation identity (continuation
ID and consumed planning slot). A lost reply can reuse that slot's original
job; changed input cannot invoke another model call under the same slot. This
does not add a dispatcher, ledger or new budget grant.

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
- `tests/test_project_iteration_continuation.py`: the existing two-batch isolated
  execution now asserts that the second selection receives the first batch's
  verified delivery and its current HEAD. Both actual Worker subprocesses,
  candidate landing and delivery receipts remain part of that fixture.
- Existing planner inventory, queue handoff and outcome-discovery checks remain
  the regression boundary. Offline fixtures do not prove real-model quality.

GitHub CI remains paused. PostgreSQL integration requires a dedicated disposable
test database; production database configuration is never used for these checks.
