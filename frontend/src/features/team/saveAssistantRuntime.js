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
