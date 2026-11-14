# frontend/src/components/ui/
> L2 | Parent: ../../../../AGENTS.md

成员按文件列出。组件通过现有回调提交操作。
SourceEvidence 仅打开侧栏。BatchSourceContent 仅读取报告。
执行与审批权限属于后端原服务。

Button.jsx: 导出 Button；依赖 lucide-react
ChatFlowchart.jsx: 导出 ChatFlowchart；依赖 react, ./flowchartLayout
ChatRichContent.jsx: 导出 InteractiveTable, ChatRichContent；依赖 react, lucide-react, ./ChatFlowchart
CollectionToolbar.jsx: 导出 CollectionToolbar；依赖 react, lucide-react
FileLocation.jsx: 导出 FileLocation；依赖 react, lucide-react, ../../lib/presentation/filePresentation
InlineNotice.jsx: 导出 InlineNotice
MarkdownView.jsx: 导出 isTableSeparatorRow, renderInlineMarkdown, parseMarkdownBlocks, MarkdownView；依赖 react, lucide-react, ./ChatRichContent
MarkdownView.links.test.jsx: 回归用例：MarkdownView.links.test
PageHeading.jsx: 导出 PageHeading
PageState.jsx: 导出 PageState；依赖 react
ProductError.jsx: 将已知错误转为产品文案，未知错误使用通用提示
ProductError.test.jsx: 回归用例：keeps request diagnostics out of the conversation without manufacturing a source-evidence entry
SourceEvidence.jsx: 按点击打开来源侧栏，复用模态焦点与关闭行为
SourceEvidence.test.jsx: 回归用例：offers a side-panel source only for retained reports, without request or identity envelopes
StatusBadge.jsx: 导出 StatusBadge；依赖 react
ToastProvider.jsx: 导出 useToast, ToastContainer, ToastProvider；依赖 react
WorkspacePanel.jsx: 导出 WorkspacePanel；依赖 react, lucide-react
chatRichContent.css: 样式：chat-rich-table, chat-rich-chart, chat-rich-steps, chat-rich-cards
chatRichContentParser.js: 导出 parseChatRichContent；依赖 ./flowchartLayout
compactPageHeading.css: 样式：product-shell, product-shell--workspace, product-page-heading, product-page-heading--compact
fileLocation.css: 样式：product-shell, file-location, file-location__content, file-location__label
flowchartLayout.js: 导出 layoutFlowchart
flowchartLayout.vitest.js: 回归用例：places nodes by connections, including branches, merges and skipped levels
workspacePanel.css: 样式：workspace-panel, workspace-panel__header, workspace-panel__body, product-source-evidence

[PROTOCOL]: Update this header on change, then check AGENTS.md
