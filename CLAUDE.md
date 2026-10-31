# CLAUDE.md — GoodTeam current contributor guidance

First read [AGENTS.md](AGENTS.md). The original intended product is an
**AI engineering team that takes ownership of a project's iteration loop**:
inspect the current project and its goals, choose next valuable work,
dispatch specialized Agents, review and fix real results, update the
project's durable backlog/knowledge, then choose and continue again.
Not merely execute one supplied issue.

The only two classes of development work are recorded in
[roadmap #381](https://github.com/LukeJiaoR/GoodTeam/issues/381):
required for that full loop, or not required. Use
[#419](https://github.com/LukeJiaoR/GoodTeam/issues/419) for observed real
delivery/iteration acceptance and [#540](https://github.com/LukeJiaoR/GoodTeam/issues/540)
for the already implemented bounded Goal continuation. Its current
same-Goal missing-criteria logic does **not** yet establish autonomous
project-wide next-iteration selection.

## Workflow for contributors

- Planner/architect/reviewer: observe code and real task/project effects;
  find what prevents GoodTeam from independently picking and completing
  the *next iteration*, not just what edge condition of Agent governance
  remains untested.
- Implementer (Codex or another configured Agent): make the smallest
  change that unblocks the project loop; use the existing Manager,
  Task/Goal, Kanban, Worker, Review, Memory and recovery capabilities
  rather than inventing another orchestration layer.
- QA: independently verify both the current real delivery and the
  resulting updated project priorities and next iteration choice.
  Simulated/fake Workers are regression evidence only.
- Existing project-level approval, sandbox, exact-source review, CI,
  destructive-action and spending boundaries remain binding. Autonomous
  iteration does not grant unbounded authority.

The old `docs/plans/workflow-role-division.md` and
`docs/product-vision.md` contain historical roles, model assignments and
market positioning. Read for background as needed, not as a mandate
to use old model versions, generate every PRD/RFC, or insert all
approval gates into each small iteration. Current code and tests are
authoritative; [testing strategy](docs/testing-strategy.md) and
[candidate validation](docs/candidate-validation.md) remain in effect.

**Project acceptance:** on an existing project with multiple real work
candidates, GoodTeam chooses the next useful change from current
evidence, executes and verifies it, reconciles the backlog and
project memory, then autonomously chooses and advances another
appropriate iteration (or states an evidence-based stop/gate).
A manually prequeued pair of Issues does not prove this capability.
