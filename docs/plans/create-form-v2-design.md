# Create-project form v2: mode-aware paths + delivery presets & Custom (design)

Status: DESIGN → dispatch · Owner: architect (Claude) · Date: 2026-06-05
Trigger: founder using the product. Two asks:
1. New project should need only ONE "Project Root" path; only adopting an existing project needs the
   three repo paths (docs/backend/frontend).
2. Delivery Mode is too narrow (3 hardcoded Web-product modes) → presets + a Custom (user-defined)
   option (isomorphic to the model picker's catalog + Custom…).

## Findings (read-only)
- `create_local_project_config` (web.py:734) takes docs/backend/frontend + delivery_mode, writes
  `repos:{docs,backend,frontend}`; `delivery_mode` is gated by `normalize_delivery_mode`.
- `normalize_delivery_mode` is DEFINED TWICE (graph.py:1226 + web.py:1993) — keep both in sync.
- `DELIVERY_MODE_STANDARDS` (graph.py:25) maps a mode → a list of standards text injected into role
  prompts (graph.py:223/311/417). `DELIVERY_MODES` tuple lives in projects.py:13.
- Worker/memory root: `resolve_project_runtime_root` uses `repos.values()` (ANY repo name works), so a
  single-root project written as `repos:{root:<path>}` resolves fine; `collect_project_context` keys on
  docs/backend/frontend and simply skips when absent (fine for a brand-new project with no source yet).
- Frontend already has `startMode` = `'existing_project' | 'new'` (toggle buttons "Adopt Existing
  Project" / "Start New Project"); the create form posts docs_path/backend_path/frontend_path +
  delivery_mode to `/api/project-config`.

## Track A — BE (codex): single-root create + delivery presets/Custom
Files: `src/goodteam_os/web.py`, `src/goodteam_os/graph.py`, `projects.py`, `projects/example.yaml`,
`README.md`, `tests/`.
1. **Single-root create.** `create_local_project_config` accepts a new optional `project_root`. When
   present (new-project path), write `repos:{root: <project_root>}` instead of docs/backend/frontend
   (validate via `_validate_directory_path`). Keep docs/backend/frontend support for the adopt path.
   At least one of {project_root, any repo} required (current "no repos" error preserved).
2. **Delivery presets.** Extend `DELIVERY_MODES` (projects.py) + `DELIVERY_MODE_STANDARDS` (graph.py)
   with these presets (keep the existing 3):
   - `static_site` — Static site / landing page
   - `cli_tool` — CLI tool
   - `library` — Library / SDK
   - `data_pipeline` — Data pipeline / ETL
   - `mobile_app` — Mobile app
   - `feature_increment` — Incremental feature on an existing system
   Each gets a 2–3 line standards block in the same spirit as the existing entries (runnable path,
   tests, README/docs, the domain's must-haves — e.g. data_pipeline: idempotent stages, schema/
   contract, sample-data run, backfill/repro; library: public API + semver + usage docs + tests, no
   app assumptions; cli_tool: arg/flag contract, exit codes, help text, deterministic tests; etc.).
3. **Custom mode.** Add `custom` as a delivery_mode value. `create_local_project_config` accepts
   `delivery_standards` (multiline text); when `delivery_mode == custom`, write the lines to a config
   key `delivery_standards:` (list). Injection: in graph.py, when mode is `custom`, inject the
   project's `delivery_standards` from the target config; otherwise look up `DELIVERY_MODE_STANDARDS`.
   `normalize_delivery_mode` (BOTH copies) must accept all new presets + `custom` (custom falls back to
   init_project only if no standards present — otherwise honor custom). Guard the
   `DELIVERY_MODE_STANDARDS[mode]` lookups so `custom` doesn't KeyError.
4. Update `projects/example.yaml` (show single-root + delivery_standards), README, tests
   (single-root config render + each preset normalizes + custom standards round-trip + injection picks
   custom standards).

## Track B — FE (codex): mode-aware fields + delivery picker
File: `frontend/src/App.jsx` only.
1. **Mode-aware paths.** When `startMode === 'new'`: show ONE "Project Root" input + Browse… (reuse the
   directory picker, target e.g. `'root'`). When `startMode === 'existing_project'`: show the three
   repo inputs + Browse (current). Submit: new → `project_root`; existing → docs/backend/frontend.
2. **Delivery picker.** Replace the 3-option select with the full preset list (labels above) + a
   `Custom…` entry; when Custom is chosen, show a textarea for the standards (one per line), submitted
   as `delivery_standards`. Keep it styled like the existing modal controls.
3. Keep manual paste working; lint 0 warnings + build clean.

## Contract pin (A ↔ B — the join key)
`POST /api/project-config` body gains: `project_root` (string, new-project), `delivery_mode` (one of the
presets or `custom`), `delivery_standards` (string[] or multiline string, only when custom). New →
`repos:{root}`; existing → `repos:{docs,backend,frontend}`. File-disjoint (App.jsx vs BE) → dispatch
A+B in parallel; BE lands first for live wiring.

## Verification
- BE: pytest + ruff green; live `curl` create with project_root → config has `repos:{root}`; create with
  delivery_mode=custom + standards → config has `delivery_standards`; start a run → injected standards
  match.
- FE: lint/build; **Playwright live smoke**: toggle New → single Project Root + Browse; toggle Adopt →
  three inputs; pick a preset and Custom… → textarea appears; create succeeds.
- Triple-check each → human land gate. BE first, then FE rebased.
