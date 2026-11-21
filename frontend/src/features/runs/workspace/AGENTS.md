# workspace/
> L2 | Parent: [AGENTS.md](../../../../../AGENTS.md)

项目工作台的会话、任务、执行与交付界面。
组件接收上层状态与操作，不决定后端授权。

AGENTS.md: 本目录的成员与职责边界。
AssistantControls.jsx: 助手请求恢复与待确认操作。
AssistantControls.test.jsx: 助手操作与恢复界面检查。
AssistantPendingReply.jsx: 回复状态、流式文本与停止按钮。
AssistantPendingReply.test.jsx: 回复状态与停止按钮检查。
ContinuationActivity.test.jsx: 项目持续迭代活动检查。
ContinuationScopeAddition.jsx: 持续迭代范围追加确认。
ContinuationScopeAddition.test.jsx: 范围追加确认检查。
ConversationAttachments.jsx: 会话附件选择与列表。
ConversationComposer.jsx: 会话输入、附件与发送操作。
ConversationTimeline.jsx: 会话时间线及消息展示。
ConversationTimeline.test.jsx: 会话时间线展示检查。
CurrentWorkContext.jsx: 当前任务上下文与刷新入口。
GoalContinuationPanel.jsx: Goal 持续执行的控制面板。
GoalHistory.test.jsx: Goal 历史展示检查。
GoalRetryConsent.test.jsx: Goal 重试授权检查。
PreparationFacts.jsx: 项目准备步骤所需事实。
ProjectContinuationControls.jsx: 项目持续迭代的授权与控制。
ProjectContinuationControls.test.jsx: 持续迭代授权与控制检查。
ProjectContinuationHandoff.test.jsx: 持续迭代交接状态检查。
ProjectConversation.jsx: 项目会话、草稿及任务准备。
ProjectConversation.priority.test.jsx: 回复期间追加输入与停止检查。
ProjectIterationSuggestion.jsx: 项目下一轮建议与入口。
ProjectIterationSuggestion.test.jsx: 迭代建议展示检查。
ProjectQuestionBoard.jsx: 项目问题与回答入口。
ProjectQuestionBoard.test.jsx: 项目问题板检查。
ProjectUnderstandingReply.jsx: 项目初步检查结果与覆盖范围。
TaskAttention.jsx: 任务准备输入与确认。
TaskDelegationSummary.jsx: 任务委派范围与验收摘要。
TaskDraftControls.jsx: 项目模式与任务草稿操作。
TaskDraftControls.test.jsx: 任务草稿操作检查。
TaskNavigation.jsx: 选择及编辑待确认的任务队列。
TaskNavigation.test.jsx: 任务导航检查。
TaskSearchConfiguration.jsx: 任务搜索配置与授权入口。
TaskSearchConfiguration.test.jsx: 任务搜索配置检查。
TeamDecisionPreview.jsx: 待确认团队变更预览。
TeamWorkspace.jsx: 排列项目会话、Worker 状态与 Goal 记录。
WorkWorkspaceShell.jsx: 工作台路由与外层布局。
WorkWorkspaceShell.test.jsx: 工作台外层布局检查。
assistantControls.css: 助手操作与请求恢复样式。
attemptMemory.js: 解析指定尝试关联的记忆。
continuationActivity.js: 持续迭代活动的展示数据。
conversationFeedback.js: 会话发送反馈与时间线投影。
conversationFeedback.vitest.js: 会话发送反馈检查。
conversationReading.js: 会话阅读位置与未读状态。
conversationReading.vitest.js: 会话阅读位置检查。
currentWorkContext.css: 当前任务上下文样式。
currentWorkContext.js: 当前任务上下文投影。
currentWorkContext.vitest.js: 当前任务上下文检查。
deliverySummary.css: 交付摘要样式。
goalContinuation.css: Goal 持续执行面板样式。
presentationUtils.js: 工作台状态展示工具。
projectConversation.css: 项目会话样式。
projectQuestionBoard.css: 项目问题板样式。
skillReviewExecutions.css: Skill 审查执行记录样式。
taskWorkspace.css: 任务工作区样式。
timelineCopy.js: 会话事件的本地化文案。
useConversationAttachments.js: 会话附件选择与上传状态。
useConversationReading.js: 会话阅读位置与跟随控制。
workspace.css: 工作台基础布局样式。
workspaceResponsive.css: 工作台响应式布局样式。

[PROTOCOL]: Update this header on change, then check AGENTS.md
