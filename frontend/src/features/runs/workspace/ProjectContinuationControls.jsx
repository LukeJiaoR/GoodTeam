import { useState } from 'react';

function DeliveryConfirmation({ batch, projectKey, onAction, disabled, zh }) {
  const [landed, setLanded] = useState('');
  const [reviewed, setReviewed] = useState(false);
  const candidate = batch.integration?.candidate_commit;
  if (!candidate || batch.status !== 'review') return null;
  return <details className="project-iteration-suggestion">
    <summary>{zh ? '确认已落地的交付' : 'Confirm landed delivery'} · {batch.task_ids.join(', ')}</summary>
    <p>{zh ? '先审查候选代码和验证证据，并将该候选提交合并到项目。确认后，已授权的自动迭代会继续下一批。'
      : 'Review the candidate and verification evidence, then merge this candidate into the project. Authorized continuation will pick and run the next batch after confirmation.'}</p>
    <a href={`/api/projects/${encodeURIComponent(projectKey)}/iteration-batches/${encodeURIComponent(batch.run_id)}`}
      target="_blank" rel="noreferrer">{zh ? '查看任务执行与验证记录' : 'Inspect execution and verification records'}</a>
    <p>{zh ? '候选提交' : 'Candidate commit'}: <code>{candidate}</code></p>
    <p>{zh ? '本地候选目录' : 'Local candidate directory'}: <code>{batch.integration?.candidate_path}</code></p>
    <label>{zh ? '项目当前已落地的完整 Git commit' : 'Full landed Git commit currently checked out in the project'}
      <input value={landed} onChange={event => setLanded(event.target.value.trim())} disabled={disabled} spellCheck={false} />
    </label>
    <label><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} disabled={disabled} />
      {zh ? '我已独立审查此候选及验收证据，确认交付满足这些任务。'
        : 'I independently reviewed this candidate and acceptance evidence and confirm it delivers these tasks.'}</label>
    <button type="button" disabled={disabled || !reviewed || !/^[0-9a-f]{40}$|^[0-9a-f]{64}$/.test(landed)}
      onClick={() => onAction?.({ action: 'accept_project_batch_delivery', run_id: batch.run_id,
        candidate_commit: candidate, landed_commit: landed, reviewed: true, confirmed: true })}>
      {zh ? '核实版本并确认交付' : 'Verify version and accept delivery'}
    </button>
  </details>;
}

export default function ProjectContinuationControls({ snapshot, session, projectKey, onAction, disabled, canStart, language = 'en' }) {
  const [maximum, setMaximum] = useState(3);
  const [confirmed, setConfirmed] = useState(false);
  const [command] = useState(() => globalThis.crypto?.randomUUID?.() || `continuation-${Date.now()}`);
  const zh = String(language).startsWith('zh');
  const value = session?.project_continuation;
  const tasks = (snapshot?.candidates || []).filter(item => item.status === 'todo');
  const batches = Object.values(session?.project_task_batches || {}).filter(item => item.status === 'review');
  if (!onAction || (!value && !tasks.length && !batches.length)) return null;
  const active = value?.status === 'active';
  const labels = zh ? { active: '自动推进中', stopped: '已停止', blocked: '需要处理阻塞', exhausted: '授权批次已用完', completed: '授权任务已交付' }
    : { active: 'Running automatically', stopped: 'Stopped', blocked: 'Needs attention', exhausted: 'Approved budget used', completed: 'Approved tasks delivered' };

  return <section aria-label={zh ? '项目自动迭代' : 'Automatic project iteration'}>
    <details className="project-iteration-suggestion" open={Boolean(value)}>
      <summary>{zh ? '项目自动迭代' : 'Automatic project iteration'}{value ? ` · ${labels[value.status] || value.status}` : ''}</summary>
      {value ? <>
        <p>{zh ? '已预留批次' : 'Batch reservations'}: {value.batches_started} / {value.max_batches} · {zh ? '已预留规划次数' : 'Planning reservations'}: {value.manager_calls} / {value.max_manager_calls}</p>
        {value.status === 'blocked' ? <p role="status">{value.reason}</p> : null}
        {active ? <button type="button" disabled={disabled} onClick={() => onAction({
          action: 'stop_project_continuation', continuation_id: value.id,
        })}>{zh ? '停止后续迭代' : 'Stop future iterations'}</button> : null}
      </> : null}
      {!active && tasks.length > 0 ? <>
        <p>{zh ? '仅授权下方列出的现有任务自动规划和执行。交付仍需审查并核实已落地的版本；每批最多同时运行 10 个 Worker，按当前配置产生模型费用。'
          : 'Authorize planning and execution of only the existing tasks listed below. Delivery still requires review and a verified landed version. Up to 10 Workers run concurrently per batch and configured model charges apply.'}</p>
        <ul>{tasks.map(task => <li key={task.task_id}>{task.task_id}: {task.title}</li>)}</ul>
        <label>{zh ? '最多执行批次（同时限制 Manager 调用次数）' : 'Maximum batches (also limits Manager calls)'}
          <input type="number" min="1" max="50" value={maximum} disabled={disabled}
            onChange={event => setMaximum(Number(event.target.value))} />
        </label>
        <label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={disabled} />
          {zh ? '我确认上述任务范围、迭代次数和模型费用。' : 'I confirm this task scope, iteration limit and model charges.'}</label>
        <button type="button" disabled={disabled || !canStart || !confirmed || !Number.isInteger(maximum) || maximum < 1 || maximum > 50}
          onClick={() => onAction({ action: 'start_project_continuation',
            idempotency_key: `${command}:${value?.id || 'new'}:${snapshot.version}:${maximum}`,
            expected_backlog_version: snapshot.version,
            task_ids: tasks.map(task => task.task_id), max_batches: maximum, confirmed: true, billing_acknowledged: true })}>
          {zh ? '授权并开始自动迭代' : 'Authorize and start continuation'}
        </button>
      </> : null}
    </details>
    {batches.map(batch => <DeliveryConfirmation key={batch.run_id} batch={batch} projectKey={projectKey}
      onAction={onAction} disabled={disabled} zh={zh} />)}
  </section>;
}
