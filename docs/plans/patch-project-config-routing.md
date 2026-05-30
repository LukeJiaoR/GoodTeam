# Patch — make `/api/project-config` routing explicit

> **This doubles as the reusable template for a "patch handoff".** A patch is a
> small, targeted fix found during review/QA. Keep the shape: *Meta → Symptom
> (with file:line) → Fix (one recommended option) → Acceptance → Out of scope.*

## Meta
- **Owning role(s):** `code_review` (found the smell) → `backend` (+ `frontend`
  if you take Option A).
- **Tracks affected:** backend (`src/goodteam_os/web.py`); frontend only if
  Option A.
- **Severity:** low — **not a live bug**, a maintainability/robustness smell.
  Fix opportunistically; not a release blocker.
- **Found during:** review of the Worker Backends panel feature.

## Symptom

`src/goodteam_os/web.py`, POST dispatch (~line 4929):

```python
elif normalized_path == "/open-path":
    ...
elif "project" in request or "workers" in request:        # ← worker-backends update
    result = update_project_worker_backend_settings(workspace=workspace, request=request)
else:                                                      # ← create-config (silent fallback)
    result = create_local_project_config(...)
```

The single path `/api/project-config` serves **two different operations**
(update per-role worker backends **vs** create a brand-new project config). They
are disambiguated **implicitly by which keys the body contains**, and the create
operation is the **silent `else` fallback** — any `/project-config` body that
isn't recognized as an "update" is treated as a "create".

Today the two real payloads don't collide (the update sends `project` + `workers`;
the create sends `project_key` + `project_name` and never sends `workers`). But:
- intent is implicit — a reader can't tell from the path what runs;
- it's fragile — if a future create payload ever gains a `project` or `workers`
  key, requests silently misroute into the wrong handler with no error;
- a malformed update (e.g. missing `workers`) silently falls through to *create*.

## Fix

### Option A — explicit `action` field (recommended; FE + BE)
Make the client state its intent instead of inferring it from body shape.

- **Backend** (`web.py`): inside an explicit `elif normalized_path ==
  "/project-config":` block, route on `request.get("action")`:
  ```python
  elif normalized_path == "/project-config":
      action = str(request.get("action", "")).strip()
      if action == "update_worker_backends":
          result = update_project_worker_backend_settings(workspace=workspace, request=request)
      elif action == "create_config":
          result = create_local_project_config(...)
      else:
          raise ValueError("project-config requires action: 'update_worker_backends' | 'create_config'")
  ```
- **Frontend** (`frontend/src/App.jsx`): add `action: "update_worker_backends"`
  to the body in `handleSaveWorkerBackends`, and `action: "create_config"` in
  `handleCreateConfig`.
- Update the two existing tests in `tests/test_api.py` and the QA doc
  (`docs/plans/worker-backends-panel-qa.md`) and any `curl` examples to include
  `action`.

### Option B — backend-only, low-churn (if you can't touch the frontend now)
Keep the body-shape discriminator but make it **path-explicit and intentional**,
removing the silent `else`-is-create fallthrough:
```python
elif normalized_path == "/project-config":
    # Worker-backends panel sends `workers`; create-config form does not.
    if "workers" in request or "project" in request:
        result = update_project_worker_backend_settings(workspace=workspace, request=request)
    else:
        result = create_local_project_config(...)
```
This is purely a clarity/robustness improvement (no behavior change, no FE/test
churn). Prefer Option A when you can coordinate both tracks.

## Acceptance
- `PYTHONPATH=src python -m pytest tests -q` green (update tests if Option A).
- `ruff check src/goodteam_os/` clean; if Option A, `cd frontend && npm run
  build && npm run lint` pass.
- An unrecognized/empty `/api/project-config` request returns a clear 400 (no
  silent misroute). Both real operations still work end-to-end.

## Out of scope
- Don't redesign the other POST routes.
- Don't rename `/api/project-config` (keeps tests/QA/FE stable).
