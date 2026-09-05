# GoodTeam Documentation

This directory is the canonical human-maintained documentation entry point for GoodTeam.

GoodTeam keeps both **current contracts** and **project history**. They serve different purposes and must not be treated as equally authoritative. Stable manuals describe what contributors and operators should rely on today; plans, progress logs, audits, research notes, and generated OpenWiki pages preserve design and implementation history.

For the short current-state summary, start with [`status.md`](status.md).

## Start Here

| Need | Read |
| --- | --- |
| Understand the current product surface | [`status.md`](status.md) |
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
| Operate the repository CI runner | [`self-hosted-ci-runner.md`](self-hosted-ci-runner.md) |
| Work as an automated agent | [`../AGENTS.md`](../AGENTS.md), [`../CLAUDE.md`](../CLAUDE.md) |
| Clean up branches and PR worktrees | [`development-branch-hygiene.md`](development-branch-hygiene.md) |
| Inspect historical implementation footprints | [`current-progress.md`](current-progress.md), [`mainline-progress.md`](mainline-progress.md) |
| Read historical plans and audits | [`plans/`](plans/), [`archive/`](archive/) |

## Documentation authority

Use this order when sources disagree:

1. **Executable behavior** — code and tests.
2. **Stable manuals** — the references listed below.
3. **`status.md`** — short-lived current product orientation.
4. **Historical material** — plans, progress logs, audits, research, dogfood evidence.
5. **Generated documentation** — `openwiki/` is a projection, not a source of truth.

A historical document can explain *why* a decision was made, but it must not silently override a stable contract.

## Stable manuals

### Product and architecture

- [`status.md`](status.md): current product surface, current control-plane model, and active-design boundary.
- [`explainer.md`](explainer.md): concise visual introduction for new readers, contributors, and design partners.
- [`architecture.md`](architecture.md): current system context, Task Assistant interaction layer, workflow/worker boundaries, approval/evidence model, memory/provenance, and current-vs-active-design boundary.

### Operator manuals

- [`operator-guide.md`](operator-guide.md): day-to-day GoodTeam operating loop.
- [`review-and-approval-guide.md`](review-and-approval-guide.md): approval-gate semantics, evidence quality, and reviewer provenance.
- [`live-smoke-playbook.md`](live-smoke-playbook.md): Tier 4 live-smoke scope, isolation, evidence, and failure handling.

### Reference manuals

- [`config-reference.md`](config-reference.md): currently implemented project config fields and safety rules.
- [`worker-backends.md`](worker-backends.md): backend lifecycle, dispatch packet shape, readiness checks, result semantics, and failure handling.
- [`reviewer-profiles.md`](reviewer-profiles.md): reviewer role contracts, provenance, dispatch, retry, and evidence projection.
- [`repository-context-provider.md`](repository-context-provider.md) and [`project-onboarding-runner.md`](project-onboarding-runner.md): snapshot-bound repository retrieval and bounded onboarding execution.
- [`execution-identity.md`](execution-identity.md): issue/attempt/execution/branch/PR/SHA/CI/ledger identity chain.
- [`memory-model.md`](memory-model.md): local run-history memory model.
- [`provenance-ledger.md`](provenance-ledger.md): run/output/log/snapshot/decision tracing.

### Contributor process

- [`testing-strategy.md`](testing-strategy.md): risk-tiered verification matrix for PRs.
- [`development-branch-hygiene.md`](development-branch-hygiene.md): branch, PR, and worktree cleanup rules.
- [`self-hosted-ci-runner.md`](self-hosted-ci-runner.md): persistent CI runner security boundary, resource limits, provisioning, and operations.
- [`../AGENTS.md`](../AGENTS.md) and [`../CLAUDE.md`](../CLAUDE.md): agent and repository operating rules.

## Historical and rolling memory

These files preserve useful decisions and evidence, but they are not automatically current reference material.

- [`current-progress.md`](current-progress.md): append-only implementation footprint history. Despite the legacy filename, do not use it as the current product contract; use [`status.md`](status.md).
- [`mainline-progress.md`](mainline-progress.md): older mainline progress archive.
- [`backlog.md`](backlog.md): historical product parking lot. GitHub Issues and pull requests are the live work queue.
- [`product-vision.md`](product-vision.md): product thesis and moat narrative; concrete status claims may be historical.
- [`plans/`](plans/): active or historically retained PRDs, implementation plans, handoffs, and patch prompts. A plan is not proof that behavior exists.
- [`archive/`](archive/): explicitly superseded or retired documents retained for traceability.
- [`audit/`](audit/) and [`dogfood/`](dogfood/): point-in-time evidence and milestone records.
- `openwiki/`: generated documentation projection. Update canonical `docs/` first, then regenerate OpenWiki when useful.

## Lifecycle rules

When documentation changes state:

- **Current contract changes:** update the relevant stable manual.
- **Implementation completes:** keep the implementation record, but stop presenting it as a pending plan.
- **A stable successor exists:** move the superseded design to `docs/archive/` or delete it only when it has no remaining historical value.
- **A point-in-time report becomes stale:** archive or remove it; do not leave it in the stable-doc path with current-sounding language.
- **A live task is created:** track it in GitHub Issues/PRs. Do not grow `backlog.md` into a second issue tracker.
- **Generated docs drift:** fix `docs/` first. Never hand-maintain `openwiki/` as a second canonical source.

## Active design boundary

The Role → TeamRole → Worker control-plane separation is active design work. Until its implementation lands, [`config-reference.md`](config-reference.md) and [`worker-backends.md`](worker-backends.md) describe the current configuration model. Historical discussion must not be promoted into stable reference text before runtime and persistence contracts exist.

## Diagram policy

Use Mermaid or another text-source diagram format whenever possible. Diagrams should be reviewable in normal GitHub diffs and should not depend on binary images as the only source of truth.

When adding or changing diagrams:

1. Keep the source in Markdown.
2. Prefer stable concepts over incidental implementation details.
3. Label planned or gated behavior clearly.
4. Link to the source modules or docs that make the diagram actionable.

## Documentation verification

Documentation-only PRs are normally Tier 0 under [`testing-strategy.md`](testing-strategy.md). Run higher-tier validation when a document changes executable commands, agent rules, PR templates, CI workflows, config examples that are tested, frontend routes, or live-smoke procedures that affect operator safety.
