# goodteam_os/
> L2 | Parent: [AGENTS.md](../AGENTS.md)

Manager 与 Goal 共用任务队列和隔离 Worker 池。

AGENTS.md: 本目录的成员与职责边界。
execution/: 执行身份、PR 绑定、审批与 Reviewer 服务。证据清单和记忆归档共用回放规则，GitHub 状态共用 PR 编号校验。
memory_core/: 角色记忆的纯投影与上下文规则。
skills/: Skill 包、绑定和快照。QA 默认方法先运行 Ego Skill 引导并附截图。Codex 读取固定文件，Pi 使用读取代理。
__init__.py: GoodTeam Python 包入口。
_repository_content_policy.py: 仓库内容的敏感路径拒绝与观察值脱敏。
_trusted_system_executable.py: 仓库快照和候选证据共用的可信 Git 解析。macOS 优先检查 Apple Command Line Tools 安装路径。
assistant_conversation.py: 围绕待确认草稿回复项目会话。
assistant_drafts.py: 编辑草稿并确认提交任务批次。
assistant_intake.py: Bound task discovery to the user's messages for the current, unstarted task.
assistant_queue.py: 保存待确认任务，按原建议版本生成队列标识。
assistant_requests.py: 会话请求接收、停止与中断恢复。
assistant_runtime.py: Manager 运行时快照、状态与安全失败信息。
assistant_workspace.py: 投影项目会话、草稿、队列与 Manager 活动。
attempt_memory.py: Bounded, read-only ordinary Worker memory observations for one exact attempt.
cli.py: 启动服务、接收 Issue 并调用批次质量和执行命令。
codex_app_server.py: Codex app-server 的 JSONL 双向传输；回调交给 Manager 服务。
context.py: 项目上下文条目与代码骨架。
context_continuity.py: Bounded, deterministic context projections, never a second task authority.
conversation_attachments.py: Project-scoped, immutable reference files for chat; never execution authority.
conversation_projection.py: 稳定排序并投影项目会话。
cross_project_memory.py: 跨项目记忆检索与报告。
database.py: 共享 PostgreSQL 配置与 Manager 检查点；进程退出前关闭连接池。
database_migration.py: 离线导入任务、会话、项目准备和 Goal 记录。
debate.py: 审批门的 Reviewer 讨论与调度。
dispatch.py: 隔离派工、并发限制和结果报告。
dispatch_capacity.py: Shared project Worker capacity; original jobs remain the execution ledger.
dispatch_methods.py: 为轨道锁定 Role 方法。重试复用原 Skill 锁。
dispatch_workspace.py: 捕获项目 Git 工作树并排除运行时目录，为派工保留隔离工作区。
failure_patterns.py: 失败模式归一化、匹配与路由记录。
frontend_assets.py: 本地 Web 前端构建目录定位。
full_smoke.py: 隔离工作区的完整 smoke 命令入口。
goal_assessment.py: 评估 Goal 所绑定的批次和候选证据。
goal_continuation.py: 推进已绑定批次、评估交付并提示自动续跑的环境变更。
goal_review_evidence.py: 从批次绑定读取 Worker 结果和独立 Reviewer 证据。
goals.py: 保存已确认的 Goal、工作证据与保留原任务的运行时暂停。
http_transport.py: Finite resource boundaries for GoodTeam's local dashboard HTTP transport.
manager_app_server.py: Manager 运行时准入、原生会话、记忆快照与执行恢复。
manager_assignment.py: Manager callable assignment adapter; existing task cards and queue own work.
manager_completion.py: Read native Manager completion from the original conversation receipt.
manager_dialogue.py: Bounded Manager conversation runtime; original services retain all authority.
manager_execution.py: Manager 对话与后台判断入口、项目运行设置及资源限制。
manager_runtime_observation.py: Bounded local Runtime observation for Manager conversations, never control.
manager_skill_context.py: Pinned Manager Role/Skill input for the native app-server session.
manager_tools.py: 提供 Manager 终止与请审规则，先校验委派验收标准，再为有效工具输入登记操作。
manager_worker_control.py: Explicit project-chat supervision through the existing Pi control protocol.
memory.py: 项目及角色记忆事件、检索与快照校验。
memory_embeddings.py: Opt-in embeddings and a rebuildable PG projection of eligible role memory.
memory_handoff.py: Internal ordinary-worker memory handoff observations, never dispatch authority.
memory_storage.py: Bounded POSIX file-store primitives; no memory/retrieval policy lives here.
memory_writeback.py: Private observations of the legacy Worker memory write-back boundary.
model_discovery.py: Process-local bounded discovery, without caching authentication or failures.
models.py: 模型配置、角色覆盖与路由解析。
native_picker.py: 本机可执行文件与运行路径选择。
native_worker_events.py: Live observations and owned-process cancellation for native Codex Workers.
operator_github.py: 校验仓库身份并读取 Issue 快照。
operator_manager_intake.py: 按来源版本写入输入并交给 Manager。
parallel_capacity.py: One bounded default for real simultaneous Worker execution.
pi_read_only.mjs: Pi Worker 的只读工具策略。
pi_read_only.py: Explicit Pi tool-level read-only policy, NOT an OS sandbox admission.
pi_rpc.py: Pi 子进程的 JSONL 通信与凭据脱敏。
pi_skill_resources.mjs: Pi Worker 的 Skill 资源读取工具。
pi_web_search.mjs: Pi Worker 的可选网页搜索工具。
postgres_goals.py: PostgreSQL adapter for the existing bounded-Goal ledger and invariants.
project_attention.py: Read-only, identity-bound attention across the existing project work surfaces.
project_batch_feedback.py: 将项目范围内的只读调查和终止批次绑定到续跑观察凭据。
project_batch_lock.py: 串行化批次确认、恢复和候选审查。
project_candidate_handoff.py: 将引用的候选传给后续任务及只读 Reviewer。
project_continuation_notice.py: 投影续跑交接，显示候选验收、显式质量检查及待答问题。
project_environment.py: Bind existing project dependencies to source worktrees.
project_human_feedback.py: Bound a Human answer to an existing discovery decision, not new authority.
project_import.py: 项目导入、工作台入口、模式确认与 readiness 绑定。
project_investigation_feedback.py: Observe settled investigation output without accepting it as delivery.
project_iteration.py: 从任务账投影候选，保留完整验收标准。校验执行选择，保留 Manager 的比较与理由。
project_iteration_continuation.py: 复用队列续跑，核对接受凭据和候选，登记 Git 交付。
project_iteration_execution.py: 通过同一队列核对项目身份和原批准，调用隔离 Worker 池。
project_iteration_planner.py: 用自然语言把候选与授权上下文交给 Manager 判断，保存只读建议。
project_iteration_quality.py: 统一读取 Manager 接受凭据与专项质量凭据。
project_candidate_acceptance.py: 校验 Work 反馈版本，将 Manager 决定和观察绑定到候选及修正链。
project_iteration_recovery.py: 重试原批次中的失败任务，保留 Provider 引用与 Skill 锁。
project_observation_judgment.py: Budgeted, read-only advice contract shared with the bounded production consumer.
project_observation_runtime.py: Bounded running-period Manager judgments over native app-server threads.
project_outcome_discovery.py: 冻结交付证据与 Work 反馈，按授权范围推进 Manager 的有效决定。
project_parallel_dispatch.py: Views over the original task/batch ledgers, not another execution queue.
project_readiness.py: 项目模式、就绪状态与能力评估。
project_readiness_executor.py: 项目导入后的隔离扫描与就绪执行。
project_runtime.py: 解析项目运行根目录和规范项目身份。
project_registry.py: 项目配置注册表与读取错误。
project_running_observation.py: Deduplicated observation input on an existing continuation, not a new scheduler.
project_worker_follow_up.py: Consume bounded Manager follow-up decisions through the original Pi queue.
projects.py: 项目配置加载、初始化与运行时配置。
provider_usage.py: Observed Codex invocation usage, separate from execution/approval authority.
providers.py: 共用 API 连接、地址绑定密钥、Worker 启动解析和原生协议配置。
repository_access.py: 受限仓库路径、文本与字节读取。
repository_context.py: 读取项目文档、仓库结构和项目记忆。
repository_inventory.py: 仓库清单及其内容摘要。
repository_snapshot.py: 仓库快照采集、身份绑定与校验。
role_prompts.json: 内置角色提示词。
roles.py: 读取 Worker 角色提示词与项目上下文来源。
service.py: 查询、启动和停止 Supervisor。
service_timeouts.py: Nested shutdown deadlines: outer supervisors must outlive the web drain.
session_runtimes.py: Pi RPC 会话在作业内生成原生 Provider 配置。
smoke.py: 报告实际执行阶段和产物。
source_preview.py: 从已核验的 Git 捕获生成来源摘要。
staging.py: 读取部署配置与交付约定的诊断信息。
state_documents.py: PG JSONB storage behind the existing state schemas and service contracts.
supervisor.py: 监督已配置的子进程及其心跳。
task_assistant.py: 项目助手会话、团队操作与执行上下文。
task_assistant_session.py: 持久化消息、草稿和任务确认。
task_delegation.py: One task input contract shared by role dispatch entry points.
tasks.py: 保存任务身份、状态、证据和依赖。
team_control_plane.py: 保存项目角色、Worker 和 Manager 运行配置，记录团队变更。
team_workspace.py: 从会话和批次凭据投影 Worker 状态。
web.py: 校验本机项目请求，路由派工与交付操作，提供历史操作记录查询。
web_search.py: Opt-in hosted search for an existing Pi job, never a new worker runtime.
worker_capabilities.py: 定义 Manager 请求档位、持久化档位与 Worker 目录后备值。
worker_output_context.py: Bounded, invocation-bound Worker output. Never a control/reply join.
worker_setup.py: Read optional project setup context for ordinary Planner/Worker tasks.
workers.py: Worker 作业、运行时与结果持久化。

[PROTOCOL]: Update this header on change, then check AGENTS.md
