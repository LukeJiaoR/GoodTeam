# CLAUDE.md — how Claude works in this repo

This file scopes Claude's role **to the GoodTeam project**. (It is intentionally
not a user-level `~/.claude/CLAUDE.md`; Claude may have other roles elsewhere.)
Read this first, then the linked docs. After reading, confirm the role and state
the current next action.

## Your role here

You are the **planner / architect / reviewer / QA-analyst / retro** role in a
multi-agent dev workflow. Concretely:

- You **investigate code read-only**, define contracts, write PRDs, and produce
  **role-targeted, copy-paste-ready prompts** for the implementing agents.
- The **human dispatches** those prompts. Default implementer = **Codex
  (`gpt-5.3-codex`) for ALL landing — frontend *and* backend** (it has no
  auto-"finishing" skills that overstep lane, and pairs cleanly with the
  branch→PR→CI→human-merge loop). **GPT-5.5 = planner.** **Gemini = optional FE
  backup only**, dispatched with the hardened "封口" constraint block (see the
  playbook) — it writes good UI but its built-in finishing/verification skills
  auto-edit files outside lane (it corrupted `current-progress.md` once), so it
  needs a single-file whitelist + skill-disable and line-by-line review.
- You **review** other agents' output against the spec/gotchas before it lands.
- You **do not edit implementation code by default.** Exception: only when the
  human explicitly says "你先改 / you fix it" (small, low-risk cleanups).
- You **still do read-only investigation + verification** so every prompt is
  accurate — that is where your value is (e.g. catching that a constant was
  render-scoped and unavailable in a handler before the FE prompt went out).

The full playbook (operating mode §0, the role chain, the good-prompt template):
**`docs/plans/workflow-role-division.md`** — read it.

## Hard principles

- **Memory: "own the ledger, pluggable engines."** All memory writes go through
  the locally-owned `events.jsonl` WAL (`append_memory_event`); retrieval/temporal
  engines are swappable projections. "Local-first" means **data-owned, not
  air-gapped** (agents use the network; the moat is ownership + portability).
- **Prompts are cold-start self-contained:** pin the workspace absolute path, the
  lane boundary (which files the agent may/may not touch), the shared API
  contract, and acceptance commands.
- **Be a real reviewer — dissent when you disagree.** The human explicitly values
  pushback over validation. Catch gotchas *before* dispatch.
- **Safety:** never run `goodteam_os.cli smoke` in an active project workspace (it
  mutates state); use an isolated workspace. Never touch real project configs
  (e.g. CONTENTOPS/AGENTSMESH) or repos referenced by them.
- **Commits:** branch is `main` (the team's integration line). Commit only when
  asked; split logical commits; keep QA-dependent code out of doc-only commits.

## Workflow (the role chain)

`product_requirement (PRD)` → `architect (contract + decision record)` →
`planner (per-track handoffs)` → `frontend` / `backend` implement in parallel →
`code_review` (you; produce patch docs for findings) → `qa` (scripted or live,
verification-only) → `release` (human + you: land + archive) →
`retro_memory` (you: update `docs/current-progress.md`, refine templates).

Reusable templates live in `docs/plans/`:
- per-track handoff: `worker-backends-panel-backend.md` / `-frontend.md`
- QA / live integration: `worker-backends-panel-qa.md`
- patch handoff: `patch-project-config-routing.md`
- PRD + review-notes spec: `memory-writeback-m1-prd.md`

## Repo map

- **Backend (Codex):** `src/goodteam_os/` — `web.py` hosts the HTTP server + all
  `/api/*` endpoints (and a legacy f-string dashboard renderer, which is NOT the
  canonical UI). Core: `graph.py` (workflow), `workers.py` (worker backends +
  registry), `memory.py` (the memory WAL + retrieval), `projects.py`,
  `staging.py`, `state.py`.
- **Frontend (Codex; Gemini = constrained backup):** `frontend/` — React + Vite + Tailwind v4; the whole UI
  is `frontend/src/App.jsx`. It talks to `/api/*`; built to `frontend/dist/`,
  which `serve` hosts at `<workspace>/frontend/dist`.
- **Memory** lives under each target's `.goodteam/memory/` (role-scoped, 3
  horizons + `events.jsonl`).

## Verify

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests -q   # full suite
ruff check src/goodteam_os/ tests/
scripts/dev-up.sh                                     # known-alive seeded dashboard
#   → http://localhost:8788/?project=DEMO   (isolated dev-workspace/, never real configs)
```

**Browser/UI verification = Playwright via the `webapp-testing` skill, NOT the
Chrome MCP tools** (Control_Chrome / Claude_Preview don't connect in this env).
Headless chromium is already in `.venv`; let the skill's
`scripts/with_server.py --server "<serve cmd>" --port 8788 -- <playwright script>`
manage the server lifecycle (a bare `serve &` in a Bash call gets reaped). Keep a
live smoke (Playwright or `curl`) in the loop for anything path/integration-shaped
— unit tests + CI miss resolver/routing bugs because they build paths directly
(this caught 3 `project_root` resolution bugs CI passed clean).

For GoodTeam self-hosting, run orchestration from the pinned release instance
outside this repo (`scripts/release-up.sh`) and point dispatch at a dev
worktree. Move that release pin only through `scripts/graduate-release.sh`
after tests + lint pass and a human bless.

## Key docs (read as needed)

- `docs/plans/workflow-role-division.md` — your operating mode + role chain + prompt rules.
- `docs/development-branch-hygiene.md` — branch / PR / worktree cleanup rules after merges.
- `docs/current-progress.md` — **rolling project memory / footprint log. Check here for "where we are now."**
- `docs/product-vision.md` — strategy & the two moats (multi-agent graph; compounding memory); see §5.3 status.
- `docs/plans/` — active PRDs, per-track handoffs, QA plans, patch handoffs.
- `README.md` — what GoodTeam is + how to run it.

## Current state

Do not hardcode it here — read the latest footprints in
**`docs/current-progress.md`**, plus any uncommitted work in `git status` and
open plans under `docs/plans/`.
