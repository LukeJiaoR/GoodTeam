# Product UI capability parity

This matrix is the implementation gate for retiring the module-oriented
dashboard. The backend remains authoritative for readiness, workflow state,
approval policy, evidence, execution identity, and mutations.

| Legacy capability | Authoritative API | Product route / component | Boundary | Verification | Status |
| --- | --- | --- | --- | --- | --- |
| Projects overview and load | `GET /api/projects`, `POST /api/projects/import` | `/projects`, `ProjectsPage` | Import is GoodTeam-owned and does not mutate the selected target | Projects unit + isolated import E2E | Migrated |
| Project readiness and workflow entry | `/api/projects/:key/home`, readiness actions, workflow bridge | `/projects/:key/home`, `ProjectHomePage` | Server capability projection controls entry | Readiness backend/frontend + isolated E2E | Migrated; bridge target updated in this migration |
| Workflow stages and current/next node | `GET /api/state`, `GET /api/workflow/graph` | `/projects/:key/work`, `WorkPage` | State and graph are read-only projections | Work route/unit + deep-link E2E | Migrated |
| Run identity and history | `GET /api/projects/:key/runs`, `GET /api/projects/:key/runs/:runId` | `/projects/:key/work/runs/:runId`, `WorkPage` | URL run ID must match a server-known run; history is read-only | Projection/backend + refresh/back-forward E2E | Migrated |
| Plan and Agile board | `GET /api/state` (`agile_board`, workflow steps) | Work / Plan | No client-side task-state inference | Work projection tests | Migrated |
| Worker status, retry, and dispatch | `GET /api/state`, `POST /api/retry`, `POST /api/dispatch-track` | Work / Execution | Same-origin and project-root dispatch safeguards stay server-owned | Focused backend + isolated mutation E2E | Migrated |
| Messages and debate | `GET /api/messages`, `POST /api/debate` | Work / Messages; Review detail | Run and gate identity supplied to server | Message/debate focused tests | Migrated |
| Dev loop, PR, CI, evidence, server actions | `GET /api/devloop`, server action APIs | Work / Delivery | Server action allowlist and confirmation remain authoritative | Dev-loop/server-action tests + isolated E2E | Migrated as contextual delivery projection |
| Smoke | `GET /api/state`, `POST /api/smoke-run` | Work / Delivery | Mutating smoke only in isolated workspace; dry-run defaults | Smoke focused + isolated E2E | Migrated |
| Review queue and workbench | `GET /api/state`, artifact/open-question/reviewer endpoints | `/projects/:key/review`, `ReviewPage` | Actionability and policy come from server review projection | Review focused/full backend + E2E | Migrated |
| Artifact content, refinement, questions, diff | artifact and refinement APIs | `/projects/:key/review/:reviewId`, `ReviewPage` | Artifact key/version and active gate are server-bound | Artifact/refinement tests + E2E | Migrated |
| Approve / request changes / reject | `POST /api/approve` | Review detail decision panel | Existing evidence, provenance, ledger, and execution identity checks are unchanged | Full backend approval suite + E2E | Migrated |
| Repository understanding and source index | readiness artifact APIs | `/projects/:key/knowledge`, `KnowledgePage` | Generated understanding is labeled and provenance retained | Knowledge route/unit + E2E | Migrated |
| Artifacts and docs | `GET /api/artifact`, `GET /api/state` | Knowledge / Artifacts | Paths are server-resolved; raw source is never fabricated | Artifact focused + E2E | Migrated |
| Project and cross-project memory | project memory and `/api/memory/cross` APIs | Knowledge / Memory with URL-persisted role, event inspector, query, and scope | Retrieval scope and role stay explicit | Memory backend + E2E | Migrated |
| Cross-project human attention | Projects/home/state projections | `/inbox`, `InboxPage` | Links target concrete project/run/review/readiness resources | Inbox unit + deep-link E2E | Migrated |
| Immutable activity | State action log projection | `/activity`, `ActivityPage` | Events are read-only and filter-only in the client | Activity unit + filter E2E | Migrated |
| Global appearance, locale, health | Preferences and `GET /api/state` | `/settings`, `SettingsPage` | Preferences remain local; health is server-observed | Settings unit + bilingual/theme E2E | Migrated |
| Worker/model defaults and project configuration | `GET/POST /api/settings`, agent/project-config APIs | global Settings and `/projects/:key/settings/:section` | Workspace defaults affect only future imports; project mutations keep same-origin and server validation | Settings/config backend + isolated E2E | Migrated |
| Legacy bookmarks | product router + SPA fallback | redirects to product Home/Work/Review/Knowledge | No normal navigation targets `/legacy` | Router/backend + E2E assertion | Migrated |

The full Tier 4 run proved this matrix. The unmounted legacy application,
module-dashboard components, and their now-orphaned hooks/utilities were removed;
the directory picker and shared product primitives remain because product routes
still use them.
