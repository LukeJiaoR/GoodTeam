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

## Boundary

This repo must not read Codex local MCP credentials at runtime. Plane credentials for future adapters should come from environment variables or a secret manager.
