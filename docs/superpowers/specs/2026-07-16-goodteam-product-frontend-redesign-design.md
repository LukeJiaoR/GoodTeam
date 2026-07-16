# GoodTeam product frontend redesign

Status: approved design

Date: 2026-07-16

Scope: product information architecture, project loading, readiness, core workspaces, visual system, i18n, frontend architecture, API contracts, migration, and verification

## 1. Summary

GoodTeam will replace its current module-oriented dashboard with a calm execution
workspace organized around the operator's next decision. The redesign keeps the
existing React, Vite, Tailwind, and Python backend stack, but changes the product
model and navigation.

The first product contract is that any valid directory can be loaded once and
immediately opened as a recoverable project home. Loading only registers the
project in GoodTeam. New-project initialization and existing-codebase
understanding happen afterward as explicit, resumable readiness steps.

The project workspace has four primary spaces:

1. **Home** — next action, readiness, blockers, mission, and project facts.
2. **Work** — runs, workflow stages, tasks, workers, messages, and execution
   evidence.
3. **Review** — approvals, workbench, artifacts, diffs, evidence, dissent, and
   human decisions.
4. **Knowledge** — project understanding, documents, artifacts, memory, and
   source indexes.

The interface supports Simplified Chinese (`zh-CN`) and English (`en`) as equal
product locales.

## 2. Current-state audit

The present frontend exposes backend modules directly instead of providing a
coherent operator workflow.

- `frontend/src/utils/navigation.jsx` exposes thirteen project-level navigation
  items across Work, Observe, Inspect, and System groups. Approvals, Workbench,
  Artifacts, Actions, Board, Workflow, Dev Loop, Messages, Agents and Memory,
  Smoke, System, and Docs compete for equal prominence.
- `frontend/src/App.jsx` owns application data loading, forms, mutations,
  routing-like state, and cross-feature coordination in one component of roughly
  1,400 lines.
- `frontend/src/components/ProjectDashboard.jsx` has a very wide prop contract,
  while `ProjectTabRouter.jsx` manually switches every top-level view and forwards
  a second wide set of props.
- `ApprovalsTab.jsx` is roughly 1,500 lines and combines navigation, artifact
  editing, open questions, refinement, reviewer provenance, debate, evidence, and
  decisions without stable subcomponent boundaries.
- `NewProjectModal.jsx` combines project identity, path selection, context
  generation, Git, workers, delivery, staging, and task integrations. The primary
  launch action does not guarantee that project configuration and context steps
  have completed.
- Existing-project start requires a project config, while the frontend can call
  start without first creating one. New-project start can succeed without a
  config and create a `manual` run disconnected from the selected root.
- Context estimate and generation require an existing project config and are not
  part of a unified import transaction. The current deterministic context output
  does not use the model displayed as the recommended generation model.
- `frontend/src/index.css` contains a Duolingo-like vivid theme, a separate
  minimal theme, thick borders, physical button shadows, high-saturation status
  colors, tiny type, and many all-caps labels. The two themes imply different
  product personalities.
- `frontend/src/App.css` still contains starter-template styles unrelated to the
  product.
- The selected project is represented in the URL, but active project spaces,
  runs, review items, and inspector views are local state and cannot be reliably
  refreshed, shared, or restored.

This is a product-architecture problem, not a styling-only problem.

## 3. Goals

### 3.1 Primary goals

- Load any valid local project directory exactly once and enter a usable,
  recoverable project home without waiting for full initialization or codebase
  understanding.
- Give new and existing projects a shared product shell and readiness model while
  preserving their different preparation paths.
- Organize the interface around project readiness, runs, decisions, and knowledge
  instead of backend modules.
- Make the operator's next required action obvious on every primary page.
- Consolidate approvals and review workbench capabilities into one decision
  workspace.
- Preserve control-plane evidence, provenance, gates, and safety boundaries.
- Support `zh-CN`, `en`, and an automatic system-locale preference.
- Provide complete loading, empty, input-required, blocked, failed, stale,
  offline, permission-denied, partial-success, and complete states.
- Replace the frontend incrementally, with route-level parity gates and no
  big-bang cutover.

### 3.2 Non-goals

- Migrating away from React, Vite, Tailwind, or the Python backend.
- Replacing GoodTeam's workflow engine, ledger, approval policy, evidence model,
  or worker execution contracts.
- Automatically modifying an adopted repository during the minimal load step.
- Automatically spending model tokens before the user sees and confirms the
  relevant readiness action and estimate.
- Keeping Vivid and Minimal as separate visual themes. The redesign has one
  visual language with light and dark appearance modes.
- Exposing every diagnostic capability in primary navigation.

## 4. Product principles

### 4.1 Loaded is not ready

`loaded` means that GoodTeam has a durable local project record, a canonical root
reference, and a route that can be reopened. `ready` means that the project has
passed the minimum preparation contract for starting work.

Loading must be fast, deterministic, and safe. Readiness can be incremental,
interactive, costly, and recoverable.

### 4.2 One next action

Each primary page identifies one highest-priority action. Supporting data remains
available but does not visually compete with that action.

### 4.3 Progressive disclosure

Common product actions stay visible. Diagnostics, integration configuration,
raw evidence, and advanced routing appear in context or under project settings.

### 4.4 Evidence stays close to decisions

Review recommendations, dissent, evidence coverage, missing evidence, artifact
content, diffs, questions, and decision controls belong in the same workspace.

### 4.5 URLs are durable UI state

Project, space, run, review item, selected artifact, and inspector view are
represented in the URL. Refreshing or sharing the URL restores the same product
context, subject to access and data availability.

## 5. Project lifecycle and readiness

### 5.1 Project lifecycle

The project summary exposes one of these lifecycle states:

- `loaded` — the project record exists and Home is available.
- `preparing` — one or more readiness steps are incomplete or running.
- `ready` — the minimum readiness contract is satisfied.
- `attention_required` — a readiness step needs human input or failed safely.
- `archived` — the project remains inspectable but cannot start new work.

This lifecycle is separate from a run's execution state.

### 5.2 Readiness step contract

Every readiness step stores:

- stable step ID;
- status: `pending`, `running`, `needs_input`, `failed`, `complete`, or `skipped`;
- whether the step is required for the current project mode;
- attempt count;
- start and finish timestamps;
- last error code and safe explanation;
- evidence references;
- input requirements;
- whether it can modify the target root;
- whether it can invoke a model or another paid provider;
- the capability it unlocks.

Steps are idempotent or explicitly refuse unsafe repetition. Refreshing the page
or restarting GoodTeam does not create another project or lose step progress.

### 5.3 Shared steps

All projects use these shared steps:

1. project record;
2. safe path and permission preflight;
3. project-mode classification or confirmation;
4. worker and model preflight;
5. minimum-readiness evaluation.

### 5.4 New-project readiness

New projects add:

1. goal and delivery-type confirmation;
2. initialization template and directory structure selection;
3. explicit Git initialization and optional remote creation;
4. project brief and baseline context generation;
5. initialization verification.

Git remote creation is not a minimum-readiness requirement. Target-root writes
require explicit confirmation and show their planned paths before execution.

### 5.5 Existing-project readiness

Existing projects add:

1. read-only repository profile: Git state, languages, manifests, size, and
   instruction files;
2. repository map and source index;
3. project understanding with provenance and confidence;
4. build, test, and run command discovery and verification;
5. constraints, risks, and prohibited-area confirmation.

The minimum existing-project contract is a repository map, key command set,
confirmed constraints, and at least one usable worker. Deeper indexing may
continue after the project becomes usable.

### 5.6 Ambiguous projects

A non-empty directory that does not confidently look like a codebase still loads.
Home shows `project_mode_unconfirmed` and offers:

- treat as a new project;
- treat as an existing asset;
- keep registered and decide later.

Classification uncertainty never turns a valid directory into a load failure.

## 6. Project import contract

### 6.1 Minimal import

The new import operation is conceptually:

```http
POST /api/projects/import
Content-Type: application/json

{
  "root": "/absolute/path",
  "display_name": "GoodTeam",
  "requested_key": "GOODTEAM"
}
```

The server:

1. normalizes and validates the path;
2. confirms that it is an existing readable directory;
3. derives a canonical-root fingerprint and detects duplicates;
4. resolves or creates the local project key;
5. writes the GoodTeam-owned project record atomically;
6. creates initial readiness records;
7. returns the project summary and Home URL.

The operation does not create `docs/`, initialize Git, call a model, scan full
file bodies, or modify the target root.

Repeated imports of the same canonical root return the existing project and its
Home URL instead of creating duplicates.

### 6.2 Readiness execution

```http
POST /api/projects/{project_key}/readiness/{step_id}/run
Content-Type: application/json

{
  "confirmation": true,
  "inputs": {},
  "idempotency_key": "..."
}
```

The response contains the step status, evidence refs, required follow-up input,
and current capability projection. Long-running work returns an accepted status
and can be polled from a step-status endpoint.

### 6.3 Capability projection

Home and the global shell consume a server-produced capability projection rather
than duplicating readiness rules in the frontend. At minimum it reports whether
the operator can:

- inspect project facts;
- edit project settings;
- run readiness steps;
- start a workflow;
- execute target-root writes;
- run delivery and smoke actions;
- make review decisions.

Disabled actions explain the missing step or permission and link to the recovery
action.

## 7. Information architecture and routes

### 7.1 Global spaces

- **Projects** — project portfolio, readiness, active run, attention count, and
  last activity.
- **Inbox** — cross-project decisions and input requests that require a person.
- **Activity** — cross-project immutable activity projection and filters.
- **Search / Command** — navigate, find projects, runs, reviews, artifacts, and
  knowledge; initiate safe contextual actions.
- **Settings** — global appearance, locale, service health, and global defaults.

### 7.2 Project spaces

- **Home** — next action, readiness, mission, blockers, project facts, and safe
  shortcuts.
- **Work** — run selector, stages, plan, tasks, workers, messages, delivery, and
  execution evidence.
- **Review** — review queue, document or diff, questions, evidence inspector,
  dissent, rationale, and decisions.
- **Knowledge** — project understanding, repository map, source index, artifacts,
  docs, and memory.
- **More** — project settings, workers and models, integrations, delivery and CI,
  diagnostics, smoke runs, and advanced system information.

### 7.3 Routes

The target route model is:

```text
/projects
/inbox
/activity
/settings
/projects/:projectKey/home
/projects/:projectKey/work
/projects/:projectKey/work/runs/:runId
/projects/:projectKey/review
/projects/:projectKey/review/:reviewId
/projects/:projectKey/knowledge
/projects/:projectKey/settings/:section
```

Query parameters may select a subview or inspector but do not replace stable
resource routes.

### 7.4 Existing-to-new mapping

| Current area | New location |
| --- | --- |
| Overview | Home |
| Approvals + Workbench | Review |
| Workflow + Agile Board | Work / Plan |
| Workers + Messages | Work / Execution |
| Actions | Project or global Activity |
| Artifacts + Docs + Memory | Knowledge |
| Dev Loop + Smoke | More / Delivery and diagnostics |
| System + agent configuration | Project settings |

Inbox, Home, and Activity show different projections of shared events: Inbox is
what needs a person, Home is the current project's next action, and Activity is
the historical record.

## 8. Screen designs

### 8.1 Projects

Projects is a portfolio, not a grid of equal generic cards. It prioritizes:

- projects requiring attention;
- active runs;
- projects still preparing;
- recently used ready projects.

The primary action is `Load project`. Selecting a directory uses minimal import
and navigates immediately to Home.

### 8.2 Project Home

Home shows:

- project identity and lifecycle;
- a single next-action module;
- readiness steps and progress;
- current run or a clear no-active-run state;
- blockers and input requests;
- verified project facts;
- a concise statement of currently unlocked capabilities.

An unready project can still inspect facts, edit settings, view scan logs, and
continue readiness. Starting a run is enabled only when the server capability
projection permits it.

### 8.3 Work

Work is organized around one selected run:

- top: ordered workflow stages and run selector;
- left: run-local views such as current work, plan, messages, and delivery;
- center: the selected stage, task, or worker work item;
- right: run inspector with latest events, changed paths, evidence, and logs.

Historical runs are selectable but visually distinct from the active run.
Pausing, retrying, dispatching, or running delivery checks remains contextual to
the selected run and capability projection.

### 8.4 Review

Review combines the current Approvals and Workbench capabilities:

- left: review queue and status;
- center: artifact, rendered document, diff, or open questions;
- right: recommendation, provenance, evidence coverage, policy status, reviewer
  dissent, and decision controls.

Approve, request changes, and reject remain distinct actions. Required rationale
is validated inline. The decision request carries review item IDs, artifact
version, evidence snapshot, reviewer provenance, and the existing control-plane
identity contract.

### 8.5 Knowledge

Knowledge presents:

- current project understanding and confidence;
- repository map and command catalog;
- source provenance and refresh status;
- documents and generated artifacts;
- project and role memory;
- search across indexed project knowledge.

The UI distinguishes generated understanding from source material and shows
staleness or missing provenance.

## 9. Visual system

### 9.1 Direction

The approved direction is a calm execution workspace:

- warm neutral surfaces;
- one restrained terracotta accent;
- stronger typography and more whitespace on default pages;
- higher density only in Work, Review, logs, and inspectors;
- editorial reading treatment for PRDs, RFCs, diffs, and rationale;
- dark appearance mode using the same hierarchy and accent behavior.

The system does not retain separate Vivid and Minimal themes.

### 9.2 Typography

- UI: locally bundled Geist, followed by `PingFang SC`, `Microsoft YaHei`, and
  system UI fallbacks for Simplified Chinese glyphs.
- Data and code: Geist Mono followed by platform monospace.
- Default UI body text is 14–16 px.
- Labels use sentence case by default. Small caps and uppercase are reserved for
  short metadata, not page-wide hierarchy.
- Numeric dashboards use tabular figures.
- Long-form artifacts use a readable editorial measure and line height.

### 9.3 Color and surfaces

- Warm neutral surface scale provides hierarchy.
- Terracotta is the single brand and primary-action accent.
- Success, warning, danger, and information colors are semantic and are not used
  as decoration.
- Large containers use 8–10 px radii; nested controls use 4–7 px radii.
- Borders and shadows are sparse. A card exists only when elevation or grouping
  communicates structure.

### 9.4 Interaction and motion

- Interactive transitions use 160–240 ms and transform or opacity.
- Hover, pressed, focus-visible, disabled, loading, and selected states are
  defined for every control.
- Reduced-motion preferences disable nonessential motion.
- Skeletons preserve layout and replace generic full-page spinners.
- Destructive and target-root-writing actions show impact before confirmation.

## 10. Internationalization

### 10.1 Supported locales

- `zh-CN`
- `en`
- `auto`, resolved from the operating system or browser and persisted as the
  user's preference

### 10.2 Contract

- All user-visible frontend strings use semantic translation keys. JSX and hooks
  contain no hard-coded user-facing English or Chinese.
- Backend status IDs, error codes, resource keys, and capability names remain
  stable machine identifiers.
- The frontend maps stable IDs to localized labels and explanations.
- Dates, times, numbers, relative time, and lists use `Intl` APIs.
- Paths, commands, model identifiers, Git refs, code symbols, and raw evidence
  are not translated.
- Critical actions and error recovery text are never truncated.
- Layouts are tested with at least 35 percent English text expansion relative to
  compact Chinese labels.
- Missing translations fail CI rather than silently exposing the key in shipped
  UI.

## 11. Responsive and accessible behavior

- At 1280 px and above, Work and Review may use three panes.
- From 768 to 1279 px, the inspector becomes a drawer or secondary route and the
  main workspace uses two panes.
- Below 768 px, list, content, and inspector become explicit drill-down routes.
  Critical review actions stay above the bottom safe area.
- Every drill-down view has visible back navigation.
- Keyboard focus order follows the visual workflow.
- Focus indicators meet contrast requirements.
- Color never carries status alone.
- Headings, landmarks, live-region announcements, form labels, and error
  associations use semantic HTML and accessible attributes.
- The product meets WCAG 2.2 AA for the redesigned routes.

## 12. First-class UI states

Every primary route and major component supports:

- **Loading** — shape-matching skeleton without losing navigation context.
- **Empty** — explains why the state is empty and offers the correct next action.
- **Needs input** — identifies the missing decision, impact, and required input.
- **Blocked** — identifies the blocker, evidence, safe retry, and alternative.
- **Failed** — inline error with stable error code and request ID.
- **Stale** — last-updated time, stale reason, and safe refresh behavior.
- **Offline** — preserves readable cached state and never pretends a mutation
  succeeded.
- **Permission denied** — identifies the boundary and recovery method.
- **Partial success** — reports each completed and failed sub-operation without
  discarding the usable project Home.
- **Complete** — summarizes outcome and proposes the next meaningful action.

Browser alerts are not used for product errors.

## 13. Frontend architecture

### 13.1 Stack

The redesign keeps React 19, Vite, Tailwind 4, and JavaScript. It adds
`react-router-dom` for nested, durable resource routes. The backend serves the
React entry point for recognized non-API product routes so direct navigation and
browser refresh work. `/api/**`, static assets, and explicit backend utility
routes retain their existing routing boundaries and never fall through to the
SPA entry point.

### 13.2 Module boundaries

```text
frontend/src/
  app/                    routing, global shell, providers, capability guards
  components/ui/          reusable design-system primitives
  features/projects/      portfolio, import, project summary
  features/readiness/     readiness list, step details, step execution
  features/runs/          Work and run-local data
  features/reviews/       Review queue, content, evidence, decisions
  features/knowledge/     understanding, docs, artifacts, memory, search
  features/settings/      project and global settings
  i18n/                   locale runtime and zh-CN/en catalogs
  lib/api/                API client, error normalization, request cancellation
  lib/routing/            route builders and legacy redirects
```

Features depend on UI primitives and API contracts, not on sibling feature
internals. Components receive focused view models rather than the entire project
payload.

### 13.3 State ownership

- Server-owned project, run, review, evidence, and readiness state comes from
  feature endpoints.
- Resource selection and shareable view state live in the URL.
- Short-lived form drafts remain local to the feature.
- Drafts that must survive navigation state this requirement explicitly and use
  scoped persistence.
- Global context is limited to appearance, locale, current identity, and global
  notification transport.

### 13.4 Data refresh

- Static project metadata refreshes on navigation or explicit invalidation.
- Active readiness and run views poll while visible and reduce or stop polling
  when hidden.
- Mutations invalidate only affected resources.
- Requests are cancellable when routes or selected resources change.
- A future event stream can replace polling without changing feature view-model
  contracts.

## 14. Error contract

New and migrated endpoints return stable machine-readable errors:

```json
{
  "code": "project_root_not_readable",
  "message": "The project root cannot be read.",
  "details": {},
  "recovery": ["choose_another_directory"],
  "request_id": "req_..."
}
```

The frontend localizes known `code` and `recovery` values. It preserves backend
detail and request ID for diagnostics. Unknown codes use a safe localized
fallback and never hide the original request ID.

Partial operations return per-item results rather than collapsing to one generic
failure. Target-root mutations and model invocations report whether execution
started before an error occurred.

## 15. Migration strategy

### Phase 0 — Contract inventory

- Freeze the current capability inventory and map every current tab, mutation,
  state, and test to a target route.
- Add contract coverage for legacy behavior that must survive.
- Define import, readiness, capability, route, error, and i18n contracts.

### Phase 1 — Foundation

- Introduce route shell, route builders, legacy redirects, design tokens, UI
  primitives, `react-router-dom`, backend SPA route fallback, locale runtime, and
  complete `zh-CN` and `en` catalogs.
- Keep current pages reachable behind legacy routes.

### Phase 2 — Projects and readiness

- Ship Projects, minimal import, Home, readiness persistence, new-project Init,
  and existing-project understanding.
- This phase delivers the original priority: any project loads once and reaches
  a usable Home.

### Phase 3 — Work

- Migrate workflow, board, workers, messages, actions, Dev Loop, and run evidence
  into the run-centered Work workspace.

### Phase 4 — Review

- Migrate Approvals and Workbench into Review while preserving execution
  identity, evidence snapshots, reviewer provenance, decision policy, and ledger
  writes.

### Phase 5 — Knowledge and settings

- Migrate artifacts, docs, context, memory, agents, integrations, delivery,
  smoke, and system diagnostics.

### Phase 6 — Legacy retirement

- Run the capability parity matrix, redirects, full verification, isolated live
  smoke, and visual acceptance.
- Remove legacy pages, dead CSS, obsolete themes, and compatibility props only
  after parity is demonstrated.

Each phase is independently releasable and keeps unrelated current routes
operational.

## 16. Verification strategy

### 16.1 Verification tier

The redesign is **control-plane / full verification tier** whenever a change
touches project registration, readiness persistence, target-root mutations,
capability gates, worker dispatch, approval decisions, evidence, reviewer
provenance, ledger writes, PR/CI binding, or security-sensitive server actions.

Pure UI primitive and static layout changes may use focused frontend verification
only when they cannot alter these contracts.

### 16.2 Required verification

- focused frontend unit tests for route parsing, capability guards, i18n, state
  projection, forms, and error recovery;
- frontend lint, unit test, and production build;
- backend focused tests for new endpoints and persistence;
- full backend suite and Ruff for control-plane changes;
- bilingual catalog completeness and English text-expansion checks;
- keyboard, focus, landmark, contrast, and reduced-motion checks;
- viewport coverage for desktop, tablet, and mobile;
- visual regression for Projects, Home, Work, Review, Knowledge, and all
  first-class states;
- isolated-workspace E2E for new project, existing project, ambiguous project,
  duplicate import, interrupted readiness, permission failure, partial success,
  restart recovery, and bilingual navigation;
- isolated live smoke evidence for live-path changes.

Mutating smoke tests never use an active real project workspace.

## 17. Acceptance criteria

The redesign is accepted when:

1. Selecting any existing readable directory creates or resolves exactly one
   project and opens Home without running Git init, creating docs, scanning full
   source bodies, or invoking a model.
2. Reloading Home or restarting GoodTeam preserves the project and readiness
   progress.
3. New and existing projects share Home but receive the correct readiness path.
4. Ambiguous projects load and can defer mode selection.
5. Workflow start is controlled by the server capability projection and explains
   unmet readiness.
6. The four primary project spaces cover every capability in the current
   navigation parity matrix.
7. Approvals and Workbench operate as one Review workspace without losing
   decision identity, provenance, evidence, rationale, or ledger behavior.
8. Project, run, review, and selected view can be restored from the URL.
9. All redesigned user-facing UI is complete in `zh-CN` and `en`; missing keys
   fail CI.
10. Desktop, tablet, mobile, keyboard, and reduced-motion paths are usable.
11. Required control-plane, frontend, accessibility, visual, E2E, and isolated
   live-smoke verification passes for the changed contract areas.
12. Legacy pages and themes are removed only after the parity matrix is complete.

## 18. Risks and mitigations

- **Scope size:** deliver route-by-route phases with explicit parity gates.
- **Control-plane regression:** keep server policy authoritative and run full-tier
  verification for sensitive contracts.
- **Parallel old/new state drift:** use shared server view models and capability
  projections; do not reimplement policy independently in each UI.
- **Context cost or unexpected writes:** minimal import is read-only; paid or
  target-root-writing steps require explicit confirmation and impact preview.
- **Bilingual drift:** central semantic catalogs, automated completeness checks,
  and no user-visible hard-coded strings.
- **Responsive degradation in dense workspaces:** use the same list, content, and
  inspector information order at every breakpoint, changing navigation rather
  than deleting evidence.
- **Migration never finishes:** each phase defines legacy routes to retire and a
  parity checklist; Phase 6 blocks on measurable completion rather than visual
  preference.
