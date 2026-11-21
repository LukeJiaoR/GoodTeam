# GoodTeam Operator Guide

## Start a project

Run `goodteam restart` and open the Projects page. Import the local repository
and open Work. Project readiness records repository facts and preparation.
Configure the intended Worker and verify the Manager runtime in Team before
a real run.

```sh
goodteam restart
goodteam status
```

## Select and execute

Use **New task** to save a request or discuss the scope in the Task Assistant
conversation. Use **Manage tasks** to select a queued draft. Confirm the exact
draft to start the batch. Inspect Worker status in Work.

For work across several tasks, use the
[project continuation controls](project-continuation.md). Set the authorized
task inventory and iteration budget before confirming.

## Verify and deliver

Inspect each original Worker report and the retained candidate.
Use [batch quality checks](project-batch-quality.md) for configured host
checks, independent review and bounded correction.
Use [batch recovery](project-batch-recovery.md) for a proven failed Worker.
An unknown process outcome needs reconciliation before the batch can release
that slot.

Use the repository’s authorized Git process to land the reviewed candidate.
Record delivery for that exact candidate in the batch. Follow the
[branch and worktree cleanup rules](development-branch-hygiene.md) after merge.

Use a disposable project and workspace for the
[local verification procedure](testing-strategy.md).
