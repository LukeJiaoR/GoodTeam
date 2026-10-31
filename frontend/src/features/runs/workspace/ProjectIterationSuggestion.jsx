/** Project-level advice only; this component never dispatches a Worker. */
export function iterationPlanningRequest(snapshot, language = 'en') {
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
            ? `直接派发上限：同时运行 ${chosen.max_simultaneous_workers_per_dispatch} 个 Worker；超出的待接入排队执行`
            : `Direct-dispatch cap: ${chosen.max_simultaneous_workers_per_dispatch} concurrent Workers; excess work requires queueing after integration`}</small>
        ) : null}
      </div>
    ) : candidate ? (
      <p><strong>{candidate.task_id}: {candidate.title}</strong> · {candidate.priority}
        {candidate.description ? <span> — {candidate.description.slice(0, 220)}</span> : null}</p>
    ) : null}
    {chosen && !current ? <small>{zh ? '旧建议已过期，请重新评估。' : 'Previous recommendation is stale; re-evaluate.'}</small> : null}
    {current && (fullyQueued ? (
      <small>{zh ? '已进入普通任务队列；通过现有任务确认流程执行' : 'In the existing task queue; the normal confirmation flow applies'}</small>
    ) : (
      <button type="button" disabled={disabled} onClick={() => onQueue?.({
        action: 'queue_iteration_choice',
        expected_backlog_version: snapshot.version,
        confirmed: true,
      })}>{zh ? '将 Manager 选择加入现有任务队列' : 'Queue selected tasks in existing workflow'}</button>
    ))}
    {!current && <button
      type="button" disabled={disabled || unavailable} onClick={submit}>
      {candidate
        ? (zh ? '请 Manager 比较并选择（可能消耗模型额度）' : 'Ask Manager to choose (uses model)')
        : (zh ? '请 Manager 分析项目' : 'Ask Manager to inspect the project')}
    </button>}
  </section>;
}
