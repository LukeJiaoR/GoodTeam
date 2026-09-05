# GoodTeam — Local-First AI Engineering Team OS

GoodTeam is a local-first control layer for AI-assisted software delivery. It coordinates project understanding, AI workers, workflow state, review evidence, CI, human decisions, provenance, and memory through one owned control plane.

**GoodTeam owns the delivery protocol; external tools are adapters.** Coding agents, GitHub, CI, Telegram, and browser automation can perform work or produce evidence, but GoodTeam keeps the authoritative state and human decision trail.

## Current product model

The current product surface is:

```text
Projects → Work / Team / Review / Knowledge
```

- **Projects** imports or opens a local project.
- **Work** is the primary interaction surface. Task Assistant / 任务助手 handles project understanding, questions, work intake, clarification, progress, retries, and server-authorized actions in one project-scoped conversation.
- **Team** exposes project execution configuration.
- **Review** is the evidence-backed human review and approval surface.
- **Knowledge** exposes repository understanding, artifacts, and memory.

Readiness still exists, but it is a backend-owned safety state rather than a separate user journey. Model interpretation may explain or classify intent; it cannot authorize consequential mutations. Workflow start and approval remain explicit human decisions bound to server-owned state.

Legacy Project Home, Inbox, Activity, and Settings product pages have been retired. Compatibility redirects remain for old bookmarks.

For the short current-state summary, see [`docs/status.md`](docs/status.md). For the canonical documentation map and authority rules, see [`docs/README.md`](docs/README.md).

## Quick start

Create a development environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
npm --prefix frontend install
npm --prefix frontend run build
```

Start GoodTeam as the managed local service:

```bash
goodteam restart
goodteam status
```

The managed service defaults to port `21001`. Open:

```text
http://localhost:21001/projects
```

`goodteam restart` uses the current directory as its runtime workspace unless you pass `--workspace`. The older `goodteam-os` command remains available for compatibility and direct CLI workflows.

To stop the managed service:

```bash
goodteam stop
```

## Add or inspect a project

The normal product path is to open `/projects`, select an absolute local directory, and continue directly into the project's Work conversation. Import records GoodTeam metadata without treating the selected repository as disposable workspace state.

For CLI-oriented setup and doctor checks:

```bash
goodteam-os init --key MYAPP --name "My App" --repo /absolute/path/to/repo
goodteam-os doctor --project-config projects/myapp.local.yaml
```

Machine-specific project configs should normally live in `projects/<key>.local.yaml`; do not commit credentials or private secrets.

See [`docs/config-reference.md`](docs/config-reference.md) for the implemented project configuration contract.

## Worker backends

GoodTeam supports bounded worker execution through replaceable backends. Current references include Codex, Claude/Anthropic, Pi persistent sessions, `fake`, `human`, and `noop` paths; backend readiness and exact capabilities vary by adapter.

The currently implemented configuration model is still role-oriented: projects can declare a default worker backend plus per-role overrides. See [`docs/worker-backends.md`](docs/worker-backends.md) and [`docs/config-reference.md`](docs/config-reference.md).

The proposed **Role → TeamRole → Worker** separation is active design work tracked outside this README. Do not treat it as an implemented runtime contract until its domain model, persistence, migration, and invariant tests land.

## Human authority and evidence

Worker output is evidence, not approval authority. GoodTeam keeps consequential transitions bound to explicit state and human decisions.

A typical path is:

```text
Project conversation
  ↓
read-only understanding / proposed work
  ↓
explicit human confirmation
  ↓
workflow + bounded workers
  ↓
tests / CI / smoke / reviewer evidence
  ↓
human review decision
  ↓
ledger + provenance + memory
```

The same project conversation remains available across onboarding and workflow execution; starting a run does not replace the user's original request with a model rewrite.

## Local-first state

GoodTeam keeps authoritative runtime and project records in local workspace/project storage, including workflow state, checkpoints, artifacts, worker jobs, action history, provenance, task ledgers, and memory. Exact storage locations are feature-specific; for example, the durable Task Assistant session is workspace-scoped under `state/projects/<PROJECT_KEY>/` rather than written into the imported repository.

External systems may participate, but they are not the only copy of the delivery record.

## Development

Typical repository checks are:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests -q
.venv/bin/ruff check src/goodteam_os/ tests/
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
git diff --check
```

Use the risk-tiered verification policy in [`docs/testing-strategy.md`](docs/testing-strategy.md) rather than running expensive suites mechanically for every change.

For branch and worktree cleanup rules, read [`docs/development-branch-hygiene.md`](docs/development-branch-hygiene.md).

## Documentation

Use documentation in this order when sources disagree:

1. **Code and tests** for executable behavior.
2. **Stable manuals** under `docs/` for current product/operator contracts.
3. [`docs/status.md`](docs/status.md) for short-lived current orientation.
4. Historical plans, audits, research, progress logs, and generated OpenWiki pages for design history and evidence.

Start here:

- [`docs/README.md`](docs/README.md) — documentation map, authority, and lifecycle rules.
- [`docs/status.md`](docs/status.md) — current product surface and active-design boundary.
- [`docs/explainer.md`](docs/explainer.md) — concise product explanation.
- [`docs/architecture.md`](docs/architecture.md) — current system architecture.
- [`docs/operator-guide.md`](docs/operator-guide.md) — day-to-day operation.
- [`docs/review-and-approval-guide.md`](docs/review-and-approval-guide.md) — review and approval semantics.
- [`docs/live-smoke-playbook.md`](docs/live-smoke-playbook.md) — isolated live-smoke guidance.
- [`docs/testing-strategy.md`](docs/testing-strategy.md) — verification policy.

Historical implementation records remain useful for traceability, but a completed plan or old progress snapshot is not proof of the current contract.
