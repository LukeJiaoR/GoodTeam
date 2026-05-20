# GoodTeam AI Team OS

GoodTeam is a local-first LangGraph-oriented AI Native agile development team system.

V1 focuses on the workflow foundation:

- Start a run from an idea.
- Generate a durable PRD artifact.
- Persist workflow state to JSON.
- Write a checkpoint.
- Pause at a human approval gate.

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

Local worker commands use the CLIs currently available on this machine:

- Claude CLI: product, requirement, architect, review, planner, release, retro/memory
- Gemini CLI: UI and frontend
- Codex CLI: backend, QA, code review

DeepSeek can remain in `models.yaml` as a model provider target, but there is no local `deepseek` CLI command currently available in `PATH`.

## Boundary

This repo must not read Codex local MCP credentials at runtime. Plane credentials for future adapters should come from environment variables or a secret manager.
