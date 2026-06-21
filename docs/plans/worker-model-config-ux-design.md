# Design - Worker model configuration UX at init

> Stage: architect. Confirmed requirement: project init should let the user pick
> both the **agent backend** and **model** for a few core worker buckets, using
> defaults and live local CLI discovery instead of free-text `role=model` input.

## 1. Actual Need

The current New Project modal still exposes worker model configuration as a
free-text `role=model` textarea. That is too error-prone at the moment a user is
creating or adopting a project:

- The user has to know the exact role names.
- The user has to know which local agent CLIs are installed.
- The user has to know which model strings are valid for that agent.
- The user has to mentally distinguish agent backend routing from model routing.

The desired UX is: **at init, show a small number of functional buckets, each
with a backend dropdown and a model dropdown, prefilled with sensible defaults**.
Users can leave defaults alone or override the buckets they care about.

## 2. Ground Rules

- Backend routing and model routing stay separate in config.
  - Backend choices write to `workers:` role-to-backend overrides.
  - Model choices write to `role_model_overrides:` role-to-`backend:model`
    overrides.
- Defaults should be visible but should not dirty project config.
  - Initial state is `Inherit` for backend and model.
  - The UI shows the resolved default backend/model beside or inside the control.
  - If the user does not change a bucket, no explicit override is written.
- Advanced per-role configuration remains post-create in the existing
  Workers/Agents & Memory panel.
- Init should not expose all roles. It should expose a few buckets.

## 3. Init Buckets

Show these three buckets in New Project:

- `product` - Product + Architecture
- `implementation` - Implementation
- `quality` - Quality + Release

Do not show `ops` at init. It inherits profile defaults and remains configurable
in Advanced.

The existing `WORKER_ROUTING_BUCKETS` mapping is the source of roles:

- `product` expands to `product_requirement`, `architect`, `review`, `planner`
- `implementation` expands to `ui`, `frontend`, `backend`, `implementation`
- `quality` expands to `qa`, `code_review`, `release`

## 4. Default Semantics

Each bucket row has two controls:

1. Backend select
   - Default option: `Inherit`
   - Display text should include the resolved backend, for example
     `Inherit (codex)`
   - Explicit options come from `/api/agents`, filtered to `available == true`

2. Model select
   - Default option: `Inherit`
   - Display text should include the resolved model for the bucket, for example
     `Inherit (gpt-5.4)`
   - If backend is explicit, the model default is that backend's `model_default`
   - If backend is inherited, the model default is the current profile's resolved
     role model for that bucket
   - Options come from the selected/effective backend's `models`
   - Include `Custom...` for manual model entry

No user edits means no `workers:` block and no `role_model_overrides:` block are
created from the init bucket controls.

## 5. Write Semantics

When a bucket has an explicit backend:

- Expand the bucket to its roles.
- Add each role to `workers:` with that backend.

When a bucket has an explicit model:

- Expand the bucket to its roles.
- Add each role to `role_model_overrides:`.
- Store the value as `backend:model` where `backend` is the effective backend
  for the bucket.

When a bucket is returned to `Inherit`:

- Remove explicit `workers:` entries for that bucket's roles.
- Remove explicit `role_model_overrides:` entries for that bucket's roles.

The same computed overrides must be used for both init actions:

- `Create Config` sends backend overrides as a map suitable for writing
  `workers:` and model overrides as a map suitable for writing
  `role_model_overrides:`.
- `Start Workflow` sends one-run model overrides in the existing list format,
  and must persist/create the same worker backend config before calling
  `/api/start`. `/api/start` does not get a second transient backend-routing
  contract in this increment.
- If a project config already exists, Start first updates that config's
  `workers:` and `role_model_overrides:` via `/api/project-config`; if no config
  exists, Start first creates one and then passes the returned `project_config`
  path to `/api/start`.

## 6. Agent Discovery

Use `/api/agents` as the source of selectable backends and models.

Required behavior:

- Only `available == true` backends appear as selectable init backends.
- `ready == false` backends may still appear if installed, but must show a
  readiness/auth warning.
- The UI should gracefully handle loading and discovery failure by falling back
  to the static `FALLBACK_AGENT_MODES` catalog where possible.
- Update stale frontend fallback Codex values from `gpt-5.3-codex` to current
  `gpt-5.4` defaults.

## 7. Opencode Backend

Register `opencode` as a real backend before relying on it in the init UI.

Verified locally:

- Binary: `opencode`
- Non-interactive command shape: `opencode run --dir <project-root>
  --dangerously-skip-permissions -m <provider/model> <message>`
- Model listing command: `opencode models`
- Model strings use provider/model format, for example
  `opencode-go/deepseek-v4-pro`
- GoodTeam's existing `prompt_via="arg"` helper only injects after `-p` or
  `--prompt`; `opencode run` takes the prompt as a positional message. The
  backend work must add an explicit positional-prompt mode or equivalent marker
  before registering opencode.

Backend work should add:

- `DEFAULT_OPENCODE_PATH`
- `DEFAULT_OPENCODE_MODEL` / `GOODTEAM_OPENCODE_MODEL`
- `OPENCODE_BACKEND` as a `CliBackendSpec`
- `_query_opencode_models`
- `CLI_BACKENDS["opencode"]`
- discovery/doctor tests

## 8. API Routing Choice

Use project config as the single source of backend routing.

Chosen path: **Create or update config before start**. The New Project modal
already has config creation semantics; keeping backend routing in project config
avoids introducing a transient backend override shape that only exists for one
run.

`/api/project-config` should distinguish operations explicitly:

- create config: create-form fields such as `project_name`, `project_key`,
  `project_root`, plus optional `workers` and `role_model_overrides`.
- update worker settings: `action: "update_worker_backends"` with `project` or
  `project_key`, `worker_backend_mode`, `workers`, and `role_models` /
  `role_model_overrides`.

This avoids routing a create request with `workers` into the update branch.

## 9. Advanced Fallback

The basic init surface should no longer force users to type `role=model`, but
the Advanced drawer may keep a free-text role/model fallback for uncommon cases
and backward compatibility. The bucket UI is the primary path; the textarea is
an escape hatch, not the default workflow.

## 10. Acceptance

- New Project modal no longer asks users to type bucket/role model overrides for
  basic init.
- The three init buckets each show backend + model controls.
- Defaults are visible and usable without creating explicit overrides.
- Choosing backend/model for a bucket writes the correct expanded `workers:` and
  `role_model_overrides:` entries.
- Existing Advanced per-role backend/model picker continues to work.
- `/api/agents` includes opencode when installed.
- Frontend fallback model defaults are consistent with backend defaults.
- Tests cover config writes, start/create payload conversion, opencode
  discovery, and frontend contract/build/lint.
