# GoodTeam — Local-First AI Engineering Team OS

GoodTeam is the local-first control layer for AI software delivery. It coordinates
specialized coding agents, project tools, CI, review, deployment evidence, and memory
into one owned workflow ledger, so a human can ship software through explicit,
reviewable gates.

**GoodTeam owns the engineering-org protocol; external tools are adapters.** The role
chain, workflow state, approval gates, evidence model, review workbench, memory, and
provenance are first-party. Plane, GitHub, CI, Playwright, deployment tooling, and the
agent CLIs are integrated as replaceable adapters — they produce evidence, but the
GoodTeam ledger remains the source of truth for workflow decisions.

The foundation:

- Start a run from an idea.
- Generate durable PRD/RFC artifacts.
- Persist workflow state to JSON and write checkpoints.
- Pause at human approval gates with an evidence-backed review workbench.
- Record an append-only approval audit trail (decision origin, actor, review mode,
  evidence snapshot) across Web and Telegram clients.
- Keep project-local GoodTeam memory and task ledgers under each target project.

GoodTeam can operate without Plane, Linear, or any external tracker. External systems are optional sync surfaces; the local project runtime is the source of truth for memory, artifacts, decisions, and tasks.

## Quick Start

Install the package (this pulls in `rank-bm25` and the `goodteam-os` CLI):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

### Configure one worker backend (codex)

For a first real demo run, use `codex` as the single recommended backend path:

1. Install the `codex` CLI.
2. Authenticate the `codex` CLI per the official Codex docs.
3. Verify readiness with doctor (or `init`, which runs doctor automatically) and confirm `codex` is reported as ready in worker backend preflight:

```bash
# doctor-only
goodteam-os doctor --project-config projects/myapp.local.yaml

# or create the config and run doctor in one step
goodteam-os init --key MYAPP --name "My App" --repo /path/to/your/repo
```

Full 30-minute demo walkthrough is separate and is being documented around `scripts/demo-up.sh`.

### No-credentials PRD gate path (fake worker)

You can still scaffold a project and reach the first approval gate with **no model credentials**, because the PRD gate is deterministic and the default worker backend is `fake`:

```bash
# 1. Create a project config (writes projects/myapp.local.yaml + runs doctor)
goodteam-os init --key MYAPP --name "My App" --repo /path/to/your/repo

# 2. Start a run from an idea
goodteam-os start "Build a task tracker" --project-key MYAPP \
  --project-config projects/myapp.local.yaml --workspace ./run

# 3. Open the dashboard
goodteam-os serve --workspace ./run --port 8788
```

Expected result:

- `run/artifacts/runs/<run_id>/prd/prd.md`
- `run/state/projects/MYAPP/current.json`
- `run/state/projects/MYAPP/checkpoints/<run_id>/prd_review_gate.json`
- `next_node` is `await_prd_approval`

See [`projects/example.yaml`](projects/example.yaml) for the full project-config schema.

Other backends: `fake` is the fastest no-creds path to the PRD gate; `codex` is the recommended real implementation/QA backend for the full demo; `anthropic` (Claude CLI) remains supported when you prefer that stack.

Open the dashboard at:

```text
http://localhost:8788/
```

The root dashboard is a multi-project overview. Open a project detail page with:

```text
http://localhost:8788/?project=<PROJECT_KEY>
```

### Frontend-Backend Separation & Local Development

GoodTeam OS operates under a clean frontend-backend separated architecture.

#### 1. Backend Server
The Python backend manages the APIs, workflow state transitions, and file system interactions:
```bash
# Start backend server on port 8788
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli serve --port 8788
```

#### 2. Frontend Development (Independent Dev Server)
The React dashboard runs as a separate dev server with proxy settings forwarding `/api/*` requests to the backend:
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:8790/` in your browser. GoodTeam pins the local frontend dev server to port 8790 so it does not collide with Vite's default 5173 used by other workspaces. All operations (e.g., loading project overview, submitting approvals, starts, and edits) will route seamlessly through the dev server proxy to the backend.

#### 3. Production Hosting (Backend Static File Serving)
To deploy or host the React dashboard directly via the Python backend, compile the static files:
```bash
cd frontend
npm run build
```
Once compiled to `frontend/dist/`, starting the backend server (`goodteam_os.cli serve`) will automatically serve the built React application on `http://localhost:8788/` (falling back to legacy template rendering if the directory is missing or unbuilt).

## Model Profiles

Default role-to-model routing lives in `models.yaml`.

Start with a preset profile:

```bash
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli start "Build AI Team OS" \
  --workspace /tmp/goodteam-os-run \
  --model-profile balanced
```

Available V1 profiles:

- `balanced`
- `cheap`
- `max_quality`

Override one role for a single run:

```bash
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli start "Build AI Team OS" \
  --workspace /tmp/goodteam-os-run \
  --model-profile cheap \
  --role-model architect=codex:gpt-5.5 \
  --role-model qa=codex:gpt-5.3-codex
```

Preview the model matrix and planned artifact paths without writing state or artifacts:

```bash
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli start "Build AI Team OS" \
  --workspace /tmp/goodteam-os-run \
  --model-profile cheap \
  --role-model architect=codex:gpt-5.5 \
  --dry-run
```

The chosen profile and role model matrix are saved into `state/projects/<project-key>/current.json`, so each project can keep its own active run and model choices.

## tmux

`tmux` is not required, but it is the preferred way to keep the dashboard attached once installed:

```bash
tmux new -s goodteam-dashboard
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli serve --workspace /tmp/goodteam-os-run --port 8788
```

## Smoke Loops

GoodTeam can run local smoke loops that exercise the workflow, approval gates, workers, verification commands, and machine-readable evidence reports:

```bash
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli smoke \
  --workspace /tmp/goodteam-smoke \
  --suite contentops,self \
  --cycles 2 \
  --autonomy full \
  --worker-mode fake
```

Use `--dry-run` to preview planned cycles without creating project configs, state, artifacts, or target files. Smoke reports are written to:

```text
<workspace>/state/smoke-runs/latest.json
```

Supported smoke suites:

- `contentops`: runs the CONTENTOPS canary workflow against an isolated local target under the smoke workspace. Set `GOODTEAM_CONTENTOPS_ROOT` only when you intentionally want to point at an existing target root.
- `self`: runs GoodTeam against GoodTeam from a temporary git worktree. If the smoke workspace is inside this repo, GoodTeam automatically places the self worktree under an external temp root; set `GOODTEAM_SELF_WORKTREE_ROOT` to override it. Because this uses `git worktree`, it tests committed code at `HEAD`; commit the current GoodTeam changes before treating self-smoke results as acceptance evidence.

Supported smoke worker modes:

- `fake`: deterministic local worker adapter for smoke-loop verification.
- `noop`: records protocol-only worker completion.
- `codex`: invokes the configured Codex CLI worker adapter.
- `anthropic`: drives the `claude` CLI (Claude Code) headless worker; set `ANTHROPIC_API_KEY` and pair with `model_profile: anthropic`.

On failure, GoodTeam classifies the failure as `product`, `test`, or `env` and writes a blocker task into the target's `.goodteam/tasks/tasks.json`.

Smoke projects are also first-class agile board tasks. Each smoke cycle creates or updates a high-priority task in the target task ledger, moves it through `backlog`/`doing`/`blocked`/`done`, and attaches the smoke report plus worker result paths as evidence when available.

For full product acceptance evidence, use the isolated wrapper:

```bash
scripts/full-acceptance-smoke.sh \
  --output-root /tmp/goodteam-full-smoke \
  --suite contentops,self \
  --cycles 1 \
  --worker-mode fake
```

The wrapper standardizes `report.md`, `summary.json`, `coverage-matrix.md`, `logs/`, `screenshots/`, and `state-snapshots/` under the selected output root. It defaults to credential-free fake/noop workers; real CLI workers such as Codex or Claude require `--allow-real-worker`.

View the same lightweight Kanban data from the CLI:

```bash
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli board \
  --project-root /absolute/path/to/project
```

The dashboard exposes the same data in the `Agile Board` tab, alongside the existing `Smoke Runs` tab.

## 30-Minute Demo: idea → PRD → working app → release

The end-to-end path a new user can run on a clean machine: start from an idea,
approve each gate in the dashboard, and watch real workers build and release a
working app — with a full audit trail. (Requires the `codex` backend configured
above. ~15–25 min, mostly the real implementation/QA workers.)

```bash
# Brings up an isolated, real-worker demo OUTSIDE this repo (../goodteam-demo),
# checks codex is ready, starts the pinned idea "Build a task tracker", and serves
# the dashboard at http://localhost:8788 stopped at the first approval gate.
scripts/demo-up.sh --reset
#   flags: --port=NNNN  --build (rebuild frontend/dist)  --reset (wipe + reseed)
```

Then drive it from the dashboard at `http://localhost:8788/?project=DEMO`
— everything is approved in the UI, no extra CLI steps:

1. **Approvals tab → approve PRD.** The PRD is generated up front (no model
   credentials needed for this step).
2. **Approve RFC / plan.** This triggers the real `codex` workers (UI →
   implementation → QA, dispatched in sequence). This is the long step.
3. **Approve release.** The run reaches `complete`.
4. **Read the audit trail:** the **Artifacts** tab (PRD, RFC, QA report,
   release notes), the **Approvals** tab (every gate decision), and the
   **Memory** tab (what each role retrieved, with `mem:` handles).

Result: a working app scaffold under `../goodteam-demo/workspace/target`
(frontend + backend + tests + README), built to match your idea, with every gate
and worker result recorded. Swap the pinned idea by editing the `start` line in
`scripts/demo-up.sh`, or run your own project via `init` + `start` (Quick Start).

The demo workspace lives outside this repo so worker context stays clean; it
never touches your real project configs.

## GoodTeam Self-Hosting (Release/Dev)

For GoodTeam-on-GoodTeam development, run orchestration from a pinned release instance outside the repo, and target a dev worktree:

```bash
scripts/release-up.sh --ref v1.0.0
```

Then dispatch from the release instance toward a dev worktree (`--project-root /abs/path/to/dev-worktree`). To advance the pinned release ref, use the graduation gate:

```bash
scripts/graduate-release.sh --ref <tag-or-commit> --yes
```

## Project Targets

Create a local project config for your own repos from the dashboard, or add one directly under `projects/`:

```yaml
name: My Product
key: MYPROD
model_profile: balanced
delivery_mode: init_project
repos:
  docs: /absolute/path/to/my-product-docs
  backend: /absolute/path/to/my-product-api
  frontend: /absolute/path/to/my-product-web
task_backend:
  mode: local
  sync:
worker_backend:
  mode: codex
deployment_backend:
  mode: ssh_compose
  staging_ssh_target: my-product-staging
  staging_remote_dir: /srv/my-product
  staging_git_ref: main
  staging_public_url: https://staging.example.com
  staging_deploy_command: git fetch --all && git checkout main && git pull --ff-only origin main && docker compose -f deploy/docker-compose.staging.yml up -d --build --remove-orphans
  staging_healthcheck_command: docker compose -f deploy/docker-compose.staging.yml ps && curl -fsS http://127.0.0.1:8080/health
  staging_smoke_command: curl -fsS https://staging.example.com/health
delivery_contract:
  required: true
  minimum_evidence: functional
  fatal_output_patterns:
    migration_missing: migrate: executable file not found
    missing_relation: relation .* does not exist
  staging_acceptance:
    migrations:
      type: ssh_command
      command: docker compose -f deploy/docker-compose.staging.yml exec -T backend ./migrate status
    critical-user-journey:
      type: local_command
      command: ./scripts/e2e-critical.sh https://staging.example.com
```

For a new single-root project, the config can use one repo entry:

```yaml
delivery_mode: custom
delivery_standards:
  standard_0001: Deliver a locally runnable first slice with documented setup and verification commands.
  standard_0002: Preserve existing contracts and include focused regression tests.
repos:
  root: /absolute/path/to/my-product
```

Then check the project target:

```bash
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli doctor \
  --project-config projects/my-project.local.yaml
```

`doctor` (and the `init` command, which runs it automatically) reports a `worker_backend_preflight` section: whether the configured worker CLI is on `PATH`, whether credentials are present (presence only — never the secret value), and an actionable `hint`. For example it flags when a stale `CODEX_API_KEY` could shadow `~/.codex/auth.json`, or when `claude login` is needed for the `anthropic` worker — so credential gaps surface before a worker fails.

Local project configs are intended to be machine-specific and may contain repo paths or tracker metadata. Keep secrets out of them; use environment variables or local secret files for credentials.

Target projects can also declare an optional staging deployment backend. V1 supports `Docker Compose + SSH` only and is designed for hosts that are already provisioned:

```yaml
deployment_backend:
  mode: ssh_compose
  staging_ssh_target: my-product-staging
  staging_remote_dir: /srv/my-product
  staging_git_ref: main
  staging_public_url: https://staging.example.com
  staging_deploy_command: git fetch --all && git checkout main && git pull --ff-only origin main && docker compose -f deploy/docker-compose.staging.yml up -d --build --remove-orphans
  staging_healthcheck_command: docker compose -f deploy/docker-compose.staging.yml ps && curl -fsS http://127.0.0.1:8080/health
  staging_smoke_command: curl -fsS https://staging.example.com/health
```

GoodTeam is positioned as a full-cycle internet product team, not just a deploy runner. For staging-backed projects, add a delivery contract so GoodTeam can distinguish "infrastructure is alive" from "the product is functionally usable":

```yaml
delivery_contract:
  required: true
  minimum_evidence: functional
  fatal_output_patterns:
    migration_missing: migrate: executable file not found
    missing_relation: relation .* does not exist
  staging_acceptance:
    critical-user-journey:
      type: local_command
      command: ./scripts/e2e-critical.sh https://staging.example.com
```

`staging_acceptance` checks are project-declared representative user journeys or operational readiness checks. GoodTeam does not guess product behavior; it executes the contract, records evidence, and blocks staging when required functional evidence is missing.

When `deployment_backend.mode: ssh_compose` is configured for an adopted existing project, GoodTeam inserts a staging gate between QA/code review and final release approval:

- QA/code review complete
- deploy to staging over SSH
- validate staging health/smoke commands
- run delivery-contract acceptance checks
- block if fatal output patterns appear, even when a command exits 0
- human staging approval
- final release approval

Doctor reports whether the staging backend is fully configured, whether `ssh` is available locally, and whether the delivery contract has enough evidence for the requested gate. GoodTeam only records command metadata and result evidence; it does not copy secrets, manage SSH keys, initialize remote hosts, or upload files with rsync in this version.

Project management actions are scoped to GoodTeam metadata:

- `Terminate run` archives the selected project's active run and clears `state/projects/<project-key>/current.json`.
- `Delete project` removes the selected `projects/<project-key>.local.yaml` config and `state/projects/<project-key>/` runtime state from GoodTeam.
- `Delete project` does not delete any repository directories listed under `repos:`.

Projects can carry their own default model strategy:

```yaml
model_profile: balanced
role_model_overrides:
  architect: codex:gpt-5.5
  frontend: codex:gpt-5.3-codex
```

At run start, GoodTeam resolves models in this order: the global `models.yaml` profile, the project `model_profile`, the project `role_model_overrides`, then any one-run `--role-model` or dashboard override.

Projects can also declare their delivery shape:

```yaml
delivery_mode: init_project
```

Supported delivery modes:

- `mvp`: validation-ready slice with the smallest useful product surface, local run commands, deterministic tests, and a clear evolution path.
- `init_project`: production starter for a real internet product, including frontend/backend separation, API contracts, data layer, routing, DevOps, CI/test commands, deployment notes, and health checks.
- `multi_service`: multi-service product shape with service boundaries, API or event contracts, gateway/router responsibilities, database ownership, async/queue needs, observability, orchestration, and rollback strategy.
- `static_site`: static site or landing page with build output, local preview, content/route structure, responsive accessibility basics, and deployment notes.
- `cli_tool`: command-line tool with argument and flag contracts, help text, exit codes, deterministic tests, and installation/run docs.
- `library`: library or SDK with public API docs, semantic versioning expectations, host-independent examples, and unit tests.
- `data_pipeline`: idempotent ETL/data pipeline with schemas or contracts, sample data, reproducible backfill, retry behavior, and tests.
- `mobile_app`: mobile app with target platform, build/run commands, navigation/state expectations, offline/error states, and basic flow verification.
- `feature_increment`: incremental feature for an existing system with contract compatibility, regression tests, and rollout/rollback notes.
- `custom`: project-defined delivery standards read from `delivery_standards`.

If omitted, GoodTeam defaults to `init_project`. The selected delivery mode is stored in `state/projects/<project-key>/current.json` and flows into PRD, RFC, implementation handoff, worker expected artifacts, and QA expectations.

For multi-repo products, GoodTeam stores project runtime data in the shared parent project directory when the configured repos live together:

```text
<project-root>/.goodteam/memory/project-memory.md
<project-root>/.goodteam/memory/roles/<role-key>/long-term.md
<project-root>/.goodteam/memory/roles/<role-key>/mid-term.md
<project-root>/.goodteam/memory/roles/<role-key>/short-term.md
<project-root>/.goodteam/memory/roles/<role-key>/index.json
<project-root>/.goodteam/memory/roles/<role-key>/events.jsonl
<project-root>/.goodteam/tasks/tasks.json
```

Each role owns its own long-, mid-, and short-term memory files. Long-term role memory is for durable role-specific project facts and contracts, mid-term role memory is for active decisions and conventions, and short-term role memory is for interrupted work, pickup notes, next actions, blockers, and handoffs.

Role memory is designed for low-token pickup:

- `events.jsonl` is the append-only source for memory updates and rebuilds.
- `index.json` stores hashes, character counts, and compact summaries for each horizon.
- Worker jobs should read `short-term.md` plus the `index.json` summaries first, not the full long/mid files.
- Full `long-term.md` or `mid-term.md` should be expanded only when the current task needs that specific context.
- Rebuilds can regenerate role memory files from `events.jsonl` when the markdown files become stale or too noisy.

The local task ledger is built in. Plane or Linear adapters can synchronize from this ledger later, but they are not required for planning or execution.

Task backends are configured per project:

```yaml
task_backend:
  mode: local
  sync:
```

To sync local tasks to an external kanban later, keep `mode: local` and add adapters:

```yaml
task_backend:
  mode: local
  sync: plane
  plane:
    api_host_url: https://api.plane.so/
    workspace_slug: leader_kanban
    project_id: "<plane-project-id>"
    api_key_env: PLANE_API_KEY
    mcp_server: plane
```

Do not put the Plane API key itself in project config. `api_key_env` names the local environment variable or MCP-side secret source.

Worker execution is also configured separately from the workflow protocol. Set a global default and, optionally, override the backend per role:

```yaml
worker_backend:
  mode: codex            # global default for any role not listed below
workers:
  implementation: anthropic   # this role runs on the claude CLI
  qa: codex                   # this role runs on the codex CLI
```

Supported V1 modes are `codex` (Codex CLI), `anthropic` (Claude Code `claude` CLI), `fake`, `noop`, and `human`. Each role resolves its backend from `workers.<role>`, falling back to `worker_backend.mode`, then `codex`; `doctor` reports readiness for every backend you reference (`worker_backends_preflight`). Backends are a registry (`CLI_BACKENDS` in `workers.py`) — adding a new agentic CLI is a single `CliBackendSpec`. The `codex` and `anthropic` workers share one run loop — they dispatch the job protocol to an agentic coding CLI, stream heartbeats, and capture the result (codex via `--output-last-message`, claude via stdout). Override the executables with `GOODTEAM_CODEX_PATH` / `GOODTEAM_CLAUDE_PATH`. The worker job protocol is local-first under `<project-root>/.goodteam/workers/jobs/<job_id>/`; CLI tools are adapters, not the source of truth. Each job includes `role_prompt.md`, `task.md`, `context.md`, `constraints.md`, `expected_artifacts.json`, `role_memory_paths.json`, `role_pickup_packet.json`, `result.md`, `stdout.txt`, `stderr.txt`, and `status.json`. The stable role prompt stays separate from the concrete task. `role_memory_paths.json` points workers at their role-specific long-, mid-, and short-term project memory files; `role_pickup_packet.json` is the default token-light pickup packet.

Early CONTENTOPS E2E worker execution uses Codex CLI for every role:

- Command: `codex exec --model gpt-5.3-codex --skip-git-repo-check`
- Adapter: `worker_backend.mode: codex`
- Evidence: each job stores command, exit code, stdout, stderr, preflight diagnostics, and final result under `.goodteam/workers/jobs/<job_id>/`.

Codex CLI uses the locally configured provider and credentials. Provider credentials must come from environment variables or a secret manager when future adapters need them. Do not commit API keys or paste secrets into GoodTeam project configs.
Codex worker preflight records only non-secret facts, such as whether `OPENAI_API_KEY`, `CODEX_API_KEY`, or `CODEX_ACCESS_TOKEN` is present; it never stores credential values.

Staging deploy commands follow the same local-only rule: SSH authentication must come from the user's existing SSH config, agent, and known_hosts; GoodTeam never stores or writes keys.

## Boundary

This repo must not read Codex local MCP credentials at runtime. Plane credentials for future adapters should come from environment variables or a secret manager.

## Telegram Approval & Remote Supervision Client

Telegram is a first-class approval client for GoodTeam's human gate protocol, not a
shallow notification adapter. It drives decisions through the same ledger, approval
state, evidence model, and stale-button safety rules as the dashboard — the dashboard
remains the deep review workbench, Telegram is the complete mobile client. The channel
is local-first and uses long polling, not a public webhook:

```bash
export GOODTEAM_TG_BOT_TOKEN="<telegram-bot-token>"
export GOODTEAM_TG_CHAT_ID="<chat-id>"
export GOODTEAM_TG_ALLOWED_USERS="<telegram-user-id>,<telegram-user-id>"

PYTHONPATH=src .venv/bin/python -m goodteam_os.cli tg-poll \
  --workspace /tmp/goodteam-os-run \
  --project-key CONTENTOPS
```

From Telegram, an allowed operator can approve / request changes / reject a gate
(including step-by-step item review with masked request-change notes), and supervise a
run remotely with read-only commands:

- `/gt status` — active project, current run, current node, pending gate, active
  worker count, blocker count.
- `/gt approvals` — pending approval cards with quick review buttons that route through
  the same audited decision path.
- `/gt workers` — recent/active worker jobs, status, elapsed time, masked last-error
  excerpt.
- `/gt evidence` — latest CI / smoke / QA / release-readiness status and blocker count.

Every approval decision records an audit row — `decision_origin` (`web`/`telegram`),
`actor_ref`, `review_mode`, an evidence snapshot, and item-level review state — so a
later reviewer can reconstruct who approved a gate, from which client, after which
review path. All outbound Telegram text is masked (paths, IPs, SSH targets, tokens,
deploy dirs); the supervision commands are read-only and cannot run arbitrary shell or
bypass server-action safety. Only environment variable names and configured/allowed
metadata are recorded by GoodTeam. Do not put bot tokens in project configs or
committed files.
