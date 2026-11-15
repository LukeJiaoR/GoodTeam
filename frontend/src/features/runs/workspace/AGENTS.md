# frontend/src/features/runs/workspace/
> L2 | Parent: ../../../../../AGENTS.md

成员按文件列出。组件通过现有回调提交操作。
SourceEvidence 仅打开侧栏。BatchSourceContent 仅读取报告。
执行与审批权限属于后端原服务。

AssistantControls.jsx: 展示运行时配置与请求恢复，未知错误使用通用提示
AssistantControls.test.jsx: 回归用例：requires cost consent and submits a read-only runtime check
AssistantPendingReply.jsx: 导出 AssistantPendingReply；依赖 react
AttemptInspector.jsx: 导出 AttemptInspector；依赖 ../../../components/ui/WorkspacePanel, ./TaskInputContext.jsx, ./DeliverySummary
BatchSourceContent.jsx: 绑定候选和检查回执后展示报告，不推进执行
BatchSourceContent.test.jsx: 回归用例：reads the matching original review only after opening and displays its findings and source quote
ContinuationActivity.test.jsx: 回归用例：shows actual waits and original Manager rationale without claiming execution or starting actions
ContinuationScopeAddition.jsx: 导出 ContinuationScopeAddition；依赖 react
ContinuationScopeAddition.test.jsx: 回归用例：keeps selection unapproved until confirmation and invalidates it when displayed scope changes
ConversationAttachments.jsx: 导出 AttachmentPicker, AttachmentList；依赖 react, lucide-react, ./useConversationAttachments
ConversationChangeContext.jsx: 导出 ConversationChangeContext；依赖 lucide-react, ../../../components/ui/Button, ./conversationChangeContext
ConversationComposer.jsx: 导出 ConversationComposer；依赖 react, lucide-react, ../../../components/ui/Button
ConversationTimeline.jsx: 展示对话及状态，来源报告通过独立侧栏呈现
ConversationTimeline.test.jsx: 回归用例：renders a compact system chip when an event has no extra details
CurrentWorkContext.jsx: 导出 CurrentWorkContext；依赖 ../../../components/ui/Button, ../../../components/ui/StatusBadge, ./currentWorkContext.js
DeliverySummary.jsx: 导出 DeliveryArtifacts, DeliverySummary；依赖 react, lucide-react, ../../../lib/api/client
GoalContinuationPanel.jsx: 导出 GoalContinuationPanel；依赖 react, ../runApi, ../../../components/ui/Button
GoalHistory.test.jsx: 回归用例：uses the canonical project identity from the current Goal on a lowercase route
GoalRetryConsent.test.jsx: 回归用例：preserves consent across same-identity polling, submits the bound target once, then consumes consent
ManagerApprovalBrief.jsx: 展示审批范围及后果，完整证据保留在侧栏
PreparationFacts.jsx: 导出 PreparationFacts
ProjectContinuationControls.jsx: 展示接续与交付控制，确认仍绑定具体候选
ProjectContinuationControls.test.jsx: 回归用例：binds explicit scoped continuation and reviewed landed delivery to visible evidence
ProjectContinuationHandoff.test.jsx: 回归用例：offers a current bilingual Manager handoff through existing controls without starting work
ProjectConversation.jsx: 导出 ProjectConversation；依赖 ./blockerPresentation, ./currentWorkContext.js, react
ProjectIterationSuggestion.jsx: 导出 ProjectIterationSuggestion；依赖 react, ./TaskSearchConfiguration, ./TaskDelegationSummary
ProjectIterationSuggestion.test.jsx: 回归用例：asks Manager to choose without automatically running Workers
ProjectQuestionBoard.jsx: 导出 ProjectQuestionBoard；依赖 react, lucide-react
ProjectQuestionBoard.test.jsx: 回归用例：keeps current and historical questions visible and records an answer without closing the question
ProjectUnderstandingReply.jsx: 解释扫描覆盖边界，将入口和命令放入来源侧栏
SkillReviewExecutions.jsx: 导出 SkillReviewExecutions；依赖 react, ../../../lib/api/client
TaskAttention.jsx: 展示待决事项和恢复操作，保留审批决策边界
TaskDelegationSummary.jsx: 导出 TaskDelegationSummary
TaskDraftControls.jsx: 导出 ProjectModeControl, TaskDraftControls；依赖 react, lucide-react, ../../../components/ui/Button
TaskDraftControls.test.jsx: 回归用例：shows the bound task contract but not stale metadata after human editing
TaskInputContext.jsx: 导出 TaskInputContext；依赖 react, lucide-react, ../runApi
TaskNavigation.jsx: 导出 TaskNavigation；依赖 react, lucide-react, ../../../components/ui/Button
TaskNavigation.test.jsx: 回归用例：project conversation, current iteration and historical iterations keep their own identities and status
TaskSearchConfiguration.jsx: 导出 TaskSearchConfiguration；依赖 react, ../../settings/providerApi
TaskSearchConfiguration.test.jsx: 回归用例：search defaults off and explicit save pins provider revision without executing
TeamDecisionPreview.jsx: 导出 TeamDecisionPreview
TeamWorkspace.jsx: 模块 TeamWorkspace；依赖 ./ProjectConversation, ./ConversationTimeline, ./TaskAttention
WorkApprovalAction.jsx: 导出 WorkApprovalAction；依赖 react, ../../../components/ui/Button, ./workApprovalContext
WorkTaskDetailDialog.jsx: 导出 resolveTaskDependencies, findMatchingReview, WorkTaskDetailDialog；依赖 react, react-router-dom, lucide-react
WorkWorkspaceShell.jsx: 导出 WorkWorkspaceShell, WorkWorkspaceRoute；依赖 react-router-dom
WorkWorkspaceShell.test.jsx: 回归用例：does not carry draft or navigation state into a different project
WorkflowRetryAction.jsx: 导出 WorkflowRetryAction；依赖 ../../../components/ui/Button
assistantControls.css: 样式：assistant-controls, assistant-control-actions, product-button, assistant-team-preview
attemptInspection.js: 导出 resolveAttemptInspection
attemptInspection.vitest.js: 回归用例：uses the server assignment and keeps attempt and assignment states separate
attemptInspector.css: 样式：attempt-inspector, attempt-inspector__scope, attempt-inspector__scope-facts, attempt-inspector__section
attemptMemory.js: 导出 resolveAttemptMemory
blockerPresentation.js: 导出 isArtifactRevisionBlocker, blockerSummary
continuationActivity.js: 导出 continuationActivity
conversationChangeContext.css: 样式：team-change-context
conversationChangeContext.js: 导出 availableChangeTarget, changeContextStatus, matchesSavedChange
conversationChangeContext.vitest.js: 回归用例：uses the exact existing actionable review target
conversationFeedback.js: 导出 createConversationDelivery, conversationTimeline, shouldSubmitConversationOnEnter, deliveryLabel
conversationFeedback.vitest.js: 回归用例：publishes immediately, then records only after the API acknowledges
conversationReading.js: 导出 READING_STORAGE_KEY, READING_MAX_AGE, readingScope, timelineReadingId, loadReadingPosition, saveReadingPosition, unreadConversation, readingChangeSummary, readingCopy
conversationReading.vitest.js: 回归用例：checkpoints persist only bounded identity and viewport metadata and remain scope isolated
conversationRouting.js: 导出 isChangeDiscussion
conversationRouting.vitest.js: 回归用例：keeps ordinary questions on the existing message endpoint
currentWorkContext.css: 样式：work-workspace-shell, work-current-context, work-current-context__identity, work-current-context__project
currentWorkContext.js: 导出 currentWorkContext, WORK_CONTEXT_TARGETS, focusWorkContext；依赖 ./blockerPresentation, ./presentationUtils
currentWorkContext.vitest.js: 回归用例：uses execution truth, not a completed message or percentage
deliveryLinks.js: 导出 isBoundReportUrl, deliveryLinkResolver
deliverySummary.css: 样式：delivery-summary, delivery-report-list, delivery-report, delivery-report-heading
executionEvidence.js: 导出 executionEvidenceHref
executionEvidence.vitest.js: 回归用例：uses the backend-provided relative URL without synthesizing an identity
goalContinuation.css: 样式：goal-continuation, goal-continuation-heading, goal-continuation-snapshot, goal-continuation-work
presentationUtils.js: 导出 WORK_VIEWS, tone, translated, statusText, reviewGateName, recommendationReasonText
projectConversation.css: 样式：work-workspace-shell, project-conversation-scope, project-composer-modes, project-conversation-follow
projectQuestionBoard.css: 样式：project-question-board, project-question-board__heading, project-question-board__total, project-question-board__column
skillReviewExecutions.css: 样式：skill-review-executions, skill-review-advisory
taskInputContext.css: 样式：task-input-context, task-input-context__toggle, task-input-context__panel, task-input-context__body
taskInputContext.js: 导出 validTaskInputContext
taskWorkspace.css: 样式：css, work-workspace-shell, dark, product-work-page
timelineCopy.js: 区分对话正文和状态文案，保留人工问题的回答入口
useConversationAttachments.js: 导出 IMAGE_ACCEPT, FILE_ACCEPT, useConversationAttachments；依赖 react, ../runApi
useConversationReading.js: 导出 useConversationReading；依赖 react, ./conversationReading
workApprovalContext.js: 导出 requiresInformedWorkApproval, workApprovalContext；依赖 ../../../lib/reviews/approvalConfirmation.js
workspace.css: 样式：work-workspace-shell, work-start-panel, work-section-header, work-plan-container
workspaceResponsive.css: 样式：work-workspace-shell, work-board, work-section-header, work-primary-view
workspaceTaskDetail.css: 样式：work-workspace-shell, task-detail-processing-banner, task-detail-processing-spinner, task-detail-backdrop

[PROTOCOL]: Update this header on change, then check AGENTS.md
