# tests/
> L2 | Parent: [AGENTS.md](../AGENTS.md)

测试使用独立目录和协议夹具；外部服务测试使用一次性存储。

AGENTS.md: 本目录的成员与职责边界。
fixtures/: 测试数据与子进程夹具。
conftest.py: 隔离项目注册表与 Telegram 网络的共享夹具。
execution_fixture.py: Minimal execution fixtures shared by the retained sandbox smoke checks.
http_transport_helpers.py: HTTP transport objects for handler-unit fixtures using in-memory bodies.
native_observation_fixtures.py: A native receipt fixture at the Manager transport seam; no provider execution.
pi_worker_fixtures.py: Shared offline native Pi Worker fixtures, independent of retired Manager tests.
review_workbench_helpers.py: 为 CLI 和 HTTP 评审查询准备执行账本与记忆快照。
test_assistant_recovery_api.py: 验证 assistant recovery api 的行为。
test_backlog_board.py: Real grant/ledger/question/answer/queue path; Manager and capability are fixtures.
test_backlog_wait_receipt.py: Committed waiting decisions reconcile their own lost acknowledgement only.
test_builtin_role_methods.py: Actual first-party content through existing snapshots/Pi input; no model call.
test_ci_change_classifier.py: One smoke check for focused CI classifier paths after test consolidation.
test_cli_doctor.py: 验证 cli doctor 的行为。
test_codex_app_server.py: Protocol subprocess evidence; these tests do not invoke a model provider.
test_codex_local_engine.py: 验证 codex local engine 的行为。
test_context_freshness.py: Project iterations must not reuse understanding of changed source files.
test_conversation_attachments.py: 验证 conversation attachments 的行为。
test_database_subprocess.py: 验证数据库配置隔离与连接池退出清理。
test_delivery_backlog_judgment.py: Real grant/ledger/question/answer/queue path; Manager and capability are fixtures.
test_delivery_feedback_drain.py: Real grant/ledger/source/discovery; Git verification and Manager are fixtures.
test_discovery_context_refresh.py: Real discovery/grant/ledger path; original Git verifier and Manager are fixtures.
test_dispatch_capacity.py: Ten is a real direct-dispatch concurrency bound, not a planning limit.
test_dispatch_isolation.py: 共享派工池通过 Git worktree 验证隔离、确认重放与失败证据。
test_dispatch_shared_capacity.py: Actual shared locking/threads/process exit; Worker terminal bytes are fixtures.
test_execution_authority.py: Semantic authority checks use isolated storage and no provider execution.
test_execution_cli.py: 验证 execution cli 的行为。
test_execution_idempotency.py: 验证 execution idempotency 的行为。
test_execution_ledger.py: 验证 execution ledger 的行为。
test_execution_path_claims.py: 验证 execution path claims 的行为。
test_execution_review_surfaces.py: 验证 CLI/HTTP 证据查询与记忆归档回放一致。
test_execution_sandbox_integration.py: 验证 execution sandbox integration 的行为。
test_feedback_recovery_handoff.py: Explicit recovery rejoins released feedback ownership; execution is a fixture.
test_full_smoke_preflight.py: 验证 full smoke preflight 的行为。
test_goal_assessment_native.py: 验证 goal assessment native 的行为。
test_goal_continuation.py: 验证 Goal 权限与真实批次证据。
test_goal_continuation_http.py: Real HTTP/background service/run/Worker evidence, with synthetic local models.
test_greenfield_recovery.py: Representative regressions from the real StockFlow greenfield trial.
test_init.py: 验证 init 的行为。
test_legacy_launch_routes.py: 验证已退役的写入口返回 404，历史操作记录仍可读取。
test_iteration_context_inputs.py: Representative continuity, complete-prompt budget and planner replay contracts.
test_manager_app_server_lifecycle.py: 验证 Manager 原生请求、并发控制、角色记忆与只读对话回退。
test_manager_assignment.py: Native Manager tools traverse the real queue and isolated subprocess pool.
test_manager_completion.py: Completed native replies retain their exact thread/turn identity across restart.
test_manager_completion_planning.py: Native planning replay keeps the original billable turn and input binding.
test_manager_contention.py: Native Manager work overlaps while original authority and budgets remain bound.
test_manager_dialogue.py: Offline continuation adapter with an in-memory checkpoint, not live-model acceptance.
test_manager_execution.py: Manager conversation uses native threads and plain replies, never WorkerJobs.
test_manager_goal_intake.py: Saving later work never admits it into an already active Goal.
test_manager_input_priority.py: Durable admission and native recovery do not block project conversation.
test_manager_runtime_config.py: 验证 Manager 配置迁移、默认启动和推理档位。
test_manager_runtime_observation.py: Read real local session files; no provider execution or paid Manager call.
test_manager_skill_context.py: Native Manager input reuses pinned Role/Skill records without a Worker job.
test_manager_tools.py: Host tools retain queue identity and the shared project's control allowance.
test_manager_worker_control.py: Original chat routes exact explicit controls; Runtime responses are fixtures.
test_memory_core.py: Legacy content compatibility with explicit pickup-policy invalidation.
test_memory_embeddings.py: 验证 pgvector 记忆检索和项目迭代发现。
test_memory_multilingual.py: Local lexical and injected semantic retrieval; no downloaded model or provider.
test_memory_view_contract.py: The Memory UI consumes this actual public API projection fixture.
test_mixed_method_dispatch.py: Mixed Runtime capture uses original jobs and per-track immutable methods.
test_models.py: 验证 models 的行为。
test_native_worker_events.py: Real offline subprocess evidence, not paid Codex/model acceptance.
test_node_runtime_contract.py: Keep frontend and manual documentation builds on patched Node LTS lines.
test_observation_recovery_human_board.py: Original native observation recovery, without transport replay or new authority.
test_pending_write_review_feedback.py: Real local grant/ledger/board route with terminal-evidence and model fixtures.
test_pi_authorized_write.py: Bounded atomic input writes; only local offline subprocesses are allowed.
test_pi_method_dispatch.py: Actual dispatch/Skill binding/Git workspace/Pi subprocess; no paid provider.
test_pi_method_execution.py: Original job -> locked methods -> existing Pi RPC, using a free fake process.
test_pi_method_search.py: One original ordinary method invocation, two existing tools, no network spend.
test_pi_rpc.py: 验证 pi rpc 的行为。
test_pi_skill_resources.py: Actual locked broker and Pi extension contract; no Pi/model/HTTP acceptance.
test_pi_terminal_contract.py: Official RPC contract, exercised with free local subprocess fixtures.
test_planning_context_refresh.py: Real grant/queue/planning consumer with deterministic Manager output, no Worker.
test_postgres_manager.py: 使用一次性 PostgreSQL 验证项目准备、目录身份与迭代状态。
test_project_batch_feedback.py: One report-to-decision contract with deterministic storage/dispatch seams.
test_project_blocked_judgment.py: Bounded Manager judgment over blocked facts, not an execution bypass.
test_project_conflict_wait.py: Real Manager/queue/claim/thread locks; execution output is an offline seam.
test_project_continuation_notice.py: A durable handoff is a user message, never permission to resume execution.
test_project_discovery_investigation.py: One contract: explicit investigation authority maps to a read-only Worker.
test_project_discovery_multiple.py: One decision publishes a bounded set through the original ledger and queue.
test_project_discovery_planner.py: Planner uses existing discovery/ledger contracts without widening old grants.
test_project_investigation_feedback.py: One report-to-decision contract with deterministic storage/dispatch seams.
test_project_iteration.py: First-vertical-slice checks for project-level next-iteration facts.
test_project_iteration_continuation.py: 验证按项目路径纳入待办、Git 落地和交付后的派工。
test_project_iteration_execution.py: One real subprocess batch proves queue -> isolated pool -> original cards.
test_project_iteration_planner.py: 验证建议重放、执行范围与比较信息入队回归。
test_project_iteration_quality.py: 验证 Manager 按证据交付、QA 修正链和专项质量凭据。
test_project_iteration_recovery.py: 验证 Manager 与用户重试已结束的失败任务，保留同批成功结果。
test_project_live_pool.py: Real Manager/queue/claim/thread locks; execution output is an offline seam.
test_project_observation_judgment.py: Offline judgment contract; no live Manager Runtime or Worker reply evidence.
test_project_observation_runtime.py: Native observation integration with local event files and a simulated Manager.
test_project_outcome_discovery.py: One bounded project loop learns from delivery and creates its next task.
test_project_question_board.py: One lifecycle through real file stores/locks, with explicit Manager packets.
test_project_readiness_web.py: 验证 project readiness web 的行为。
test_project_running_observation.py: Observation input contract, not autonomous model/provider acceptance.
test_project_worker_follow_up.py: Manager decision -> durable original Pi queue; no external model requests.
test_providers.py: Provider configuration in disposable PG; local HTTP/CLI fixtures only.
test_queued_web_search.py: Explicit queue search authority reaches the original Worker, no paid transport.
test_readonly_pi_dispatch.py: Read-only Role dispatch producer, exact methods and unchanged old contracts.
test_readonly_pi_runtime.py: New production dispatch producer + broker + Runtime with explicit workspace/listener doubles. Not real Git baseline, installed Pi SDK, or model acceptance.
test_readonly_pi_search.py: Captured read-only Role + authorized Search through original offline Runtime.
test_repository_access.py: 验证 repository access 的行为。
test_repository_ancestor_identity.py: Ancestor churn is unrelated content, but replacement is still an error.
test_repository_inventory.py: 验证 repository inventory 的行为。
test_review_capture.py: Pinned native JSONL compatibility; no provider or project acceptance claim.
test_review_inputs.py: The tool-free local Reviewer still receives the complete inline input.
test_review_question_ready.py: Real local grant/ledger/board route with terminal-evidence and model fixtures.
test_role_loop_composition.py: Real Manager choice -> queue -> batch -> Role method capture, before Git execution.
test_role_runtime_source.py: Explicit configuration reuse reaches original Worker jobs, not a new pool.
test_role_skill_input.py: Ordinary Role input contract only; no native runtime or paid invocation.
test_running_human_board.py: Real grant/ledger/question/answer/queue path; Manager and capability are fixtures.
test_selected_task_entry.py: 验证单任务确认和批次确认复用队列准入。
test_skill_library.py: 验证 skill library 的行为。
test_task_assistant.py: Project research uses task intake; import and reads never start inspection.
test_task_assistant_session.py: 验证 task assistant session 的行为。
test_task_delegation.py: Persisted assignment -> runtime track -> job evidence, without a live provider.
test_tasks.py: 验证 tasks 的行为。
test_web_cors.py: CORS response-header policy, independent of POST execution authority.
test_web_search.py: 验证 web search 的行为。
test_worker_invocation_context.py: Real job persistence/readback with fixture states, not live-model acceptance.
test_worker_output_context.py: Offline original Runtime entry -> existing Manager observation contract.
test_worker_retry_evidence_process.py: Actual offline CLI boundaries: retry evidence, host timeout, and artifact copy.

[PROTOCOL]: Update this header on change, then check AGENTS.md
