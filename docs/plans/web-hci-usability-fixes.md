# Web HCI Usability Fix Plan

> Status: **B0/W2/A/B/C/W 已落地（PRs #365/#366/#367，2026-08-15 合入 main）；D/E/F 仍为 proposed。**
> Date: 2026-07-21（走查与计划）；2026-08-15（落地）。
> Context: HCI 细节审查（2026-07-21 会话输出：Web 全路由 + 共享组件 + i18n + 样式）。
> 语境：**Web 优先作为本地可工作的状态**，远程操作（Telegram）不在本轮。
> 本计划是已批准设计稿 `docs/superpowers/specs/2026-07-16-goodteam-product-frontend-redesign-design.md`
> 的落地补账：当前 UI 是该设计的 Phase 1-5 实现，本计划修复其未落地项（渐进披露 §4.3、
> Stale 一等状态 §12、导航存续草稿 §13.3、Complete 反馈 §12）。所有轨道不得与设计稿冲突；
> 设计稿是视觉与交互规范的唯一事实来源。
> 走查证据（§2）与轨道正文（§4-§11）按落地时的实际情况修订；已落地轨道保留验收标准供回归参考。

## 1. 目标与非目标

### 目标

1. 消除评审工作台的数据丢失路径（唯一 P1 数据损失问题）。
2. 让"决策"这个产品核心价值面有明确、即时的成功反馈与下一步指引。
3. 用结构化呈现替代三处裸 JSON，让本地操作者不用读原始数据。
4. 补齐激活态的可访问性语义与信息新鲜度。
5. 清理一批低成本交互细节。

### 非目标（本轮不做）

- 不做 Telegram / 远程操作改动（上一轮已单独排）。
- 不做 Plan 看板下钻（H10）、主题切换 UI 入口、键盘快捷键、新手引导——记入 backlog，后续单独评估。
- 不做新一轮视觉重设计（已批准设计稿 `2026-07-16-goodteam-product-frontend-redesign-design.md` 是视觉/交互规范唯一事实来源；本计划是它的落地补账）。

## 2. 任务走查证据（2026-07-21，Playwright 活体，隔离 workspace + fake worker）

5 任务全流程实测（`/tmp/goodteam-hci-walk`，端口 8791，截图与日志在 `/tmp/goodteam-hci-evidence/`）。
**结论先行：Web 目前的主要"不好用"不是视觉问题，是流程被后端 bug 卡死 + 交互细节缺失。**

### W1（后端 bug，P0）canonical_root 解析不一致 → 整个 Web 就绪流程无法提交

macOS `/tmp` → `/private/tmp` 符号链接。项目记录 root（YAML 原值 `/tmp/...`）与 readiness 顶层 canonical_root（resolve 后 `/private/tmp/...`）不一致；不同写入方（import 顶层 resolve、executor 的 execution/artifact 不 resolve）各写各的。绑定校验横跳在两个规范形之间：
- `_bound_step_artifacts`（`project_readiness_executor.py:278`）对 readiness.canonical_root 比较 → artifact 原始值时失败；
- `artifact()`（`project_readiness_executor.py:774-786`）对 `Path(root).expanduser()` 比较 → artifact resolve 后仍失败。

后果（实测）：命令确认提交 409 `readiness_artifact_mismatch` → 就绪永远卡在 `needs_input`；即便绕过（手工修状态）到 complete，`execution.canonical_root` 不一致又让能力门禁 `start_workflow=False`（`project_readiness.py:818`）。Linux CI 上 `/tmp` 非符号链接，全量测试照过——正是团队记录过的"unit tests + CI clean，live smoke 抓路径 bug"家族的新成员。
修复方向：全链路统一 resolve（比较处或写入处二选一，建议写入处统一 `resolve()`，并加 macOS 符号链接路径的回归测试）。Tier 3/4。

### W2（前端 bug，P1）命令确认面板推荐项不预选

`ReadinessInputPanel.jsx` 预选逻辑只看 `recommended/confirmed` 标记，而后端 `command_verification` 只发 `requires_confirmation: true`——实测 3 个标准命令（npm build/lint/test）全部未勾选、提交按钮禁用、仅靠一行小字提示。`isStandardCommand` 已经能识别这些命令（用于 primary/advanced 分组），却没用在同一处。修复：预选标准命令（或后端补 `recommended` 标记）。

### W3（P1，live 验证）决策后无成功反馈

T3 实测：批准 TAKEOVER 后，唯一变化是决策面板翻转为"服务器未将此审查标记为可操作，因此当前为只读"，页面无"已批准 → 下一步"确认。审核队列卡 badge 变化是唯一反馈。轨道 B 成立。

### W4（P1，live 验证）Home 与就绪状态自相矛盾

实测截图：Home 同时显示"已完成（7/7）"、启动按钮可用、**以及** `readiness_artifact_mismatch` 错误横幅。根因：Home 挂载即拉取 repository_understanding 工件 → `artifact()` 绑定校验失败（同 W1）→ 错误横幅；且该 409 每次加载 Home 都出现在 console。修复 W1 后横幅自然消失；另建议 Home 的工件拉取失败降级为静默（`loadingArtifact` 区提示）而不是全局错误横幅。

### W5（P2）"启动工作流"两次点击语义混淆

Home 的"启动工作流"按钮只 `navigate` 到 Work 页（capability 的 `workflow_entry_url`），真正的 run 在 Work 页 StartRunPanel 再填目标再点一次"启动工作流"。两处同名按钮、两个步骤。建议：Home 按钮文案改为"前往工作区开始运行"，或 Home 直接带出目标表单。

### W6（P2）Review 队列与 Inbox 噪音/矛盾

- Review 队列一次列出全部 6 个 gate（PRD/RFC/STAGING/SECURITY/RELEASE 全标 pending），只有当前 gate 真正可操作——未来 gate 的 pending 状态是噪音。
- Inbox 实测 7 项中 6 项是"缺少证据"警告（含尚未到达的 RFC/STAGING/SECURITY/RELEASE）；且同一 PRD 出现两条矛盾条目：一条"Artifact content is present and no blocking signals were found"（等待操作），一条"PRD 审查缺少证据"。建议：Inbox/Review 只对当前 pending gate 出证据警告；推荐与证据策略状态矛盾时以证据策略为准或合并为一条。

### W7（P2）StageRail 已存在但引导力弱

T2 实测：14 阶段 StageRail 只在 CurrentView 竖排展示，`action_label`（"Review TAKEOVER"）等引导字段后端已算好但前端未渲染；CurrentView 标题是"最高优先级下一步"却只显示阶段名与"Human review is required…"通用文案。→ 管线可视化轨道（W）直接消费 `build_workflow_steps` 已有字段，无需新后端数据。

### 其他实测确认

- T1 导入→Home 3 秒、导入去重正常；T2 填目标后 2 秒到达首个 gate（Takeover，existing_project 前置段，管线设计需包含）；评审 3 按钮齐全；Delivery 4 块裸 JSON 确认；Messages 空态正常；verdict 英文原文（i18n 缺口确认）；console 仅 1 个 409（W1 引起）。
- 环境注意：`serve` 单独跑不含 operator 组件，批准后 run 不会自动推进（需 supervisor）；走查 T4 的"批准后下一 gate 出现"受此限制，活体验收时用 `supervise` 或 `dev-up` 全组件。

## 3. 轨道总览（按走查证据修订）

| 轨道 | 内容 | 问题 | 优先级 | 验证层级 |
| --- | --- | --- | --- | --- |
| B0 | **后端修复 canonical_root 统一 resolve** + 符号链接回归测试 | W1 | **P0** | Tier 3/4 |
| A | 评审工作台脏状态保护（自定义 dialog，不用 `window.confirm`，设计稿 §12） | H1 | P1 | Tier 2 + 活体 |
| B | 决策成功反馈 + 下一步指引；命令确认预选标准命令 | H2、W2 | P1 | Tier 2 + 活体 |
| C | 结构化呈现替代裸 JSON（Delivery/证据/preflight） | H3（W4 已验） | P1/P2 | Tier 2 + 活体 |
| W | **工作流管线可视化**（消费 `build_workflow_steps` 现有字段：status/action_label/artifact_path；Home+Work 常驻水平管线；Takeover 前置段；并行分支用泳道不强行拉直；点击节点直达对应视图） | W7 + 用户方向 | P1 | Tier 2 + 活体 |
| D | 激活态 aria 语义 | H4 | P2 | Tier 2 |
| E | 信息新鲜度（Messages 轮询 + Inbox/Activity 数据年龄） | H5/H6 | P2 | Tier 2 + 活体 |
| F | 交互细节批量清理（含 W5 按钮文案、W6 Inbox/队列噪音、时间格式、fallback 对齐等） | H7/H8/H9/W5/W6 + P3 | P2/P3 | Tier 1/2 |

执行顺序：**B0 → W1 修复**（堵住流程，先于一切）→ A、B、C、W 按 P1 排 → D、E 并行 → F 收尾。每轨道一个 PR。

## 4. 轨道 B0 — canonical_root 解析一致性（W1，P0）

### 问题

macOS `/tmp` → `/private/tmp` 符号链接下，项目记录 root（YAML 原值，未 resolve）与 readiness 顶层 canonical_root（resolve 后）不一致；`execution`/artifact 写入方不 resolve，`import`/`upgrade` 写入方 resolve。绑定校验在两个规范形之间横跳，导致：
1. 命令确认提交 409 `readiness_artifact_mismatch`（`project_readiness_executor.py:278` `_bound_step_artifacts` 对 `readiness.canonical_root` 比较）；
2. 即便就绪 complete，能力门禁 `start_workflow=False`（`project_readiness.py:818` `execution.canonical_root` 比较）；
3. Home 每次加载拉取 repository_understanding 工件 → `artifact()`（`project_readiness_executor.py:774-786`，对 `Path(root).expanduser()` 比较）失败 → 错误横幅 + console 409（W4）。

Linux CI `/tmp` 非符号链接，全量测试无法捕获。实测完整复现与手工修复路径见走查证据 W1。

### 变更（后端）

- 统一 canonical_root 的规范形：建议在写入处统一 `Path(root).resolve()`（import 记录、readiness 顶层、execution、artifact、step 五处一致），或提供单一 `canonical_root()` 帮助函数，所有比较/写入走它。
- 新增 macOS 符号链接路径回归测试：`/tmp` 下创建符号链接目录作为 repos.root，跑通 import → 命令确认 → readiness complete → `start_workflow=True` 全链。
- 顺带：`artifact()` 的 `str(Path(root).expanduser())` 与 `_bound_step_artifacts` 的 `readiness.canonical_root` 必须用同一规范形。

### 验收

- 符号链接根目录下：提交命令确认成功、就绪 complete、`start_workflow=True`、Home 无错误横幅、console 无 409。
- 既有测试全量通过；新增回归测试在 Linux/macOS 均绿。

### 验证（Tier 3/4）

- 全量后端 `PYTHONPATH=src .venv/bin/python -m pytest tests -q` + ruff。
- 活体：隔离 workspace 符号链接路径走查（复用 `/tmp/goodteam-hci-evidence` 方法），观察 Home 横幅消失、就绪提交成功。

## 5. 轨道 A — 评审工作台脏状态保护（H1）

### 问题

`ReviewPage.jsx` 的工件 textarea、开放问题答案、细化提案全是内存态；切换队列卡、切换 run、刷新页面即静默丢失。评审工作台是 Web 核心价值面，这是数据损失级缺陷。

### 变更

- 在 `ReviewPage.jsx` 引入 dirty 状态：
  - `content !== 当前选中卡的已保存基线` → dirty；
  - `answers` 有未提交输入 → dirty；
  - `proposed` 细化提案存在且未 accept/discard → dirty（已有明确动作，可不计入，见开放问题）。
- 导航守卫：路由是 data router（`main.jsx` 用 `createBrowserRouter`），用 react-router v7 的 `useBlocker` 拦截离开评审工作台的路由跳转；匹配到 dirty 时弹**自定义 dialog**（复用现有 `DirectoryPickerModal` 的 `role="dialog"` 模式，**不用 `window.confirm`**——设计稿 §12 明确 "Browser alerts are not used for product errors"；文案走 i18n：`review.dirty.confirm`，说明未保存内容会丢失，提供"继续离开/留下"）。
- 页面卸载守卫：`beforeunload` 监听，dirty 时 `preventDefault()`（浏览器原生确认）。
- 保存成功 / 细化接受后清除 dirty 标记。

### 验收

- 编辑工件后点击另一张队列卡 → 出现确认，取消则停留在当前卡且编辑不丢失。
- dirty 时刷新/关标签页 → 浏览器原生确认。
- 保存后切换队列卡 → 无确认（已保存）。
- 队列卡切换被取消后，URL 与选中卡均不变化。

### 验证（Tier 2 + 活体）

- 新增 vitest 契约测试（`frontend/src/features/reviews/ReviewPage.dirty.test.jsx` 或并入现有契约文件）：模拟编辑 → 触发导航 → 断言 confirm 出现 / 取消后状态保留。
- `npm run lint --prefix frontend`、`npm run build --prefix frontend`、`npm run test:unit --prefix frontend`。
- 活体冒烟：serve 隔离 workspace（`scripts/dev-up.sh` 或手动 serve，**绝不对真实项目 workspace 操作**），Playwright 走 T4 流程验证确认框与数据保留。按 `docs/testing-strategy.md` Tier 4 要求写明 smoke 观察。

## 6. 轨道 B — 决策反馈与下一步指引（H2）

### 问题

批准/送回修改/拒绝提交后无成功反馈；`/api/approve` 返回后唯一信号是队列卡 badge 变化。没有"已批准 PRD → 下一步进入 RFC 规划"的指引，与"每个 gate 都是可审计决策"的重量级不匹配。

### 变更（纯前端消费现有数据，不改 API）

- 决策成功后，在 `review-decision-panel` 位置显示内联确认条（`role="status"`）：
  - 文案：`✅ 已批准 {GATE}` / `已送回修改` / `已拒绝`，带决策时间。
  - 下一步指引：从 `refresh()` 后的 `state.action_items` 中取该 gate 最近一条 action，读 `after_node`/`node_transition`，映射为 i18n 文案（`review.decision.next`，如 "下一步：RFC 规划"）。映射不到时退化为不显示指引。
  - 提供"查看下一步"链接（指向 Work 页对应视图）与"返回队列"链接。
- 决策后 3s 或切换卡时确认条消失（不阻塞后续操作）。
- verdict 文案同步 i18n（新增 `review.verdict.approve|caution|request_changes|reject|none`），替换 `ReviewPage.jsx:75` 的英文原文渲染。
- **W2（命令确认预选，已落地 PR #366）**：`ReadinessInputPanel.jsx` 预选逻辑已改为"`recommended/confirmed` 标记 或 `isStandardCommand(item)`"（标准命令 npm test/build/lint 默认勾选）。

### 验收

- 批准后立即看到"已批准 + 下一步"反馈，无页面跳动（不强制滚动）。
- 未知下一步时只显示批准确认，不显示错误。
- verdict 在 en/zh-CN 下均为本地化文案。
- 命令确认面板打开时标准命令已预选、提交按钮可用；非标准命令保留手动勾选。
- i18n 契约测试覆盖新增 key。

### 验证（Tier 2 + 活体）

- 现有 i18n/契约测试扩展（`frontend/src/i18n/catalogs.vitest.js` 等）+ 新增决策反馈契约测试。
- lint/build/test:unit；活体冒烟：mock 或真实 fake worker 跑一次 PRD 关卡决策，观察确认条。

## 7. 轨道 C — 结构化呈现替代裸 JSON（H3）

### 变更

- **C1 DeliveryView**（`WorkPage.jsx:219-222`）：PR 列表（标题/分支/状态徽章/外链）、CI 检查（名称/状态徽章）、证据（键/路径）、冒烟运行列表；原始 JSON 收进 `<details>` 供高级用户。
- **C2 ReviewPage Inspector 证据区**（`ReviewPage.jsx:78`）：顶部渲染摘要行——CI / Smoke / QA / 阻断数徽章（数据即 `evidence_snapshot` 的 `ci_status/smoke_status/qa_status/blocker_count`，与 Telegram 卡同源）；完整 JSON 保留在 `<details>`。
- **C3 ProjectSettings preflight**（`ProjectSettingsPage.jsx:30`）：worker backends preflight 渲染为列表（backend 名 / ready 徽章 / hint 文案），复用 Inbox 已有的 `worker_backends_preflight` hint 字段；原始 JSON 进 `<details>`。
- **C4 顺带**：Knowledge 事件详情、Settings diagnostics 保持 JSON（低频、专家向，不动，避免范围膨胀）。

### 验收

- 四个区块（PR/CI/证据/冒烟）在无数据时显示空态文案，有数据时显示结构化列表；JSON 仍在 `<details>` 可展开。
- 徽章 tone 与全站一致（复用 `StatusBadge`）。
- 不改变任何 API 消费路径与数据键。

### 验证（Tier 2 + 活体）

- 每个 C1-C3 一个契约测试（组件渲染结构化数据 + JSON 折叠）。
- lint/build/test:unit；活体冒烟：对 DEMO 项目打开 Delivery 与 Review 证据区截图比对。

## 8. 轨道 W — 工作流管线可视化（W7 + 用户方向）

设计文档：`docs/plans/workflow-pipeline-design.md`（本节是其落地摘要）。

### 变更

- 新组件 `frontend/src/components/WorkflowPipeline.jsx`：水平管线，节点= `workflow_steps`（现有字段：`status/status_label/action_label/feedback/artifact/approval/artifact_path/number/title`），关卡= 带 `approval` 的 step。
- 放置：Work 页顶部常驻 + Home 当前 run 摘要区（Review 页只在 gate 上下文显示"第 N/14 步"小指示，不抢主体）。
- 节点状态：done（成功色 + CI/Smoke/QA 证据徽章，数据同 Track C2）、active（脉冲）、review-needed（警示 + `▶ 待你批准` 直链 Review）、blocked（危险色 + 原因 + 重试入口）、ready/not-started（弱化）。
- 点击导航：`review-needed` → Review 对应卡；有 artifact → Knowledge 对应工件；否则 → Work 对应视图。
- existing_project 的 Takeover 前置段独立绘制（虚线分隔，标注"接管"）。
- 并行分支（未来 D3）：设计文档规定泳道+汇合点渲染规则，本轮只实现顺序渲染，遇并行计划降级为列表（不硬画）。
- 状态不只靠颜色：图标 + 文字标签；键盘可达；`prefers-reduced-motion` 生效；窄屏横向滚动。

### 验收

- 管线在 fake run 的 PRD 待审、blocked、active、complete 四种状态下的呈现符合状态矩阵（契约测试断言 DOM 结构与链接）。
- 点击 review-needed 节点直达 Review 对应卡。
- 无新后端字段；`build_workflow_steps` 已含全部所需数据（走查 W7 确认）。

### 验证（Tier 2 + 活体）

- WorkflowPipeline 契约测试（状态矩阵、链接路由、Takeover 前置段）+ i18n 覆盖。
- lint/build/test:unit；活体：`supervise` 全组件下跑 fake run，截图 4 状态管线（当前 `serve` 无 operator，run 不自动推进，验收时须用全组件）。

## 9. 轨道 D — 激活态 aria 语义（H4）

### 变更

- Work/Knowledge 的 `product-subnav` 按钮：激活项加 `aria-pressed`（或改为 tab 语义时用 `aria-selected`，保持按钮语义则用 `aria-pressed`）。
- Review 队列链接、Artifacts/Memory 列表按钮、ProjectSettings 分区导航：激活项加 `aria-current="true"`（链接）或 `aria-pressed`（按钮）。
- 复用现有 `.is-active` 判断位置，纯属性补充，不改布局。

### 验收

- 读屏用户可感知当前 tab/队列项/分区。
- 现有视觉样式与契约测试不破坏（`.is-active` 类名保留）。

### 验证（Tier 2）

- 扩展现有 frontend 契约测试断言激活项 aria 属性；lint/build/test:unit。

## 10. 轨道 E — 信息新鲜度（H5/H6）

### 变更

- **E1 MessagesView**（`WorkPage.jsx:182-189`）：视图激活且标签页可见时 5s 轮询（复用 `useProjectState` 的 `document.hidden` 模式）；不产生后端变更。
- **E2 Inbox/Activity**（`InboxPage.jsx:49-53`、`ActivityPage.jsx:31-39`）：加载后显示"最后更新于 {时间}"（`role="status"` 或页面头 meta 区），并提供刷新按钮；标签页可见时每 30s 轻量轮询（先不加 SSE，保持简单）。

### 验收

- Messages 视图停留期间新消息自动出现。
- Inbox/Activity 显示数据年龄，刷新按钮可用；轮询失败静默降级（保留上次数据 + 时间戳不更新），不弹错误。

### 验证（Tier 2 + 活体）

- 契约测试：轮询定时器调用（vitest fake timers）、数据年龄渲染。
- 活体冒烟：fake worker 运行中观察 Messages 新消息出现、Inbox 时间戳变化。

## 11. 轨道 F — 交互细节批量清理（H7/H8/H9/W5/W6 + P3）

### 变更

- **F1 memory 搜索防抖**（`KnowledgePage.jsx:117`）：`updateSearch` 前 250ms 防抖；事件列表与跨项目结果加空态文案（新增 i18n key）。
- **F2 跨项目搜索前置提示**（`KnowledgePage.jsx:120`）：scope 未选时按钮下方显示"先选择要搜索的项目"提示（`aria-describedby` 关联），替代当前静默禁用。
- **F3 静默成功反馈**：
  - `ProjectSettingsPage.jsx:20` 保存后显示 `role="status"` 的已保存提示（复用 SettingsPage 模式）。
  - `WorkPage.jsx:145` dispatch 成功后表单区显示内联成功提示（含任务名/角色），3s 消失。
  - `SettingsPage.jsx:19` "Saved" 提示在再次编辑时重置为 false。
- **F4 run 选择器保留 view 参数**（`WorkPage.jsx:311`）：`navigate` 时合并当前 `search`（保留 `view`）。
- **F5 时间格式统一**（`WorkPage.jsx:89`）：action items 的 `<time>` 用 `Intl.DateTimeFormat`（与 Readiness/Activity 一致），语言取当前 locale。
- **F6 StartRunPanel fallback 对齐**（`WorkPage.jsx:293`）：`delivery_mode` fallback 由 `feature_increment` 改为 `init_project`（与后端/README 默认一致）。
- **F7 开放问题提交语义**（`ReviewPage.jsx:85`）：提交后清空 `answers` 中已提交项并显示"已提交 N 条回答"反馈；未答问题保留在面板。

### 验收

- 每项行为可被契约测试断言；文案全部走 i18n（en/zh-CN 同步新增）。
- 无任何 API 变更、无布局/主题变更。

### 验证（Tier 1/2）

- 纯逻辑项（F1/F5/F6）按 Tier 1；组件行为项（F2/F3/F4/F7）按 Tier 2。
- 对应契约测试 + lint/build/test:unit。

## 12. 验证总则

- 每 PR 按 `docs/testing-strategy.md` 命名：验证层级、变更契约区、运行命令与通过数、跳过的验证及理由。
- 涉及浏览器可见行为（A/B/C/E）的 PR 必须附隔离 workspace 的活体冒烟观察（Playwright 优先，见 `webapp-testing` skill；冒烟绝不针对真实项目 workspace）。
- 后端全量测试本轮不要求（无 `src/goodteam_os/` 改动）；若某轨道意外需要后端配合，升级为 Tier 3 并跑全量。

## 13. 文档与记忆

- 每轨道落地后在 `docs/current-progress.md` 追加 footprint（仓库惯例）。
- 落地完成后更新本文档状态为 shipped 并归档对应条目。

## 14. 开放问题

1. 细化提案（proposed refinement）存在但未处理时，是否计入 dirty 拦截？倾向：不计入（有明确 Accept/Discard 动作面），但队列切换时若提案存在给轻提示。
2. B 轨道"查看下一步"链接的目标视图：建议指向 Work 页 `?view=current`（下一步节点在 CurrentView 呈现），是否够用？
3. E2 的 30s 轮询是否与未来通知/SSE 方案冲突？倾向：先轮询，SSE 作为独立 backlog 项。
