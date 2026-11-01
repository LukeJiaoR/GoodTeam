/** Project advice enters the existing queue; starting requires explicit confirmation. */
function iterationPlanningRequest(snapshot, language = 'en') {
  if (snapshot?.schema_version !== 'project_iteration_backlog.v1') return '';
  const zh = String(language).startsWith('zh');
  return 'GoodTeam project iteration preview (read-only).\n' + (zh
    ? '请只读分析本项目的现状、代码、历史任务和验证结果，提出可能的下一轮候选，不要凭空宣称任务已获授权或已经执行。'
    : 'Inspect the project, its real code, task history and evidence. Identify possible next iterations. Do not invent completed work or start execution.');
}

export default function ProjectIterationSuggestion({
  snapshot, choice, queuedTasks = [], onPlan, onQueue, onInspect, disabled, language = 'en',
}) {
  if (snapshot?.schema_version !== 'project_iteration_backlog.v1') return null;
  const zh = String(language).startsWith('zh');
  const candidate = snapshot.next_candidate;
  const chosen = choice?.schema_version === 'project_iteration_choice.v1' ? choice : null;
  const current = chosen && chosen.status === 'recommended' && chosen.backlog_version === snapshot.version;
  const batch = current && chosen.execution_mode === 'parallel' && Array.isArray(chosen.selected_tasks)
    && chosen.selected_tasks.length > 1 ? chosen.selected_tasks : null;
  const queued = new Set((queuedTasks || [])
    .filter(item => ['queued', 'selected'].includes(item.status))
    .map(item => item.project_task_id).filter(Boolean));
  const selectedIds = current ? (chosen.selected_task_ids || [chosen.selected_task_id]) : [];
  const queuedCount = selectedIds.filter(id => queued.has(id)).length;
  const fullyQueued = Boolean(current && selectedIds.length && queuedCount === selectedIds.length);
  const selectedQueue = selectedIds.map(id => (queuedTasks || []).find(item => item.project_task_id === id && item.status === 'queued'));
  const canStartBatch = fullyQueued && selectedQueue.every(Boolean);
  const activeBatch = (queuedTasks || []).some(item => item.batch_run_id && item.status === 'running');
  const activeQueue = (queuedTasks || []).filter(item => item.batch_run_id && item.status === 'running');
  const batchProgress = (queuedTasks || []).filter(item => item.batch_run_id && ['running', 'review', 'blocked'].includes(item.status));
  const unavailable = snapshot.status === 'unavailable';
  const label = zh ? '下一轮项目迭代' : 'Next project iteration';
  const hint = unavailable
    ? (zh ? '项目任务账本不可读取' : 'Project backlog is unavailable')
    : candidate
      ? (zh ? '基于本地任务账本的候选，并非已经批准执行' : 'Grounded in local tasks; not yet approved for execution')
      : (zh ? '暂无可规划的待办，先分析项目现状' : 'No eligible task; inspect the project first');

  const submit = () => {
    if (candidate) {
      onPlan?.({
        action: 'plan_next_iteration',
        expected_backlog_version: snapshot.version,
        confirmed: true,
      });
    } else {
      onInspect?.(iterationPlanningRequest(snapshot, language));
    }
  };

  return <section className="project-iteration-suggestion" aria-label={label}>
    <div className="project-iteration-suggestion__header">
      <strong>{label}</strong>
      <span>{hint}</span>
    </div>
    {current ? (
      <div className="project-iteration-suggestion__choice">
        <strong>{batch ? (zh ? 'Manager 建议并行任务组' : 'Manager recommends parallel tasks')
          : (zh ? 'Manager 建议' : 'Manager recommends')}: {batch ? '' : chosen.selected_task_id}</strong>
        {batch ? (batch.length > 6 ? (
          <details>
            <summary>{zh ? '查看并行候选任务' : 'View candidate tasks'} ({batch.length})</summary>
            <ol className="project-iteration-suggestion__batch">
              {batch.map(item => <li key={item.task_id}>{item.task_id} — {item.title}</li>)}
            </ol>
          </details>
        ) : (
          <ol className="project-iteration-suggestion__batch">
            {batch.map(item => <li key={item.task_id}>{item.task_id} — {item.title}</li>)}
          </ol>
        )) : <p>{chosen.selected_title}</p>}
        <p>{chosen.rationale}</p>
        <small>{batch ? (zh ? '并行仅为规划建议；未隔离、派发或合并'
          : 'Parallel plan only; no workers launched, isolation or merge verified')
          : (zh ? '仅供规划；尚未执行' : 'Advisory only; no execution started')}</small>
        {batch && Number.isInteger(chosen.max_simultaneous_workers_per_dispatch) ? (
          <small>{zh
            ? `确认后最多同时运行 ${chosen.max_simultaneous_workers_per_dispatch} 个 Worker；超出部分排队`
            : `After confirmation, up to ${chosen.max_simultaneous_workers_per_dispatch} Workers run concurrently; excess tasks wait in the pool`}</small>
        ) : null}
      </div>
    ) : candidate ? (
      <p><strong>{candidate.task_id}: {candidate.title}</strong> · {candidate.priority}
        {candidate.description ? <span> — {candidate.description.slice(0, 220)}</span> : null}</p>
    ) : null}
    {chosen && !current ? <small>{zh ? '旧建议已过期，请重新评估。' : 'Previous recommendation is stale; re-evaluate.'}</small> : null}
    {batchProgress.length > 0 && <div aria-label={zh ? '批次执行结果' : 'Batch execution progress'}>
      <strong>{zh ? '已确认的项目任务' : 'Confirmed project tasks'}</strong>
      <ul>{batchProgress.map(item => <li key={item.id}>{item.project_task_id} — {
        item.status === 'running' ? (zh ? '执行中' : 'Running')
          : item.status === 'review' ? (zh ? '待独立审查与交付确认' : 'Awaiting independent review and delivery')
            : (zh ? '已阻塞，请检查执行证据' : 'Blocked; inspect execution evidence')
      }</li>)}</ul>
      {activeQueue.length > 0 && <button type="button" disabled={disabled} onClick={() => onQueue?.({
        action: 'start_queued_task_batch', proposal_ids: activeQueue.map(item => item.id),
        expected_revisions: Object.fromEntries(activeQueue.map(item => [item.id, item.revision])), confirmed: true,
      })}>{zh ? '检查或恢复已确认批次' : 'Check or resume confirmed batch'}</button>}
    </div>}
    {current && (fullyQueued ? (
      <div>
        <small>{zh ? '已进入普通任务队列；确认后使用隔离工作区执行，结果需审查与交付确认'
          : 'Queued tasks run in isolated workspaces after confirmation; results need review and delivery confirmation'}</small>
        {canStartBatch && <button type="button" disabled={disabled || activeBatch} onClick={() => onQueue?.({
          action: 'start_queued_task_batch',
          proposal_ids: selectedQueue.map(item => item.id),
          expected_revisions: Object.fromEntries(selectedQueue.map(item => [item.id, item.revision])),
          confirmed: true,
        })}>{zh ? '确认并执行所选任务（可能消耗模型额度）' : 'Confirm and run selected tasks (uses model)'}</button>}
      </div>
    ) : (
      <button type="button" disabled={disabled} onClick={() => onQueue?.({
        action: 'queue_iteration_choice',
        expected_backlog_version: snapshot.version,
        confirmed: true,
      })}>{zh ? '将 Manager 选择加入现有任务队列' : 'Add selected tasks to existing queue'}</button>
    ))}
    {!current && <button
      type="button" disabled={disabled || unavailable || activeBatch} onClick={submit}>
      {candidate
        ? (zh ? '请 Manager 比较并选择（可能消耗模型额度）' : 'Ask Manager to choose (uses model)')
        : (zh ? '请 Manager 分析项目' : 'Ask Manager to inspect the project')}
    </button>}
  </section>;
}
