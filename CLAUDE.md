# CLAUDE.md — GoodTeam contributor guidance

Read [AGENTS.md](AGENTS.md) first. The **only current development priority
classification** is the two-class [roadmap #381](https://github.com/LukeJiaoR/GoodTeam/issues/381):
necessary for a real bounded software-delivery result, or not necessary.
A task is not justified by an old design, a large amount of existing code,
or a speculative Agent-control edge case.

## Current source of truth

- Executable behavior: current `main` source, relevant tests and actual
  exact-version run records.
- Product delivery gate and observed defects: [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419).
- Existing bounded Goal continuation: [#540](https://github.com/LukeJiaoR/GoodTeam/issues/540).
- Implementation and approval rules: [AGENTS.md](AGENTS.md),
  [testing strategy](docs/testing-strategy.md), and
  [candidate validation](docs/candidate-validation.md).

`docs/current-progress.md`, `docs/mainline-progress.md`,
`docs/product-vision.md`, `docs/plans/` and
[the earlier role-division playbook](docs/plans/workflow-role-division.md)
are **historical context**, not instructions to produce new work or to
restore a fixed multi-role development ceremony.

## How to contribute

When acting as planner/reviewer, inspect current code and test evidence,
identify a real barrier to the bounded user journey, then make the
smallest testable implementation handoff. Do not automatically create
PRD/RFC/architecture/QA/retro documents, extra worker roles, platform
abstractions or new gates for an ordinary bug fix. Use the existing
`fix` profile or established workflow where appropriate; do not
assume it is fully accepted without a real task check.

When implementing with Codex or another coding Agent, follow the current
task's explicit scope and existing repository instructions. Choose
model/runtime from the actual configured capabilities; **do not rely on
historical hardcoded model versions or tools**. Isolate writes and
preserve user files and original reports. An independent reviewer and
real task-relevant checks remain important, but a generic full feature
matrix is not a substitute for the user-facing result.

The product's delivery design remains:
original user goal → Manager/Team → real Workers → relevant QA/review/fix
→ verifiable output → meaningful human decision → recoverable history.
Treat correctness controls as support for this delivery, not as a
separate expansion objective.
