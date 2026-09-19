# Product UI capability parity

This matrix records implementation coverage for retiring the module-oriented
dashboard. **Migrated is not a claim of current real-model delivery acceptance.**
The Verification column names relevant coverage, not a declaration that every
case ran successfully at this revision. Require exact-head CI and the applicable
isolated evidence described in [the M0 acceptance protocol](m0-product-acceptance.md).
The backend remains authoritative for readiness, workflow state, approval policy,
evidence, execution identity, and mutations.

| Legacy capability | Authoritative API | Product route / component | Boundary | Verification | Status |
| --- | --- | --- | --- | --- | --- |
| Projects overview and load | `GET /api/projects`, `POST /api/projects/import` | `/projects`, `ProjectsPage` | Import is GoodTeam-owned and does not mutate the selected target | Projects unit + isolated import E2E | Migrated |
| Project readiness and workflow entry | `/api/projects/:key/home`, readiness actions, workflow bridge | `/projects/:key/work`, Task Assistant | Task Assistant drives the server-owned readiness engine and workflow gate | Readiness backend + Work frontend + isolated E2E | Migrated; legacy readiness page removed |
| Workflow stages and current/next node | `GET /api/state`, `GET /api/workflow/graph` | `/projects/:key/work`, `WorkPage` | State and graph are read-only projections | Work route/unit + deep-link E2E | Migrated |
| Persistent current-task context | Existing `team_workspace.v1` projection | Work / `CurrentWorkContext` | Project/task/state are visible without hover; next action only locates existing controls; stale data is unknown | Current-context unit + `current-work-context.spec.js` | Implemented; exact-head evidence required |
| Run identity and history | `GET /api/projects/:key/runs`, `GET /api/projects/:key/runs/:runId` | `/projects/:key/work/runs/:runId`, `WorkPage` | URL run ID must match a server-known run; history is read-only; late/foreign responses cannot supply current context | Projection/backend + history unit and E2E | Migrated |
| Plan and Agile board | `GET /api/state` (`agile_board`, workflow steps) | Work / Plan | No client-side task-state inference | Work projection tests | Migrated |
| Worker status, retry, and dispatch | `GET /api/state`, `POST /api/retry`, `POST /api/dispatch-track` | Work / Execution | Same-origin and project-root dispatch safeguards stay server-owned | Focused backend + isolated mutation E2E | Migrated |
| Messages and debate | `GET /api/messages`, `POST /api/debate` | Work / Messages; Review detail | Run and gate identity supplied to server | Message/debate focused tests | Migrated |
| Dev loop, PR, CI, evidence, server actions | `GET /api/devloop`, server action APIs | Work / Delivery | Server action allowlist and confirmation remain authoritative | Dev-loop/server-action tests + isolated E2E | Migrated as contextual delivery projection |
| Smoke | `GET /api/state`, `POST /api/smoke-run` | Work / Delivery | Mutating smoke only in isolated workspace; dry-run defaults | Smoke focused + isolated E2E | Migrated |
| Review queue and workbench | `GET /api/state`, artifact/open-question/reviewer endpoints | `/projects/:key/review`, `ReviewPage` | Actionability and policy come from server review projection | Review focused/full backend + E2E | Migrated |
| Artifact content, refinement, questions, diff | artifact and refinement APIs | `/projects/:key/review/:reviewId`, `ReviewPage` | Artifact key/version and active gate are server-bound | Artifact/refinement tests + E2E | Migrated |
| Approve / reject / request changes | `POST /api/approve`; `POST /api/intent-commit/start`, `POST /api/intent-commit/discuss`, `POST /api/intent-commit/keep-discussing`, `POST /api/intent-commit/confirm` | Review and Work | Exact direct decisions use existing approval policy; semantic changes are drafts until separately confirmed | Approval/Intent Commit tests + business-loop E2E | Migrated |
| Composer-to-change handoff | Existing `/api/intent-commit/start` with exact target and `initial_text` | Work / `ConversationComposer` | Preserve original text; only exact acknowledged draft may clear it; no automatic interpretation, confirmation or hot Worker change | Handoff unit + `conversation-change-handoff.spec.js` | Implemented; exact-head evidence required |
| Repository understanding and source index | readiness artifact APIs | `/projects/:key/knowledge`, `KnowledgePage` | Generated understanding is labeled and provenance retained | Knowledge route/unit + E2E | Migrated |
| Artifacts and docs | `GET /api/artifact`, `GET /api/state` | Knowledge / Artifacts | Paths are server-resolved; raw source is never fabricated | Artifact focused + E2E | Migrated |
| Project and cross-project memory | project memory and `/api/memory/cross` APIs | Knowledge / Memory with URL-persisted role, event inspector, query, and scope | Retrieval scope and role stay explicit | Memory backend + E2E | Migrated |
| Cross-project human attention | Projects/state projections | Projects and per-project Work; no dedicated Inbox | `/inbox` redirects to `/projects`; richer cross-project prioritization remains #397 | Router redirect and Projects filtering tests | Legacy Inbox retired; expanded attention not claimed complete |
| Immutable activity | State action log projection | Work run views (Actions/Delivery) for the current run | `/activity` redirects to `/projects`; events stay read-only | Router redirect test | Retired; redirect preserved |
| Global appearance, locale | Product chrome (`ProductLayout`) | Language and light/dark controls in the product header | Preferences remain local to the device | Product layout tests | Migrated |
| Team/Worker configuration | `/api/projects/:key/team/commands`, state/agent APIs; legacy `POST /api/project-config` bridge | `/projects/:key/team`, `TeamPage` | One project-scoped control-plane command/event path; Role membership and Worker lifecycle/runtime are distinct; shared Team ownership remains #409 | Team control-plane, runtime and UI tests | Implemented; legacy settings redirect to Team |
| Legacy bookmarks | product router + SPA fallback | redirects to product Work/Review/Knowledge | No normal navigation targets `/legacy` or project Home | Router/backend + E2E assertion | Migrated |

The unmounted legacy application, module-dashboard components and orphaned
hooks/utilities were removed; the directory picker and shared primitives remain
because product routes still use them. Historical Tier 4 migration runs demonstrate
their recorded scope only. A router test, fake-Worker business loop or old live smoke
does not prove current provider compatibility, independent review/fix convergence,
all adapters, production deployment or the real-task product gate in #411.
