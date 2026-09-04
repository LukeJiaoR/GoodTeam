# Task-Assistant-Led Project Interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a directory is imported, make the Task Assistant (`任务助手`) conversation the single user-facing entry for project understanding, confirmations, configuration problems, and real work intake.

**Architecture:** Keep readiness as an internal, backend-owned safety state machine, but remove it as a separate user journey. A project-scoped conversation stores ordered turns and proposed actions; a Task Assistant intake service interprets the user's intent, combines it with authoritative project state, and chooses between answering, advancing safe project understanding, requesting confirmation, or proposing executable work. A workflow starts only from an explicit, server-bound human confirmation and always retains the user's exact request.

**Tech Stack:** Python control plane and JSON stores, existing readiness executor, React/Vitest, pytest, Playwright.

---

## Product contract

- `orchestrator` remains the stable internal role key. Every user-visible `Manager` label becomes `Task Assistant` in English and `任务助手` in Chinese.
- Importing a directory always opens `/projects/{key}/work` directly in a chat interaction; there is no initial task-submission form.
- The UI never labels a project “unready” and never sends the user to a separate readiness page.
- Project type detection is a hint, not an irreversible decision. Empty directories strongly suggest a new project; Git/source/manifests at the root or one bounded level below strongly suggest an existing project; weak evidence remains internally ambiguous.
- Nested repositories such as `CLIProxyAPI-local/CLIProxyAPI` must be detected by the bounded classifier instead of falling through merely because the selected parent has no root manifest.
- A confident hint adapts the greeting and suggestions. An ambiguous hint produces a neutral greeting and one conversational clarification. The user may correct any hint in free text before execution.
- The Task Assistant speaks first, while the composer is visible and focused immediately. The client does not decide whether the user's reply means onboarding or workflow start.
- The opening message and suggestions adapt to project type, but suggestions are optional accelerators rather than required branches.
- Clicking a suggestion copies natural-language text into the composer without auto-sending it. The user can edit, replace, or ignore it before sending.
- Free input is always visually primary. The Task Assistant must accept requests outside the suggested choices and must not force the user through a questionnaire.
- `POST /api/projects/{key}/task-assistant/messages` is the only free-text intake mutation used by Work.
- Informational input such as “看一下当前的项目” produces an answer or project-understanding progress in the conversation; it must not silently become a delivery workflow.
- An actionable request produces a proposed task in the conversation. The user may edit it, add context, discard it, or explicitly start it.
- `POST /api/projects/{key}/task-assistant/actions` executes only a current server-issued action ID with an idempotency key. Readiness confirmations, retries, and workflow start appear as chat actions but remain backend-authorized transitions.
- Before internal readiness is complete, the Task Assistant may safely advance project understanding while preserving each user message. It does not maintain one global `pending_request` that later messages can overwrite.
- A normal workflow starts only after both internal readiness and explicit human confirmation. Model restatements never replace human text.
- The conversation is project-scoped and continuous. Starting a workflow attaches a `run_id` to later turns and progress events; it does not replace or hide import/onboarding history.
- While a run is active, status questions are answered in place, requested changes are routed through the existing intent-confirmation authority, and unrelated new work becomes a separate proposal rather than silently mutating the active run.
- The Task Assistant is the primary command surface: ordinary onboarding, task intake, progress, clarification, retry, and approval can be completed in the conversation. Team, Review, and Knowledge remain linked detail workbenches for advanced configuration, full-document judgment, and evidence inspection; opening them never loses the conversation context.
- Readiness, root binding, snapshot/evidence binding, read-only inspection, idempotency, and worker preflight remain backend invariants.
- Existing `project_mode=unconfirmed` records migrate in place on first Task Assistant intake; users do not need to delete and re-import projects.

### Task Assistant message response

```json
{
  "task_assistant_session": {
    "schema_version": "task_assistant_session.v1",
    "project_key": "CLIPROXYAPI-LOCAL",
    "status": "onboarding",
    "project_mode_hint": {
      "mode": "existing_project",
      "confidence": "high"
    },
    "timeline": [
      {
        "message_id": "welcome:CLIPROXYAPI-LOCAL",
        "actor": "task_assistant",
        "content": "我已经连接到这个项目。有什么可以帮你？",
        "created_at": "2026-09-04T12:00:00Z",
        "run_id": ""
      }
    ],
    "suggestions": [
      {"id": "understand-project", "label": "先了解当前项目", "draft": "先帮我了解一下当前项目"}
    ],
    "attention_request": null,
    "proposed_task": null
  },
  "workflow_started": false,
  "run_id": ""
}
```

`status` is one of `onboarding`, `waiting_for_human`, `ready`, `workflow_active`, or `blocked`. Every timeline turn has a stable `message_id`, `actor`, `created_at`, optional `run_id`, and content. `attention_request` and `proposed_task` contain server-defined action IDs; the model may explain them but may not invent or authorize actions.

The initial greeting and suggestions are deterministic server projections from the mode hint; they require no model call and remain available when the configured model is unavailable. Suggestions contain draft text only and are never executable action IDs.

### Intent routing

| User intent | Task Assistant behavior | Workflow start |
| --- | --- | --- |
| Greeting or general conversation | Reply naturally using available project context. | Never |
| Understand, summarize, or inspect the project | Advance bounded read-only understanding if needed, then answer with evidence links and known limitations. | Never |
| Ask a question about code or project state | Answer from current evidence; say what is unknown instead of fabricating. | Never |
| Request a change, fix, feature, or delivery | Create an editable proposed task using the exact user text as its source. | Only after explicit confirmation |
| Change Task Assistant/team configuration | Explain the current setting and present a structured configuration action or Team link. | Never |
| Answer a required safety confirmation | Submit the exact server-issued action with the user's values. | Only if this completes preparation and a separately confirmed task exists |

Intent interpretation is advisory. When intent is ambiguous, the assistant asks one concise follow-up question while preserving the user's text; it does not choose the more consequential route by default.

### Opening conversation

For an existing project:

```text
任务助手：我已经连接到这个项目。有什么可以帮你？

[先了解当前项目] [找出值得先处理的问题] [实现一个新需求] [修复一个已知问题]

输入消息…
```

For a new project:

```text
任务助手：这是一个新项目。你想从一个想法开始，还是直接告诉我具体要做什么？

[帮我梳理产品想法] [生成初始方案] [创建项目基础结构] [我直接描述需求]

输入消息…
```

For an ambiguous project:

```text
任务助手：我已经连接到这个目录。你可以直接告诉我想做什么；如果有帮助，也可以先告诉我这是一个新项目，还是已有项目。

[这是一个新项目] [这是已有项目] [先帮我看看目录] [我直接描述需求]

输入消息…
```

The suggestion row may wrap or collapse on small screens, but it never replaces or disables the composer. Once the user sends a message, the greeting and any internal onboarding progress remain ordinary items in the same conversation timeline.

---

### Task 1: Make project-type detection bounded and correctable

**Files:**
- Modify: `src/goodteam_os/project_readiness.py`
- Test: `tests/test_project_readiness.py`
- Test: `tests/test_project_import.py`

- [ ] Add a failing test proving a container with nested repository signals produces a high-confidence `existing_project` hint.

```python
def test_nested_repository_container_suggests_existing_project(tmp_path: Path) -> None:
    nested = tmp_path / "CLIProxyAPI"
    nested.mkdir()
    (nested / "go.mod").write_text("module example.com/proxy\n", encoding="utf-8")
    (tmp_path / "Management-Center").mkdir()

    assert assess_project_mode(tmp_path) == {
        "mode": "existing_project",
        "confidence": "high",
        "evidence": ["CLIProxyAPI/go.mod"],
    }
```

- [ ] Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_project_readiness.py -q -k nested_repository_container` and verify it fails because only root-level signals are inspected.
- [ ] Introduce `assess_project_mode()` returning `mode`, `confidence`, and bounded relative-path evidence. Inspect root entries plus one child level, skip symlinks/vendor/generated directories, and do not read file contents beyond names needed for classification.
- [ ] Keep `classify_project_mode()` as a compatibility wrapper over the assessment. Do not convert every non-empty directory into an existing project.
- [ ] Add tests for empty directories, root repositories, nested repositories, documentation-only directories, symlinked children, and bounded entry limits.
- [ ] Persist the assessment as an internal hint. Allow a user-authored Task Assistant message/action to correct it before workflow start, recording both the original evidence and correction provenance.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_project_readiness.py tests/test_project_import.py -q`.

### Task 2: Add a durable project-scoped Task Assistant conversation

**Files:**
- Create: `src/goodteam_os/task_assistant_session.py`
- Create: `tests/test_task_assistant_session.py`

- [ ] Write failing tests for atomic persistence, ordered multi-turn history, exact user-text preservation, idempotent message append, proposed-task revisions, and status transitions.
- [ ] Store the conversation at `<workspace>/state/projects/<KEY>/task-assistant-session.json`; never write it into the imported repository.
- [ ] Implement a narrow `TaskAssistantSessionStore` API: `read()` returns a validated session; `append_message(message_id, actor, content, run_id="")` appends exactly once; `set_attention(attention)` replaces the current structured request; `propose_task(source_message_ids, exact_request)` creates a revisioned proposal without rewriting source messages; `revise_task(proposal_id, exact_request)` records a new human-authored revision; and `bind_workflow(proposal_id, run_id)` atomically binds one confirmed proposal while retaining the full project conversation.

- [ ] Reject blank text, conflicting reuse of a `message_id`, unknown status values, and malformed attention actions.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_task_assistant_session.py -q`.

### Task 3: Route Task Assistant messages and actions

**Files:**
- Create: `src/goodteam_os/task_assistant.py`
- Modify: `src/goodteam_os/web.py`
- Test: `tests/test_task_assistant.py`
- Test: `tests/test_project_readiness_web.py`

- [ ] Write failing service tests for conversation, project-understanding, actionable-task, ambiguous-intent, legacy `unconfirmed`, readiness-awaiting-input, and confirmed workflow-start routes.
- [ ] Implement `handle_task_assistant_message()` with this ordering:

```python
def handle_task_assistant_message(*, workspace: Path, project_key: str, text: str, message_id: str) -> dict[str, object]:
    # 1. Persist exact user text idempotently.
    # 2. Read root-bound readiness.
    # 3. Interpret intent without granting authority to the model.
    # 4. Answer read-only requests or advance safe project understanding.
    # 5. Project required confirmation as a Task Assistant action.
    # 6. Create a proposed task for actionable requests; never auto-start it.
```

- [ ] Do not let model interpretation authorize readiness or workflow actions. The Task Assistant model supplies intent and explanation; `ProjectReadinessExecutor`, `build_capabilities()`, and action IDs remain authoritative.
- [ ] During onboarding and project-understanding answers, give the Task Assistant model only persisted readiness artifacts or bounded Repository Context Provider observations. Do not reuse a worker with unrestricted project-root or shell access.
- [ ] Generate the greeting and suggestion payload deterministically before any model invocation, so model/configuration failure still leaves a functional conversation shell with retry and Team-configuration actions.
- [ ] Add `POST /api/projects/{key}/task-assistant/messages` accepting exactly `{text, message_id}` and `POST /api/projects/{key}/task-assistant/actions` accepting `{action_id, values, idempotency_key}`.
- [ ] Make repeat submission with the same `message_id` return the same session/run rather than launching a second readiness execution or workflow.
- [ ] Add a failing test proving “看一下当前的项目” completes as a read-only conversational request with no `start_workflow()` call.
- [ ] Add a failing test proving a change request cannot start until its exact proposed-task revision is explicitly confirmed.
- [ ] Keep `/api/start` and readiness endpoints for CLI/backward compatibility, but remove them from the Work-page path.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_task_assistant.py tests/test_project_readiness_web.py tests/test_web.py -q -k 'task_assistant or readiness or start_workflow'`.

### Task 4: Project onboarding into the Task Assistant workspace

**Files:**
- Modify: `src/goodteam_os/team_workspace.py`
- Modify: `src/goodteam_os/web.py`
- Test: `tests/test_team_workspace.py`
- Test: `tests/test_web.py`

- [ ] Add failing tests proving a project with no workflow run projects a Task Assistant conversation, and that starting or completing a run preserves all earlier turns in order.
- [ ] Extend `build_team_workspace()` with a `task_assistant_session` input. Join workflow and Operator events into that project conversation using `run_id`/`execution_id`; do not choose one timeline and discard the others.
- [ ] Map readiness states to user-facing Task Assistant events such as `project_inspection_started`, `project_summary_ready`, `confirmation_required`, `worker_preflight_failed`, and `project_ready`.
- [ ] Render machine progress as compact system events, not fake conversational prose from the Task Assistant. Reserve assistant bubbles for actual replies to the user.
- [ ] Keep internal names (`readiness`, `project_mode_unconfirmed`) out of rendered copy and timeline content.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_team_workspace.py tests/test_web.py -q -k 'task_assistant_session or onboarding'`.

### Task 5: Make Work the only onboarding surface

**Files:**
- Modify: `frontend/src/features/projects/ProjectsPage.jsx`
- Modify: `frontend/src/features/projects/ProjectsPage.test.jsx`
- Modify: `frontend/src/features/runs/runApi.js`
- Modify: `frontend/src/features/runs/WorkPage.jsx`
- Modify: `frontend/src/features/runs/workspace/TeamWorkspace.jsx`
- Modify: `frontend/src/features/team/teamModel.js`
- Modify: `frontend/src/features/team/TeamPage.jsx`
- Modify: `frontend/src/features/team/TeamPage.test.jsx`
- Modify: `frontend/src/features/runs/WorkPage.test.jsx`
- Modify: `frontend/src/features/inbox/InboxPage.jsx`
- Modify: `frontend/src/features/inbox/InboxPage.test.jsx`
- Modify: `frontend/src/features/knowledge/KnowledgePage.jsx`
- Modify: `frontend/src/features/knowledge/KnowledgePage.test.jsx`
- Modify: `frontend/src/i18n/locales/en.js`
- Modify: `frontend/src/i18n/locales/zh-CN.js`

- [ ] Add failing tests proving both new import and existing project entry navigate to `/projects/{key}/work`, never expose a misleading “Ready” label, and render `任务助手`/`Task Assistant` instead of Manager.
- [ ] Add `runApi.sendTaskAssistantMessage(projectKey, {text, message_id})` and `runApi.runTaskAssistantAction(projectKey, {action_id, values, idempotency_key})` for the new endpoints.
- [ ] Replace `ManagerMissionEntry` with a `TaskAssistantComposer` chat component that is mounted and focused immediately. Seed the timeline with the server-projected Task Assistant greeting.
- [ ] For existing projects, render soft suggestions for understanding the project, finding priorities, adding a feature, and fixing a problem. For new projects, render soft suggestions for clarifying the idea, drafting a plan, creating the base structure, and entering a custom request.
- [ ] Make suggestion clicks populate the composer without submitting. Keep typed input primary, editable, and valid even when it matches no suggestion.
- [ ] Change the composer to call only `sendTaskAssistantMessage`; remove its direct `/api/start` payload construction.
- [ ] Render Task Assistant onboarding progress and structured attention in the existing conversation pane. Confirmation buttons post server-issued action IDs/values; free text remains available for corrections.
- [ ] Render editable proposed-task cards with “开始执行”, “继续补充”, and “取消” actions. Never show a proposal for a purely informational request.
- [ ] If a proposal already exists and a new message could be either a revision or unrelated work, ask one concise clarification instead of overwriting the proposal.
- [ ] Keep model/backend failure in the Task Assistant thread with a retry action and a link to Team configuration. Do not render it as a workflow-start readiness error.
- [ ] Remove user-visible readiness warnings/labels from Inbox and Knowledge. Preserve project-understanding artifacts in Knowledge under user-facing names such as “项目概览” and “项目约束”.
- [ ] Poll project state while the Task Assistant reports background understanding in progress, pausing when the tab is hidden and stopping on attention, completion, or failure.
- [ ] Preserve the draft on request failure; use Enter to send and Shift+Enter for a newline on desktop; keep a visible send button; hide or collapse opening suggestions after the first user message.
- [ ] Retain `/projects/{key}/home` as a compatibility redirect to Work; remove it from normal navigation and onboarding actions.
- [ ] Run `npm run test:unit --prefix frontend -- src/features/projects/ProjectsPage.test.jsx src/features/runs/WorkPage.test.jsx`.

### Task 6: Migrate the reported project and verify the live contract

**Files:**
- Modify: `tests/test_task_assistant.py`
- Delete: `frontend/e2e/project-load-home.spec.js`
- Create: `frontend/e2e/task-assistant-interaction.spec.js`
- Modify: `docs/current-progress.md`

- [ ] Add a fixture matching `CLIProxyAPI-local`: a non-Git parent containing two nested project directories and an old schema-1 `project_mode=unconfirmed` readiness record.
- [ ] Prove “看一下当前的项目” upgrades the old record, starts bounded read-only understanding, persists the exact Chinese request, answers in the conversation, and never creates a workflow.
- [ ] Prove a later explicit change request becomes a proposed task; required command/constraint confirmations remain in the thread; “开始执行” then starts exactly one workflow using the confirmed exact request.
- [ ] Run the Tier 3 backend suite because this changes readiness authority and workflow start:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests -q
.venv/bin/ruff check src/goodteam_os/ tests/
```

- [ ] Run frontend validation:

```bash
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
```

- [ ] Run an isolated Tier 4 browser smoke using a disposable parent directory with two nested repositories. Verify import → greeting → free-text project question → read-only answer → editable task proposal → explicit start, with zero console errors. Do not mutate `/Users/luke/WorkSpace/CLIProxyAPI-local` during smoke.
- [ ] Run `git diff --check` and record exact pass counts and the isolated live evidence in `docs/current-progress.md`.

## Non-goals for this change

- Removing backend readiness safety checks.
- Letting model prose authorize filesystem writes, commands, or workflow start.
- Moving readiness state into the imported repository.
- Replacing the Team configuration screen with natural-language-only configuration.
- Automatically executing discovered project commands during onboarding.
- Deleting the Team, Review, or Knowledge detail workbenches; this change makes them secondary surfaces rather than competing entry flows.
