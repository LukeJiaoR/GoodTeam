# 基本 gate 收敛：第二批测试审查

基线：`a11a336f842baeb818782f1457bbae143be72395`（2026-10-02 云端抓取的
`origin/main`）。本批为独立测试清理 Draft；#477 的 `4845869` 另行复审，
未把它或其他未合并 PR 的成功结果混入本批。实现、frontend、workflow、runner
和安全策略均未更改。没有调用真实模型、发送 Telegram/业务消息或手动重跑云 workflow。

## 为什么原套件有 3067 例

它不是 3067 项独立用户需求：2332 个 Python 测试定义经参数化展开增加了 735 例。
历次开发把领域正例、模拟 HTTP 正例、逐步 milestone、固定模板/投影快照和浏览器闭环
同时留在默认套件中。另一方面，多阶段身份、授权窗口、取消和报告来源确有不同风险；
不能仅凭速度、mock 数或 feature 已开发完判定无用。

这次先用当前用户流程约束职责：Projects → Work → 明确确认 → 执行 → 人工 review/
修改 → 当前 outcome，Team/Knowledge 提供准确配置、历史和来源。基本 gate 应检查这些
动作的结果及副作用边界，而不是复写每个内部字典、提示词句子和旧页面标签。

## 全套映射与候选，尚未批准替代默认套件

- [159 个测试族的目的、层次、参数展开前后、核心候选和已测耗时](basic-gate-families-a11a336-2026-10-02.csv)。
- [完整 3067 个参数展开场景索引](basic-gate-cases-a11a336-2026-10-02.csv)。
- [基本 gate 候选：762 个定义、1179 个展开场景、79 个文件](basic-gate-candidate-a11a336-2026-10-02.csv)。
- [本批 177 个删除场景及具体保留覆盖](basic-gate-deletions-a11a336-2026-10-02.csv)。

**1179 只是按职责选出的审查候选，不是已证明充分的下限或最终目标。** 除本批 177 例外，
候选清单外的 1711 例仍保留且仍运行；其中可能有未被候选覆盖的唯一风险。
需逐族证明替代覆盖或重写后再删，不能把它们一律降为手动专项。本批没有新增 selector、
marker、分层门禁或另一套长期测试框架，也未把候选清单接入 CI。

| 候选职责 | 展开例数 | 保护的用户风险 |
| --- | ---: | --- |
| 项目根、可信 Git、完整快照/只读 broker | 135 | 读错项目、执行目标控制的程序、根置换、越预算/泄密 |
| 项目准备事实和并发归属 | 37 | 旧扫描/租约复活，错命令被确认，准备失败却可启动 |
| 明确提案、人工授权、后续 Team 配置 | 33 | 问题变执行、模型伪造授权、Worker/runtime 混用、改写历史 |
| 持久流程、并发和恢复 | 102 | 重复确认/执行，半提交重放，取消/崩溃后误继续 |
| 流程失败和独立审查 | 39 | QA/Worker 失败却前进、陈旧审查/fanout 或非独立 verdict |
| HTTP 权限和请求绑定 | 51 | 跨来源、本地 DNS rebinding、错根/版本或非编辑资源写入 |
| Worker 生命周期、重试和受限运行 | 25 | 不安全运行模式、死 PID 当运行、重试无界/丢失模型 effort |
| Skill 包身份和输入完整性 | 90 | 可执行/链接资源、遗漏字节、伪造版本或不完整包被接受 |
| Skill 任务授权、消费和并发身份 | 189 | 未确认 pin、撤销/暂停后继续读，跨 Worker/任务消费或半发布 |
| Skill 审查进程、取消和完整结果 | 181 | exit 0 被当真实报告，取消后晚到复活、租约不明仍启动/清理 |
| Execution、PR、CI 与闭环身份 | 34 | issue/PR/head 混用、重复 PR/merge/闭环事件 |
| 执行隔离和 CI 门禁本身 | 79 | 清理他人资源、敏感 runner/权限放宽、计划 job 未通过却绿 |
| 报告真实来源和当前身份 | 65 | 历史/跨项目报告当当前成功，失败被同 role 的成功掩盖 |
| 可选入口的身份、取消和人工决定 | 89 | Telegram 跨 chat/session，旧 Workbench 越权，错误 PID 取消 |
| 持久来源、记忆和本地启动契约 | 28 | 重复事件、旧配置、错误 workspace/启动或跨项目记忆 |
| 辅助运行不得越过付费/资源授权 | 2 | 默认真实模型调用或操作真实项目 |

现有 6 Node、270 Vitest、31 Playwright 场景未在本批修改。浏览器分别保护实际 HTTP
闭环、并发批准、Skill 撤销/取消/来源、报告读取、当前任务和历史隔离；没有因其慢而删。
后续 Web unit 应优先留下取消/晚到、重复提交、当前 request/version 和身份绑定；纯展示
断言才能由对应浏览器行为替代。CLI 保留实际 `main`/启动动作和错误退出；文档/契约保留
可执行命令、严格 runner/权限和 CI gate。旧模板质量、可选 adapter/catalog 与布局的专项
只在相关行为更改时人工检查，不能用它证明真实模型交付已接受。

## 本批删除与保留

| 原测试族 | 删除展开例数 | 保留的更有力检查/删除理由 |
| --- | ---: | --- |
| `test_web.py` | 37 | 固定步骤/角色标签、空态、重复成功批准；保留准确 artifact version、来源/路径、并发 start、staging 失败和恢复；真实浏览器闭环覆盖六阶段与第二任务 |
| `test_graph_sample.py` | 24 | 样例模板/预设/提示词字串、重复成功阶段；保留失败保持反馈/人工产物、重试耗尽、QA 阻塞、实际 release 证据和 transient 约束 |
| `test_repository_context_provider.py` | 22 | 删除软关闭提示逐维度 22 例矩阵；保留 80% 和 elapsed 两代表、硬上限、预留容量、并发、身份及全部 Git/regex 安全检查 |
| `test_api.py` | 18 | mock builder 回显、旧路由成功复演和重复字段读回；保留精确 approval binding、403/409、origin/nonce/DNS、异步写失败、路径/目标拒绝和最新 retrieval 来源 |
| `test_telegram.py` | 18 | 帮助/按钮文字和成功回显；保留 allowlist、禁网熔断、exact-session/cross-chat、陈旧拒绝、原文/refs 和无效输入不得写入 |
| `test_workflow_table.py` | 12 | 静态表副本/旧规划快照；保留真正 transition 调用、非法 DAG、真实 Worker 禁止并行和 human-only security |
| `test_reviewer_dispatch.py` | 11 | 诊断计数/重复底层 fanout 正例；保留图执行重复去重、全部身份字段、当前 artifact version 和无显式 ack 不建 job |
| `test_memory.py` | 7 | 默认模板/简单读回/空值自检；保留 WAL 去重、supersede、篡改恢复、原始来源和跨项目 |
| `test_smoke.py` | 4 | 固定注册表/输出字段副本；保留 dry-run 不改目标、当前 run 证据、失败/恢复、no-paid opt-in |
| `test_review_workbench_diff_navigation.py` | 4 | anchor/CSS/重复只读复演；保留实际行号解析、缺失/坏 patch 提示和 HTTP 完整只读快照 |
| `test_reviewer_profiles.py` / `test_resources.py` / `test_model_config_reuse.py` | 3 / 3 / 3 | 静态配置顺序、数据类序列化、缓存内部次数/容量；保留报告必须字段、不可变值、资源能力权限、当前字节/失效文件/并发隔离 |
| `test_review_workbench_diff_artifact.py` | 3 | 重复读回/空字段/只读；保留实际 diff 持久化、hash 变化不可覆盖、缺失不可合成文件 |
| `test_tasks.py` / `test_state.py` | 2 / 2 | 看板快照、初始字段及重复全局正例；保留项目持久化、历史/重复节点、状态写入/任务迁移唯一性 |
| `test_review_workbench_browser_mvp.py` / `test_review_workbench_rationale_mapping.py` | 2 / 2 | 旧导出字段快照和重复只读；保留实际 CLI/HTTP 导出、ledger/memory 不变、精确路径引用、缺失不能合成证据和脱敏 |

逐项 testname、原耗时及剩余覆盖见删除 CSV。固定措辞/布局的删除并不宣称浏览器证明了
同一句文案或模型输出质量。旧 `/review-workbench` 仍有兼容入口，权限/只读/真实性检查
保留；当前 frontend 不消费该旧页面，并不意味着可删除它的全部边界。

## 净计数和收益

| 指标 | main 基线 | 本批 |
| --- | ---: | ---: |
| Python 测试文件 | 159 | 159 |
| 测试定义 | 2332 | 2176 |
| 参数展开场景 | 3067 | 2890 |
| 交替测量的 focused 场景 | 662 | 485 |
| 测试代码净行数 | — | −3890 |
| focused 进程耗时中位数，3 轮 | 6.798 s | 5.807 s |
| collection 中位数（含测试导入） | 0.568 s | 0.550 s |
| call / setup / teardown 中位数 | 5.003 / 0.707 / 0.072 s | 4.328 / 0.488 / 0.054 s |
| fixture 时间诊断小计中位数 | 0.567 s | 0.384 s |
| focused 子进程数，每轮 | 56 | 55 |

测量范围为 17 个完整受影响测试族，加上本次 budget 矩阵及三个保留的 budget 代表。
它不包含该文件其余未改动 Git 检查，不能把 focused 全通过冒充整个文件或全套通过。
**唯一可直接报告的进程收益是该范围约 0.992 s；不能将 phase/fixture/导入收益重复相加，
也不能外推整条 CI 节省。** fixture 小计嵌套且与 setup 重叠；不同 phase 中位数不构成同一轮。
没有证据表明产品模块导入本身更快。主要收益是撤掉重复测试职责和约四千行维护代码。

## 验证与限制

云环境：Linux x64、Python 3.12.14、pytest 9.1.1、Ruff 0.16.10；隔离工作树
`codex/basic-gate-compression-2`，`PYTHONPATH=src` 绑定本工作树。所有删除项在原
基线中通过；没有通过删除失败用例给云环境刷绿。Ruff、diff check 和三轮 A/B focused
已通过。准确最终 commit、收集结果、全套验证和自动 CI 状态记录于 Draft PR 及附件。

基线云本地全量为 **2705 passed / 358 failed / 4 setup errors，188.26 s**。
`/usr/bin/git` 及祖先为 UID 65534，与产品要求的 root-owned 可执行来源不符；因此 Git
trust 等检查失败，部分后续恢复检查无法建立服务。它们仍保留，最终失败 node ID/异常类型
须与基线比较；不能称其为真实产品缺陷、成功覆盖，或替换 runner 安全策略使其通过。
regex stderr overflow 历史偶发与上述基线环境失败分开；相关测试未删/skip，失败若重现需
诊断 fixture/断言并保留真实管道回收保护。没有付费模型验证，也没有真实消息发送。

UI/业务源码和 workflow 的 tree identity 用于复用同源码的已有证据；不同 PR 的 head、
完整 test suite 结果不能互相代替。#477 的 10 例削减不包含在上述 177 例内；若两批均独立
通过并合并，净变化才是 3067→2880，而非当前 main 已变为该数量。本批不会自行合并。
