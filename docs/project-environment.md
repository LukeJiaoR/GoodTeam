# Project environment and ordinary tasks

GoodTeam uses the selected machine and the existing project environment. It does
not create `.goodteam/environment`, copy source for installation, or require an
installation task before development. Merely configuring `worker_setup` does not
run its command or create dependencies between tasks.

Planner reads project facts, splits the requested work, and decides whether any
environment work is needed. Environment work uses ordinary tasks and ordinary
dependencies. The planning-handoff Skill describes this method; there is no
special environment task type, fixed write domain, mandatory two-round plan, or
separate installation queue. Workers execute assigned tasks and report actual
errors and results.

## Execution context

The project root is the environment source. Existing `.venv` and `node_modules`
are reused without requiring setup configuration. Optional `worker_setup.shared_paths`
can describe other existing dependency directories. The setup command remains
project context, not a command automatically executed before an agent starts.
The optional configured setup tool is exposed to the Worker as a command, not
run by startup. When an assigned task calls for installation, the Worker may
invoke it; it uses the captured command in the current Worker's actual source
worktree, not the project environment root. Lockfile and generated source writes
therefore enter the same candidate collection as ordinary Worker edits. Existing
project dependencies are still reused through the existing bindings and runtime
paths. The selected command may target shared dependency outputs explicitly;
Planner arranges those changes as ordinary environment tasks rather than moving
all source-affecting commands into the user's original checkout. Only that
installation subprocess receives `worker_setup.env`; the agent's HOME,
CODEX_HOME and provider variables are not changed by installation settings.
Values are not copied into model context. The tool inherits the invoking
Worker's actual permissions and process lifetime. It grants no extra filesystem,
network access and reports command failure normally. GoodTeam’s injected
provider-connection key is excluded from the installer environment. Existing
system provider variables (such as OPENAI_API_KEY) and files reachable through
HOME remain accessible under the invoking process permissions: this is reuse of
the host environment, not credential isolation. The old clean-HOME/allowlist
isolation is not claimed or silently restored.

Worker source changes remain in Git worktrees. Dependency bindings do not copy
source or install packages. Node workspace-package links resolve to the current
worktree. Python source resolution places the current worktree's `src` (when
present) and root ahead of inherited PYTHONPATH. Existing system environment is
retained and available project executable paths are prepended.

Preparation tasks must state which project installation they change. Installing
only inside one task's worktree does not establish a shared project installation.
Planner uses actual execution results when deciding subsequent tasks; a task
status or readiness checklist is not evidence that a command or installation
succeeded.

## Records and acceptance

Existing task records, dependencies, worktree identities, review receipts and
results are not rewritten. Historical setup receipts remain readable. No new
run uses the removed automatic setup wrapper. Old hidden environment directories
are not automatically deleted or migrated into the project.

Readiness records remain project discovery context, not a development admission
checklist. The existing scheduler consumes task dependencies and capacity; this
change adds no second task ledger or state machine.

Only static source inspection is performed for this change. Actual project
installation, shared dependency reuse, Python editable imports, Node workspace
links, parallel execution, Stop/recovery and two related delivery iterations
remain acceptance work tracked in #419. No model run or test suite was executed.
