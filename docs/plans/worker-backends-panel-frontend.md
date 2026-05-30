# Plan — Worker Backends config panel: FRONTEND track (for Gemini)

You are implementing the **frontend** half of a feature in the GoodTeam repo. A
separate agent (Codex) is building the Python backend/API in parallel against the
same contract below. **Stay in your lane**: only touch `frontend/`. Do **not**
edit Python under `src/goodteam_os/` or `tests/`.

## Workspace

- **Repo root:** `/Users/luke/WorkSpace/codeAi/GoodTeam`
- **Your working directory:** `/Users/luke/WorkSpace/codeAi/GoodTeam/frontend`
  (run `npm` commands from here).
- **You edit:** `frontend/src/App.jsx` (the whole UI lives here), and
  `frontend/src/App.css` / `frontend/src/index.css` for styles.
- **You must NOT edit:** anything outside `frontend/` — especially
  `src/goodteam_os/` (Codex's track) or `docs/plans/worker-backends-panel-backend.md`.

## Repo orientation (cold start)

- The dashboard is a React + Vite app in `frontend/`. The entire UI is in
  `frontend/src/App.jsx` (one large component file, ~2200 lines) with styles in
  `frontend/src/App.css` / `frontend/src/index.css`.
- It talks to a Python backend over `/api/*` with `fetch`. Look at the existing
  `fetchState(key)` function (it GETs `/api/state?project=<KEY>`) and existing
  POST calls (e.g. `/api/terminate`, `/api/project-delete`, `/api/retry`) for the
  established patterns to copy: how it builds URLs, sends JSON, handles errors,
  and re-fetches state afterward.
- The project detail view is rendered when a `project` key is selected; it shows
  tabs/sections (overview, roles, etc.). You will add a "Worker Backends" section
  (a new tab or a card in the project detail — match whatever pattern the file
  already uses for tabs/cards).

### Setup & how to run/test

```bash
cd frontend
npm install
npm run dev          # opens Vite dev server; it proxies /api/* to the backend
npm run build        # production build into frontend/dist/ (must succeed)
npm run lint         # eslint must pass
```
During dev, run the backend separately so `/api/*` works:
`PYTHONPATH=src python -m goodteam_os.cli serve --workspace ./run` (from repo
root, in the project's venv). The Vite dev proxy forwards `/api/*` to it.

## The feature

In the project detail view, add a **"Worker Backends"** panel that lets the user
see and change which worker backend each role uses, and see each backend's
readiness (is the CLI installed / are credentials present). This is the UI for
GoodTeam's per-role worker routing.

## Shared API contract (both tracks build to this — do not change unilaterally)

**Read** — `GET /api/state?project=<KEY>` (already returned by `fetchState`)
gives a `start_context` object containing:
- `start_context.worker_backend.mode` — current global default backend (string).
- `start_context.project_report.role_worker_backends` — `{ role: backend }`,
  only roles that currently have an explicit override.
- `start_context.project_report.worker_backends_preflight` — `{ backend: report }`.
  Each `report` has:
  - `ready` (bool) — is this backend runnable right now,
  - `hint` (string) — human-readable, actionable (e.g. "No ANTHROPIC_API_KEY …
    run `claude login`", or a warning that a stale `CODEX_API_KEY` shadows
    `~/.codex/auth.json`),
  - `kind` (`"builtin"` | `"cli"` | `"unknown"`), and for CLI backends
    `available` (bool), `command` (e.g. `"claude"`), `auth_present` (bool).
- `start_context.worker_backend_options` — **(being added by the backend track)**
  ordered list of selectable backends, e.g.
  `["fake", "codex", "anthropic", "noop", "human"]`. Use this to populate
  dropdowns. If absent (backend not merged yet), fall back to that literal list.
- `start_context.worker_roles` — **(being added by the backend track)** roles to
  show rows for, e.g. `["ui", "implementation", "qa"]`. Fall back to that literal
  list if absent.

**Write** — `POST /api/project-config` with JSON:
```json
{
  "project": "MYAPP",
  "worker_backend_mode": "codex",
  "workers": { "implementation": "anthropic", "qa": "codex" }
}
```
- Send the chosen global default in `worker_backend_mode`.
- `workers` must contain ONLY explicit per-role overrides. If the user sets a
  role back to "Use default", **omit it** from `workers` (don't send empty
  string).
- Success response: `{ "ok": true, "role_worker_backends": {...},
  "worker_backends_preflight": {...} }`. On failure: HTTP 400 with
  `{ "error": "<message>" }`.

## Tasks

1. **Read state.** From the already-fetched project state, pull
   `start_context.worker_backend.mode`, `…project_report.role_worker_backends`,
   `…project_report.worker_backends_preflight`, `…worker_backend_options`,
   `…worker_roles` (with the literal fallbacks noted above).

2. **Render the panel.** Add a "Worker Backends" section to the project detail
   (new tab or card, consistent with existing ones):
   - A **Global default** row: a `<select>` bound to `worker_backend.mode`,
     options = `worker_backend_options`.
   - One row **per role** in `worker_roles`: a `<select>` whose options are
     `["Use default", ...worker_backend_options]`. Preselect the role's current
     override from `role_worker_backends`, or "Use default" if none.
   - A **readiness** display: for each distinct backend currently in use
     (global default + any overrides), show a badge from
     `worker_backends_preflight[backend]` — green when `ready` is true, amber/red
     otherwise — and show its `hint` text. Reuse existing badge/status styles in
     the app (look for how approval/status badges are styled) rather than
     inventing new ones.

3. **Save.** Add a Save button that POSTs to `/api/project-config` with
   `{ project, worker_backend_mode, workers }` built from the form (omit
   "Use default" roles), then re-run the existing state fetch so badges/hints
   refresh from the server (the server recomputes readiness). Show a saving
   state and surface any `{ error }` to the user (match how other actions show
   errors). Use the same project key the rest of the view uses.

4. **States.** Handle: loading, save-in-flight (disable Save), error message,
   and the "no project config / fields absent" case gracefully (fall back to
   literal options/roles; if there's no active project config, the panel can show
   a short empty/explanatory note).

## Acceptance
- `npm run build` and `npm run lint` succeed.
- With the backend running, selecting a different backend for `implementation`
  and clicking Save persists it (a reload shows the new selection) and the
  readiness badges/hints update from the server response.
- Setting a role back to "Use default" removes its override on save.
- No changes outside `frontend/`.

## Notes
- The server is the source of truth for readiness — never compute "is this CLI
  installed" in the browser; just render `worker_backends_preflight`.
- Backend values and roles come from the server (`worker_backend_options`,
  `worker_roles`); keep the literal fallbacks only as a safety net so the panel
  still works if you integrate before the backend field lands.
