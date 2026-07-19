# Project readiness executors

GoodTeam owns project readiness state. The browser renders the persisted state
returned by the server; it never marks a project ready or enables workflow start
on its own.

## Lifecycle and persistence

Readiness is stored at `WORKSPACE/state/projects/PROJECT_KEY/readiness.json`.
Schema version 2 records the project key, canonical root, imported root device and
inode, execution identity, attempt, timestamps, current step, structured inputs,
artifact references, errors, and recovery actions. Step states are `pending`,
`running`, `needs_input`, `complete`, `failed`, or `cancelled`.

Mutations use the descriptor-anchored atomic writer introduced by the project-load
foundation. A workspace lock serializes competing requests, idempotency keys bind
start payloads, and execution/attempt checks prevent an old runner from publishing
after cancellation or retry. A server that finds an orphaned `running` execution
records `readiness_interrupted` and exposes a retry instead of repeating completed
steps.

## Read-only and approval boundary

Existing-project understanding opens the imported root with `O_NOFOLLOW`, verifies
the stored device/inode, traverses a bounded number of entries, skips symlinks, and
reads only bounded regular files needed for manifests and key documentation. It
does not install dependencies, execute discovered commands, modify configuration,
or write into the target repository.

Discovered commands are candidates only. They carry stable IDs and require explicit
confirmation. Worker preflight is local and dry-run only. A warning requires user
acknowledgement; a failure requires configuration and retry.

New-project goal, brief, preferences, constraints, and prohibited operations are
stored as GoodTeam readiness inputs and artifacts. Initialization does not create
files in the target directory.

## HTTP resources

- `POST /api/projects/{key}/readiness/start`
- `GET /api/projects/{key}/home`
- `POST /api/projects/{key}/readiness/input`
- `POST /api/projects/{key}/readiness/retry`
- `POST /api/projects/{key}/readiness/cancel`
- `GET /api/projects/{key}/readiness/steps/{step_id}/artifact`
- `POST /api/projects/{key}/workflow`

Errors use the stable project API envelope: `code`, `message`, `details`,
`recovery`, and `request_id`.

`start_workflow` is computed only by the backend. It requires schema version 2,
the exact mode-specific required-step set, complete project/root-bound steps, a
complete bound execution, and lifecycle `ready`. The workflow endpoint enters
the product Work route at `/projects/PROJECT_KEY/work` after rechecking that
capability.

## Local preview and isolated E2E

Use a disposable GoodTeam workspace and a target under `/private/tmp`:

```sh
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli serve \
  --workspace /private/tmp/goodteam-readiness-preview/workspace \
  --host 127.0.0.1 --port 8899
```

Then open `http://127.0.0.1:8899/projects`. The repository Playwright configuration
creates fresh fixtures under `/private/tmp/goodteam-project-load-e2e`:

```sh
npm run test:e2e --prefix frontend
```

## Verification record

Verification tier: **Tier 4** browser/live path plus **Tier 3** persisted control
state. Contract scope: project import compatibility, readiness schema and atomic
mutation, read-only repository inspection, user-input approval, Worker preflight,
capability gating, workflow bridging, bilingual browser UI, and project-root
security boundaries.

Final commands run on 2026-07-18 from the isolated feature worktree:

```text
PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest \
  tests/test_project_readiness_executor.py tests/test_project_readiness_web.py \
  tests/test_project_readiness.py tests/test_project_import.py -q
  -> 121 passed

PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests -q
  -> 1443 passed in 112.95s

/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os tests
  -> All checks passed

npm test --prefix frontend
  -> 6 Node tests and 125 Vitest tests passed

npm run lint --prefix frontend
  -> passed

npm run build --prefix frontend
  -> passed; Vite reported the existing advisory for a chunk larger than 500 kB

GOODTEAM_E2E_PYTHON=/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python \
  npm run test:e2e --prefix frontend -- --reporter=line
  -> 5 passed in 10.4s

git diff --check
  -> passed
```

The Playwright run used fresh targets and a GoodTeam workspace under `/private/tmp`.
It covered existing and new projects, command and constraint confirmation,
failure/retry, refresh persistence, service restart persistence, workflow unlock and
real legacy-route navigation, English and Chinese, a 390-pixel viewport, and strict
before/after target snapshots. No required validation was skipped. No mutating smoke
was run against an active project.
