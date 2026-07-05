# GoodTeam Architecture

GoodTeam is a local-first control layer for AI software delivery. It owns the
workflow ledger, approval state, artifacts, memory, and provenance. External
systems such as GitHub, CI, Telegram, Playwright, and coding agents are adapters:
they produce evidence or perform bounded work, but they do not own GoodTeam's
source of truth.

This document is a stable architecture overview. Historical design context lives
in `docs/plans/`, `docs/product-vision.md`, `docs/current-progress.md`, and
`docs/backlog.md`.

## System Context

```mermaid
flowchart LR
    Human[Human operator]
    GoodTeam[GoodTeam local control layer]
    Target[Target project workspace]

    subgraph LocalFiles[Owned local files]
        State[state/projects/*]
        Artifacts[artifacts/runs/*]
        Memory[.goodteam/memory/*]
        Tasks[.goodteam/tasks/*]
        Provenance[.goodteam/provenance/*]
    end

    subgraph WorkerBackends[Worker backends]
        Codex[Codex CLI]
        Claude[Claude CLI]
        Fake[fake]
        HumanBackend[human]
        Noop[noop]
    end

    subgraph Adapters[External adapters]
        GitHub[GitHub PRs]
        CI[GitHub Actions / CI]
        Telegram[Telegram review client]
        Playwright[Playwright / live smoke]
    end

    Human -->|starts, reviews, lands| GoodTeam
    GoodTeam -->|reads and writes| LocalFiles
    GoodTeam -->|configures and inspects| Target
    GoodTeam -->|dispatches bounded jobs| WorkerBackends
    WorkerBackends -->|patches, reports, stdout/stderr| Target
    GoodTeam -->|opens, binds, inspects| GitHub
    GitHub -->|head SHA and checks| CI
    CI -->|status evidence| GoodTeam
    GoodTeam -->|approval cards and commands| Telegram
    Telegram -->|human decisions| GoodTeam
    GoodTeam -->|isolated live verification| Playwright
    Playwright -->|observed evidence| GoodTeam

    LocalFiles -. source of truth .-> GoodTeam
    Adapters -. evidence producers .-> GoodTeam
```

Key rule: the GoodTeam ledger remains authoritative. Adapters can fail, retry,
or be replaced without changing the ownership model.

## Component Architecture

```mermaid
flowchart TD
    CLI[goodteam_os.cli]
    Web[web.py API and dashboard server]
    Frontend[frontend React dashboard]
    Graph[graph.py workflow runner]
    State[state.py state store]
    Workers[workers.py backend registry and jobs]
    Memory[memory.py WAL and retrieval]
    Staging[staging.py delivery contracts]
    Tasks[tasks.py local task board]
    Telegram[telegram.py review client]
    Projects[projects.py project config discovery]
    Provenance[provenance manifests and action events]

    CLI --> Web
    CLI --> Graph
    CLI --> Projects

    Frontend -->|/api/*| Web
    Web --> State
    Web --> Graph
    Web --> Workers
    Web --> Memory
    Web --> Staging
    Web --> Tasks
    Web --> Projects
    Web --> Telegram
    Web --> Provenance

    Graph --> State
    Graph --> Workers
    Graph --> Staging
    Graph --> Memory
    Graph --> Provenance

    Workers --> Memory
    Workers --> Provenance
    Telegram --> Web

    State --> LocalState[(state JSON and checkpoints)]
    Workers --> JobFiles[(worker job dirs)]
    Memory --> MemoryFiles[(events.jsonl and horizons)]
    Staging --> Evidence[(delivery evidence)]
    Tasks --> TaskFiles[(tasks.json)]
    Provenance --> ProvFiles[(PR/job/review lineage)]
```

## Module Responsibilities

| Module / area | Responsibility |
| --- | --- |
| `src/goodteam_os/cli.py` | CLI entry point for init, start, serve, doctor, and inspection commands. |
| `src/goodteam_os/web.py` | HTTP API, dashboard state projection, approval decisions, review workbench helpers, and same-origin guarded actions. |
| `src/goodteam_os/graph.py` | Workflow progression, gate transitions, worker scheduling, and run advancement. |
| `src/goodteam_os/state.py` | Durable run state, approval decisions, checkpoints, and project current state. |
| `src/goodteam_os/workers.py` | Worker backend registry, job store, dispatch packets, stdout/stderr/result handling, and backend readiness. |
| `src/goodteam_os/memory.py` | Project-local and role-scoped memory, WAL events, retrieval, and pickup context. |
| `src/goodteam_os/staging.py` | Delivery contract configuration and staging acceptance evidence. |
| `src/goodteam_os/tasks.py` | Local task board projection and task persistence. |
| `src/goodteam_os/projects.py` | Project config discovery and root resolution. |
| `src/goodteam_os/telegram.py` | Telegram commands, approval cards, remote review sessions, and decision notes. |
| `frontend/` | React dashboard that consumes `/api/*` and renders project state, approvals, workers, memory, and Dev Loop surfaces. |
| `.goodteam/` under target projects | Project-owned tasks, memory, provenance, and local GoodTeam state artifacts. |

## End-To-End Workflow

```mermaid
sequenceDiagram
    autonumber
    actor Human as Human operator
    participant CLI as goodteam-os CLI / Web UI
    participant Projects as Project config
    participant Graph as Workflow runner
    participant State as StateStore
    participant Workers as Worker backends
    participant Evidence as Evidence and provenance
    participant Review as Review Workbench
    participant GitHub as GitHub / CI

    Human->>CLI: init project or select existing config
    CLI->>Projects: resolve project root and config
    Human->>CLI: start idea / run
    CLI->>Graph: create initial workflow state
    Graph->>State: write current state and checkpoint
    Graph->>Evidence: write PRD/RFC artifacts
    Graph-->>Human: pause at approval gate
    Human->>Review: inspect artifact, evidence, reviewers
    Human->>Review: approve / request changes / reject
    Review->>State: write approval decision and action event
    State-->>Graph: gate decision available
    Graph->>Workers: dispatch bounded implementation/review/QA jobs
    Workers->>Evidence: write results, logs, provenance handles
    Workers->>GitHub: create or update PRs when scoped
    GitHub-->>Evidence: CI status and head SHA evidence
    Evidence-->>Review: render current gate evidence
    Human->>CLI: land approved PR and clean branch/worktree
    CLI->>Evidence: record land/cleanup/provenance
```

## Approval And Evidence Model

```mermaid
sequenceDiagram
    autonumber
    participant Graph as Workflow runner
    participant Artifact as Artifact store
    participant Evidence as Evidence snapshot builders
    participant Reviewers as Reviewer jobs and provenance
    participant Web as Web Review Workbench
    participant TG as Telegram review client
    actor Human as Human operator
    participant Ledger as Action ledger / state

    Graph->>Artifact: generate gate artifact
    Graph->>Evidence: build CI/smoke/QA/blocker snapshot
    Evidence->>Reviewers: read reviewer provenance and recommendations
    Evidence-->>Web: actionable review object
    Evidence-->>TG: approval card digest
    Web-->>Human: evidence, recommendation, provenance
    TG-->>Human: remote approval card and commands
    Human->>Web: approve / request changes / reject
    Human->>TG: optional Telegram decision or note
    Web->>Ledger: write approval_decided event
    TG->>Ledger: write approval_decided event with origin metadata
    Ledger-->>Graph: decision unblocks advance or loop
```

Recommendations and reviewer outputs are advisory. The human decision remains the
approval authority.

## Execution Identity And PR/CI Binding

```mermaid
flowchart TD
    Issue[Issue number]
    Attempt[Attempt number]
    ExecutionID[Execution ID]
    Branch[Deterministic branch]
    PR[Pull request]
    HeadSHA[PR head SHA]
    CIRun[CI workflow run]
    Ledger[Execution ledger]
    Idempotency[Idempotency keys]

    Issue --> ExecutionID
    Attempt --> ExecutionID
    ExecutionID --> Branch
    Branch --> PR
    PR --> HeadSHA
    HeadSHA --> CIRun
    CIRun --> Ledger
    ExecutionID --> Ledger
    ExecutionID --> Idempotency
    Idempotency -->|prevents duplicate work| Ledger

    classDef identity fill:#fff8e1,stroke:#f9a825,color:#5d4037;
    classDef evidence fill:#eef3ff,stroke:#5c6bc0,color:#1a237e;
    class Issue,Attempt,ExecutionID,Branch,Idempotency identity;
    class PR,HeadSHA,CIRun,Ledger evidence;
```

This identity chain is why execution and binding tests are stricter than normal
helper tests. Loose identifiers can bind evidence to the wrong work.

## Memory And Provenance Graph

```mermaid
flowchart LR
    Run[Workflow run]
    WorkerJob[Worker job]
    TaskContract[task.md / constraints.md]
    Result[result.md]
    Review[pre-review and review rationale]
    PR[Pull request]
    Manifest[provenance manifest]
    WAL[events.jsonl]
    RoleMemory[role-scoped memory horizons]
    ProjectMemory[project memory]
    FutureCrossProject[future cross-project memory]

    Run --> WorkerJob
    WorkerJob --> TaskContract
    WorkerJob --> Result
    Result --> Review
    Review --> PR
    PR --> Manifest
    TaskContract --> Manifest
    Result --> Manifest
    Review --> Manifest
    Manifest --> WAL
    WAL --> RoleMemory
    WAL --> ProjectMemory
    ProjectMemory -. planned projection .-> FutureCrossProject
    RoleMemory -. planned projection .-> FutureCrossProject
```

The current moat is ownership: project-local state, artifacts, and memory are
portable files. The roadmap is compounding: every delivery should make future
runs cheaper, faster, and better grounded.

## Current vs Planned Boundary

```mermaid
flowchart TD
    Current[Implemented now]
    Planned[Planned or gated]

    Current --> LocalLedger[Local state, checkpoints, action events]
    Current --> ApprovalGates[Human approval gates]
    Current --> WorkerRegistry[Pluggable worker backend registry]
    Current --> ReviewWorkbench[Review Workbench evidence surfaces]
    Current --> TelegramReview[Telegram remote supervision]
    Current --> TestingStrategy[Risk-tiered verification strategy]

    Planned --> VisualDocs[Full visual docs system]
    Planned --> OperatorGuide[Stable operator guide]
    Planned --> ConfigReference[Config reference]
    Planned --> LiveSmokePlaybook[Live smoke playbook]
    Planned --> CrossProjectMemory[Cross-project memory]
    Planned --> RealParallelWorkers[Real parallel worker fan-in]
    Planned --> WebExplainer[Dashboard /docs or /explainer route]
```

When changing architecture docs, keep this boundary explicit. Do not document
planned behavior as if it already exists.
