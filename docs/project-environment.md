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
   Setup keeps its private HOME, minimal inherited host environment, filesystem policy,
   configured network policy and per-job command/result logs.
3. Once its normal task delivery is accepted, later development tasks reference
   that actual task ID through `dependencies`. Preparation and dependent work
   are not an independent parallel batch. No new scheduler or queue is added.
4. All related Workers get links to the same configured dependency directories
   and the same project tool PATH. Node workspace-source links are resolved against
   each Worker’s current source; installed third-party packages remain shared. Worker errors and proposed environment changes
   return to Planner; a Worker does not approve or rediscover task admission.

The existing project `worker_setup` configuration supplies installation argv;
model output cannot replace it. Its optional `shared_paths` contains relative
installed dependency directories. If omitted, root `package.json` selects
`node_modules`, and root `pyproject.toml`/`requirements.txt` selects `.venv`, based
on the captured source. Projects without setup configuration retain their host
runtime and receive no guessed Node/Python directories.

A preparation task copies captured tracked source files to the shared environment
before installation. Runtime links are excluded from source candidate changes.
Batch and Workflow entries retain the same project environment reference. A
Workflow preparation task installs at its implementation stage, not at every
planning/review role. CLI and Pi session Workers share `.venv/bin` and `node_modules/.bin` when those
paths are declared. Preparation retries reuse a successful setup result only for
identical saved command input; a failed setup still retries as the same task.

Pi waits for a fresh setup completion receipt before starting its RPC request
clock. `worker_setup.timeout_seconds` controls installation wall-clock time
(default 1800 seconds); it is not a model or spending budget. Stop remains
available while installation is running.

Source copies open `.goodteam/environment` without following symlink directory
components, and copy through anchored directory descriptors with atomic file
replacement. The installer policy is unchanged.

Read-only-only continuation does not require environment preparation. Discarded
queue preparations are not silently reused for a new development assignment.

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

## Explicit installation configuration

Planner-owned `worker_setup.env` accepts explicit project installation variables
(such as proxy, registry and compiler configuration), without a hardcoded
package-manager name allowlist. Only OS environment name/value validity is
checked. The host environment is not inherited wholesale. Private installation
HOME/temp/CODEX_HOME remain controlled by the installer; credentials still need
their own explicit provisioning authority, not implicit host inheritance.

The Pi executable is pinned to the absolute host-preflight path before adding
project dependency tools to PATH. Project executables cannot replace that
selected runtime merely by sharing its name.

## Task source worktrees

Dispatch now creates detached Git worktrees in the registered project repository
for its baseline, each task, and the integrated candidate. Tasks retain separate
source working directories and indexes, but share the repository object database
and the project dependency environment. There is no new per-task clone route.
Integration merges the recorded task commits and materializes the merged Git
objects; it does not install another environment or rewrite the project branch.
Python source resolution includes the current task's `src` directory when present.

New dispatch records use `dispatch_workspace.v2`. Existing clone-era records and
frozen review receipts are left untouched, not silently rebound to another Git
identity. Existing records retain their original identity and result refs for recovery.
An existing v1 run resumed in place keeps its original task directories; newly
created attempts always use project worktrees. No new clone route is retained.
When starting a new attempt or integrating their results, their exact Git objects
are imported into the project repository and new directories use worktrees.
Old candidates and frozen review receipts are not rewritten or discarded.
Whether such unfinished records exist on the user's computer has not been verified. The retained artifact policy remains unchanged: this change
does not automatically delete old workspaces or their task results.

This worktree replacement has static validation only. Actual parallel worktree
execution, conflict integration, failure recovery and shared dependency reuse
remain unverified. Pinning the Pi executable does not itself pin an interpreter
selected by an `env` shebang; the actual installed launcher must be inspected
before claiming that interpreter resolution is fixed.
