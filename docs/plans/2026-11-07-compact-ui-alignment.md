# 紧凑工作台规范的跨页面迁移

日期：2026-11-07（Asia/Singapore）。

- 进度与完成条件：[Issue #677](https://github.com/LukeJiaoR/GoodTeam/issues/677)。
- 状态：**跨页面迁移待实施；本轮只记录上下文与方案。**
- 已落地基线：[PR #674](https://github.com/LukeJiaoR/GoodTeam/pull/674)，合并提交 `d4ddab5e35bb528b04261d51b7827de4233e8e29`。
- 本次代码核对基线：`e99fdc90077ab04a52d405f6b7bd404d786f0c9d`，包含 #673 Provider 设置及 #674 工作台改进。

本文保存决策、源码入口和验收依据。任务状态统一更新到 #677 及关联 PR，不在本文维护第二套进度清单。文档 PR 使用 `Refs #677`；写完文档不关闭迁移 Issue。

## 1. 为什么需要这个跟踪项

用户在本轮多次指出：整体风格不满意、标题和字号过大、1080p 全屏时显得粗大、信息密度低、团队执行占据过多位置、看不清具体 Worker 状态，以及界面与所提供的组件参考不一致。用户也明确要求延续已认可的设计，不要在后续调整中丢掉左右对话、导航层级和收窄后的对话边界。

参考经历包括用户提供的 Retool、Beautiful UI、ChatGPT 与 dots 布局/截图。它们是理解偏好的历史背景；后续工作的直接基准是 #674 已实现的工作台及实际浏览器表现。

#674 合并后，用户提出“其他页面要怎么做才能跟上我们这次的改进”，随后要求“新增一个 issue/doc 跟踪这个事情，把 context 讲清楚”。本项由此建立。较早的 [Beautiful UI 全站改造记录](../beautiful-ui-product-redesign.md) 对应历史阶段；其中宽松卡片、旧导航和旧验证数字不能覆盖本轮的紧凑基线。

## 2. 已确认并落地的基线

以下来自 #674，后续共享样式调整需要保持这些行为：

| 方面 | 已落地基线 |
| --- | --- |
| 视觉 | 中性灰白、克制边框/阴影及对应深色样式；避免重复的大标题和说明。 |
| 桌面字号 | 正文 14px、控件 13px、辅助信息 12px。 |
| 导航 | 256px 侧栏；项目内区分项目对话、当前迭代和历史迭代。窄屏使用可折叠导航。 |
| 对话 | 对话和输入区最大宽度 760px；用户在右侧灰色气泡，助手在左侧正文；不显示“你 / Manager”身份标签，保留无障碍身份。 |
| Worker | 300px 轻量状态卡，显示实际职责、状态和依赖原因，点击查看详情；展开不改变对话/输入区位置。 |
| 连续性 | 切换历史、查看详情保留输入框、草稿和附件；项目及任务身份仍由原有状态逻辑拥有。 |
| 附件 | 输入框「＋」支持图片、PDF、DOCX、XLSX、UTF-8 文本/代码/CSV；保留选择/拖放/粘贴、预览、移除、失败保留及重试。现有限制为每条消息最多 6 个、每个 5 MiB；旧 DOC/XLS 需要转换。 |

#674 的本地验证记录在该 PR 中。它只证明对应源码及实际执行的检查，不能直接作为后续迁移提交的通过证据。

本机可选参考材料位于 `output/design/2026-11-06-goodteam-uiux/`，包括 `workbench-v11.html`、`implemented-workbench-1080p.png`、`implemented-worker-detail-1080p.png`、深色/窄屏截图及验证记录。这些是本地输出，不保证跟随 Git 分发；协作者应从已合并源码与 PR 恢复基线，不能依赖临时截图路径才能理解需求。

## 3. 项目级结果与范围

负责人在同一项目的多轮迭代中，从对话进入项目、团队、评审、知识或设置时，能够用一致的控件和信息层级看清实际状态、交付证据及下一步操作，继续回到原有项目上下文。

按 [#381](https://github.com/LukeJiaoR/GoodTeam/issues/381) 的两类范围，本项属于用户明确要求跟踪的**非自治闭环必需的界面一致性改进**。它不改变核心迭代工作的优先级，也不代表真实自治产品验收通过；真实 Manager 的 pick → execute → learn → pick again 证据仍归 [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419)。

实施范围是既有 Web 页面的视觉、信息层级和必要交互整理。复用当前数据、任务队列、Workflow、Worker 投影、审批及配置接口；不新增执行账本、调度器、自动批准或新的 Provider 能力。本轮建跟踪项不等于启动队列执行、部署、付费模型调用或授权合并后续实现。

## 4. 已观察到的差距与源码入口

当前 [workbench.css](../../frontend/src/styles/workbench.css) 大量规则限定在工作台内，通用侧栏/字号仅覆盖部分全局元素；[studio.css](../../frontend/src/styles/studio.css) 仍为其他页面提供旧的页头、卡片与间距。以下为源码核对结果，不是本轮新做的浏览器像素测量。

| 页面 | 当前差距 | 主要入口 |
| --- | --- | --- |
| 共享基础 | 页标题仍使用 1.5rem；普通按钮和搜索框最小高度 40px；页头、介绍和分区说明叠加。 | [PageHeading](../../frontend/src/components/ui/PageHeading.jsx)、[Button](../../frontend/src/components/ui/Button.jsx)、[CollectionToolbar](../../frontend/src/components/ui/CollectionToolbar.jsx)、[StatusBadge](../../frontend/src/components/ui/StatusBadge.jsx)、[WorkspacePanel](../../frontend/src/components/ui/WorkspacePanel.jsx) |
| 项目 | 三个大统计卡与筛选功能重复；项目行至少 76px，另有文件位置；待处理事项有独立摘要。 | [ProjectsPage](../../frontend/src/features/projects/ProjectsPage.jsx)、[ProjectAttentionOverview](../../frontend/src/features/projects/ProjectAttentionOverview.jsx)、[LoadProjectPanel](../../frontend/src/features/projects/LoadProjectPanel.jsx) |
| 团队 | 统计、Manager、职责分组、角色和 Worker 层层成卡，按钮和说明占据大量首屏。 | [TeamPage](../../frontend/src/features/team/TeamPage.jsx)、[ResponsibilityGroup](../../frontend/src/features/team/ResponsibilityGroup.jsx)、[WorkerDialog](../../frontend/src/features/team/WorkerDialog.jsx) |
| 评审 | 已有队列—产物—决策三栏，但页头、统计和卡片间距仍偏宽松。 | [ReviewPage](../../frontend/src/features/reviews/ReviewPage.jsx) |
| 设置 | 大分区卡和主题预览占位较多；#673 新增 Provider 列表、内联表单、用量表，有独立尺寸规则。 | [SettingsPage](../../frontend/src/features/settings/SettingsPage.jsx)、[ProviderSettings](../../frontend/src/features/settings/ProviderSettings.jsx)、[providerSettings.css](../../frontend/src/features/settings/providerSettings.css) |
| 知识 | 概览介绍、视图切换与记忆记录仍采用宽松卡片层级。 | [KnowledgePage](../../frontend/src/features/knowledge/KnowledgePage.jsx)、[KnowledgeArtifacts](../../frontend/src/features/knowledge/KnowledgeArtifacts.jsx) |

## 5. 待实施方案：先样板，再迁移

下面的跨页尺寸及顺序是本次提出的实施方案，**尚未落地，不应写成用户已验收的页面效果**。

共享尺寸的初始目标：页面标题约 16px、普通桌面按钮约 32px，正文/控件/辅助信息沿用 14/13/12px。统计优先收成一行，首屏优先实际记录；列表行根据一行/两行内容确定高度，避免裁掉状态或原因。触控区域、键盘焦点和长文本需按实际页面检查。

760px 是对话阅读宽度；项目列表、评审和设置按各自内容使用合适空间。先在项目页的 1920×1080、100% 缩放实测校准，再用于其他页面。

| 顺序 | 最小切片 | 保留的关键行为 |
| --- | --- | --- |
| 1 | 共享基础 + 项目页样板：更新已有共享样式/组件，把工作台内可复用参数归入共享层；统计并入筛选/摘要，项目用紧凑列表。 | 搜索/筛选、导入和进入项目有效；待处理事项的原因、数据不可用提示和目标入口仍清楚。工作台专用布局继续保留。 |
| 2 | 团队：按职责分组的紧凑成员列表，显示名称、模型、可用状态；配置详情按需展开。 | 配置可用性与实际执行状态分别标明；只有真实执行数据才能显示运行情况。保留配置保存、失败恢复及既有版本约束。 |
| 3 | 评审：保留三栏结构，收紧标题/摘要/队列间距，让产物获得主要空间。 | 完整页与工作台内嵌评审均检查；审批对象、证据、阻塞理由和操作保持可见，未保存编辑及审批前置条件不受影响。 |
| 4 | 设置：紧凑分组行，Provider 列表、展开编辑表单及用量表统一尺寸。 | 主题/语言偏好，以及 Provider 保存、激活、删除、加载/错误状态沿用当前语义；不把密钥内容呈现在普通列表。 |
| 5 | 知识：统一概览/产物/记忆入口、搜索、记录列表和选中详情。 | 搜索条件、来源追溯、项目上下文和长内容阅读保持；不隐藏无法读取或缺失来源的提示。 |

优先修改现有共享组件和对应规则，不为每页复制一套高优先级 CSS，也不为此次迁移引入新的组件框架。每个关联 PR 只承接表中的一个可验证切片，并说明相对项目级用户结果的改善。

## 6. 验收与验证层级

本次文档修改为 Tier 0：检查差异、相对链接、源码入口和 Issue/PR 关联；不运行应用测试来证明文档正确。

后续浏览器可见迁移按 Tier 4 记录，遵循 [精简验证策略](../testing-strategy.md)：

- **视觉**：每个页面提供 1920×1080、100% 缩放的真实浏览器截图，检查实际字号、按钮尺寸及首屏内容；补充深色和窄屏。设计图、缩小后的图片、单测及构建不能替代实际页面检查。
- **交互**：每页保留一个与变更相关的主要路径；沿用现有项目搜索、团队配置、知情评审、设置、知识概览测试及相关浏览器用例。只为缺失的独立行为补最小代表性用例，不恢复历史大套件。
- **工作台回归**：共享样式改动后检查左右对话、侧栏层级、760px 输入区、Worker 展开，以及草稿/附件连续性。可复用 `frontend/e2e/project-manager.spec.js` 和 `frontend/e2e/conversation-attachments.spec.js`。
- **跨迭代上下文**：用同一项目的当前/历史迭代、交付产物和记忆场景，检查往返其他页面后的身份与状态一致；这类 UI fixture 不替代真实 Manager 多轮自治验收。
- **边界状态**：检查键盘操作、长文本及空/加载/错误状态；主要操作可达，窄屏不横向溢出或遮住输入框。用代表性场景检查，不为所有排列新增测试。
- **报告**：关联 PR 写明源码 SHA、相关前端测试、lint/build、隔离浏览器验证的实际命令/结果及跳过原因。Provider 实库验证没有可丢弃 PostgreSQL 时明确记为未验证。

浏览器验证使用隔离工作区，不操作正在运行的真实项目。若实现意外需要修改执行身份、消息持久化或审批/派发边界，则不能继续按纯 UI 变更声称覆盖，须按 [AGENTS.md](../../AGENTS.md) 补齐对应验证。

GitHub CI 自 2026-10-31 起仍按所有者决定暂停：不自动发布 candidate 分支或派发 Actions，不把旧 SHA 的结果当作新提交的证据。历史设计文档中恢复前的候选 CI 流程不覆盖此规则。

## 7. 接手入口

1. 先读 #677 的最新状态与关联 PR，确认哪些切片已完成。
2. 对照 #674 与本文件第 2 节恢复已认可基线，再查看最新 `main`；若源码已变化，更新差距描述，不按旧样式数值机械改动。
3. 从尚未完成的最小切片开始，提交实际页面效果与相关验证。各页验收完成且工作台回归通过后，再关闭 #677。
