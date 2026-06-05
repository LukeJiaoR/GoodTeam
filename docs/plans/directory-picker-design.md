# Directory picker for Add/Create Project (design)

Status: **DESIGN / founder-gate** · Owner: architect (Claude) · Date: 2026-06-05
Trigger: founder hit the gap while using the product — the Add/Create Project modal only has
free-text "Absolute path to … repo" inputs (App.jsx:1887/1897/1907); no way to browse + pick a
local folder.

## Findings (read-only investigation)
- Add Project modal posts `docs_path`/`backend_path`/`frontend_path` to `/api/project-config`
  (App.jsx:1159 → backend `build_project_config`); pasting absolute paths already works.
- Backend has a half-baked `discover_project_directories()` (web.py:625) but it **hardcodes a
  personal path** `Path("/Users/luke/WorkSpace/codeAi")`, only scans 2 levels, and is a fixed
  candidate list, not an interactive browser. **This is a bug**: a personal path baked into
  shipped code (violates the earlier "no personal paths" cleanup), breaks on any other machine.
- `_validate_directory_path()` (web.py) already validates a typed path; reuse it.
- No filesystem-listing API exists; browsers can't return server-side absolute paths natively,
  so the correct pattern for this local-first, same-machine tool is a backend read-only
  list-directory API the UI navigates.

## Goal
An interactive directory picker in the Add/Create Project modal: browse server-side folders,
descend/ascend, select a folder → its absolute path fills the field. Manual paste stays. And
**kill the hardcoded personal path.**

## Track P1 — BE: read-only list-directory API (codex)
- New endpoint `GET /api/fs/list?path=<abs-path>` returning JSON:
  ```
  { "path": "<resolved abs>", "parent": "<abs or null at ceiling>",
    "root": "<allowed-root abs>",
    "entries": [ {"name": "...", "path": "<abs>", "is_git_repo": true|false}, ... ] }
  ```
  - Directories only (we pick folders); each entry flags `is_git_repo` (has `.git`) as a hint.
  - `path` omitted/empty → default **start** path = user home (`Path.home()`). This is only a
    convenience starting point, NOT a ceiling — navigation up to `/` and anywhere is allowed.
  - `parent` is `null` only at the filesystem root (`/`).
  - Sorted case-insensitive; hide the heavy noise set
    (`.git/.github/.venv/__pycache__/node_modules/.next/dist/build`) but **show other
    dotfolders** so full navigation isn't blocked.
- **Decision: browse scope = any absolute path, traversal-safe only (no root ceiling).**
- **Safety (hard requirements):**
  - GET only, read-only. No file contents, no writes.
  - `realpath`/resolve the requested path to canonicalize it (return the clean absolute path).
    No `..` ceiling rejection needed (no root), but resolve so the returned path is canonical.
  - Single-level listing only (no recursion → no symlink-loop risk); resolve symlinked dir
    entries but don't descend automatically.
  - 404 for missing/not-a-dir, 400 for a non-absolute/garbage `path`; permission errors
    (unreadable dir) → a clean error entry, never a raw traceback to the client.
  - Cap entries (e.g. first N) to avoid huge dirs hanging the UI.
- **Remove the hardcoded path:** `discover_project_directories` (and the new API) must derive
  their base from the browse-root logic below, not `/Users/luke/WorkSpace/codeAi`. Keep existing
  callers working (default base = browse root).
- Lane: `src/goodteam_os/web.py` + `tests/`. Tests: lists a tmp tree, dirs-only, ignored-name
  filtering, `..`/out-of-root → 403, missing → 404, `is_git_repo` flag, default-root behavior.

## Track P2 — FE: directory picker in the modal (agy)
- Each repo path field (docs/backend/frontend) gets a **"Browse…"** button opening a directory
  navigator panel: shows current `path` (breadcrumb), an "Up" control (to `parent`, disabled at
  root), the list of subfolders (click name to descend; git-repo entries badged), and a
  **"Select this folder"** action that fills that field with the current absolute path and closes.
- Manual text entry stays (don't remove paste). Read-only browsing; selection only fills input.
- Calls `GET /api/fs/list`; handle 403/404/empty gracefully (inline message, no crash).
- Lane: `frontend/src/App.jsx`. Lint 0 warnings + build clean.

## Contract pin (P1 ↔ P2 — the join key)
The `/api/fs/list` response schema above is the contract. FE joins on `entries[].path` for
descent and uses `path`/`parent`/`root` for breadcrumb + Up. `is_git_repo` is display-only.
File-disjoint (web.py vs App.jsx) → dispatch P1 + P2 in parallel; FE coded against the pinned
schema, BE lands first for the live wiring.

## Decision needed (founder gate)
Browse-root scope / security ceiling — see the question posed alongside this doc.

## Verification
- BE: `pytest -q` + `ruff` green; live `curl /api/fs/list` walks a couple levels + 403 on escape.
- FE: lint/build clean; live smoke (agy, dev-workspace): open Add Project → Browse → descend →
  Select → field fills with the right absolute path → create project succeeds.
- Each track through the triple-check → land (human gate).
