# GoodTeam AI Team OS

GoodTeam is a local-first LangGraph-oriented AI Native agile development team system.

V1 focuses on the workflow foundation:

- Start a run from an idea.
- Generate a durable PRD artifact.
- Persist workflow state to JSON.
- Write a checkpoint.
- Pause at a human approval gate.
- Keep project-local GoodTeam memory and task ledgers under each target project.

GoodTeam can operate without Plane, Linear, or any external tracker. External systems are optional sync surfaces; the local project runtime is the source of truth for memory, artifacts, decisions, and tasks.

The long-term design lives in Obsidian under:

```text
/Users/luke/Documents/note/note/GoodTeam/AI Team OS
```

Plane tracking:

```text
Project: GoodTeam AI Team OS
Key: GTO
URL: https://app.plane.so/leader_kanban/projects/11b3926b-7464-438b-b419-6555802677fa
```

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install pytest
PYTHONPATH=src .venv/bin/python -m pytest tests -q
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli start "Build AI Team OS" --workspace /tmp/goodteam-os-run
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli serve --workspace /tmp/goodteam-os-run --port 8788
```

Expected result:

- `artifacts/runs/<run_id>/prd/prd.md`
- `state/current.json`
- `state/checkpoints/<run_id>/prd_review_gate.json`
- `next_node` is `await_prd_approval`

Open the dashboard at:

```text
http://localhost:8788/
```

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
  --role-model architect=claude:opusplan \
  --role-model qa=gemini:gemini-2.5-flash
```

Preview the model matrix and planned artifact paths without writing state or artifacts:

```bash
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli start "Build AI Team OS" \
  --workspace /tmp/goodteam-os-run \
  --model-profile cheap \
  --role-model architect=claude:opusplan \
  --dry-run
```

The chosen profile and role model matrix are saved into `state/current.json`, so the dashboard and future resume logic can observe the exact model choices for that run.

## tmux

`tmux` is not required, but it is the preferred way to keep the dashboard attached once installed:

```bash
tmux new -s goodteam-dashboard
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli serve --workspace /tmp/goodteam-os-run --port 8788
```

## Project Targets

Eastern Birth Chart is registered as the first real workspace target:

```bash
PYTHONPATH=src .venv/bin/python -m goodteam_os.cli doctor \
  --project-config projects/eastern-birth-chart.local.yaml
```

The committed file is `projects/eastern-birth-chart.example.yaml`. The real local file is `projects/eastern-birth-chart.local.yaml` and is git-ignored because it contains machine-specific repo paths and Plane metadata.

For multi-repo products, GoodTeam stores project runtime data in the shared parent project directory when the configured repos live together:

```text
<project-root>/.goodteam/memory/project-memory.md
<project-root>/.goodteam/tasks/tasks.json
```

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

Local worker commands use the CLIs currently available on this machine:

- Claude CLI: product, requirement, architect, review, planner, release, retro/memory
- Gemini CLI: UI and frontend
- Codex CLI: backend, QA, code review

DeepSeek can remain in `models.yaml` as a model provider target, but there is no local `deepseek` CLI command currently available in `PATH`.

DeepSeek API access is detected through environment configuration and never through committed files. If Codex was launched from a process that does not inherit your shell environment, put the key in the git-ignored `.env.local` file instead:

```bash
# Option A: shell environment, if Codex inherits it.
export DEEPSEEK_API_KEY="<your-rotated-deepseek-api-key>"

# Option B: local fallback file, if Codex does not inherit your shell environment.
printf 'DEEPSEEK_API_KEY=<your-rotated-deepseek-api-key>\n' > .env.local

PYTHONPATH=src .venv/bin/python -m goodteam_os.cli doctor \
  --project-config projects/eastern-birth-chart.local.yaml
```

The doctor output reports only `configured: true` or `false`; it does not print the key. Rotate any key that has been pasted into chat before putting the replacement into `.env.local`.

## Boundary

This repo must not read Codex local MCP credentials at runtime. Plane credentials for future adapters should come from environment variables or a secret manager.
