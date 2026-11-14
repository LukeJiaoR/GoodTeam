# One project environment

The project root determines one environment at `.goodteam/environment`. A Worker,
source worktree, candidate, batch or retry never determines a second environment.
Source worktrees remain separate for result collection; installed dependencies
and tool paths belong to the project.

Planner uses the existing task discovery and dependency flow:

1. An ordinary implementation task with the exact write domain
   `.goodteam/environment` prepares the configured installation.
2. The task uses the existing restricted `worker_setup` command executor. The
   command runs in the shared environment, not once in every Worker worktree.
   Setup keeps its private HOME, environment allowlist, filesystem policy,
   configured network policy and per-job command/result logs.
3. Once its normal task delivery is accepted, later development tasks reference
   that actual task ID through `dependencies`. Preparation and dependent work
   are not an independent parallel batch. No new scheduler or queue is added.
4. All related Workers get links to the same configured dependency directories
   and the same project tool PATH. Worker errors and proposed environment changes
   return to Planner; a Worker does not approve or rediscover task admission.

The existing project `worker_setup` configuration supplies installation argv;
model output cannot replace it. Its optional `shared_paths` contains relative
installed dependency directories. If omitted, root `package.json` selects
`node_modules`, and root `pyproject.toml`/`requirements.txt` selects `.venv`, based
on the captured source. Projects without setup configuration retain their host
runtime and receive no guessed Node/Python directories.

A preparation task copies captured tracked source files to the shared environment
before installation. Runtime links are excluded from source candidate changes.
CLI and Pi session Workers share `.venv/bin` and `node_modules/.bin` when those
paths are declared. Preparation retries reuse a successful setup result only for
identical saved command input; a failed setup still retries as the same task.

A new direct assignment retains its original development task and creates or
reuses an ordinary environment prerequisite in the same existing queue. Both
have stable identities for replay; existing approval/continuation behavior
remains in charge of executing the queued work. A blocked or review preparation
task remains a pending dependency, not proof of a usable environment. Canceled
preparation tasks are not reused.

Existing frozen development task intents are not rewritten by this migration.
Planner completes preparation before continuing those tasks; new development
tasks can declare explicit dependencies. Accepted old preparation tasks are
history, not an automatic proof that every future dependency change is prepared.

## Validation status

This change has had syntax/static inspection only. No test suite, installation,
model call, concurrent execution or failure/recovery runtime validation has run.
Do not report the project environment or full GoodTeam delivery as verified from
these code changes. Real package-manager behavior, including workspace-local
package links, still requires project-specific execution evidence.
