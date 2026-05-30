# Patch — worker-backends Save sends unmanaged roles → 400

> Patch handoff (template: see `patch-project-config-routing.md`).
> Found by the QA pass on the Worker Backends panel.

## Meta
- **Owning role(s):** `frontend` (required fix) + `backend` (root-cause cleanup).
- **Tracks affected:** `frontend/src/App.jsx` (required); `src/goodteam_os/projects.py`
  + a doc (cleanup).
- **Severity:** HIGH — **live bug, Save is broken** for any project initialized
  with `goodteam-os init`. Must fix before the feature lands.
- **Found during:** QA (`worker-backends-panel-qa.md`), reported by Gemini.

## Symptom
Saving in the Worker Backends panel fails:
```
POST /api/project-config → 400
{ "error": "Unknown worker role: frontend. Use one of: ui, implementation, qa" }
```

## Root cause (two parts)
1. **Frontend sends unmanaged roles.** `handleSaveWorkerBackends`
   (`frontend/src/App.jsx`, ~line 583) builds the `workers` payload by iterating
   `panelRoleOverrides`. That object is seeded from
   `start_context.project_report.role_worker_backends`, which includes **every**
   role present in the config's `workers:` map — including roles the panel does
   not manage. So an extra role like `frontend` gets sent, and the backend
   (`WORKER_ROLES = ["ui","implementation","qa"]`) rejects it → 400.
2. **Backend seeds a bogus role.** `build_project_config`
   (`src/goodteam_os/projects.py`, ~lines 56–59) seeds the new config's
   `workers:` with `implementation / frontend / qa` — it includes the
   **non-managed** `frontend` and **omits** the managed `ui`. This is where the
   stray `frontend` role comes from.

The frontend fix is the durable one: a config may legitimately contain arbitrary
roles (hand-edited, multi-service), and the panel must only ever submit the roles
it manages. The backend fix removes the specific stray role at its source.

## Fix

### Frontend track (REQUIRED) — `frontend/src/App.jsx`
In `handleSaveWorkerBackends`, build the payload from the **managed roles**, not
from `panelRoleOverrides`. Note `DEFAULT_WORKER_ROLES` is render-scoped (~line
1155) and is **not** in this handler's scope, so read the managed roles from
state (`data`) with a literal fallback:

```js
// Only submit overrides for roles the panel manages; the config can contain
// extra roles (e.g. `frontend` seeded by `goodteam-os init`) the backend rejects.
const managedRoles = data?.start_context?.worker_roles || ["ui", "implementation", "qa"];
const workers = {};
managedRoles.forEach((role) => {
  const backend = panelRoleOverrides[role];
  if (backend && backend !== 'default' && backend !== '') {
    workers[role] = backend;
  }
});
```
(Optional tidy-up: hoist `DEFAULT_WORKER_ROLES` / `DEFAULT_BACKEND_OPTIONS` to
module scope so the handler and the render share one source instead of a literal.)

### Backend track (cleanup) — `src/goodteam_os/projects.py`
In `build_project_config`, seed the `workers:` block with the actually managed /
dispatched roles `ui / implementation / qa` (drop `frontend`, add `ui`):
```
workers:
  ui: {worker_mode}
  implementation: {worker_mode}
  qa: {worker_mode}
```
No test currently pins these seed roles (`tests/test_init.py` only asserts
`worker_backend.mode`), but add a small assertion that the seeded roles are
exactly the managed set so this can't drift again.

### Docs — `docs/plans/worker-backends-panel-qa.md`
Remove/replace the "Known expected quirk" note: it incorrectly predicted the
seeded `frontend` entry would silently disappear. After this patch, new configs
have no `frontend` role and Save submits only managed roles, so there is no quirk
— Save should simply succeed.

## Acceptance
- Re-run the QA plan (`worker-backends-panel-qa.md`): Save succeeds (200), the
  config persists `ui/implementation/qa` overrides, readiness refreshes, and
  "Use default" removes an override.
- `cd frontend && npm run build && npm run lint` pass.
- `PYTHONPATH=src python -m pytest tests -q` green (incl. any new init assertion);
  `ruff check src/goodteam_os/ tests/` clean.

## Out of scope
- Don't change `WORKER_ROLES` or the backend validation (rejecting unknown roles
  is correct behavior; the client simply must not send them).
- Don't redesign the panel's role set.
