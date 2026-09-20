# 后端测试价值审查 — 2026-09-21

审查基线：`618b361`（上一轮展示测试与 CI 清理后的独立分支）。
范围：**117 个文件、1,879 个测试函数、2,235 个 pytest 实例**。
调整后：**1,874 个函数、2,230 个实例，全部通过**。

[完整逐项记录](backend-test-value-2026-09-21.jsonl) 每行对应一个测试函数，展开列出其全部
pytest node ID、参数、处置、断言、调用、运行证据以及调整后的对应实例。
记录包含基线源码行号和 SHA-256，便于回到该提交核对；它是本次审查的快照。

## 方法与判断标准

本次采用全量静态证据提取、逐实例运行追踪，以及对重复、过时、弱断言、源码检查和耗时异常的定向语义复核。
所有收集到的实例均有记录和处置；参数化测试按同一函数的契约及各输入分区评估，不省略实例。

- 静态检查覆盖测试体、全部显式断言、预期异常、共享断言 helper、调用和参数使用情况。
- 使用 pytest 收集实际实例，并用 Coverage 的逐测试 context 记录 `src/goodteam_os` 的当前进程执行。
- 候选删除项比较输入、前置状态、断言及 fixture 来源。相同行覆盖只能发现候选，不能单独证明冗余。
- 保留项附有具体场景和完整断言。存在明确可观察契约且未确认等价替代时，不靠测试数量目标删除。
- 对两条弱断言做内存中的错误实现对照，验证改写后能检出原来漏掉的错误。

这不是全量 mutation testing，也不代表每个断言都能检出所有可能的错误。
覆盖采样不包含子进程内 Python、shell、浏览器或 `scripts/ci`。
79 个实例没有当前进程产品覆盖，其中包含 CLI、HTTP 故障恢复、shell 集成、CI 分类器和配置不变量；未据此删除。
694 个实例具有本次采样中独占的产品代码行，其余 1,541 个仍可能保护不同输入、状态或错误顺序。
8 个没有普通 `assert` / `pytest.raises` 的函数已核对其共享断言 helper、显式 `AssertionError` 或禁止调用探针。
未发现完全没有结果检查的函数，也未发现参数化参数完全未使用的情况。

## 逐实例处置

按基线 2,235 个实例统计：

| 处置 | 实例数 | 含义 |
| --- | ---: | --- |
| 保留 | 2,209 | 具体输入、输出、状态、异常或副作用契约 |
| 有局限地保留 | 4 | 静态 CI / 主脚本接线检查，局限见下文 |
| 删除 | 6 | 确认重复或只约束源码组织方式 |
| 合并 | 1 | 过时的“未实现工具”用例并入查询参数矩阵 |
| 补强 | 5 | 增加缺失的安全或副作用断言 |
| 改写 | 2 | 源码子串检查改为执行型接线检查 |
| 消除无关运行成本 | 6 | 替换假进程等待和真实 CLI 探测 |
| 精简断言 | 2 | 删除私有属性或个人路径源码检查 |

共删除 5 个函数。因为保留缺参分区并增加 shell 参数的第二种语法，最终实例数净减少 5：
`2,235 → 2,230`。审查目标是提高信号质量，不是扩大删除数量。

## 已落实的改变

### 重复和过时用例

- 删除 `test_ci_change_classifier.py::test_historical_pr_file_sets` 的三个历史文件集合实例。
  单路径策略、最高风险合并、前后端影响并集和固定目标已有直接测试；大集合的高风险项还会掩盖其他路径映射错误。
- 删除 `test_review_workbench_mvp.py::test_missing_diff_artifact_returns_explicit_empty_state`。
  它与 diff artifact 套件的 `test_missing_diff_artifact_empty_state_is_preserved`
  使用同一生命周期 helper、相同调用及相同完整字典断言，保留后者。
- 删除重复工具清单以及用 False 标志、源码不含 getattr 推断派发安全的检查。
  保留公开工具目录、未知工具实际被拒绝及其审计结果；不再锁死私有属性名。
- 删除 focused/full handler 源码包含相同 helper 名称的检查。
  真实完整 HTTP server 与 focused handler 验收继续保护对外项目 API 契约。
- 已实现的 `search_text` 不再保留“未来工具未实现”测试；其缺参分区合入 missing/empty/oversized query 矩阵，
  同时保留错误状态、空 observations 和审计 envelope 断言。

### 修复会放过错误实现的断言

| 用例 | 发现 | 已验证的改变 |
| --- | --- | --- |
| Reviewer HTML 转义 | 恶意内容放在根本不输出的 summary 中；关闭 escape 仍通过 | 改测实际输出的 operator_message/profile_id/profile_label，检查转义内容存在且原文不存在；关闭 escape 后失败 |
| GitHub snapshot labels | 只比较 fixture 刚传入的值；取消防别名复制仍通过 | 创建快照后修改调用方列表，检查快照未改变；取消复制后失败 |
| mkdir 非法名称 | 只检查 400，会放过“先写目录再报错” | 每个非法名称同时检查目标目录仍为空 |
| Reviewer retry 接线 | 只检查源码中的路由和函数名 | 通过完整 handler 请求验证 workspace/body、409 状态和响应传递 |
| dispatch shell 接线 | 只检查 case 分支字符串 | 在临时 checkout 执行原脚本，验证两种参数语法和含空格路径原样传递 |

错误实现对照仅在独立 Python 进程内临时替换函数，未改生产源码。
shell 测试使用自己的临时 checkout 和只记录 argv 的 Python 替身，没有派发真实 Worker 或创建真实分支。

### 运行成本

FakePopen 安装器现在替换 `_sleep`，五个错误分类/落盘用例各省去 `2 + 8` 秒真实退避，
共消除 **50 秒固定等待**。专门的 retry 用例仍记录并检查间隔、尝试次数、恢复和升级结果。
Pi 发现用例保留 fake-Pi 的真实 doctor，只将其他后端声明为不可用，避免调用开发机真实 CLI 和在线模型目录。

| 文件 | 基线 call 时间 | 调整后 call 时间 |
| --- | ---: | ---: |
| `test_workers.py` | 52.05 秒 | 1.60 秒 |
| `test_pi_session_runtime.py` | 21.91 秒 | 4.48 秒 |
| `test_repository_context_provider.py` | 306.29 秒 | 289.93 秒 |

基线启用了覆盖采样，调整后仅记录 pytest 结果；这些是本机单次观测，不是严格的 A/B 性能基准。
仓库上下文测试仍是主要成本，涉及真实 Git、路径替换、资源限额、清理和读取隔离。
未用大范围 mock 或删除安全分区换取缩短运行时间。

## 四项有限保护

以下逐项标为 `retain_limited`，没有将其通过解释为完整行为证明：

1. `test_ci_workflow.py::test_ci_is_pr_or_manual_only_with_read_only_permissions`：静态触发器/权限形状，不能替代真实 GitHub 调度。
2. `test_ci_workflow.py::test_every_job_keeps_the_self_hosted_runner_security_boundary`：静态 runner guard，配合执行型 gate 测试保留。
3. `test_land_provenance.py::test_land_pr_script_accepts_review_rationale_option_and_exports_for_airdrop`：主脚本接线仍主要依靠字符串，helper 执行验收不能完全替代它。
4. `test_pre_review_script.py::test_pre_review_main_script_uses_fallback_and_report_selector`：同样只作主脚本接线补充，不能证明完整 fallback 循环。

两条主脚本检查适合后续用隔离 gh/CLI 替身进行完整脚本验收；当前仍保留已有的有限保护。

## 验证与观察到的失败

Verification tier：**Tier 3，调整测试对控制面契约的覆盖**。
本轮只改后端测试和审查记录，未改产品实现、API payload 或浏览器代码。

- 基线逐 context 全量：2,234 passed、1 failed，525.71 秒。
  失败为 `test_append_window_binding_mutations_fail_closed_and_write_nowhere[error-job_id-job-redirected]`；
  在构造 fixture 快照时检测到系统临时目录祖先元数据变化，尚未进入目标变异窗口断言。
  原样单独复跑和最终全量都通过，没有删掉该用例或放宽安全检查。
- 定向子系统验证：209 passed，7.77 秒。
- 最终全量：`PYTHONPATH=src .venv/bin/python -m pytest tests -q --durations=15`，
  执行时附加只读 pytest 收集/耗时插件；**2,230 passed，428.15 秒**。
  有 14 条既有 macOS 多线程进程使用 fork 的弃用警告。
- `.venv/bin/ruff check src/goodteam_os/ tests/ scripts/ci/`、`git diff --check` 通过。
- 前端 `npm test`：6 个 Node + 183 个 Vitest 测试通过；`npm run build`、`npm run lint` 通过。
- HTTP 崩溃/超时恢复四个实例包含在最终后端全量中，使用各自临时工作区。
  本轮未重跑 Playwright：浏览器、业务实现和 E2E fixture 相对 `618b361` 未变，上一轮该基线的 10 个隔离浏览器用例已通过。
- 未运行真实模型、Telegram、GitHub 发布或部署；本轮未改变这些适配器实现，也未触及其他 worktree 的未提交功能改动。

## 文件级索引

所有文件的逐项记录都包含在 JSONL 中。耗时为 call 阶段合计，删除数按基线实例统计。

| 文件 | 基线函数 | 基线实例 | 最终实例 | 删除实例 | 基线 call 秒 | 最终 call 秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `test_agent_sandbox_v0.py` | 16 | 21 | 21 | 0 | 0.190 | 0.051 |
| `test_api.py` | 73 | 75 | 75 | 0 | 1.025 | 0.471 |
| `test_api_approve_debate_ref.py` | 1 | 1 | 1 | 0 | 0.091 | 0.027 |
| `test_artifacts.py` | 2 | 2 | 2 | 0 | 0.003 | 0.001 |
| `test_assistant_conversation.py` | 6 | 8 | 8 | 0 | 0.545 | 0.157 |
| `test_assistant_draft_actions.py` | 9 | 12 | 12 | 0 | 0.678 | 0.223 |
| `test_assistant_drafts.py` | 7 | 9 | 9 | 0 | 0.324 | 0.108 |
| `test_assistant_execution_logic_regressions.py` | 7 | 9 | 9 | 0 | 0.536 | 0.192 |
| `test_assistant_recovery.py` | 9 | 9 | 9 | 0 | 0.202 | 0.070 |
| `test_assistant_recovery_api.py` | 2 | 2 | 2 | 0 | 0.068 | 0.028 |
| `test_assistant_team_confirmation.py` | 5 | 5 | 5 | 0 | 0.153 | 0.054 |
| `test_ci_change_classifier.py` | 13 | 28 | 25 | 3 | 0.137 | 0.122 |
| `test_ci_workflow.py` | 3 | 7 | 7 | 0 | 0.487 | 0.583 |
| `test_cli.py` | 21 | 21 | 22 | 0 | 2.062 | 2.892 |
| `test_cli_doctor.py` | 2 | 2 | 2 | 0 | 0.407 | 0.528 |
| `test_context.py` | 9 | 9 | 9 | 0 | 0.029 | 0.007 |
| `test_cross_project_memory.py` | 4 | 4 | 4 | 0 | 0.023 | 0.015 |
| `test_debate.py` | 4 | 4 | 4 | 0 | 0.079 | 0.038 |
| `test_debate_dispatch.py` | 2 | 2 | 2 | 0 | 0.008 | 0.003 |
| `test_debate_m2.py` | 6 | 6 | 6 | 0 | 0.083 | 0.022 |
| `test_delivery_api.py` | 2 | 2 | 2 | 0 | 0.047 | 0.024 |
| `test_delivery_outcomes.py` | 21 | 38 | 38 | 0 | 0.125 | 0.029 |
| `test_delivery_review_truth.py` | 4 | 13 | 13 | 0 | 0.017 | 0.001 |
| `test_execution_api.py` | 6 | 6 | 6 | 0 | 0.133 | 0.055 |
| `test_execution_ci_binding.py` | 6 | 6 | 6 | 0 | 0.058 | 0.027 |
| `test_execution_cli.py` | 4 | 4 | 4 | 0 | 0.098 | 0.059 |
| `test_execution_e2e_contract.py` | 1 | 1 | 1 | 0 | 0.066 | 0.027 |
| `test_execution_evidence_query.py` | 7 | 7 | 7 | 0 | 0.248 | 0.078 |
| `test_execution_final_provenance.py` | 5 | 5 | 5 | 0 | 0.169 | 0.075 |
| `test_execution_first_run.py` | 4 | 4 | 4 | 0 | 0.086 | 0.034 |
| `test_execution_github_contract.py` | 9 | 9 | 9 | 0 | 0.015 | 0.003 |
| `test_execution_github_loop.py` | 4 | 4 | 4 | 0 | 0.104 | 0.051 |
| `test_execution_idempotency.py` | 8 | 8 | 8 | 0 | 0.005 | 0.000 |
| `test_execution_ledger.py` | 5 | 5 | 5 | 0 | 0.009 | 0.004 |
| `test_execution_memory_provenance_refs.py` | 7 | 7 | 7 | 0 | 0.345 | 0.145 |
| `test_execution_models.py` | 9 | 9 | 9 | 0 | 0.014 | 0.001 |
| `test_execution_orchestrator.py` | 8 | 8 | 8 | 0 | 0.128 | 0.038 |
| `test_execution_path_claims.py` | 10 | 10 | 10 | 0 | 0.012 | 0.001 |
| `test_execution_pr_binding.py` | 6 | 6 | 6 | 0 | 0.125 | 0.049 |
| `test_execution_pr_boundary.py` | 6 | 6 | 6 | 0 | 0.089 | 0.035 |
| `test_execution_sandbox_integration.py` | 19 | 19 | 19 | 0 | 0.444 | 0.195 |
| `test_failure_patterns.py` | 9 | 9 | 9 | 0 | 0.029 | 0.008 |
| `test_fix_approval_reviews.py` | 3 | 3 | 3 | 0 | 0.019 | 0.006 |
| `test_fix_profile.py` | 7 | 7 | 7 | 0 | 0.392 | 0.124 |
| `test_frontend_assets.py` | 6 | 7 | 7 | 0 | 0.013 | 0.005 |
| `test_full_smoke.py` | 5 | 5 | 5 | 0 | 0.624 | 0.597 |
| `test_graph_sample.py` | 86 | 86 | 86 | 0 | 1.492 | 0.697 |
| `test_init.py` | 6 | 6 | 6 | 0 | 0.009 | 0.002 |
| `test_intake_team_dispatch.py` | 4 | 6 | 6 | 0 | 0.076 | 0.025 |
| `test_intent_commit.py` | 12 | 12 | 12 | 0 | 0.476 | 0.230 |
| `test_intent_feedback.py` | 11 | 17 | 17 | 0 | 0.510 | 0.259 |
| `test_land_provenance.py` | 10 | 10 | 10 | 0 | 20.226 | 22.121 |
| `test_load_profile.py` | 7 | 7 | 7 | 0 | 0.011 | 0.002 |
| `test_manager_execution.py` | 6 | 6 | 6 | 0 | 0.031 | 0.011 |
| `test_memory.py` | 33 | 33 | 33 | 0 | 0.554 | 0.291 |
| `test_messages.py` | 7 | 7 | 7 | 0 | 0.029 | 0.031 |
| `test_models.py` | 4 | 4 | 4 | 0 | 0.009 | 0.002 |
| `test_multi_reviewer_projection.py` | 16 | 16 | 16 | 0 | 0.057 | 0.007 |
| `test_native_picker.py` | 9 | 14 | 14 | 0 | 0.015 | 0.003 |
| `test_onboarding_backend.py` | 12 | 14 | 14 | 0 | 0.054 | 0.008 |
| `test_onboarding_profile.py` | 9 | 10 | 10 | 0 | 0.028 | 0.003 |
| `test_onboarding_runner.py` | 10 | 10 | 10 | 0 | 8.212 | 5.456 |
| `test_onboarding_store.py` | 31 | 35 | 35 | 0 | 3.921 | 2.844 |
| `test_operator_intake.py` | 8 | 11 | 11 | 0 | 0.140 | 0.106 |
| `test_operator_queue.py` | 11 | 11 | 11 | 0 | 0.050 | 0.021 |
| `test_operator_state_machine.py` | 18 | 21 | 21 | 0 | 0.558 | 0.207 |
| `test_pi_rpc.py` | 7 | 7 | 7 | 0 | 0.787 | 0.807 |
| `test_pi_session_runtime.py` | 20 | 20 | 20 | 0 | 21.913 | 4.483 |
| `test_pre_review_script.py` | 7 | 7 | 7 | 0 | 0.540 | 0.554 |
| `test_project_import.py` | 47 | 60 | 60 | 0 | 0.829 | 0.617 |
| `test_project_readiness.py` | 13 | 13 | 13 | 0 | 0.039 | 0.016 |
| `test_project_readiness_executor.py` | 30 | 34 | 34 | 0 | 3.827 | 3.117 |
| `test_project_readiness_web.py` | 9 | 16 | 16 | 0 | 0.571 | 0.372 |
| `test_project_registry.py` | 6 | 6 | 6 | 0 | 0.016 | 0.007 |
| `test_projects.py` | 21 | 21 | 21 | 0 | 0.041 | 0.007 |
| `test_report_bug.py` | 8 | 8 | 8 | 0 | 0.159 | 0.055 |
| `test_repository_access.py` | 21 | 39 | 39 | 0 | 0.091 | 0.023 |
| `test_repository_context_provider.py` | 142 | 247 | 246 | 1 | 306.294 | 289.931 |
| `test_repository_inventory.py` | 17 | 22 | 22 | 0 | 12.724 | 16.255 |
| `test_repository_snapshot.py` | 67 | 109 | 109 | 0 | 31.424 | 35.018 |
| `test_resources.py` | 11 | 11 | 11 | 0 | 0.048 | 0.034 |
| `test_review_gate.py` | 2 | 2 | 2 | 0 | 0.001 | 0.000 |
| `test_review_policy.py` | 7 | 10 | 10 | 0 | 0.070 | 0.026 |
| `test_review_provenance.py` | 7 | 7 | 7 | 0 | 0.021 | 0.007 |
| `test_review_workbench_browser_decisions.py` | 12 | 17 | 17 | 0 | 0.775 | 0.328 |
| `test_review_workbench_browser_mvp.py` | 5 | 5 | 5 | 0 | 0.633 | 0.148 |
| `test_review_workbench_diff_artifact.py` | 10 | 10 | 10 | 0 | 0.365 | 0.185 |
| `test_review_workbench_diff_navigation.py` | 7 | 7 | 7 | 0 | 0.299 | 0.119 |
| `test_review_workbench_mvp.py` | 15 | 15 | 14 | 1 | 0.532 | 0.231 |
| `test_review_workbench_rationale_mapping.py` | 8 | 8 | 8 | 0 | 0.413 | 0.175 |
| `test_review_workbench_sandbox_evidence.py` | 7 | 7 | 7 | 0 | 0.139 | 0.073 |
| `test_reviewer_dispatch.py` | 30 | 30 | 30 | 0 | 0.165 | 0.070 |
| `test_reviewer_evidence_snapshot.py` | 5 | 5 | 5 | 0 | 0.022 | 0.003 |
| `test_reviewer_profiles.py` | 10 | 10 | 10 | 0 | 0.009 | 0.002 |
| `test_roles.py` | 4 | 4 | 4 | 0 | 0.004 | 0.002 |
| `test_runtime_effort_contract.py` | 1 | 5 | 5 | 0 | 0.065 | 0.018 |
| `test_service_cli.py` | 13 | 13 | 13 | 0 | 0.026 | 0.011 |
| `test_smoke.py` | 14 | 14 | 14 | 0 | 3.110 | 1.638 |
| `test_staging.py` | 10 | 10 | 10 | 0 | 0.018 | 0.005 |
| `test_state.py` | 8 | 8 | 8 | 0 | 0.023 | 0.015 |
| `test_supervisor.py` | 8 | 8 | 8 | 0 | 0.274 | 0.257 |
| `test_supervisor_runtime.py` | 5 | 5 | 5 | 0 | 5.734 | 5.835 |
| `test_task_assistant.py` | 9 | 9 | 9 | 0 | 0.134 | 0.053 |
| `test_task_assistant_session.py` | 8 | 8 | 8 | 0 | 0.071 | 0.036 |
| `test_tasks.py` | 9 | 9 | 9 | 0 | 0.072 | 0.018 |
| `test_team_control_plane.py` | 42 | 42 | 42 | 0 | 0.765 | 0.312 |
| `test_team_workspace.py` | 37 | 43 | 43 | 0 | 0.199 | 0.055 |
| `test_team_workspace_next_task.py` | 2 | 2 | 2 | 0 | 0.003 | 0.000 |
| `test_telegram.py` | 69 | 71 | 71 | 0 | 0.367 | 0.210 |
| `test_telegram_review.py` | 18 | 20 | 20 | 0 | 0.068 | 0.030 |
| `test_telegram_runtime_controls.py` | 30 | 39 | 39 | 0 | 1.205 | 1.246 |
| `test_web.py` | 198 | 216 | 215 | 1 | 4.579 | 3.515 |
| `test_workers.py` | 104 | 116 | 116 | 0 | 52.046 | 1.597 |
| `test_workflow_builtin_changes.py` | 1 | 3 | 3 | 0 | 0.098 | 0.057 |
| `test_workflow_http_recovery.py` | 3 | 4 | 4 | 0 | 2.645 | 2.637 |
| `test_workflow_recovery.py` | 15 | 25 | 25 | 0 | 0.603 | 0.270 |
| `test_workflow_table.py` | 28 | 28 | 28 | 0 | 0.217 | 0.172 |

数据核对：基线 node ID 无重复、无缺失，共 2,235 个；所有非删除项均对应存在的最终实例。
最终 2,230 个实例均被对应关系覆盖，最终结果全部为 passed。
