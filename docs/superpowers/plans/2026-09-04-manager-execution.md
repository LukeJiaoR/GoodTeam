# Manager Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Team-page Task Assistant configuration drive the Work-page Task Assistant conversation while preserving human approval authority.

**Architecture:** Keep `orchestrator` as the stable internal role key and present it as Task Assistant. A bounded execution service resolves the latest project role backend, model, reasoning, and additional prompt, creates one read-only `WorkerJobStore` job for every configured chat turn, parses its structured interpretation, and stores runtime-policy provenance. Confirmation always commits the user's exact text, never model-expanded text. A cross-process start claim and deterministic run ID prevent duplicate workflow dispatch; an unprovable interrupted start fails closed for manual reconciliation.

**Tech Stack:** Python control plane and worker store, pytest, React, Vitest/Testing Library.

---

### Task 1: Define the Manager execution contract

**Files:**
- Create: `src/goodteam_os/manager_execution.py`
- Create: `tests/test_manager_execution.py`

- [x] Write tests proving `orchestrator` backend/model/reasoning/additional prompt are copied into a single Manager job and structured output carries job provenance.
- [x] Run the focused tests and verify they fail because the service does not exist.
- [x] Implement job construction, execution, bounded structured-result parsing, and explicit failure errors.
- [x] Run the focused tests and verify they pass.

### Task 2: Route durable Manager discussion through the execution service

**Files:**
- Modify: `src/goodteam_os/intent_commit.py`
- Modify: `src/goodteam_os/web.py`
- Modify: `tests/test_intent_commit.py`

- [x] Write tests proving configured discussions use Manager output and confirmation still commits the user's exact request.
- [x] Run the focused tests and verify they fail for the missing integration.
- [x] Allow the intent store to persist a validated external interpretation, invoke Manager execution for file-backed projects, and attach execution provenance to approval audit context.
- [x] Preserve deterministic behavior for legacy/manual projects and run the focused tests to green.

### Task 3: Unify Team naming and singleton configuration

**Files:**
- Modify: `frontend/src/features/team/teamModel.js`
- Modify: `frontend/src/features/team/TeamPage.jsx`
- Modify: `frontend/src/features/team/TeamPage.test.jsx`
- Modify: `frontend/src/i18n/locales/en.js`
- Modify: `frontend/src/i18n/locales/zh-CN.js`
- Modify: `src/goodteam_os/web.py`
- Modify: `tests/test_web.py`

- [x] Write frontend and backend tests asserting `orchestrator` renders as Manager and its desired instance count is fixed at one.
- [x] Run tests and verify the old Orchestrator label/editable capacity fail.
- [x] Update presentation and enforce the singleton invariant at the API normalization boundary.
- [x] Run focused frontend and backend tests to green.

### Task 4: Verify the control-plane and browser contract

**Files:**
- Modify: `docs/current-progress.md`

- [x] Run focused Manager, intent, web, and Team tests.
- [x] Run the full backend suite and Python lint.
- [x] Run frontend tests, lint, and production build.
- [x] Run `git diff --check` and record the exact verification evidence and any environmental failures.

### Task 5: Close PR review execution-chain gaps

**Files:**
- Modify: `src/goodteam_os/manager_execution.py`
- Modify: `src/goodteam_os/task_assistant.py`
- Modify: `src/goodteam_os/task_assistant_session.py`
- Modify: `src/goodteam_os/web.py`
- Modify: `src/goodteam_os/workers.py`
- Modify: focused tests and localized Task Assistant copy

- [x] Route every configured primary chat message through the selected Task Assistant provider, model, reasoning depth, and additional prompt.
- [x] Initialize missing legacy readiness records before the first message.
- [x] Surface completed repository-understanding artifacts as idempotent assistant messages.
- [x] Serialize message retries and workflow-start claims across processes.
- [x] Publish workflow state only after execution reaches a durable gate; block automatic replay when completion is indeterminate.
- [x] Enforce read-only execution for pre-confirmation model turns and reject adapters without a verifiable read-only boundary.
- [x] Repeat full Tier 4 validation and update PR #400 with evidence.
