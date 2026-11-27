# Native Manager runtimes

Each GoodTeam project can run its Manager on Codex app-server or CodeBuddy CLI
over ACP. Both use the same GoodTeam task ledger, confirmation queue, isolated
Worker pool, review and project continuation services.

## Observable behavior

Project chat keeps a native thread across messages. Replies stream into the
existing assistant-request record. The composer becomes available as soon as
the backend durably accepts a message. Codex can steer an active conversation
turn. CodeBuddy starts the next message after the active turn settles.
Independent background analysis uses separate native sessions.

The Manager can query the project and Workers, queue a concrete Role assignment,
read its actual result, advance an existing authorized continuation, ask a
bounded Worker follow-up, stop a supported Worker, and prepare a Team change.
Tool results return to the same Agent loop. Ordinary conversation is plain text;
it no longer depends on an entire classification JSON response or a temporary
Manager WorkerJob/result.md pair. Structured Goal and planning judgments retain
their own bounded output contracts because their consumers validate that data.

New delegation returns the existing task ID and proposal ID. It preserves the
original user messages, purpose, scope and acceptance criteria. Queued work uses
the existing confirmation/authorization and `run_queued_iteration_batch` path.
An active project continuation can advance already authorized work without a
new per-task decision. The dispatch context tells the Worker that the queue has already authorized execution. The exact user request remains attached for provenance. A GoodTeam tool cannot create that authorization, accept its own
result or grant review, merge or deployment permissions.

## Runtime and configuration

In the project's Team page, select **Configure assistant**, choose
**Codex app-server** or **CodeBuddy CLI**, then select **Save and check
connection**. Selecting a different provider starts its model and reasoning
fields at their defaults and keeps the additional instructions. Each provider
has its own native session history. Worker roles use their own saved runtimes.

GoodTeam launches `codex app-server` from PATH or `GOODTEAM_CODEX_PATH`.
Codex owns the sign-in, model connection, integration configuration, and managed
policies. Changing local MCP settings can change what the Manager can use.
External tools follow their own permissions and confirmation rules.

GoodTeam launches `codebuddy --acp` from PATH or `GOODTEAM_CODEBUDDY_PATH`.
CodeBuddy can reuse the CLI login of the operating-system user running GoodTeam.
GoodTeam gives CodeBuddy a project-scoped runtime directory and exposes its
project tools over local stdio MCP. The adapter disables CodeBuddy built-in tools
and hooks. GoodTeam loads the Manager's Role and Skills for both providers.
The Team page reads the CodeBuddy model list for Manager and Worker settings
from a temporary ACP session. The refresh sends no prompt and uses the service
user's existing CLI login.

Use **Stop** to interrupt the active turn. A connection check runs a bounded
read-only prompt against the selected provider.

If the Skill store is inside a Worker-writable root, foreground chat can
still reply and inspect project state. Skill loading and project actions
stay disabled for that turn. Background judgments still require Skill preflight.

Manager settings show the effort requested from the selected provider.
The GoodTeam default is `high`. Users can choose `low`, `medium`, `high`,
or `xhigh`. CodeBuddy uses its default model when the model field is empty.

Codex uses app-server v2. GoodTeam registers scoped tools at
`thread/start.dynamicTools` and answers `item/tool/call`. Subsequent turns use
`thread/resume`, `turn/start`, `turn/steer`, and `turn/interrupt`.
CodeBuddy uses ACP stdio. Each turn opens or loads a session with the same
GoodTeam tools, calls `session/prompt`, then records the terminal response
before closing the CLI process.

`GOODTEAM_MANAGER_TURN_TIMEOUT_SECONDS` bounds a turn (default 600 seconds).
Process exits, tool errors, unknown outcomes and unsupported Worker capabilities
are reported explicitly. Codex Worker currently streams progress and supports
owned-process Stop; Pi Workers retain their native follow-up/steering behavior.
The UI and tools do not advertise Codex CLI steering as available.

## Role and Skill input

`ManagerSkillContext` uses the existing project Team, immutable task locks,
package versions and revocation preflight. The original Role prompt and selected
required methods are included in native instructions. On-demand resources are
read through `goodteam_skill_resource` from the captured package and are checked
again before use. This loader does not create a Worker job, borrow a Pi session
or claim strict container isolation.

Each Manager request captures the existing orchestrator Role memory outside the
conversation lock. Its frozen pickup is validated before use; corrections can
remove facts from a retry but cannot add newer facts. Outcome discovery reuses
its own frozen pickup. When the memory source changes, a new native thread
prevents the old thread history from restoring superseded facts. If a thread is
still active, the next input waits for it to settle before using a new thread.
The bounded conversation context remains available after the change. Memory is
project evidence and does not grant instructions, costs or permissions.

## Execution identity and recovery

The existing Task Assistant session stores a `manager_app_server` projection:

- `threads` associates conversation/background work with the native thread.
- `requests` binds logical input, phase, original context and Role memory,
  native thread/turn, runtime configuration and terminal result.
- `tool_receipts` records stable operation identity and the actual returned
  business IDs. Tasks and runs continue to live in their original stores.

Duplicate input IDs reuse the original outcome. Codex can reconcile completed
turns against native history. CodeBuddy recovery reuses the GoodTeam completion
receipt. If the ACP terminal response was lost, GoodTeam treats the turn as
interrupted and retains any recorded tool receipts. Stopping a shared Codex turn
stops its linked requests. Project continuation Stop also interrupts its active
native background judgments and retains the original authorization history.

A separate operation lock deduplicates host mutations. Follow-up reservations
consume the existing continuation allowance before enqueue and share the budget
with the observation service. Stop is rechecked at the final local enqueue.
Worker reports and polling feed the same observation digest, so the same evidence
does not purchase duplicate Manager judgments.

## Validation and acceptance

The focused subprocess checks exercise real bidirectional JSONL transport,
native thread continuity, tool response continuation, steering during a blocked
tool, foreground/background overlap, cancellation, exact-input replay, pinned
methods, and the existing queue-to-isolated-Worker result path. The subprocess
peers are protocol fixtures; they do not establish model quality or production
provider behavior.

The product acceptance scenario remains:

1. Discuss and refine a requirement in the project conversation.
2. Delegate a real task to another Role through the task queue and existing
   authorization.
3. While the Worker is running, send a new user requirement and receive an
   informative Manager reply before the background judgment finishes.
4. Observe real progress/blocker/result reports, with a single Manager response
   to the same issue discovered by both polling and reporting.
5. Review the real result and arrange the next authorized action; completion and
   acceptance are recorded separately.
6. Stop and restart; verify that stopped work does not resume and existing tasks
   are not created a second time.

Record provider-backed evidence in #419 and implementation status in #738.
The repository's paused GitHub Actions policy remains in force; fixture results
are not represented as real-provider acceptance or remote CI results.

The implementation was checked against the installed Codex
`0.167.0-alpha.4` protocol schemas. In the implementation workspace, an actual
app-server initialization failed with `Read-only file system (os error 30)` even
with supported disposable SQLite and log directories. No provider-backed model
turn ran there. A later macOS check with Codex `0.169.0` started a real app-server
and drove the browser through a local simulated model. A disposable configured
MCP server started when a read-only Manager thread was created; an empty
`mcp_servers` override did not remove the inherited server. The existing local
Reviewer research explains why reading and overriding configuration alone does
not establish a bound native tool inventory. The assignment integration test also injects its test Git
executable at the admission seam because this host maps system executable
ownership to UID 65534; production executable trust checks remain intact.

The current deployment runs one threaded Web server process per workspace.
Native runtime ownership is local to that process. Running multiple Web server
processes against the same workspace requires additional native-thread ownership
coordination before it is a supported deployment.

## Source map

| Area | Source |
| --- | --- |
| JSONL transport | `codex_app_server.py` |
| CodeBuddy ACP and local MCP | `codebuddy_manager.py`, `manager_mcp.py` |
| Native Manager identity and lifecycle | `manager_app_server.py`, `manager_completion.py` |
| Existing service tools | `manager_tools.py`, `manager_assignment.py` |
| Pinned Role/Skill input | `manager_skill_context.py` |
| User ingress and recovery | `assistant_requests.py`, `task_assistant.py` |
| Planning / Goal / observation | `project_iteration_planner.py`, `goal_assessment.py`, `project_observation_runtime.py` |
| Native Worker observations | `native_worker_events.py`, `worker_output_context.py` |

Protocol reference: [official Codex app-server documentation](https://learn.chatgpt.com/docs/app-server).
