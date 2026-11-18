# Manager on Codex app-server

Implementation for #738, based on merged #737 (`e83228f`). GoodTeam's project
Manager runs through a local `codex app-server` process. Other Roles keep their
own native runtimes. The task ledger, queue, isolated Worker pool, review and
project continuation services remain the business owners.

## Observable behavior

Project chat keeps a native thread across messages. Replies stream into the
existing assistant-request record, and the composer becomes available as soon
as the backend durably accepts a message. Additional input can steer an active
conversation turn. Independent background analysis uses other native threads,
so a slow running-period judgment does not occupy the user conversation.

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
new per-task decision. A GoodTeam tool cannot create that authorization, accept its own
result or grant review, merge or deployment permissions.

## Runtime and configuration

In the project's Manager settings, select **Use local Codex** to authorize the
native runtime from the loopback dashboard. The choice defaults to off, is saved in the project Team state,
and is recorded in the Team audit log. Conversation, background analysis and
native recovery require this authorization. Clearing it blocks new requests
and recovery. Use Stop to interrupt a turn already in progress.

GoodTeam launches `codex app-server` from PATH or `GOODTEAM_CODEX_PATH`.
Codex owns the sign-in, model connection, integration configuration, and managed
policies. Changing local MCP settings can change what the Manager can use.
External tools follow their own permissions and confirmation rules. GoodTeam
commands continue to use the project queue and approvals. A connection check
verifies that the runtime responds. It does not certify tool isolation.

The transport uses app-server v2. GoodTeam registers scoped tools at
`thread/start.dynamicTools` and answers `item/tool/call`. Subsequent turns use
`thread/resume`, `turn/start`, `turn/steer`, and `turn/interrupt`.

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

Duplicate input IDs reuse the original outcome. An uncertain native outcome is
not silently replayed. Native completed turns can be reconciled without rerunning
tools; stopped work stays stopped. A shared steered turn has one visible final
reply, and stopping it stops the linked request records. Project continuation
Stop also interrupts its active native background judgments and retains the
original business authorization history.

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
| Native Manager identity and lifecycle | `manager_app_server.py`, `manager_completion.py` |
| Existing service tools | `manager_tools.py`, `manager_assignment.py` |
| Pinned Role/Skill input | `manager_skill_context.py` |
| User ingress and recovery | `assistant_requests.py`, `task_assistant.py` |
| Planning / Goal / observation | `project_iteration_planner.py`, `goal_assessment.py`, `project_observation_runtime.py` |
| Native Worker observations | `native_worker_events.py`, `worker_output_context.py` |

Protocol reference: [official Codex app-server documentation](https://learn.chatgpt.com/docs/app-server).
