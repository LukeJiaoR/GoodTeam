/** Read-only task-ledger recommendation; planning never dispatches a Worker. */
export function iterationPlanningRequest(snapshot, language = 'en') {
  if (snapshot?.schema_version !== 'project_iteration_backlog.v1') return '';
  const candidate = snapshot.next_candidate;
  const isZh = String(language).startsWith('zh');
  if (!candidate) return 'GoodTeam project iteration preview (read-only).\n' + (isZh
    ? '请先只读检查本项目的代码、历史、现有任务和真实验证结果，发现并比较下一轮值得做的工作。不要假装已有可执行任务，也不要自动运行或变更权限。'
    : 'Read this project and its existing code, history, tasks and verified results. Discover and compare possible next iterations. Do not invent actionable work or start execution.');
  const criteria = (candidate.acceptance_criteria || []).map(value => '- ' + value).join('\n');
  const source = [
    'Local task: ' + candidate.task_id,
    'Backlog snapshot version: ' + snapshot.version,
    'Title: ' + candidate.title,
    'Description: ' + candidate.description,
    'Priority in source ledger: ' + candidate.priority,
    'Acceptance criteria:\n' + (criteria || '(not yet specified)'),
  ].join('\n');
  const instruction = isZh
    ? '请基于当前仓库、项目目标、依赖与实际工作状态独立判断下一轮迭代。这是一个真实本地 Backlog 候选，不是系统已经确定的最优优先级。核对该任务是否仍存在、版本/依赖是否有效；如有更有价值的候选请解释证据。给出有界实施草稿与验收标准，不能凭此直接开工。'
    : 'Evaluate the next project iteration against actual repository evidence, goals, dependencies and outcomes. This is a candidate from the local backlog, not a proven optimum. Recheck its identity, revision and dependencies; explain any better alternative. Draft bounded work and acceptance criteria, but do not execute from this suggestion.';
  return 'GoodTeam project iteration preview (read-only).\n' + instruction + '\n\n' + source;
}

export default function ProjectIterationSuggestion({ snapshot, onPlan, disabled, language = 'en' }) {
  if (snapshot?.schema_version !== 'project_iteration_backlog.v1') return null;
  const zh = String(language).startsWith('zh');
  const next = snapshot.next_candidate;
  const name = zh ? '下一轮项目迭代' : 'Next project iteration';
  const detail = next ? (zh ? '本地待办候选，尚非已批准的开发任务' : 'Local backlog candidate, not an approved task')
    : snapshot.status === 'unavailable' ? (zh ? '任务数据不可读取，不能据此选题' : 'Task data unavailable; cannot choose work')
      : (zh ? '尚无可直接规划的任务，需要先分析项目现状' : 'No eligible backlog entry; inspect the project first');
  return <section className="project-iteration-suggestion" aria-label={name}>
    <div className="project-iteration-suggestion__header">
      <strong>{name}</strong>
      <span>{detail}</span>
    </div>
    {next ? <p><strong>{next.task_id}: {next.title}</strong> · {next.priority}
      {next.description ? <span> — {next.description.slice(0, 220)}</span> : null}</p> : null}
    <button type="button" disabled={disabled || snapshot.status === 'unavailable'}
      onClick={() => onPlan?.(iterationPlanningRequest(snapshot, language))}>
      {next ? (zh ? '交给 Manager 评估并拟定计划' : 'Ask Manager to evaluate and plan')
        : (zh ? '请 Manager 分析项目候选' : 'Ask Manager to inspect the project')}
    </button>
  </section>;
}
