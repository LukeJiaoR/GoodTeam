// Presentation only: a valid grant or reserved call is not proof of execution.
export function continuationActivity(session, zh = false) {
  const value = session?.project_continuation;
  if (value?.status !== 'active') return null;
  const copy = (label, detail, enLabel, enDetail, rationale = '') => ({
    label: zh ? label : enLabel, detail: zh ? detail : enDetail, rationale,
  });
  const pending = value.pending_batch;
  if (pending?.run_id) {
    const batch = session?.project_task_batches?.[pending.run_id];
    if (batch?.expected_continuation_id !== value.id) return copy('批次归属待核对', '保留原批次，不据此声称 Worker 正在执行。',
      'Batch attribution needs checking', 'Retain the original batch; this is not proof of a running Worker.');
    if (batch.status === 'review') return copy('等待审查与交付', '已有候选结果；尚不能当作验收或交付通过。',
      'Awaiting review and delivery', 'A candidate exists; this is not accepted delivery.');
    if (batch.status === 'blocked') return copy('批次结果需要处理', batch.error || '核对原结果和恢复入口，不重复未知执行。',
      'Batch result needs attention', batch.error || 'Inspect the original evidence and recovery controls; do not repeat unknown execution.');
    if (['claimed', 'running'].includes(batch.status)) return copy('等待 Worker 结果', '已记录原批次。当前进程是否活跃，以对应调用的实际状态为准。',
      'Awaiting Worker results', 'The original batch is recorded. Check its invocation evidence for current process activity.');
    return copy('等待批次状态核对', '原批次还未完成本轮接续判断。',
      'Awaiting batch reconciliation', 'The original batch has not finished this continuation step.');
  }
  if (pending) return copy('已预留，待启动', '已保留原任务批次，尚不代表 Worker 已启动。',
    'Reserved, awaiting start', 'The original task batch is reserved; no Worker start is implied.');
  if (value.discovery_pending) return value.discovery_pending.selection
    ? copy('已有决定，待落实', '保留原决定和身份，尚未确认后续任务开始。',
      'Decision retained, awaiting application', 'The original decision and identity are retained; follow-up execution is not confirmed.')
    : copy('等待原判断结果', '已预留发现判断调用；核对原调用，不把预约当作正在执行。',
      'Awaiting original judgment result', 'A discovery call is reserved; inspect that invocation rather than treating reservation as execution.');
  if (value.planning_backlog_version) return copy('等待原规划结果', '已预留原规划调用；保留其身份和已用额度。',
    'Awaiting original planning result', 'The original planning call is reserved, with its identity and spent budget retained.');
  if (value.reason === 'awaiting_project_context') {
    const attempt = value.waiting_attempt_id;
    const event = attempt?.startsWith(`${value.id}:`) && [...(session?.timeline || [])].reverse().find(row =>
      row?.event === 'project_iteration_choice_recorded' && row.details?.choice?.planning_attempt_id === attempt
      && row.details.choice.status === 'waiting');
    return copy('等待项目事实变化', 'Manager 已决定暂不派发。提供相关新事实后，原调度可在剩余额度内重判。',
      'Waiting for project facts', 'Manager chose not to dispatch. Relevant new facts can trigger another judgment within the remaining budget.',
      event?.details?.choice?.rationale || '');
  }
  if (value.reason === 'backlog_judgment_waiting') return copy('等待任务事实变化', '已有判断，当前未派发新任务。依赖或相关事实变化后再判断，不把等待当作执行。',
    'Waiting for task facts', 'A judgment is retained without new dispatch. Reconsider when dependencies or relevant facts change; waiting is not execution.',
    value.discovery_last?.rationale || '');
  if (['planning_context_changed', 'discovery_context_changed', 'delivery_feedback_pending'].includes(value.reason)) return copy(
    '等待下一次判断', '有新的 Context 或尚未处理的交付证据；下一次调度仍受原授权、预算和 Stop 约束。',
    'Awaiting next judgment', 'New Context or unconsumed delivery evidence remains; the next tick retains the original authority, budget and Stop checks.');
  if (value.reason === 'manager_resource_busy') return copy('等待可用资源', '当前未获得 Manager 调用资源；没有据此启动新的执行者。',
    'Waiting for available resources', 'A Manager resource has not been acquired; no new executor is implied.');
  if (value.reason === 'awaiting_human_decision') return copy('等待 Human 决定', '查看原问题板；其他已授权工作仍由 Manager 根据依赖判断。',
    'Waiting for a Human decision', 'Use the original question board; Manager judges other authorized work against its dependencies.');
  return copy('授权有效，待调度核对', '授权仍有效，但这条记录本身不能证明有任务正在执行。',
    'Authorized, awaiting scheduler check', 'The grant is active; this record alone does not establish ongoing execution.');
}
