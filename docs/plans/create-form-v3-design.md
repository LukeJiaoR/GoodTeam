# Create-form v3: single-root everywhere + mkdir-in-picker + auto docs/ (design)

Status: DESIGN → dispatch · 2026-06-05 · founder using the product.
Decisions (founder-locked):
- Adopt (existing) ALSO uses a single Project Root (drop the 3 repo inputs); context is
  auto-discovered from root.
- New project: single root + auto-create `docs/` under root AND mirror generated artifacts
  (PRD/RFC/...) into it so the project carries its own docs.
- Directory picker can create a new folder under the currently-browsed path.

## Findings (read-only)
- Artifacts are written to the workspace `ArtifactStore.root/artifacts/runs/<run_id>/...`
  (artifacts.py:8) — NOT into the repo today.
- `collect_project_context` (graph.py:102/123) reads files per repo name (docs/backend/frontend)
  from `context_file_specs()` (roles.py:58). Single-root projects would read nothing → must add
  root-based discovery.
- No write FS endpoint yet (`/api/fs/list` is read-only).
- `create_local_project_config` already supports `project_root` → `repos:{root}` (from v2).

## Track A — BE (codex)
Files: web.py, graph.py, artifacts.py (or graph call sites), projects.py if needed, README, tests.
1. **`POST /api/fs/mkdir`** (write): body `{parent, name}`. Create `parent/name` and return the new
   abs path. Safety: `parent` must be an existing dir (realpath); `name` must be a SINGLE path
   segment — reject empty / containing `/` `\` / `..` / leading-or-only dots / NUL. 400 on bad
   input, 404 if parent missing, 409 if already exists (or return existing — your call, documented).
   Read/limited-write only; never delete.
2. **Adopt single-root context discovery.** When the project config's `repos` has a single root
   (e.g. `root:`), `collect_project_context` discovers context FROM that root instead of requiring
   docs/backend/frontend: pick high-signal files — top-level `README*`, `docs/**` (bounded count),
   manifest files (`package.json`/`pyproject.toml`/`go.mod`/`Cargo.toml`/etc.), and a shallow tree
   overview. Keep the existing 3-repo path working (back-compat); only add the root path.
3. **New project auto-docs + artifact mirror.** When creating a new (non-adopt) single-root
   project, create `<root>/docs/`. When artifacts are generated, ALSO write a copy of each into
   `<root>/docs/<artifact>.md` (latest wins), in addition to the workspace artifacts dir. Idempotent;
   never fail the run if the repo dir is unwritable (warn).
Tests: mkdir happy/invalid-name/missing-parent; root-discovery yields README/manifest packets;
new-project create makes docs/; artifact write mirrors into root/docs.

## Track B — FE (codex)
File: frontend/src/App.jsx only.
1. **Picker "New folder".** In the directory browser, add a "New folder" control: input a name →
   `POST /api/fs/mkdir {parent: pickerPath, name}` → on success refresh the listing (re-fetch
   pickerPath) and ideally select/descend into it. Handle 400/404/409 inline.
2. **Adopt uses single root.** Make Adopt Existing show ONE Project Root input + Browse (same as
   New Project), dropping the three repo inputs. Submit `project_root` for BOTH modes (source_type
   still distinguishes adopt vs new). Keep manual paste.

## Contract pin (A ↔ B)
`POST /api/fs/mkdir` body `{parent: string, name: string}` → `{path: string}` (errors 400/404/409
JSON). Create-config payload: both modes send `project_root`.

## Verification
- BE: pytest + ruff; live curl mkdir (valid + `..` rejected); adopt config with root → context
  packets present; new-project create → root/docs exists + an artifact mirrored.
- FE: lint/build; Playwright smoke: New folder creates + appears; Adopt shows single root.
- Triple-check each → human land gate. BE first, FE rebased.
