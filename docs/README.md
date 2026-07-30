# GoodTeam Documentation

This directory is the stable documentation entry point for GoodTeam.

GoodTeam also keeps rich historical material in `docs/plans/`,
`docs/current-progress.md`, and `docs/backlog.md`. Those files are valuable
project memory, but they are not always current reference material. Use this
index to find the right document for the job.

## Start Here

| Need | Read |
| --- | --- |
| Understand what GoodTeam is | [`../README.md`](../README.md), [`explainer.md`](explainer.md) |
| Understand the system architecture | [`architecture.md`](architecture.md) |
| Run GoodTeam day to day | [`operator-guide.md`](operator-guide.md) |
| Review approval gates | [`review-and-approval-guide.md`](review-and-approval-guide.md) |
| Run live smoke safely | [`live-smoke-playbook.md`](live-smoke-playbook.md) |
| Configure a project | [`config-reference.md`](config-reference.md) |
| Understand worker dispatch | [`worker-backends.md`](worker-backends.md) |
| Understand bounded existing-project onboarding | [`repository-context-provider.md`](repository-context-provider.md), [`project-onboarding-runner.md`](project-onboarding-runner.md) |
| Understand execution identity | [`execution-identity.md`](execution-identity.md) |
| Understand memory and provenance | [`memory-model.md`](memory-model.md), [`provenance-ledger.md`](provenance-ledger.md) |
| Choose the right PR verification | [`testing-strategy.md`](testing-strategy.md) |
| Work as an automated agent | [`../AGENTS.md`](../AGENTS.md), [`../CLAUDE.md`](../CLAUDE.md) |
| Clean up branches and PR worktrees | [`development-branch-hygiene.md`](development-branch-hygiene.md) |
| Inspect current project history | [`current-progress.md`](current-progress.md), [`backlog.md`](backlog.md) |
| Read design handoffs and historical plans | [`plans/`](plans/) |

## Documentation Map

```mermaid
flowchart TD
    Reader[Reader]
    Reader --> Operator[Operator]
    Reader --> Contributor[Contributor or agent]
    Reader --> External[External reader]

    Operator --> RootReadme[README.md]
    Operator --> OperatorGuide[docs/operator-guide.md]
    Operator --> ReviewGuide[docs/review-and-approval-guide.md]
    Operator --> SmokePlaybook[docs/live-smoke-playbook.md]
    Operator --> ConfigReference[docs/config-reference.md]

    Contributor --> Agents[AGENTS.md]
    Contributor --> Claude[CLAUDE.md]
    Contributor --> Architecture[docs/architecture.md]
    Contributor --> WorkerBackends[docs/worker-backends.md]
    Contributor --> ExecutionIdentity[docs/execution-identity.md]
    Contributor --> MemoryModel[docs/memory-model.md]
    Contributor --> ProvenanceLedger[docs/provenance-ledger.md]
    Contributor --> Testing[docs/testing-strategy.md]
    Contributor --> Hygiene[docs/development-branch-hygiene.md]

    External --> Explainer[docs/explainer.md]
    External --> ProductVision[docs/product-vision.md historical thesis]

    Plans[docs/plans/*] --> HistoricalPlans[Design and handoff history]
    Current[docs/current-progress.md] --> RollingMemory[Rolling project memory]
    Backlog[docs/backlog.md] --> ProductParkingLot[Product decisions and gated work]

    classDef stable fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20;
    classDef historical fill:#eef3ff,stroke:#5c6bc0,color:#1a237e;

    class RootReadme,OperatorGuide,ReviewGuide,SmokePlaybook,ConfigReference stable;
    class Agents,Claude,Architecture,WorkerBackends,ExecutionIdentity,MemoryModel,ProvenanceLedger,Testing,Hygiene,Explainer stable;
    class ProductVision,Plans,HistoricalPlans,Current,RollingMemory,Backlog,ProductParkingLot historical;
```

## Stable Manuals

### Visual And Orientation

- [`explainer.md`](explainer.md): concise visual introduction for new readers,
  contributors, and design partners.
- [`architecture.md`](architecture.md): system context, component architecture,
  workflow, approval/evidence, execution identity, memory/provenance, and
  current-vs-planned diagrams.

### Operator Manuals

- [`operator-guide.md`](operator-guide.md): day-to-day GoodTeam operating loop.
- [`review-and-approval-guide.md`](review-and-approval-guide.md): approval gate
  decision semantics, evidence quality, and reviewer provenance guidance.
- [`live-smoke-playbook.md`](live-smoke-playbook.md): Tier 4 live-smoke scope,
  isolation, evidence, and failure handling.

### Reference Manuals

- [`config-reference.md`](config-reference.md): project config fields and safety
  rules.
- [`worker-backends.md`](worker-backends.md): backend lifecycle, dispatch packet
  shape, readiness checks, result semantics, and failure handling.
- [`repository-context-provider.md`](repository-context-provider.md) and
  [`project-onboarding-runner.md`](project-onboarding-runner.md): snapshot-bound
  repository retrieval and the non-promoting Track 2A onboarding-runner boundary.
- [`execution-identity.md`](execution-identity.md): issue/attempt/execution/
  branch/PR/SHA/CI/ledger identity chain.
- [`memory-model.md`](memory-model.md): local run-history memory model.
- [`provenance-ledger.md`](provenance-ledger.md): run/output/log/snapshot/
  decision tracing.

### Contributor Process

- [`testing-strategy.md`](testing-strategy.md): risk-tiered verification matrix
  for PRs.
- [`development-branch-hygiene.md`](development-branch-hygiene.md): branch, PR,
  and worktree cleanup rules.
- [`../AGENTS.md`](../AGENTS.md) and [`../CLAUDE.md`](../CLAUDE.md): agent and
  project operating rules.

## Historical And Rolling Memory

These documents preserve useful decisions and history, but may include stale
status snapshots or completed implementation plans.

- [`product-vision.md`](product-vision.md): product thesis and moat narrative.
  Treat specific status counts as historical unless a newer progress document
  says otherwise.
- [`current-progress.md`](current-progress.md): rolling project footprint log.
- [`mainline-progress.md`](mainline-progress.md): mainline progress archive.
- [`backlog.md`](backlog.md): gated work, product parking lot, and resolved
  backlog notes.
- [`plans/`](plans/): PRDs, design notes, implementation handoffs, and patch
  prompts. These are source material for stable manuals, not automatically
  current contracts.

## Diagram Policy

Use Mermaid or another text-source diagram format whenever possible. Diagrams
should be reviewable in normal GitHub diffs and should not depend on binary
images as the only source of truth.

When adding or changing diagrams:

1. Keep the source in Markdown.
2. Prefer stable concepts over incidental implementation details.
3. Label planned or gated behavior clearly.
4. Link to the source modules or docs that make the diagram actionable.

## Documentation Tiers

Documentation PRs usually fall under Tier 0 in
[`testing-strategy.md`](testing-strategy.md). Run higher-tier validation when a
document changes executable commands, agent rules, PR templates, CI workflows,
config examples that are tested, frontend routes, or live-smoke procedures that
affect operator safety.
