# projects/

> L2 | Parent: [AGENTS.md](../../../../AGENTS.md)

AGENTS.md: 记录项目目录的成员与职责。
ProjectsPage.jsx: 显示项目目录与待处理事项，调用登记 API 导入或移除项目。
LoadProjectPanel.jsx: 选择本地目录并提交导入。
GitRecoveryPanel.jsx: 在项目准备阶段核对要纳入初始提交的文件。
gitRecoveryApi.js: 提交初始 Git 恢复请求。
RepositoryOverview.jsx: 显示仓库准备信息与来源上下文。
ProjectAttentionOverview.jsx: 在目录汇总需处理的项目事项。
ProjectAttentionFocus.jsx: 根据来源标识定位项目页面中的待处理事项。
projectAttention.js: 校验待处理事项并生成项目导航目标。
projectAttention.css: 定义跨项目待处理事项与定位提示的样式。
projectAttention.vitest.js: 验证待处理事项的校验与导航。
projectBrowse.js: 按 URL 查询与状态筛选项目快照。
projectList.js: 校验项目列表响应与状态字段。
projectPresentation.js: 把项目错误和恢复标识映射到界面文案与操作。
projectApi.js: 通过共享 JSON 客户端查询、导入和移除项目。
useProjectsSnapshot.js: 获取项目快照，使目录与侧栏在登记变更后同步刷新。
ProjectsPage.search.test.jsx: 验证目录搜索和 URL 筛选。

[PROTOCOL]: Update this header on change, then check AGENTS.md
