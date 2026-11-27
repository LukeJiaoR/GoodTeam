# team/

> L2 | Parent: [AGENTS.md](../../../../AGENTS.md)

AGENTS.md: 记录 Team 模块的成员与职责。
AssistantRuntimePanel.jsx: 从工作台打开并保存任务助手配置。
AssistantRuntimePanel.test.jsx: 验证任务助手配置的编辑、保存与刷新。
BatchWorkerDialog.jsx: 为单个角色或已选角色提交运行配置。
ResponsibilityGroup.jsx: 展开职责组并提供角色数量与可选的组配置入口。
RoleSkillsDialog.jsx: 为项目角色选择并保存 Skill 绑定。
RuntimeFields.jsx: 根据目录和运行规则显示模型与推理深度字段。
SkillLibraryPanel.jsx: 查询 Skill 库并导入本地 Skill。
TeamPage.jsx: 按职责配置角色。独立视图提供 Worker 管理。
WorkerDialog.jsx: 创建 Worker 或编辑 Worker 与任务助手的运行配置。
batchWorkerPlan.vitest.js: 验证角色配置命令的目标范围与字段。
catalogState.js: 合并单个执行器的目录响应与刷新状态。
catalogState.vitest.js: 验证目录更新时保留可用的模型数据。
runtimeOptions.vitest.js: 验证模型选择与推理深度联动。
saveAssistantRuntime.js: 保存任务助手配置并汇报连接验证结果。
team.css: 定义角色配置、Worker 管理和共享对话框样式。
teamModel.js: 将 Team API 快照投影为名册与编辑命令。
useAgentCatalog.js: 按当前执行器加载模型目录，在页面内复用响应。
useTeamDialog.js: 维护对话框的键盘操作与焦点。

[PROTOCOL]: Update this header on change, then check AGENTS.md
