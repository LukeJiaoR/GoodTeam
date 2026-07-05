# GoodTeam Explainer

GoodTeam is a local-first AI engineering team OS. It coordinates specialized AI
workers, project tools, CI, review evidence, and memory into one workflow ledger
that a human can inspect and approve.

This page is written as a concise, visual introduction for new users,
contributors, and design partners. For implementation details, see
[`architecture.md`](architecture.md). For PR verification policy, see
[`testing-strategy.md`](testing-strategy.md).

## The Short Version

Most coding tools optimize one agent. GoodTeam optimizes the delivery system
around many agents.

A delivery is not just code. A delivery includes product intent, architecture,
implementation, QA, review, CI evidence, approval, release, and memory. GoodTeam
turns those steps into an owned local protocol:

```mermaid
flowchart LR
    Idea[Idea] --> PRD[PRD]
    PRD --> RFC[RFC / architecture]
    RFC --> Gate1[Human gate]
    Gate1 --> Build[Implementation workers]
    Build --> QA[QA and review]
    QA --> Evidence[CI / smoke / provenance evidence]
    Evidence --> Gate2[Human gate]
    Gate2 --> Land[Land and cleanup]
    Land --> Memory[Record memory]
    Memory --> Next[Next delivery is sharper]
```

The human stays in charge. The system collects evidence, runs bounded workers,
and makes the decision surface legible.

## What GoodTeam Owns

```mermaid
mindmap
  root((GoodTeam))
    Control plane
      Workflow state
      Approval gates
      Action ledger
      Human decisions
    Worker backends
      Codex
      Claude
      fake
      human
      noop
    Evidence and provenance
      CI status
      Smoke results
      Reviewer output
      PR manifests
      Audit events
    Memory
      events WAL
      Role horizons
      Project memory
      Future cross-project recall
    Review Workbench
      Evidence snapshot
      Reviewer provenance
      Advisory recommendation
      Human approval
    Remote supervision
      Telegram cards
      Commands
      Decision notes
    Verification
      Focused tests
      Full suite
      Live smoke
      Risk tiers
    Future moats
      Collaboration graph
      Compounding memory
      Expert synthesis
```

## The Control-Plane Idea

GoodTeam does not try to replace every tool. It gives the tools a protocol.

```mermaid
flowchart TD
    GoodTeam[GoodTeam control plane]
    State[Owned state]
    Evidence[Owned evidence model]
    Decision[Human decision]

    GitHub[GitHub]
    CI[CI]
    Telegram[Telegram]
    Playwright[Playwright]
    Agents[Agent CLIs]

    GitHub -->|PR metadata| GoodTeam
    CI -->|check results| GoodTeam
    Telegram -->|remote decisions| GoodTeam
    Playwright -->|observed live behavior| GoodTeam
    Agents -->|patches and reports| GoodTeam

    GoodTeam --> State
    GoodTeam --> Evidence
    Evidence --> Decision
    State --> Decision
```

External tools are replaceable adapters. GoodTeam's ledger is the durable record
of what happened and why a human approved it.

## Why Gates Matter

Autonomous coding without review can create fast ambiguity. GoodTeam adds
explicit gates where the system must stop and explain itself.

At a gate, the operator should be able to answer:

- What artifact am I approving?
- What changed since the last gate?
- Which tests, CI checks, smoke results, or reviewer findings exist?
- Is the recommendation advisory or blocking?
- What happens if I approve, request changes, or reject?

```mermaid
flowchart LR
    Artifact[Artifact] --> Evidence[Evidence snapshot]
    Evidence --> Recommendation[Advisory recommendation]
    Recommendation --> Human[Human operator]
    Human --> Approve[Approve]
    Human --> Changes[Request changes]
    Human --> Reject[Reject]
    Approve --> Advance[Advance workflow]
    Changes --> Loop[Loop back with note]
    Reject --> Stop[Stop or re-plan]
```

## What Happens During A Run

```mermaid
sequenceDiagram
    autonumber
    actor User as Operator
    participant GT as GoodTeam
    participant Files as Local state/artifacts
    participant Worker as Worker backend
    participant GH as GitHub/CI
    participant Review as Review surface

    User->>GT: Start from idea or configured project
    GT->>Files: Create run state and artifacts
    GT->>Review: Present PRD/RFC approval gate
    User->>Review: Approve or request changes
    GT->>Worker: Dispatch scoped work packet
    Worker->>Files: Write result, logs, and provenance handles
    Worker->>GH: Create or update PR when scoped
    GH-->>GT: CI status and head SHA evidence
    GT->>Review: Present evidence-backed gate
    User->>Review: Decide
    GT->>Files: Record decision and memory
```

## What Makes It Local-First

Local-first does not mean disconnected. It means owned.

GoodTeam can use cloud or networked tools when the operator chooses them, but the
source of truth remains under local project files:

- workflow state and checkpoints,
- generated artifacts,
- worker job results,
- approval action events,
- task ledgers,
- memory events,
- provenance manifests.

This matters because engineering organizations need to answer not only "what did
the agent change?" but also "why was that change accepted?"

## How To Read The Repo

```mermaid
flowchart TD
    Repo[GoodTeam repo]
    Product[Product entry]
    StableDocs[Stable docs]
    Runtime[Runtime code]
    Frontend[Frontend]
    Historical[Historical plans and memory]

    Repo --> Product
    Repo --> StableDocs
    Repo --> Runtime
    Repo --> Frontend
    Repo --> Historical

    Product --> Readme[README.md]
    StableDocs --> DocsReadme[docs/README.md]
    StableDocs --> Architecture[docs/architecture.md]
    StableDocs --> Testing[docs/testing-strategy.md]
    Runtime --> Web[web.py]
    Runtime --> Graph[graph.py]
    Runtime --> State[state.py]
    Runtime --> Workers[workers.py]
    Runtime --> Memory[memory.py]
    Frontend --> App[frontend React app]
    Historical --> Plans[docs/plans/*]
    Historical --> Progress[docs/current-progress.md]
    Historical --> Backlog[docs/backlog.md]
```

For a new reader, start with:

1. [`../README.md`](../README.md) for setup and product overview.
2. [`docs/README.md`](README.md) for the docs map.
3. [`architecture.md`](architecture.md) for the system model.
4. [`testing-strategy.md`](testing-strategy.md) before contributing changes.

## Web Explainer Direction

This document is intentionally written so it can later become a dashboard route
such as `/explainer` or `/docs`. The first useful version should stay Markdown
only, so the diagrams and narrative are reviewable as source.

A future UI slice can adapt this page into a product-facing explainer if the
dashboard needs an onboarding surface for operators or design partners.
