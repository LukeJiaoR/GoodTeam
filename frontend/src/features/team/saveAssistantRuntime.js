/**
 * [INPUT]: Manager 配置命令、Team API 和刷新回调。
 * [OUTPUT]: 保存结果和连接验证状态。
 * [POS]: 在 Team 和项目工作台复用保存流程。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
// Shared by the full Team page and the Workbench configuration sheet.
export async function saveAssistantRuntime({ api, projectKey, commands, refresh }) {
  let saved = false;
  try {
    await api.sendTeamCommands(projectKey, commands, { reason: 'Update Task Assistant runtime' });
    saved = true;
    const result = await api.taskAssistantAction(projectKey, {
      action: 'check_runtime', confirmed: true,
      message_id: globalThis.crypto?.randomUUID?.() || `check-${Date.now()}`,
    });
    await refresh();
    return { saved, runtime: result.task_assistant_session?.runtime };
  } catch (error) {
    const latest = await refresh().catch(() => null);
    return { saved, error, runtime: saved ? latest?.task_assistant_session?.runtime : null };
  }
}
