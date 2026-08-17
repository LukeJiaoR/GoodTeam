# GoodTeam 前端产品需求文档（QM 参考版）

> 阶段：Product / Requirement
>
> 状态：Proposed，等待 PRD gate 审批
>
> 日期：2026-08-13
>
> 范围：前端产品需求与信息边界，不包含视觉设计、交互稿、组件方案或技术实现方案
>
> 参考：GoodTeam 当前产品前端、控制面能力，以及 `yc-software/qm` 2026-07-31 `main` 版本

> 验证等级：Tier 0（仅文档；本次不改变可执行契约）

> 变更契约区域：产品前端下一切片的需求边界；任何后续 API、状态、审批、执行身份或 ledger 改动均需重新按 Tier 2 至 Tier 4 分类

## 0. 决策摘要

### 0.1 本次请求审批的产品决定

GoodTeam 前端下一阶段不做单纯视觉翻新，也不复制 QM 的通用聊天界面。建议批准以下产品方向：

1. 保留 **Project** 作为安全与资源作用域；
2. 将“一项用户委派的交付工作”定义为 Project 内的主要使用上下文；
3. 在该上下文内汇聚现有 run、workflow stage、task、worker activity、artifact、evidence 和 review 投影；
4. 将跨项目需要人工处理的事项统一投影为 **Attention**，但不创造新的审批权威；
5. 第一实施切片只完成“找到工作、理解进展、处理下一动作、回到来源”的只读投影和已有 mutation 接入；
6. 运行中 steer、pause/resume、任意并行 run 和新 Work item 后端模型不在第一切片中，必须经过单独 RFC。

### 0.2 下一产品切片

**切片名称：工作上下文与 Attention 闭环。**

该切片解决一个具体问题：用户目前需要在 Home、Work、Review、Inbox 和 Activity 之间自行拼接一项交付工作的当前状态与下一动作。

切片完成后，用户应能：

- 从 Project 或 Attention 打开某个既有 run 的稳定工作上下文；
- 看到目标、当前阶段、当前责任角色、最近进展、阻塞、待处理事项和关键产物；
- 使用现有后端已经授权的输入、审批、重试或终止动作；
- 处理后回到同一个上下文并看到持久状态变化；
- 从 artifact、evidence 和 review 返回产生它们的 run。

### 0.3 本次不请求批准的事项

- 不批准新的持久 `Work item` 数据模型；第一切片以现有 Project + run 身份为准。
- 不批准向运行中 worker 追加 prompt 的 steer 协议。
- 不批准并行 run、并行写域或新的 worktree 并发策略。
- 不批准新的审批、自动批准或前端本地 capability 判断。
- 不批准视觉方向、导航版式、组件库或前端技术重写。
- 不批准多人组织、Slack scope、RBAC、通知或跨项目知识复用。

### 0.4 审批条件

PRD gate 应在以下条件满足时批准：

- 认可“Project 是作用域、run 是第一切片工作身份、Attention 是服务器投影”的边界；
- 认可第一切片不依赖新 steer 或并发执行能力；
- 第 18 节中标为“切片阻塞”的问题已有决定；
- Architect 可以基于现有 API 和状态模型做 capability inventory，而无需猜测产品语义。

## 1. 文档目的

本文档重新定义 GoodTeam 前端需要解决的用户问题，以及用户在界面中必须能够完成的工作。

本文档回答：

- 前端服务谁；
- 用户进入 GoodTeam 后要完成什么；
- 哪些对象和状态必须被看见；
- 哪些动作必须可执行、可恢复、可追溯；
- 哪些能力属于首版，哪些延后；
- 如何判断前端重构完成。

本文档不回答：

- 页面长什么样；
- 使用侧栏、标签页、分栏还是抽屉；
- 颜色、字体、间距、动效和品牌风格；
- React 组件如何拆分；
- API、状态管理和实时通道如何实现。

这些内容应在需求确认后进入信息架构、交互设计和技术设计。

### 1.1 证据与可信度

本文档遵循 GoodTeam 的证据优先原则。已验证事实、外部参考和产品提案不得混写。

| 类型 | 结论 | 来源 | 可信度与限制 |
| --- | --- | --- | --- |
| 已验证 | GoodTeam 前端已有 Projects、Home、Work、Review、Knowledge、Inbox、Activity、Settings 路由 | `frontend/src/app/AppRouter.jsx` | 代码事实，2026-08-13 本地检查 |
| 已验证 | 后端是 readiness、workflow、approval、evidence、execution identity 和 mutation 的权威 | `openwiki/architecture/control-plane.md`、`openwiki/product/web-experience.md` | 架构不变量 |
| 已验证 | 当前 Work 页面围绕 run 展示 current、plan、execution、messages、delivery | `frontend/src/features/runs/WorkPage.jsx` | 当前产品投影，不代表全部后端能力 |
| 已验证 | 当前 Inbox 已投影需要注意的项目/状态，Review 已承载正式决定 | `frontend/src/features/inbox/InboxPage.jsx`、`frontend/src/features/reviews/ReviewPage.jsx` | 需要在 RFC 阶段盘点具体 mutation parity |
| 已验证 | 当前 Project 导入/readiness 已有幂等、只读导入和 capability gate 约束 | `openwiki/workflows/project-readiness.md`、现有前端与 API 测试 | 不得在重构中回退 |
| 外部参考 | QM 以 session 为主要工作入口，并把工具活动和上下文资源连接在一起 | `yc-software/qm` 2026-07-31 `main` 的 README、Web UI 文档和截图 | 只作为交互问题的研究证据，不是 GoodTeam 契约 |
| 产品提案 | 将既有 run 包装成用户可理解的“工作上下文” | 本 PRD | 待审批；第一切片不新增持久模型 |
| 未验证 | active worker 是否支持安全 steer、pause/resume | 尚无完成的 capability audit | 不得作为第一切片依赖 |
| 未验证 | 一个 Project 是否能安全存在多个并行 active run | 尚无 write-domain/worktree 并发契约 | 不得由前端先行承诺 |

若实现调查发现本文档中的“已验证”结论与当前 `origin/main` 或实际 API 不一致，Architect 必须暂停相关设计，将差异记录为 contract drift 并返回 PRD gate，而不是用前端兼容逻辑掩盖。

## 2. 背景与判断

### 2.1 GoodTeam 的产品本质

GoodTeam 不是通用聊天机器人，也不是传统项目管理工具。它是一个本地优先的 AI 软件交付控制面：

- 将产品需求、架构、实现、评审、测试和发布组织为可持续运行的交付过程；
- 协调多个专业角色和外部 coding agent；
- 在关键节点保留人工决策权；
- 保存工作状态、产物、证据、审批和来源记录；
- 允许用户在失败、中断或重启后继续工作。

因此，前端的首要目标不是“展示所有后端模块”，而是让用户能够低负担地委派工作、掌握进展、及时介入并作出有证据的决定。

### 2.2 当前前端的问题

现有产品前端已经建立 Projects、Home、Work、Review、Knowledge、Inbox、Activity 和 Settings 等路由，也完成了项目导入与 readiness 的一部分能力。但从用户工作角度仍有以下问题：

1. **项目和模块强，会话与任务弱。** 用户需要在多个页面之间拼接“我交代了什么、现在谁在做、刚刚发生了什么、我能否补充要求”。
2. **执行过程更像状态报表，而不是可持续协作。** 运行中的 agent、工具调用、输出、阻塞和用户补充指令没有形成统一工作记录。
3. **Home、Inbox、Work 和 Review 的“下一步”存在重复表达。** 用户可能看到多个状态，但不容易判断此刻唯一最重要的动作。
4. **资源与产生资源的工作过程分离。** 产物、文件、记忆、消息、证据和执行记录能被查看，但上下文跳转和来源关系不够直接。
5. **并行工作不易掌握。** 当多个项目、run、task 或 reviewer 同时运行时，用户缺少统一的活动工作面与介入入口。
6. **高级能力暴露方式偏模块化。** worker、model、memory、delivery、diagnostics 等对系统重要，但不应与日常交付动作争夺注意力。

### 2.3 从 QM 借鉴什么

QM 将“人与 agent 的持续协作”放在主界面，并使会话、工具执行和上下文资源形成一个连续工作环境。可借鉴的产品原则包括：

| QM 模式 | 对 GoodTeam 的启发 | 不应照搬的部分 |
| --- | --- | --- |
| 会话是主要工作入口 | 用户委派、agent 执行、补充信息和结果应形成连续工作记录 | GoodTeam 不能退化成无流程约束的聊天工具 |
| 多个并行 session 可同时保持上下文 | 用户应快速切换多个正在运行的交付任务，并看见各自状态 | 不要求首版实现任意分屏或窗口系统 |
| 实时显示工具调用并允许 steer | 运行中应可查看进度，并在策略允许时补充或纠正指令 | 不允许用户绕过审批、证据或执行身份边界 |
| Files、Crons、Deploys、Memory、Skills 与会话互通 | 资源需要显示来源、用途和关联工作，并能回到产生它的上下文 | 不把所有系统能力都提升为一级导航 |
| personal/shared scope 明确 | GoodTeam 需要明确 workspace、project、run、task 的上下文边界 | 当前阶段不引入多人组织、Slack scope 或共享权限模型 |
| URL 可恢复当前会话或视图 | 项目、run、task、review 和 artifact 必须可刷新、可分享、可恢复 | 不在 URL 中暴露敏感凭证或临时 capability |

QM 是需求研究对象，不是 GoodTeam 的目标形态。GoodTeam 必须继续以交付流程、证据、审批和可审计性为核心差异。

## 3. 产品目标

### 3.1 核心目标

1. 用户可以用自然语言发起一项明确的软件交付工作，并立即知道 GoodTeam 如何理解和处理它。
2. 用户可以在一个连续工作上下文中查看计划、agent 活动、工具执行、消息、产物和证据。
3. 用户可以随时知道所有项目中哪些工作正在进行、哪些需要自己处理、哪些已经完成。
4. 用户可以在允许的阶段补充要求、回答问题、纠正方向、暂停或终止工作。
5. 所有关键决定都能在同一上下文中查看相关产物、证据、建议、异议和影响。
6. 用户离开、刷新浏览器或服务重启后，能够恢复到原来的项目、工作项和查看位置。
7. 前端只表达后端授权的能力，不自行推导审批权限、readiness、执行身份或发布资格。

### 3.2 用户价值与产品价值

**用户价值：** 用户不再需要理解 GoodTeam 的内部模块划分，也能委派、跟进和验收一项交付工作；注意力从持续巡检系统状态转移到目标澄清、风险判断和 gate-level decision。

**产品价值：**

- 将 GoodTeam 已有的 workflow、worker、artifact、evidence 和 review 能力组织成一个可理解的交付体验，而不是继续增加平行模块；
- 让“本地优先、多 agent、证据驱动、人工门禁”成为用户可直接感知的产品差异；
- 降低首次使用和日常跟进成本，为后续协作图、持续记忆和更强执行交互提供稳定产品上下文；
- 以服务器权威投影和 parity gate 控制迁移风险，避免为了前端体验削弱控制面可信度。

### 3.3 成功结果

对于日常交付，用户不需要理解 GoodTeam 内部节点名称或后端模块，也可以回答：

- 我让团队做什么？
- 现在进行到哪里？
- 哪些 agent 正在做什么？
- 它们刚刚做了什么，依据是什么？
- 当前是否偏离目标或遇到风险？
- 现在需要我做什么？
- 我介入之后会发生什么？
- 最终改了什么、验证了什么、还有什么风险？

### 3.4 非目标

- 不将 GoodTeam 改造成通用个人助理或企业聊天工具。
- 不在本轮引入多人实时协作、组织成员管理和 RBAC。
- 不替代 GitHub、CI、部署平台或 coding agent 的专业界面。
- 不在前端复制 workflow、approval、evidence 或 security policy。
- 不要求把所有 CLI 和诊断功能都迁移到日常界面。
- 不在本需求阶段决定现有页面全部推翻或技术栈重写。
- 不以“功能数量与旧版完全一致”作为可用性的唯一标准；低频诊断能力可以被收纳，但不能丢失。

## 4. 目标用户

### 4.1 首要用户：技术型创始人或项目负责人

特点：

- 同时负责产品判断、技术方向和交付结果；
- 可以读 PRD、架构、diff 和测试证据，但不希望持续盯着 agent；
- 同时推进一个或多个产品；
- 希望委派执行，但保留关键决定权；
- 关注代码与数据的本地所有权。

主要任务：

- 发起工作；
- 澄清目标和约束；
- 查看并纠正执行方向；
- 处理阻塞和审批；
- 验收结果与风险；
- 在多个项目之间管理注意力。

### 4.2 次要用户：工程负责人或独立开发者

主要任务：

- 导入并理解现有代码库；
- 配置 worker、model、验证命令和交付约束；
- 查看 agent 的具体执行过程；
- 检查变更、测试、CI 和发布证据；
- 诊断失败并恢复任务。

### 4.3 当前不覆盖的用户

- 只想通过一句话生成一次性网页的非技术用户；
- 需要完整团队排期、工时和绩效管理的项目经理；
- 需要多人权限、资源计费和企业审计导出的组织管理员。

## 5. 核心产品对象

前端必须以稳定、可定位的产品对象组织信息，而不是直接暴露后端模块。

以下对象分为两类：

- **权威对象**：Workspace、Project、Run、Task、Review、Artifact、Evidence、Memory，沿用后端持久身份；
- **产品投影**：Work context、Agent activity、Attention item，用于组织和解释权威对象，不得在没有 RFC 的情况下成为第二套状态机。

| 对象 | 用户理解 | 必须展示的核心信息 |
| --- | --- | --- |
| Workspace | 当前 GoodTeam 管理的本地工作空间 | 服务状态、项目集合、全局默认配置 |
| Project | 一个可持续交付的软件产品或代码库 | 身份、root、模式、readiness、当前工作、风险 |
| Work context（产品投影） | 用户委派的一项有结果定义的工作在前端中的连续上下文 | 第一切片绑定既有 Run；展示目标、范围、状态、发起时间、当前责任方 |
| Run | 当前控制面中一次可持久恢复的实际执行 | 用户目标、阶段、计划、执行身份、开始/结束、结果 |
| Task | Run 中可执行、可依赖、可分派的工作单元 | 负责人、依赖、状态、输入、输出、阻塞 |
| Agent activity（产品投影） | agent 在某个 task 中的连续工作记录 | 由持久 worker/message/action/ledger 事件投影消息、工具调用、等待、错误和结果 |
| Attention item（产品投影） | 必须由用户处理才能继续或降低风险的事项 | 由 readiness、approval、blocked execution 等权威状态投影原因、影响、动作和目标上下文 |
| Review | 需要人作出决定的正式门禁 | 对象版本、建议、证据、异议、策略、决定 |
| Artifact | PRD、RFC、计划、报告、文件或生成结果 | 类型、版本、来源、关联工作、更新时间、内容 |
| Evidence | 支撑状态或决定的可验证事实 | 来源、采集时间、绑定对象/SHA、结果、有效性 |
| Memory | 从工作和决定中沉淀、可再次使用的知识 | 事实、来源、范围、时间、引用、冲突/过期状态 |

### 5.1 对象关系要求

- 长期模型允许一个 Project 对应多项用户工作；第一切片仅为既有 Run 提供 Work context。
- `Work item -> 多次 Run` 是候选长期模型，不属于第一切片的持久化契约。
- 一个 Run 可以包含多个依赖或并行 Task。
- Agent activity 必须归属明确的 Run 和 Task，不能成为无来源日志。
- Review 必须绑定具体对象版本和 evidence snapshot。
- Artifact、Evidence 和 Memory 必须能回到其来源 Work item、Run、Task 或 Review。
- Attention item 必须能直接打开需要用户处理的具体位置。

## 6. 顶层用户工作模型

前端需要支持三种不同但互相连通的工作视角。

### 6.1 全局关注视角

回答“所有项目中，现在什么值得我关注”。至少包括：

- 需要用户输入或决定的事项；
- 失败、阻塞或等待过久的工作；
- 正在执行的工作；
- 最近完成且尚未查看的结果；
- 每个项目的当前状态和最近活动。

### 6.2 项目视角

回答“这个项目当前处于什么状态，下一步是什么”。至少包括：

- readiness 和可用能力；
- 当前目标、活跃 Work item 与历史 Work item；
- 项目级风险、约束和事实；
- 项目产物、知识和配置入口。

### 6.3 工作视角

回答“这项工作正在如何推进，我是否需要介入”。至少包括：

- 用户目标和已确认范围；
- 当前阶段与整体计划；
- Task 与 agent 分工；
- 连续执行记录和当前动作；
- 用户与 agent 之间的消息和澄清；
- 产物、变更、证据和决定；
- 当前可执行的介入动作。

工作视角应成为用户日常停留时间最长的区域，但这是一项产品要求，不预设具体页面布局。第一切片中的工作视角必须绑定现有 `project_key + run_id`，不得生成与控制面脱节的浏览器本地身份。

## 7. 核心用户流程

本节描述目标产品流程。第一切片只实现第 8.0 节列出的子集；涉及新 Work item、steer、pause/resume 或并发执行的步骤均为后续候选能力，不构成当前实现授权。

### 7.1 首次进入与项目导入

1. 用户进入 GoodTeam，能够判断服务是否可用。
2. 用户选择一个本地目录。
3. 系统只读校验并创建或解析唯一 Project。
4. 用户立即进入可恢复的 Project 上下文。
5. 系统说明项目当前模式、readiness、已知事实和下一步。
6. 需要扫描、模型调用或写入 target root 的步骤必须分别说明影响并取得必要确认。
7. 项目达到最低 readiness 后，系统明确开放可执行能力。

### 7.2 发起一项工作

> 路线图流程：需要在 S1 后单独定义 Work item/start contract；S1 仅打开和理解既有 Run。

1. 用户在 Project 上下文中描述目标。
2. 系统保留原始输入，并给出结构化理解：期望结果、范围、约束、未知项和执行模式。
3. 如果关键输入缺失，系统只询问会实质改变结果的问题。
4. 用户确认后生成 Work item，并进入其工作上下文。
5. 系统展示下一步将发生什么，包括是否调用模型、创建 sandbox/worktree、修改文件或产生费用。
6. 系统开始 Run，或在需要门禁时进入等待状态。

### 7.3 跟进正在执行的工作

1. 用户可以从全局或 Project 找到所有 active Work item。
2. 打开后恢复到最近查看位置，并显示当前阶段、当前 Task 和 agent 状态。
3. 用户可以看到最近活动，而无需阅读完整原始日志。
4. 用户可以按需展开工具调用、命令、文件变更和完整输出。
5. 用户能区分“仍在正常工作”“等待外部系统”“等待用户”“失败”和“已停止”。
6. 后续在完成 steer RFC 且后端明确允许时，用户可以补充要求或纠正方向；补充内容进入持久工作记录。
7. 后续 steer 能力必须显示该指令被接收、作用于哪个 Task、是否导致重规划，以及何时生效。

### 7.4 处理输入请求和阻塞

1. 所有需要用户处理的事项进入统一 Attention 集合。
2. 每项说明为什么需要用户、如果不处理会怎样、系统已尝试什么。
3. 用户可直接在该事项中回答、选择、授权、重试或终止。
4. 敏感、付费、破坏性或 target-root 写入动作显示具体影响范围。
5. 提交后显示持久结果，重复提交不得导致重复副作用。
6. 处理完成后返回原 Work item，并显示工作是否恢复。

### 7.5 评审与批准

1. 用户从 Attention 或 Work item 进入 Review。
2. 界面同时提供被评审对象、版本、变更摘要、agent 建议、异议、证据覆盖和缺失证据。
3. 用户可查看原始 Artifact、diff、测试结果、CI/PR 绑定和 reviewer provenance。
4. Approve、Request changes 和 Reject 是语义不同的动作。
5. 当策略要求时，用户必须填写理由或回答开放问题。
6. 提交前明确说明决定将推动哪个状态变化。
7. 提交后记录 actor、origin、时间、review mode、evidence snapshot 和目标版本。
8. 若对象已更新或证据过期，旧 Review 不得被静默用于新版本。

### 7.6 完成、验收与继续工作

1. Work item 完成时，系统提供面向用户目标的结果摘要。
2. 摘要至少包含：完成内容、变更范围、验证结果、交付状态、遗留风险和建议下一步。
3. 用户可回看完整工作记录及所有 Artifact、Evidence 和 Review。
4. 用户可基于结果发起后续工作，并保留来源关系。
5. 历史 Work item 默认为只读；任何重新执行形成新的 Run 并保留旧记录。

第一切片将上述 `Work item` 解释为绑定某个既有 Run 的 Work context；是否引入独立持久 Work item 由后续 RFC 决定。

## 8. 功能需求

需求优先级：

- **P0**：目标产品闭环最终必须具备；不表示同一 PR 一次交付；
- **P1**：P0 稳定后显著提高效率和透明度；
- **P2**：后续扩展。

需求进入当前实现范围还必须出现在第 8.0 节的 `S1` 列表中。只有 `P0`、但没有 `S1` 标记的需求是已认可方向，不是当前实施授权。

### 8.0 下一切片需求（S1）

| ID | 需求 | 依赖的现有权威 | 验收证据 |
| --- | --- | --- | --- |
| S1-01 | 为既有 Run 提供稳定 Work context 地址，刷新后恢复同一 Run | Project/run identity | 路由测试 + 隔离 workspace 浏览器证据 |
| S1-02 | 在一个上下文展示目标、当前 workflow stage、当前责任角色、最近持久活动、阻塞和关键 artifact | workflow state、task/worker/action projections、artifact registry | API contract 测试 + 前端状态测试 |
| S1-03 | Attention 统一投影已有 readiness input、approval 和可恢复 blocked state | 后端 capability、readiness、review/approval、execution state | 投影 contract 测试；不得由前端推导权限 |
| S1-04 | Attention 可深链到具体 Project/Run/Review；处理后返回来源且保留筛选 | stable resource identity | 路由/返回路径测试 + Playwright |
| S1-05 | 在 Work context 接入后端已存在并授权的 input、approval、retry、terminate mutation | 现有 mutation API 和 server capability | focused backend mutation tests + full control-plane suite |
| S1-06 | 每次 mutation 显示 pending/success/failure/stale，重复提交不产生重复副作用 | server idempotency/stale protection | idempotency/stale tests + UI tests |
| S1-07 | Artifact、Evidence 和 Review 显示来源并可回到绑定 Run | artifact/evidence/review provenance | provenance contract tests + navigation E2E |
| S1-08 | 保持现有 Project import/readiness、Review identity、ledger 和 evidence 行为不变 | 当前 capability parity matrix | parity regression + isolated live smoke |
| S1-09 | `zh-CN` / `en`、键盘和桌面/小屏均可完成查看、处理、返回闭环 | i18n catalogs、accessible semantics | catalog、keyboard、viewport evidence |
| S1-10 | 旧入口在迁移期间有明确映射；未迁移能力仍可访问 | `docs/product-ui-capability-parity.md` | 更新后的 parity matrix，无 silent capability loss |

### 8.0.1 S1 明确排除

- 新建持久 Work item 及其多 Run 关系；
- active worker prompt steer；
- 新增 pause/resume 语义；
- 多 active run 或任意并行 Task 执行；
- 新的实时传输协议；若现有轮询满足状态及时性，可继续使用；
- 全局搜索、桌面通知、多人协作、技能管理和跨项目 Memory；
- 视觉系统重做。

### 8.1 全局入口与注意力管理

- **FR-G01 / P0**：系统必须提供所有项目的统一入口，并展示项目状态、活跃工作、待处理数和最近活动。
- **FR-G02 / P0**：系统必须提供统一 Attention 集合，覆盖用户输入、审批、失败恢复、敏感授权和过期决定。
- **FR-G03 / P0**：Attention item 必须可直接导航到具体 Project、Work item、Task 或 Review。
- **FR-G04 / P0**：系统必须明确区分“需要我处理”“正在运行”“等待外部条件”“已完成”。
- **FR-G05 / P1**：用户可以按 Project、类型、状态和时间筛选 Attention 与历史活动。
- **FR-G06 / P1**：系统应记录用户是否已查看完成结果，但“已读”不能改变控制面状态。
- **FR-G07 / P2**：支持全局搜索 Project、Work item、Run、Artifact 和 Evidence。

### 8.2 Project 管理与 readiness

- **FR-P01 / P0**：导入同一 canonical root 必须解析到同一 Project，不能重复创建。
- **FR-P02 / P0**：最小导入不得修改目标目录、初始化 Git、调用模型或扫描全部文件正文。
- **FR-P03 / P0**：Project 必须展示 lifecycle、mode、readiness steps、当前能力和阻塞原因。
- **FR-P04 / P0**：readiness step 必须展示状态、是否必需、尝试次数、输入要求、错误、产物和可恢复动作。
- **FR-P05 / P0**：由服务器 capability projection 决定是否允许开始工作或执行高风险动作。
- **FR-P06 / P0**：刷新、浏览器重开或服务重启后，Project 与 readiness 进度必须恢复。
- **FR-P07 / P1**：Project 提供历史 Work item 与 active Work item 的清晰入口。
- **FR-P08 / P1**：Project 显示已确认约束、禁止操作、关键命令和事实的最新状态及来源。

### 8.3 Work item 与执行上下文

- **FR-W01 / P0**：用户可以通过自然语言创建 Work item，原始输入不得丢失或被覆盖。
- **FR-W02 / P0**：Work item 必须有稳定 ID、目标、状态、Project、创建时间和当前 Run。
- **FR-W03 / P0**：开始执行前必须展示系统对目标、范围、约束和未知项的理解。
- **FR-W04 / P0**：工作视角必须在一个上下文中关联阶段、Task、agent activity、消息、Artifact、Evidence 和 Review。
- **FR-W05 / P0**：用户必须能看到当前正在发生的动作、责任 agent 和最近一次有效进展时间。
- **FR-W06 / P0**：运行状态至少区分 queued、running、needs_input、blocked、failed、paused、completed、terminated。
- **FR-W07 / P0**：Work item 与 Run 的关键状态变更必须持久化，不得只存在于浏览器。
- **FR-W08 / P0**：历史 Run 必须可查看且不可与 active Run 混淆。
- **FR-W09 / P1**：用户可以在多个 active Work item 之间快速切换，并保留各自查看位置。
- **FR-W10 / P1**：系统提供本 Work item 的未读/新活动提示，且不影响执行状态。
- **FR-W11 / P2**：支持用户自定义标题、归档和收藏 Work item。

### 8.4 Task、agent 与工具执行透明度

- **FR-A01 / P0**：Task 必须展示负责人、依赖、输入、状态、阻塞和输出。
- **FR-A02 / P0**：agent activity 必须按时间顺序展示重要消息、工具调用、结果和状态变化。
- **FR-A03 / P0**：默认摘要必须帮助用户理解进展；完整命令、stdout/stderr 和原始事件按需查看。
- **FR-A04 / P0**：界面必须区分 agent 的计划、正在执行的事实、已经验证的结果和推测。
- **FR-A05 / P0**：工具调用必须显示工具类型、目标、开始/结束、结果状态和可用证据引用。
- **FR-A06 / P0**：失败必须显示稳定错误、影响、已执行副作用、可恢复路径和 request/execution identity。
- **FR-A07 / P1**：并行 Task 必须分别显示状态，不能用单一总进度掩盖阻塞或失败。
- **FR-A08 / P1**：用户可按 Task 或 agent 过滤活动记录。
- **FR-A09 / P1**：长时间无进展时系统应标识潜在停滞，但不能仅凭前端计时判定失败。

### 8.5 用户介入与 steer

- **FR-S01 / P0**：当后端允许时，用户可向 active Work item 提交补充信息或方向修正。
- **FR-S02 / P0**：系统必须说明介入内容将应用于哪个 Run/Task，以及可能触发继续、重试或重规划中的哪一种。
- **FR-S03 / P0**：用户消息必须进入持久审计记录，并保留 actor、时间和来源。
- **FR-S04 / P0**：不可在当前阶段执行的指令必须明确拒绝或转为后续 Work item，不能假装已应用。
- **FR-S05 / P0**：用户可以在后端能力允许时暂停、恢复或终止 Run，并在确认前看到影响。
- **FR-S06 / P0**：steer 不得绕过正式 Review、target-root write confirmation、security gate 或 release gate。
- **FR-S07 / P1**：如果介入导致计划、范围或验收条件变化，系统必须显示变更前后差异并要求必要确认。

### 8.6 Review 与决策

- **FR-R01 / P0**：Review 必须绑定稳定 review ID、对象版本、artifact version 和 evidence snapshot。
- **FR-R02 / P0**：Review 必须呈现建议、依据、证据缺口、异议和 policy 状态。
- **FR-R03 / P0**：用户可以从摘要下钻到原始 Artifact、diff、测试、CI、PR 和 reviewer 输出。
- **FR-R04 / P0**：Approve、Request changes、Reject 的后果必须在提交前明确。
- **FR-R05 / P0**：理由要求由服务器策略决定，前端负责收集和校验必要输入。
- **FR-R06 / P0**：提交决定必须具有防重复和 stale-object 保护。
- **FR-R07 / P0**：决定结果必须回写 Work item 活动记录和全局 Activity。
- **FR-R08 / P1**：多 reviewer 场景必须显示每个 reviewer 的身份、结论、证据和分歧，不能只显示聚合结果。
- **FR-R09 / P1**：Request changes 后必须显示反馈被路由到哪个 Task/角色以及修订进度。

### 8.7 Artifact、Evidence 与 Knowledge

- **FR-K01 / P0**：Artifact 必须展示类型、版本、来源、关联 Work item/Run/Task、创建者和更新时间。
- **FR-K02 / P0**：用户可以从工作记录打开 Artifact，也可以从 Artifact 返回来源工作上下文。
- **FR-K03 / P0**：Evidence 必须显示采集来源、时间、结果、有效性和绑定对象；与 SHA/PR/CI 相关时展示绑定关系。
- **FR-K04 / P0**：生成内容与源码事实、人工输入和外部证据必须可区分。
- **FR-K05 / P1**：Project Knowledge 支持按文档、产物、仓库理解、命令、约束和 Memory 浏览。
- **FR-K06 / P1**：Memory 条目必须显示来源、范围、时间和引用，冲突或过期时明确标记。
- **FR-K07 / P1**：用户可以从 Memory 引用回到产生它的 Artifact 或工作记录。
- **FR-K08 / P2**：支持跨项目知识搜索和受控复用，并明确来源 Project。

### 8.8 配置与高级能力

- **FR-C01 / P0**：全局配置与 Project 配置必须明确区分，Project override 的生效范围可见。
- **FR-C02 / P0**：worker/model 的可用性、默认值和 preflight 结果必须可查看。
- **FR-C03 / P0**：安全、审批和 target-root 写入边界必须以用户可理解的语言说明。
- **FR-C04 / P1**：integration、delivery、smoke 和 diagnostics 应可访问，但不与日常 Work item 争夺主入口。
- **FR-C05 / P1**：原始配置和诊断数据可按需查看，不应作为正常状态的主要表达。
- **FR-C06 / P2**：支持按 Project 管理可用技能或 agent 能力，但必须受服务器授权控制。

## 9. 状态与反馈要求

所有核心对象和动作必须处理以下状态，不能用通用空白页或浏览器 alert 代替：

| 状态 | 用户必须知道 |
| --- | --- |
| Loading | 正在加载什么；现有上下文不应消失 |
| Empty | 为什么为空；正确的下一步是什么 |
| Queued | 等待什么；是否可以取消 |
| Running | 谁在做什么；最近进展；是否允许介入 |
| Needs input | 需要用户提供什么；为什么；影响是什么 |
| Blocked | 阻塞原因；已尝试什么；如何恢复 |
| Failed | 哪一步失败；是否产生副作用；错误与 request ID |
| Partial success | 哪些成功、哪些失败；哪些结果仍可使用 |
| Stale | 哪些信息可能过期；最后更新时间；如何刷新 |
| Offline | 当前内容是否缓存；mutation 未成功时不得伪装成功 |
| Permission denied | 权限边界；可行恢复方式 |
| Paused | 谁暂停；何时暂停；如何恢复 |
| Terminated | 谁终止；已保留什么；能否新建 Run |
| Completed | 完成结果、验证、风险和下一步 |

### 9.1 时间与进度

- 所有 active 对象显示最近一次有效状态更新时间。
- 不使用无法由真实任务数据支持的虚假百分比进度。
- 可以展示阶段完成度、Task 计数或 evidence coverage，但必须说明统计口径。
- 前端计时只能表示等待时长，不能独立宣告任务超时或失败。

## 10. 导航与恢复要求

本节定义可定位性，不规定导航控件或版式。

- Project、Work item、Run、Task、Review 和 Artifact 必须有稳定可构造的地址或深链标识。
- 刷新后恢复相同对象和主要查看上下文。
- 从 Attention 进入处理后，应可返回原列表并保留筛选状态。
- 从 Activity、Artifact、Evidence 或 Memory 可以回到来源工作上下文。
- 无权访问、已归档或不存在的对象必须给出明确状态和安全返回路径。
- URL 不得包含 secret、session capability、未脱敏日志或一次性授权材料。
- 历史链接不得因为出现新的 active Run 而静默跳转到另一个对象。

## 11. 权限、安全与控制面边界

1. 前端只消费服务器返回的 capability 和 policy，不自行推断动作是否可用。
2. 所有 mutation 必须绑定明确 actor、origin、Project 和目标对象。
3. 审批、worker dispatch、ledger write、evidence snapshot、PR/CI binding 和 security action 继续由后端负责。
4. 破坏性动作、付费动作、外部发布、凭据使用和 target-root 写入必须在执行前说明具体影响。
5. 敏感字段、token、credential material 和未脱敏输出不得进入浏览器状态、日志或 URL。
6. stale capability、stale Review 或重复提交必须由服务器拒绝，前端显示可恢复说明。
7. 前端不得因为轮询失败而推断运行已停止，也不得因为 UI optimistic update 而宣告决定已生效。
8. 审计记录对用户可读，但不可从前端修改或删除。

## 12. 国际化、可访问性与响应式需求

### 12.1 国际化

- P0 支持 `zh-CN`、`en` 和自动语言偏好。
- 所有用户可见文案使用语义化翻译键。
- 后端状态 ID、错误码、路径、命令、模型名、Git ref 和证据原文不翻译。
- 日期、数字、相对时间和列表使用 locale-aware 格式。
- 中英文切换不得导致关键动作、错误恢复或状态信息不可见。

### 12.2 可访问性

- P0 达到 WCAG 2.2 AA。
- 所有核心流程可使用键盘完成。
- 状态变化、流式活动和 mutation 结果需要适当的辅助技术通知，但不得造成持续噪音。
- 颜色不能是唯一状态信号。
- 焦点在页面切换、错误、对话确认和异步完成后保持合理位置。
- 原始日志、diff 和长文档必须支持可读的语义结构或等价文本。

### 12.3 响应式

- 桌面端必须完整支持 Work 与 Review 的高密度信息。
- 小屏必须保留发起工作、查看状态、回答输入、审批、暂停/终止和查看结果等关键流程。
- 小屏可以减少并列信息，但不能删除证据、风险或决定后果。
- 所有下钻路径必须有明确返回方式，并恢复上一级上下文。

## 13. 性能与可靠性需求

- Project 列表和 Attention 首屏应优先返回可行动信息，低优先级统计可延后加载。
- 打开 active Work item 后，应先显示持久状态，再持续更新新活动。
- 大日志、大 diff 和长 Artifact 必须按需加载或分段呈现，不能阻塞核心状态。
- 活动更新中断时自动降级为可恢复刷新，并明确最后成功更新时间。
- route 或对象切换后应取消无关请求，旧响应不得覆盖新上下文。
- mutation 必须具备明确 pending、success、failure 状态，重复点击不得产生重复副作用。
- 服务重启后，前端能够重新附着到仍然有效的 active Run 或正确显示其中断状态。

## 14. 产品路线图

### 14.1 近期：S1 工作上下文与 Attention 闭环

唯一获本 PRD 请求实施授权的范围是第 8.0 节 `S1-01` 至 `S1-10`。S1 应拆成可独立审查的 API projection、frontend route、existing mutation integration 和 parity/QA 任务，不以一个混合大 PR 交付。

### 14.2 中期：P0 连续交付工作台

完成必要 RFC 后，P0 目标闭环包括：

1. Projects 与只读最小导入；
2. Project readiness 与 capability；
3. 通过自然语言创建 Work item；
4. 持久 Work item / Run 工作上下文；
5. Task、agent activity 和工具执行摘要；
6. active 工作的持续更新与恢复；
7. 统一 Attention；
8. 输入、steer、暂停、恢复、终止；
9. Evidence-backed Review；
10. 完成结果、Artifact 和 Evidence 回溯；
11. 中英文、桌面/小屏关键路径和无障碍；
12. 控制面安全边界与全状态处理。

### 14.3 P1：并行效率与知识闭环

1. 多 active Work item 快速切换与新活动提示；
2. 并行 Task 和多 reviewer 的清晰投影；
3. Activity 筛选与已查看状态；
4. Artifact、Evidence、Memory 的双向来源导航；
5. Project Knowledge 的完整浏览与搜索；
6. 重规划和范围变更差异；
7. integration、delivery、smoke 和 diagnostics 的高级入口。

### 14.4 P2：扩展能力

1. 全局搜索；
2. 跨项目知识复用；
3. Work item 收藏、归档和自定义管理；
4. Project 级技能与 agent 能力管理；
5. 多人协作和组织权限模型，需单独立项。

## 15. 验收标准

### 15.0 S1 放行标准

S1 只有同时满足以下条件才视为完成：

1. 用户从 Project 打开一个既有 active 或 historical Run 后，URL、刷新和浏览器返回始终指向同一 `project_key + run_id`。
2. Work context 显示的数据均能追溯到持久后端字段或事件；断网或刷新不会用浏览器临时状态伪造进度。
3. 用户能在不访问原始 JSON 的情况下识别当前阶段、责任角色、最近进展、阻塞原因和唯一下一动作。
4. readiness input、approval 和可恢复 blocked state 出现在统一 Attention 投影中，数量和状态与后端权威数据一致。
5. Attention 深链打开正确对象；成功处理、取消、失败或 stale rejection 后均能返回原来源，并保持筛选上下文。
6. 任何被接入的 mutation 都继续使用现有 server capability、actor/origin、idempotency、review identity 和 ledger contract。
7. artifact、evidence 和 review 的来源导航不会绑定到错误 Run、错误 artifact version、错误 SHA 或过期 evidence snapshot。
8. `docs/product-ui-capability-parity.md` 已更新；任何未迁移能力都有保留入口和责任归属。
9. `zh-CN` 与 `en`、键盘操作、桌面和小屏核心闭环均有可复现证据。
10. 通过第 20 节规定的验证，且 isolated live smoke 未使用真实活跃项目 workspace。

### 15.1 核心体验验收

1. 新用户可以导入一个可读代码目录，且导入本身不修改该目录、不调用模型。
2. 用户可以在 ready Project 中用自然语言发起工作，并看到系统保留原始目标和结构化理解。
3. 用户可以从同一工作上下文理解当前阶段、Task、agent 活动、工具执行、产物、证据和下一步。
4. 用户离开再返回或服务重启后，可以恢复同一 Work item 和 Run 状态。
5. 多个项目同时工作时，用户可以在统一入口识别 active、needs input、blocked 和 completed 项目。
6. 所有需要人工处理的事项都进入 Attention，且可以一步到达处理位置。
7. 用户提交补充信息后，可以确认内容作用于哪个任务以及工作是否恢复或重规划。
8. Review 同时呈现对象版本、建议、异议、证据、缺口和动作后果。
9. stale Review、重复 mutation 和未授权动作不会改变控制面状态，并提供可恢复反馈。
10. 完成结果能回答“完成了什么、验证了什么、还有什么风险”，并可追溯到完整记录。

### 15.2 完整性验收

1. P0 对象均覆盖 loading、empty、running、needs input、blocked、failed、stale、offline、partial success 和 complete 等适用状态。
2. 所有关键对象可通过稳定链接恢复，不包含敏感 capability 或凭据。
3. `zh-CN` 与 `en` 文案完整，缺失翻译在 CI 中失败。
4. 桌面和小屏均可完成发起、跟进、介入、审批和验收流程。
5. 键盘和辅助技术可以完成核心流程，满足 WCAG 2.2 AA。
6. 前端未复制 readiness、approval、evidence 或 execution policy。

### 15.3 产品可理解性验收

使用未参与开发的目标用户进行任务测试。不给内部节点说明，用户应能在合理时间内：

- 找到所有等待自己处理的事项；
- 判断某项 active 工作正在做什么；
- 找到最近一次失败的原因和恢复动作；
- 对一个 Review 作出有依据的决定；
- 找到某个 Artifact 的来源工作；
- 说明 GoodTeam 与普通聊天 agent 的核心差别。

具体时间阈值和样本规模在可用性测试计划中定义。

## 16. 数据与埋点需求

为判断目标产品是否有效，路线图至少需要以下产品事件。S1 只实现与 `S1-01` 至 `S1-10` 直接相关、且已有合法数据来源的子集。埋点不得包含源代码、prompt 正文、凭据或敏感日志。

- Project 导入开始、成功、重复解析和失败类型；
- Work item 创建、确认、开始和取消；
- Attention 展示、打开、处理和处理耗时；
- active Work item 打开、切换和返回；
- 用户 steer、回答输入、暂停、恢复和终止；
- Review 打开、证据下钻、决定类型和 stale rejection；
- Artifact/Evidence 来源跳转；
- error recovery 成功率；
- Work item 从创建到首次有效进展、首次人工介入和完成的时间。

建议核心指标：

- Time to first delegated work；
- Attention resolution time；
- 用户找到当前下一步的成功率；
- active Work item 的无效重复查看次数；
- Review 前证据查看率；
- Run 中断后的恢复成功率；
- 完成结果被查看和发起后续工作的比例。

指标用于发现体验问题，不用于改变审批责任或鼓励用户盲目批准。

## 17. 依赖与约束

- S1 需要稳定的 Run、Task、agent activity、Attention 和 capability 投影；若当前仅有内部状态，需要先定义只读产品 API。新的持久 Work item API 不属于 S1 依赖。
- active 执行需要增量活动或可轮询的持久事件；前端不能依赖进程内临时日志。
- steer、pause、resume 和 terminate 必须有明确后端语义与幂等保护。
- Review 继续依赖现有 execution identity、ledger、evidence snapshot、reviewer provenance 和 policy。
- Project import/readiness 必须保持 canonical root、只读检查、幂等和 stale-run 防护。
- Live-path 变更必须在隔离 workspace 中提供 smoke evidence，不得对真实活跃项目执行 mutation smoke。

### 17.1 假设

| ID | 假设 | 验证方式 | 不成立时的处理 |
| --- | --- | --- | --- |
| A-01 | 既有 Run 有稳定且可在 API 中解析的身份 | Architect 盘点 run API、checkpoint 和 route contract | S1 先补只读 identity projection，不创建前端替代 ID |
| A-02 | readiness、approval 和 blocked execution 足以构成 S1 Attention 来源 | 对 `InboxPage`、Review、readiness 和 execution API 做 capability inventory | 缩小 Attention 类型，并把缺失来源列入后续 backend task |
| A-03 | 现有持久事件足以生成“最近进展”，无需解析临时 stdout | 检查 action/ledger/worker/message stores 的时序和归属 | RFC 定义最小只读 activity projection；不得抓取进程日志冒充权威事件 |
| A-04 | S1 所需 mutation 已有后端授权与幂等保护 | 对每个 mutation 建立 API-to-test 映射 | 没有保护的 mutation 从 S1 移除，先做 Tier 3 后端设计 |
| A-05 | 当前轮询可支持 S1 的状态及时性 | 隔离 workspace 测量 active state 可见延迟 | 只有证据表明不够时，另立 event-stream RFC |

### 17.2 风险与缓解

| 风险 | 后果 | 缓解与 gate |
| --- | --- | --- |
| 前端创建第二套状态机 | UI 与 ledger/approval 状态漂移 | 所有状态与 capability 来自服务器；contract tests 验证投影 |
| “工作”包装掩盖真实 Run 身份 | artifact/evidence 绑定错误 | S1 路由和所有 mutation 显式携带 `project_key + run_id` |
| Attention 聚合产生重复或遗漏 | 用户重复决定或错过阻塞 | 服务器提供稳定 item identity；类型逐项做 parity 测试 |
| QM 参考导致产品聊天化 | 流程、门禁和证据被弱化 | Review 与 evidence 继续是一等对象；steer 不进入 S1 |
| 迁移期间能力丢失 | 旧诊断或控制入口不可达 | parity matrix 是移除旧入口的 gate |
| 浏览器可见变更通过单测但 live path 失败 | 用户无法恢复或处理事项 | Tier 4 isolated Playwright/live smoke |
| 范围重新膨胀成全站重写 | 长期双轨、无法验收 | S1 排除列表；按投影、路由、mutation、QA 分轨交付 |

## 18. 待决策问题

以下问题会改变产品范围，需要在进入设计前确认：

1. **[切片阻塞] 用户可见命名**：S1 使用“工作”还是“运行”？建议界面用“工作”，诊断和标识中保留 Run；不得使用“会话”弱化交付结果。
2. **[路线图] 创建入口**：用户是否只能在 Project 内发起工作，还是允许全局输入后再选择/推断 Project？建议 P0 仅在 Project 内发起，降低错误作用域风险。
3. **[路线图] steer 语义**：现有后端能否在 active worker 中安全追加指令，还是只能生成 follow-up Task / replan？UI 必须忠实于实际能力。
4. **[路线图] 并行模型**：一个 Project 是否允许多个 active Work item 或 active Run？需要由控制面明确并发、write-domain 和 worktree 规则。
5. **[切片阻塞] Attention 范围**：S1 建议只投影本地权威的 readiness input、approval 和可恢复 blocked execution；Telegram/GitHub 仅在已有同一 identity 的服务器投影时纳入。
6. **[切片阻塞] 完成定义**：S1 不新定义 completed，原样展示 workflow profile/服务器返回的完成状态与 gate；用户文案如何解释不同 profile 需要确认。
7. **[RFC 阻塞] 历史保留**：agent activity 和原始工具输出的保留期限、大小限制与脱敏策略是什么？S1 仅使用当前已允许浏览器读取的持久数据。
8. **[路线图] 通知**：P0 是否需要系统桌面通知，还是站内 Attention 和新活动提示足够？
9. **[切片阻塞] 迁移策略**：建议逐路由替换，以 parity matrix 为旧入口移除 gate；需确认是否接受迁移期双入口。
10. **[验证依赖] 用户研究**：除创始人本人外，首轮可用性测试是否有 3 至 5 位目标用户可参与？没有外部用户时，不能声称产品可理解性已被真实用户验证。

## 19. 需求追踪与交接

Architect 输出 RFC 前必须提交一份 contract inventory，至少包含：

| S1 需求 | 权威 API/存储 | 当前前端入口 | 缺口 | 所需验证层级 |
| --- | --- | --- | --- | --- |
| S1-01 至 S1-10 | 逐项填写，不得写“现有 API”笼统代替 | 逐项填写 | projection / mutation / route / test | Tier 2 / 3 / 4 |

Planner 必须把批准的 S1 拆成小任务，每项写清输入、输出、依赖、lane boundary、验收和回滚。UI Design 只能在 PRD 与 RFC 批准后工作，并且最终责任包括直接前端实现与截图支持的视觉验收，不得只交付设计稿。

Review 应重点挑战：

- 是否有未经验证的后端能力被当成事实；
- 是否引入第二套 capability、approval 或 execution 状态；
- 是否错误绑定 Project、Run、artifact version、SHA 或 evidence snapshot；
- 是否存在旧能力无法访问却被称为“迁移完成”；
- 是否把 QM 的模式当成需求依据，而非可替换参考。

## 20. 验证策略

### 20.1 本文档变更

- 验证层级：Tier 0，docs-only。
- 已执行：`git diff --check`。
- 未执行：backend/frontend 测试；本文档不改变 executable contract，因此安全跳过。

### 20.2 S1 实施的最低验证

S1 是浏览器可见路径，并会接入 control-plane mutation，整体按 **Tier 4** 管理；各 PR 可按实际文件和契约提高但不得人为降低 CI 分类。

最低证据：

1. Focused backend API/projection tests，逐项保护 run identity、Attention identity、capability、stale 和 idempotency contract；
2. mutation 涉及 approval、ledger、execution identity、evidence 或 worker dispatch 时运行完整 backend suite 与 Ruff；
3. frontend unit/integration tests、lint、build；
4. 中英文 catalog completeness；
5. keyboard、focus、desktop/tablet/mobile viewport 检查；
6. 更新 `docs/product-ui-capability-parity.md`；
7. Playwright 与 live smoke 使用隔离 workspace，覆盖 active Run、historical Run、needs input、approval、blocked recovery、stale mutation、刷新恢复和浏览器返回；
8. 记录所用 workspace、观察到的行为和浏览器 console 状态，不得只报告测试数量。

每个 PR 必须按 `docs/testing-strategy.md` 写明 verification tier、changed contract area、commands、结果和安全跳过项。

## 21. 后续产物

本需求文档评审通过后，按以下顺序产出：

1. 核心对象词汇表与状态机对照；
2. 信息架构与 route model；
3. 五条核心流程的低保真交互稿；
4. 前端与后端 capability / event / mutation contract；
5. P0 迁移计划与旧能力 parity matrix；
6. 视觉方向与组件规范；
7. 分阶段实施和验证计划。

在第 1 至 5 项确认之前，不进入高保真视觉设计。
