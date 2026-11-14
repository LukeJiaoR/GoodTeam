---
name: planning-handoff
description: Turn an agreed objective and relevant evidence into an actionable, non-authorizing plan.
---

# Bounded execution planning

Use this method when concrete task decomposition is requested. Planning is professional advice to the Manager, not a mandatory stage for every task or a substitute for implementation.

## Ground the plan
Read the task purpose, discussion consensus, constraints, tradeoffs, known solution/evidence, open questions and completion conditions. Retain the original user request as provenance. Identify missing facts that materially affect decomposition rather than inventing assumptions as decisions. Include blocked work as context for possible investigation or unblocking; planning it does not make it executable.

## Decompose for useful handoff
Propose bounded work items with purpose, scope, dependencies, relevant Context, completion evidence and professional Role. Explain ordering where dependencies require it and identify independent work without assuming capacity or permissions. Do not impose a fixed task count or a universal Researcher-to-Planner-to-Worker pipeline. Avoid writing implementation, running tests or approving technical work as part of a planning-only task.

## Use the project that already exists
Start from the machine and project's existing tools and dependencies. Determine
whether the requested work actually needs an environment change; a configured
setup command alone is not a reason to run it. When a change is needed, describe
it as an ordinary task and put only the work that depends on it behind that task.
Reuse existing task records and results rather than repeating completed work.
Worktrees separate concurrent source changes, not project dependency versions.
There is no required hidden environment directory or fixed number of preparation
rounds. Use execution errors and new results to revise the plan.

## Return advice, not execution claims
Return a usable plan, alternatives/tradeoffs where consequential, remaining uncertainty and useful partial results. Preserve source task/job attribution. Read references/plan-review.md to check that suggestions have not been presented as authorization, completed work or a replacement for the Manager's latest judgment.

When an environment task is needed, name the actual project installation it
changes and include that location in the task context. A command run only in a
source worktree does not prepare shared dependencies. Use the project's package
manager and current task manifests to target the chosen existing installation;
do not create a second source copy or assume a private worktree installation is
shared. Report an actual execution/access failure through the ordinary result
path rather than inventing another pre-dispatch readiness check.
