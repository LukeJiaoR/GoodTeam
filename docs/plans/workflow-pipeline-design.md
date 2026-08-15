# Workflow Pipeline Design — 工作流管线可视化

> Status: **Work 页管线已落地（PR #367，2026-08-15）；Home 紧凑变体、节点证据徽章、并行泳道按 §10 开放问题推迟。**
> Date: 2026-07-21（设计）；2026-08-15（落地）。
> 依据：用户方向（"管线的概念，把 workflow 可视化以引导用户"）+ 走查证据 W7
> （`docs/plans/web-hci-usability-fixes.md` §2，2026-07-21 Playwright 活体：14 阶段 StageRail
> 已存在但引导力弱，后端引导字段未渲染）+ 已批准设计稿
> `docs/superpowers/specs/2026-07-16-goodteam-product-frontend-redesign-design.md`
> §8.3（Work run 中心布局：顶部有序阶段轨 + run 选择器）与 §4.2（One next action）。

## 1. 概念

管线是**引导层（guidance layer），不是导航层**。它回答操作者的三个问题：

1. **现在在哪** — 当前 run 处于第几个阶段；
2. **卡在哪** — 哪个 gate 待审 / 哪个阶段 blocked，原因是什么；
3. **下一步该干什么** — 唯一高亮的下一个动作（待批准 / 生成中 / 重试）。

反模式警告：管线不得变成又一个模块列表。默认只显示关键状态；细节（artifact 路径、feedback、证据）在 hover/点击后展开，详情仍留在各视图。

## 2. 数据来源（零新增后端字段）

`build_workflow_steps`（`web.py:3876`）已为每个 step 返回：`number/key/title/roles/artifact/approval/summary/feedback/status/status_label/action_label/artifact_path`。状态枚举：`not-started | done | active | ready | review-needed | blocked`。再叠加：

- `state.current_node` / `state.next_node`（当前/下一步）；
- `approval_reviews`（gate 的 recommendation/evidence，与 Track C2/Telegram 卡同源）；
- `approval` 非空的 step 即"关卡"（gate）。

走查确认（W7）：`action_label`（如 "Review TAKEOVER"）后端已算好，前端 CurrentView 未渲染——管线直接消费，无需新数据。

## 3. 形态与位置

### 3.1 Work 页：常驻水平管线（主形态）

顶部（页面头与 subnav 之间）一条水平管线：

```
[run 选择器]  ◇接管▷──●PRD──●RFC──●安全──●Staging──●发布▷
              (done)   (待你批准) (弱化)  ...
              CI✓Smoke✓  ▶ 点击→ Review/PRD
```

- **节点** = step；**关卡** = 带 `approval` 的 step，画在节点之间（闸门形状），批准后变绿放行。
- **状态映射**（走查 W7 的数据直接可用）：

| status | 视觉 | 交互 |
| --- | --- | --- |
| `done` | 成功色 + 证据徽章（CI/Smoke/QA，`evidence_snapshot`） | 点击 → Knowledge 工件视图 |
| `review-needed` | 警示色 + `▶ {action_label}` | 点击 → Review 对应 gate 卡（深链） |
| `blocked` | 危险色 + 原因（`feedback`）+ 重试入口 | 点击 → Work 当前视图（含重试按钮） |
| `active` | 脉冲动画 + "生成中…" | — |
| `ready` | 轮廓线 | — |
| `not-started` | 弱化灰 | — |

- 状态不只靠颜色：图标 + 文字标签（设计稿 §11）。
- Takeover 前置段（existing_project）：`build_workflow_steps` 首步即 Takeover Context——画在管线最左侧，用虚线分隔并标注"接管"，语义上与产品主链区分（走查实测：新 run 第一道 gate 是 TAKEOVER，用户容易困惑"我的 PRD 呢"）。
- 并行分支（未来 D3）：本设计规定渲染规则——分支阶段画**并行泳道**，汇合点前合并为单线；本轮只实现顺序渲染，`workflow_execution_plan_v2` 出现并行拓扑时降级为列表 + "并行执行"说明，不硬画。

### 3.2 Home 页：当前 run 摘要管线

Home 的 next-action 区上方渲染同一管线的紧凑版（不滚动时只显示当前±2 节点 + 其余折叠为 "…N 步"）。无 run 时不显示。

### 3.3 Review 页：位置指示

Review 页头显示 "第 N / 14 步 · {gate 名}" 小指示（纯文本，不引入管线组件，避免抢评审主体）。

## 4. 交互规则

- 节点为 `<button>`/`<Link>`，键盘可达，`:focus-visible` 复用现有 token。
- hover/焦点：tooltip 显示 `feedback` + `artifact_path`（`title` 属性兜底，主用自定义浮层避免长文本截断）。
- 点击路由：`review-needed` → `routes.projectReviewItem(projectKey, approval)`；有 `artifact_path` → `routes.projectKnowledge(projectKey)?view=artifacts&artifact=<key>`；其余 → `routes.projectWork(projectKey)`。
- `prefers-reduced-motion`：`active` 节点用静态"运行中"标签替代脉冲动画（全局已有 reduce 规则，组件内不再叠加动画）。
- 窄屏（≤767px）：横向滚动 + 首尾固定（当前节点尽量可见），不换行压缩。

## 5. i18n

新增 `work.pipeline.*` 键（en/zh-CN 同步）：节点动作文案**不直接用后端 `action_label`**（英文原文），改为 FE 按 `approval` 映射（如 `work.pipeline.review = "待你批准 {gate}"`）；`work.pipeline.takeover = "接管"`、`work.pipeline.parallelNote` 等。契约测试保证双语齐全。

## 6. 组件与文件

- 新组件 `frontend/src/components/WorkflowPipeline.jsx`（纯展示 + 路由链接，props：`steps`、`evidence`、`projectKey`、`runId`、`t`）。
- `frontend/src/components/WorkflowPipeline.test.jsx`（vitest 契约测试）。
- 挂载：`WorkPage.jsx`（subnav 上方）、`ProjectHomePage.jsx`（next-action 上方，紧凑变体 props `compact`）。
- Review 位置指示：`ReviewPage.jsx` 页头一行。
- CSS：`frontend/src/styles/product.css` 追加 `.workflow-pipeline*` 类（沿用 token，不引入新主题色）。

## 7. 验收标准

1. fake run 四状态（PRD 待审 / worker active / blocked / complete）下管线呈现与状态矩阵一致（契约测试断言 DOM 结构与链接 href）。
2. 点击 `review-needed` 节点 → 到达对应 Review gate 卡（URL 为 `review/<approval>`）。
3. Takeover 前置段独立且标注"接管"；用户无需猜测"PRD 在哪"（走查 W7 直接动机）。
4. 无任何新后端字段/API 变更。
5. en/zh-CN 键齐全，缺失 key CI 失败。
6. 键盘可达、focus 可见、reduced-motion 生效、≤767px 可横向滚动。
7. 并行计划（D3 拓扑）出现时降级为列表而非画错。

## 8. 验证（Tier 2 + 活体）

- `npm run lint --prefix frontend`、`npm run build --prefix frontend`、`npm run test:unit --prefix frontend`（WorkflowPipeline 契约测试 + i18n 契约）。
- 活体：**必须用全组件 supervisor（`goodteam supervise` 或 `scripts/dev-up.sh`）**——走查发现 `serve` 单跑无 operator，批准后 run 不自动推进，管线四状态验收需要真实推进。隔离 workspace + fake worker，截图四状态，绝不对真实项目 workspace 操作。
- PR 按 `docs/testing-strategy.md` 命名验证层级与跳过项。

## 9. 非目标

- 不做工作流编辑器（不拖拽、不增删阶段）。
- 不替代 Work 的 5 个视图（管线是引导，视图是详情）。
- 不加后端字段；不为并行拓扑新增本轮实现（只降级）。
- 不做移动端独立形态（沿用横向滚动，Telegram 是移动端第一客户端）。

## 10. 开放问题

1. 管线默认展开全部 14 节点，还是默认折叠（只显示当前±2）？倾向：Work 页默认展开（信息密度可接受），Home 紧凑版折叠。
2. 证据徽章只挂在当前/最近完成的 gate 上，还是所有 done 节点都挂？倾向：仅最近完成的 gate（避免徽章噪音）。
3. 管线组件与 Track C2（证据摘要行）共享的 evidence 解析逻辑是否抽公共 util？倾向：抽 `frontend/src/utils/evidenceSummary.js` 供两处复用。
