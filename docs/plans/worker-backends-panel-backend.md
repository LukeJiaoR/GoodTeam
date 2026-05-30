# Plan — Worker Backends config panel: BACKEND track (for Codex)

You are implementing the **backend** half of a feature in the GoodTeam repo. A
separate agent (Gemini) is doing the React frontend in parallel against the same
API contract below. **Stay in your lane**: only touch Python under
`src/goodteam_os/` and tests under `tests/`. Do **not** edit `frontend/`.

## Workspace

- **Repo root (run all commands from here):** `/Users/luke/WorkSpace/codeAi/GoodTeam`
- **You edit:** `src/goodteam_os/*.py` (primarily `web.py`, `projects.py`) and
  `tests/*.py`.
- **You must NOT edit:** `frontend/` (Gemini's track) or
  `docs/plans/worker-backends-panel-frontend.md`.
- All paths in this doc are relative to the repo root above.

## Repo orientation (cold start)

- GoodTeam is a local-first Python system. Backend lives in `src/goodteam_os/`.
- The dashboard HTTP server + all `/api/*` endpoints live in
  `src/goodteam_os/web.py` (a `BaseHTTPRequestHandler`; see `do_GET`/`do_POST`
  near the bottom, around the `/api/state` handler at the `if parsed.path ==
  "/api/state"` block, and the POST dispatch where `normalized_path` is matched
  against `/start`, `/approve`, `/terminate`, `/project-delete`, `/retry`,
  `/open-path`).
- Project configs are small YAML files at `projects/<key>.local.yaml` (gitignored).
  They are parsed by a **custom one-way parser** `_parse_simple_yaml` in
  `src/goodteam_os/models.py` (indentation-based subset; there is **no**
  general YAML serializer in the repo — see the key constraint below).
- `src/goodteam_os/projects.py` has `check_project_target(target)` which already
  returns `role_worker_backends` (role→backend) and `worker_backends_preflight`
  (backend→readiness report). It also has `load_project_target(path)`,
  `WORKER_MODES`, and `build_project_config(...)`.
- `src/goodteam_os/workers.py` has `worker_backend_doctor(mode)` and the backend
  registry `CLI_BACKENDS` (codex, anthropic/claude); builtin modes are
  `fake`, `noop`, `human` (`BUILTIN_WORKER_MODES`).
- `src/goodteam_os/graph.py` has `resolve_role_adapter(target, role)` and
  dispatches three roles: `ui`, `implementation`, `qa`.

### Setup & how to run/test

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
PYTHONPATH=src python -m pytest tests -q      # full suite must stay green
ruff check src/goodteam_os/ tests/            # touched files must be ruff-clean
```

## The feature

A dashboard panel where the user views and edits, per project, which worker
backend each role uses (global default + per-role overrides) and sees each
backend's readiness. Your job is the data + persistence; Gemini renders it.

## Shared API contract (both tracks build to this — do not change unilaterally)

**GET `/api/state?project=<KEY>`** (already exists) returns a `start_context`
object. It ALREADY includes everything needed to *read* this feature:
- `start_context.worker_backend.mode` — global default backend (string).
- `start_context.project_report.role_worker_backends` — `{role: backend}` for
  roles explicitly listed in the config's `workers:` map.
- `start_context.project_report.worker_backends_preflight` — `{backend: report}`
  where `report` has at least `{mode, kind, ready, hint}` plus `available`,
  `command`, `command_path`, `auth_env`, `auth_present` for CLI backends.

You must ADD two fields to `start_context` (see Task 1):
- `start_context.worker_backend_options` — ordered list of selectable backends,
  e.g. `["fake", "codex", "anthropic", "noop", "human"]`.
- `start_context.worker_roles` — roles the user can configure: `["ui",
  "implementation", "qa"]`.

**POST `/api/project-config`** (new — Task 2). Request JSON:
```json
{
  "project": "MYAPP",
  "worker_backend_mode": "codex",
  "workers": { "implementation": "anthropic", "qa": "codex" }
}
```
- `workers` contains ONLY explicit per-role overrides. A role omitted here means
  "use the global default" (remove any existing override for it).
- Backend values must be one of `worker_backend_options`; reject others with 400.
- On success, persist to the project's config file and return fresh readiness:
```json
{
  "ok": true,
  "role_worker_backends": { "implementation": "anthropic", "qa": "codex" },
  "worker_backends_preflight": { "anthropic": {...}, "codex": {...} }
}
```
- On any error return HTTP 400 with `{ "error": "<message>" }` (match the
  existing handlers' error style, e.g. the `/api/state` except branch).

## Tasks

### Task 1 — expose options + roles in `start_context`
In `web.py`, in `build_start_console_context(...)` (the function that builds the
dict returned as `start_context`; it currently sets keys like `project_report`,
`worker_backend`, `role_model_overrides`), add:
- `"worker_backend_options"`: a module-level constant list. Source it from the
  real sets so it can't drift: builtin modes + registered CLI backends. Suggested
  order: `["fake", "codex", "anthropic", "noop", "human"]`. Define a constant
  (e.g. `WORKER_BACKEND_OPTIONS`) in `web.py` or import the pieces from
  `projects.WORKER_MODES` / `workers.CLI_BACKENDS`.
- `"worker_roles"`: `["ui", "implementation", "qa"]` (the roles `graph.py`
  dispatches). A module constant is fine.

### Task 2 — POST `/api/project-config` endpoint
Add a branch in the POST dispatch in `web.py` (alongside `/start`, `/retry`,
etc.; note the dispatch strips the `/api` prefix, so match `"/project-config"`).
Steps:
1. Read+parse the JSON body (reuse the same body-reading pattern the other POST
   handlers use).
2. Resolve the project config path via the existing `_project_config_for_key(
   workspace, project_key)` helper. If missing, 400.
3. Validate `worker_backend_mode` and every value in `workers` against
   `WORKER_BACKEND_OPTIONS`. Invalid → 400.
4. Persist via a new helper `update_worker_backend_config(...)` (Task 3).
5. Re-run `check_project_target(load_project_target(config_path))` and return
   `{ "ok": True, "role_worker_backends": report["role_worker_backends"],
   "worker_backends_preflight": report["worker_backends_preflight"] }`.

### Task 3 — config persistence helper (the key constraint)
Add `update_worker_backend_config(config_path, *, mode, workers)` to
`projects.py` that updates ONLY the top-level `worker_backend.mode` and the
`workers:` block, **preserving every other line of the file verbatim**
(`name`, `repos`, `providers`, `delivery_contract`, comments, etc.).

⚠️ **There is no YAML serializer in this repo and you must not add a dependency
or naively re-serialize the whole config** (values contain colons, nested lists,
and comments that a naive dumper would corrupt). Instead do a **surgical text
edit** of the two top-level blocks:
- Replace the existing `worker_backend:` block (the `worker_backend:` line plus
  its indented child lines) with:
  ```
  worker_backend:
    mode: <mode>
  ```
- Replace the existing `workers:` block with a regenerated one listing only the
  provided overrides (skip if `workers` is empty — then omit the block).
- If a block doesn't exist yet, append it.
- A "block" = the header line at column 0 (`worker_backend:` / `workers:`) plus
  all following lines that are more-indented or blank, up to the next column-0
  key. Implement this by scanning lines and rewriting those ranges.

Make it idempotent and round-trippable: after writing, `load_project_target` /
`_parse_simple_yaml` must read back the new `worker_backend.mode` and `workers`
while all other parsed keys are unchanged.

### Task 4 — tests (TDD; write these first)
In `tests/test_projects.py` (or a new `tests/test_project_config_update.py`):
- `update_worker_backend_config` sets mode + workers and, when re-parsed,
  preserves an unrelated block (e.g. a `providers:` or `delivery_contract:`
  section and a `repos:` entry) byte-for-content.
- Omitting a role removes its override (role falls back to global default).
- Empty `workers` removes the `workers:` block entirely.
In `tests/` for the endpoint (mirror existing web/API tests, e.g. how
`tests/test_api.py` exercises handlers): POST `/api/project-config` updates a
temp project config and returns refreshed `worker_backends_preflight`; invalid
backend value returns 400.

## Acceptance
- `PYTHONPATH=src python -m pytest tests -q` green (your new tests included).
- `ruff check src/goodteam_os/ tests/` clean for files you touched.
- Manual: start the server (`PYTHONPATH=src python -m goodteam_os.cli serve
  --workspace ./run`), `GET /api/state?project=<KEY>` shows the two new fields;
  `POST /api/project-config` flips a role's backend and the change persists in
  `projects/<key>.local.yaml` and is reflected on the next GET.

## Out of scope / do not touch
- `frontend/` (Gemini owns it).
- The legacy f-string dashboard renderer in `web.py` (`render_dashboard` and its
  HTML) — the canonical UI is the React app; you only provide data via `/api/*`.
