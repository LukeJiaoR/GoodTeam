# Design — New-project git init (+ optional GitHub remote via PAT)

Status: **founder-gated 2026-06-06** ("git init 先做了吧"; founder picked opt 1+3 earlier; PAT in env
var + tell required permissions). Role chain: architect (this) → backend (codex) + frontend (agy) →
review → pre-review → land.

## Why
`create_local_project_config` (web.py:824) creates the project config + auto-mkdir `docs/` but never
`git init`. So a new project isn't a git repo → Dev Loop empty AND GoodTeam's own
dispatch→worktree→PR→land loop can't run on it (`dispatch-track.sh` needs `git worktree add … main`).

## Scope (two toggles on the New-Project create flow)
1. **Initialize Git repository** — DEFAULT ON. On create, if the project root is NOT already a git
   repo, `git init` + an initial commit. Skipped for adopt/existing (already a repo) and if unchecked.
2. **Create & link GitHub remote** — DEFAULT OFF. Needs a GitHub PAT from an env var. Creates a
   GitHub repo, sets `origin`, pushes. So the Dev Loop PR list + full branch→PR→CI→land loop work on
   a fresh project end-to-end.

## Backend (src/goodteam_os/web.py + a small git helper + tests)
Extend `create_local_project_config(..., init_git: bool = True, create_remote: bool = False,
remote_private: bool = True)`. After the project root is resolved (single-root new-project path):

### git init (init_git, root has no .git)
- `git -C <root> init` (+ default branch `main`: `git init -b main` or set after).
- Write a minimal `.gitignore` and a `README.md` (project name) if absent (so the initial commit
  isn't empty); keep the auto-created `docs/`.
- `git -C <root> add -A` then commit "Initial commit (GoodTeam scaffold)". (Root is a fresh
  new-project dir, so add -A is safe.) Set a commit identity only if none configured (fallback
  `GoodTeam <noreply@goodteam.local>` via `-c user.name/-c user.email`, do not mutate global config).
- If `<root>/.git` already exists → SKIP init (never reinitialize / touch an existing repo). Record a
  note.

### GitHub remote (create_remote)
- Read the PAT from env: `GITHUB_TOKEN` (fallback `GH_TOKEN`). If absent → do NOT fail the create;
  return a clear warning: "Create-remote requested but no GITHUB_TOKEN/GH_TOKEN env var set."
- Create + link + push via the gh CLI with the token injected into the subprocess env:
  `GH_TOKEN=<token> gh repo create <key-lowercased> --private|--public --source <root> --remote origin --push`
  (gh is already a dependency). Capture warnings on failure (auth/exists/etc.); never raise — the
  project config must still be created.
- Visibility from `remote_private` (default private).

### Response / safety
- Return the existing config result PLUS a `git` section: `{initialized: bool, remote_created: bool,
  warnings: [str]}` so the FE can show what happened. All git/gh steps are BEST-EFFORT — a failure
  warns, never blocks project-config creation. Never touch an existing `.git`. Read-only elsewhere;
  never touch real CONTENTOPS/AGENTSMESH configs.

### Tests (tests/)
- init_git on a fresh tmp root → `<root>/.git` exists + one commit + README/.gitignore.
- init_git skipped when `<root>/.git` already exists (no error, note).
- create_remote with no token env → warning, no raise, config still created (monkeypatch gh / env).
- create_remote token present → gh invoked with `GH_TOKEN` env + expected args (monkeypatch
  subprocess; assert command), config still created.

## Frontend (frontend/src/App.jsx — the New-Project create modal)
- Add (only in New-Project mode, not adopt):
  - **"Initialize Git repository"** checkbox, default CHECKED.
  - **"Create GitHub repo & push"** checkbox, default UNCHECKED; when checked reveal a
    **Private/Public** toggle (default Private) + a helper/callout:
    > Requires a GitHub Personal Access Token in your environment as **`GITHUB_TOKEN`** (or `GH_TOKEN`)
    > before launching GoodTeam.
    > **Token scopes** — Classic: **`repo`**. Fine-grained: **Administration: Read & write** +
    > **Contents: Read & write** (on the target account/org).
- Pass `init_git`, `create_remote`, `remote_private` in the create POST body.
- After create, if the response `git` section has warnings, surface them (toast / inline) so the user
  knows e.g. the token was missing. Keep dark-mode readable.

## Acceptance
- BE: `pytest` + `ruff` green; the tests above.
- FE: `npm run lint` + `npm run build` green.
- Live: create a new project with Init-Git on → root becomes a git repo with an initial commit (Dev
  Loop no longer empty / dispatch loop works). Create-remote with no token → clear warning, project
  still created.

## Lane split (file-disjoint, parallel)
- **BE → codex:** web.py (+ a small git helper, e.g. in web.py or a new `gitops.py`) + tests.
- **FE → agy:** frontend/src/App.jsx only.
Contract: create POST gains `init_git`/`create_remote`/`remote_private`; response gains a `git`
section `{initialized, remote_created, warnings[]}`.

## Out of scope
Re-initializing/adopting existing repos as remotes; org-vs-user selection UI; OAuth flow (env PAT
only); non-GitHub hosts.
