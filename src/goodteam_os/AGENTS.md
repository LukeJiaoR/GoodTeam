# goodteam_os/
> L2 | Parent: [AGENTS.md](../AGENTS.md)

项目服务沿用任务账、Workflow 和各自的持久化边界。

AGENTS.md: 本目录的成员与职责边界。
execution/: 执行身份、证据、审批与 Reviewer 服务。
memory_core/: 角色记忆的纯投影与上下文规则。
nodes/: Workflow 节点实现。
skills/: Skill 包、绑定、快照与运行时。
__init__.py: GoodTeam Python 包入口。
_repository_content_policy.py: 仓库内容的敏感路径拒绝与观察值脱敏。
_trusted_system_executable.py: 校验系统可执行文件的身份与可信路径。
approval_rationale.py: Validate a new approval before any durable decision or execution begins.
artifact_revisions.py: Revision authority for editable workflow artifacts and their derived evidence.
artifacts.py: Workflow 产物存储。
assistant_conversation.py: Keep pending and running work conversational without reopening its draft.
assistant_drafts.py: Exact-text draft changes and explicit preparation gates for Assistant chat.
assistant_intake.py: Bound task discovery to the user's messages for the current, unstarted task.
assistant_queue.py: Explicit, exact-text future work intake. Selecting a draft never starts it.
assistant_requests.py: 会话请求接收、停止与中断恢复。
assistant_runtime.py: Manager 运行时快照、状态与安全失败信息。
assistant_workspace.py: Project conversation identity and model activity, independent of task lifecycle.
attempt_memory.py: Bounded, read-only ordinary Worker memory observations for one exact attempt.
cli.py: GoodTeam 命令行参数与入口。
codex_app_server.py: Codex app-server 的 JSONL 双向传输；回调交给 Manager 服务。
context.py: 项目上下文条目与代码骨架。
context_continuity.py: Bounded, deterministic context projections, never a second task authority.
conversation_attachments.py: Project-scoped, immutable reference files for chat; never execution authority.
conversation_projection.py: Read-only conversation records shared by project and historical task views.
cross_project_memory.py: 跨项目记忆检索与报告。
database.py: Shared PostgreSQL configuration for application data and Manager checkpoints.
database_migration.py: Explicit offline import of existing task, conversation, Workflow and Goal state.
debate.py: 审批门的 Reviewer 讨论与调度。
delivery_changes.py: Retain a small explicit delivery set from the workflow's immutable Git result.
delivery_outcomes.py: Read-only, run-bound reports. A report or successful process is not acceptance.
dispatch.py: Worker 批次派发与结果核验。
dispatch_capacity.py: Shared project Worker capacity; original jobs remain the execution ledger.
dispatch_methods.py: Per-track method capture for new mixed batches, using original Skill stores.
dispatch_workspace.py: Retained, isolated Git workspaces for direct dispatch.
failure_patterns.py: 失败模式归一化、匹配与路由记录。
frontend_assets.py: 本地 Web 前端构建目录定位。
full_smoke.py: 隔离工作区的完整 smoke 命令入口。
gate_report_projection.py: Read-only, attributed reports for an exact artifact's advisory reviewers.
goal_assessment.py: Read-only Manager assessment of a completed, exactly bound workflow.
goal_continuation.py: Goal intent over the existing task queue and batch execution.
goal_review_evidence.py: Original advisory Reviewer reports for an exactly bound completed Goal run.
goals.py: Transactional authority for explicitly confirmed, bounded delivery goals.
graph.py: Workflow 图、节点路由与项目上下文。
http_transport.py: Finite resource boundaries for GoodTeam's local dashboard HTTP transport.
intent_commit.py: 任务变更意图的持久化与确定性解释。
legacy_reviewer_reports.py: Read-only legacy Reviewer prose, separate from structured Skill receipts.
manager_app_server.py: Manager 原生会话、记忆快照、并发与执行恢复。
manager_assignment.py: Manager callable assignment adapter; existing task cards and queue own work.
manager_completion.py: Read native Manager completion from the original conversation receipt.
manager_dialogue.py: Bounded Manager conversation runtime; original services retain all authority.
manager_execution.py: Manager 对话与后台判断入口及运行资源限制。
manager_runtime_observation.py: Bounded local Runtime observation for Manager conversations, never control.
manager_skill_context.py: Pinned Manager Role/Skill input for the native app-server session.
manager_tools.py: 将 Manager 原生工具接入既有项目、队列与授权服务。
manager_worker_control.py: Explicit project-chat supervision through the existing Pi control protocol.
memory.py: 项目及角色记忆事件、检索与快照校验。
memory_embeddings.py: Opt-in embeddings and a rebuildable PG projection of eligible role memory.
memory_handoff.py: Internal ordinary-worker memory handoff observations, never dispatch authority.
memory_storage.py: Bounded POSIX file-store primitives; no memory/retrieval policy lives here.
memory_writeback.py: Private observations of the legacy Worker memory write-back boundary.
messages.py: 项目和角色消息读写。
model_discovery.py: Process-local bounded discovery, without caching authentication or failures.
models.py: 模型配置、角色覆盖与路由解析。
multi_reviewer_projection.py: 多 Reviewer 状态与审批证据只读投影。
native_picker.py: 本机可执行文件与运行路径选择。
native_worker_events.py: Live observations and owned-process cancellation for native Codex Workers.
operator.py: 既有 Operator 服务、操作与执行状态。
operator_candidate_review.py: Trusted startup wiring for the original Operator Builder candidate.
operator_github.py: Operator 的 GitHub Issue 解析与 CLI 读取。
operator_manager_intake.py: Source receipts consumed by the existing Manager service, never a dispatcher.
operator_queue.py: Operator 持久队列与状态转换。
operator_startup.py: Private, explicit CLI configuration for the existing Operator service.
parallel_capacity.py: One bounded default for real simultaneous Worker execution.
pi_read_only.mjs: Pi Worker 的只读工具策略。
pi_read_only.py: Explicit Pi tool-level read-only policy, NOT an OS sandbox admission.
pi_rpc.py: Strict JSONL subprocess client for Pi RPC mode.
pi_skill_resources.mjs: Pi Worker 的 Skill 资源读取工具。
pi_web_search.mjs: Pi Worker 的可选网页搜索工具。
postgres_goals.py: PostgreSQL adapter for the existing bounded-Goal ledger and invariants.
project_attention.py: Read-only, identity-bound attention across the existing project work surfaces.
project_batch_feedback.py: Bound known terminal batch results to the existing continuation Context.
project_candidate_handoff.py: Bind an authorized continuation to an existing, undelivered batch candidate.
project_continuation_notice.py: Presentation of existing continuation state; never an execution authority.
project_environment.py: Bind existing project dependencies to source worktrees.
project_human_feedback.py: Bound a Human answer to an existing discovery decision, not new authority.
project_import.py: 项目导入、模式确认与 readiness 绑定。
project_investigation_feedback.py: Observe settled investigation output without accepting it as delivery.
project_iteration.py: Read-only, evidence-grounded starting point for project iteration planning.
project_iteration_continuation.py: Bounded project continuation through the existing Assistant queue and dispatch.
project_iteration_execution.py: Task Assistant batch adapter over the existing isolated dispatch pool.
project_iteration_planner.py: 从项目任务与授权上下文生成只读迭代建议。
project_iteration_quality.py: Bind an existing project batch to the existing candidate quality services.
project_iteration_recovery.py: Explicit recovery of the existing confirmed batch, never fresh task intake.
project_observation_judgment.py: Budgeted, read-only advice contract shared with the bounded production consumer.
project_observation_runtime.py: Bounded running-period Manager judgments over native app-server threads.
project_outcome_discovery.py: 从已验证交付冻结发现输入并创建后续任务。
project_parallel_dispatch.py: Views over the original task/batch ledgers, not another execution queue.
project_readiness.py: 项目模式、就绪状态与能力评估。
project_readiness_executor.py: 项目导入后的隔离扫描与就绪执行。
project_registry.py: 项目配置注册表与读取错误。
project_reviewer_profiles.py: 项目 Reviewer 配置解析与诊断。
project_running_observation.py: Deduplicated observation input on an existing continuation, not a new scheduler.
project_scope_addition.py: Explicit Human additions to an existing grant, never a Manager action.
project_worker_follow_up.py: Consume bounded Manager follow-up decisions through the original Pi queue.
projects.py: 项目配置加载、初始化与运行时配置。
provider_usage.py: Observed Codex invocation usage, separate from execution/approval authority.
providers.py: GoodTeam-owned API connections. Agent names and API providers are separate.
release_artifacts.py: Durable release corrections from the current implementation job only.
repository_access.py: 受限仓库路径、文本与字节读取。
repository_inventory.py: 仓库清单及其内容摘要。
repository_snapshot.py: 仓库快照采集、身份绑定与校验。
resources.py: 运行时资源类型、作用域与能力登记。
review_policy.py: Reviewer 证据策略解析与评估。
review_provenance.py: 审批 Reviewer 的来源与阶段证据。
reviewer_dispatch.py: Reviewer 作业派发与运行诊断。
reviewer_evidence_snapshot.py: Reviewer 输入证据快照。
reviewer_profiles.py: Reviewer 配置档案与标识校验。
reviewer_retry_inputs.py: Replay persisted Reviewer requests without inventing a new review task.
role_prompts.json: 内置角色提示词。
roles.py: 角色定义、提示词与 Workflow 产物契约。
service.py: 本地服务状态、停止与重启。
service_timeouts.py: Nested shutdown deadlines: outer supervisors must outlive the web drain.
session_runtimes.py: Persistent worker-session contract and Pi RPC implementation.
skill_conformance.py: Opt-in, disposable-fixture Codex probe; never a production readiness gate.
skill_runtime_readiness.py: Credential-free Runtime prerequisites; never grants Skill execution authority.
smoke.py: 隔离 smoke 项目、执行与结果分类。
source_preview.py: Host-generated bounded source evidence from an already verified capture.
staging.py: 交付及 staging 后端配置与执行。
state.py: Workflow 状态与审批决定持久化。
state_documents.py: PG JSONB storage behind the existing state schemas and service contracts.
supervisor.py: 后台服务组件与存活状态监督。
task_assistant.py: 项目助手会话、团队操作与执行上下文。
task_assistant_session.py: 项目助手会话状态与事件存储。
task_delegation.py: One task input contract shared by role dispatch entry points.
task_input_context.py: Task-facing, bounded input metadata; no private prompts or Runtime authority.
tasks.py: 项目任务账、任务意图与任务板。
team_control_plane.py: Team Control Plane: Role capabilities vs Worker instances with an audit ledger.
team_workspace.py: Team 工作区只读聚合。
telegram.py: Telegram 交互、审核会话与消息格式。
telegram_approval_binding.py: Bind workflow decisions to the material actually displayed by Telegram.
telegram_runtime_controls.py: Telegram 对既有运行时会话的控制。
web.py: 本地 Web 与项目 API 请求入口。
web_search.py: Opt-in hosted search for an existing Pi job, never a new worker runtime.
worker_capabilities.py: Portable persisted effort values and conservative CLI discovery fallbacks.
worker_output_context.py: Bounded, invocation-bound Worker output. Never a control/reply join.
worker_setup.py: Read optional project setup context for ordinary Planner/Worker tasks.
workers.py: Worker 作业、运行时与结果持久化。
workflow_mutations.py: Serialize workflow authority and retain interrupted human decisions.
workflow_recovery.py: Exact failed-Workflow evidence, reused by the existing review and batch paths.
workflow_retry.py: A workflow retry acts on the exact observed run, stage and attempt set.
workflow_revisions.py: Resume recorded decisions and route requested changes back through evidence.
workflow_table.py: Workflow 节点、门与执行路径定义。
workflow_workspace.py: One retained raw Git baseline and execution directory per workflow task.

[PROTOCOL]: Update this header on change, then check AGENTS.md
