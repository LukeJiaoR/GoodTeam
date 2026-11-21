# workspace/
> L2 | Parent: [AGENTS.md](../../../../../AGENTS.md)

项目工作台的会话、任务、执行与交付界面。
组件接收上层状态与操作，不决定后端授权。

AGENTS.md: 本目录的成员与职责边界。
AssistantControls.jsx: 助手请求恢复与待确认操作。
AssistantControls.test.jsx: 助手操作与恢复界面检查。
AssistantPendingReply.jsx: 回复状态、流式文本与停止按钮。
AssistantPendingReply.test.jsx: 回复状态与停止按钮检查。
AttemptInspector.jsx: 展示指定执行尝试的详情。
ContinuationActivity.test.jsx: 项目持续迭代活动检查。
ContinuationScopeAddition.jsx: 持续迭代范围追加确认。
ContinuationScopeAddition.test.jsx: 范围追加确认检查。
ConversationAttachments.jsx: 会话附件选择与列表。
ConversationChangeContext.jsx: 会话内变更目标的选择与状态。
ConversationComposer.jsx: 会话输入、附件与发送操作。
ConversationTimeline.jsx: 会话时间线及消息展示。
ConversationTimeline.test.jsx: 会话时间线展示检查。
CurrentWorkContext.jsx: 当前任务上下文与刷新入口。
DeliverySummary.jsx: 交付结果与证据入口。
GoalContinuationPanel.jsx: Goal 持续执行的控制面板。
GoalHistory.test.jsx: Goal 历史展示检查。
GoalRetryConsent.test.jsx: Goal 重试授权检查。
ManagerApprovalBrief.jsx: Manager 审批摘要。
PreparationFacts.jsx: 项目准备步骤所需事实。
ProjectContinuationControls.jsx: 项目持续迭代的授权与控制。
ProjectContinuationControls.test.jsx: 持续迭代授权与控制检查。
ProjectContinuationHandoff.test.jsx: 持续迭代交接状态检查。
ProjectConversation.jsx: 组合项目会话、输入与待处理事项。
ProjectConversation.priority.test.jsx: 回复期间追加输入与停止检查。
ProjectIterationSuggestion.jsx: 项目下一轮建议与入口。
ProjectIterationSuggestion.test.jsx: 迭代建议展示检查。
ProjectQuestionBoard.jsx: 项目问题与回答入口。
ProjectQuestionBoard.test.jsx: 项目问题板检查。
ProjectUnderstandingReply.jsx: 项目初步检查结果与覆盖范围。
SkillReviewExecutions.jsx: Skill 审查执行记录。
TaskAttention.jsx: 当前任务的待处理事项。
TaskDelegationSummary.jsx: 任务委派范围与验收摘要。
TaskDraftControls.jsx: 项目模式与任务草稿操作。
TaskDraftControls.test.jsx: 任务草稿操作检查。
TaskInputContext.jsx: 任务原始输入与上下文。
TaskNavigation.jsx: 当前任务与历史任务导航。
TaskNavigation.test.jsx: 任务导航检查。
TaskSearchConfiguration.jsx: 任务搜索配置与授权入口。
TaskSearchConfiguration.test.jsx: 任务搜索配置检查。
TeamDecisionPreview.jsx: 待确认团队变更预览。
TeamWorkspace.jsx: 组合会话、Worker 与执行区域。
WorkApprovalAction.jsx: 工作台审批操作。
WorkTaskDetailDialog.jsx: 工作台任务详情对话框。
WorkWorkspaceShell.jsx: 工作台路由与外层布局。
WorkWorkspaceShell.test.jsx: 工作台外层布局检查。
WorkflowRetryAction.jsx: Workflow 重试操作。
assistantControls.css: 助手操作与请求恢复样式。
attemptInspection.js: 从执行数据选择指定尝试。
attemptInspection.vitest.js: 执行尝试选择检查。
attemptInspector.css: 执行尝试详情样式。
attemptMemory.js: 解析指定尝试关联的记忆。
blockerPresentation.js: 阻塞事项的展示摘要。
continuationActivity.js: 持续迭代活动的展示数据。
conversationChangeContext.css: 会话变更上下文样式。
conversationChangeContext.js: 会话变更目标与状态投影。
conversationChangeContext.vitest.js: 会话变更目标检查。
conversationFeedback.js: 会话发送反馈与时间线投影。
conversationFeedback.vitest.js: 会话发送反馈检查。
conversationReading.js: 会话阅读位置与未读状态。
conversationReading.vitest.js: 会话阅读位置检查。
conversationRouting.js: 会话操作与上下文路由。
conversationRouting.vitest.js: 会话路由检查。
currentWorkContext.css: 当前任务上下文样式。
currentWorkContext.js: 当前任务上下文投影。
currentWorkContext.vitest.js: 当前任务上下文检查。
deliveryLinks.js: 限定交付报告链接的来源。
deliverySummary.css: 交付摘要样式。
executionEvidence.js: 执行证据链接投影。
executionEvidence.vitest.js: 执行证据链接检查。
goalContinuation.css: Goal 持续执行面板样式。
presentationUtils.js: 工作台状态展示工具。
projectConversation.css: 项目会话样式。
projectQuestionBoard.css: 项目问题板样式。
skillReviewExecutions.css: Skill 审查执行记录样式。
taskInputContext.css: 任务输入上下文样式。
taskInputContext.js: 验证任务输入上下文的归属。
taskWorkspace.css: 任务工作区样式。
timelineCopy.js: 会话事件的本地化文案。
useConversationAttachments.js: 会话附件选择与上传状态。
useConversationReading.js: 会话阅读位置与跟随控制。
workApprovalContext.js: 工作台审批上下文与知情条件。
workspace.css: 工作台基础布局样式。
workspaceResponsive.css: 工作台响应式布局样式。
workspaceTaskDetail.css: 工作台任务详情样式。

[PROTOCOL]: Update this header on change, then check AGENTS.md
