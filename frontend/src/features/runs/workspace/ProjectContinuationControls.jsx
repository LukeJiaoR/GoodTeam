import { Fragment, useState } from 'react';

function qualityAccepted(quality) {
  return quality?.status === 'reviewed' && quality.check_status === 'passed'
    && quality.review_status === 'machine_recorded' && quality.acceptance_status === 'accepted_by_review_policy';
}

function BatchRecoveryControls({ batch, continuation, projectKey, onAction, disabled, zh }) {
  const [retryConfirmed, setRetryConfirmed] = useState(false);
  const [abandonConfirmed, setAbandonConfirmed] = useState(false);
  const [resumeContinuation, setResumeContinuation] = useState(false);
  const recovery = batch.recovery || {};
  const retryable = recovery.retryable_task_ids || [];
  const preserved = recovery.preserved_task_ids || [];
  const unknown = recovery.unknown_task_ids || [];
  const abandoned = batch.status === 'abandoned';
  const canRetry = !abandoned && batch.status === 'blocked' && retryable.length > 0 && unknown.length === 0;
  const canResume = recovery.can_resume_continuation === true && continuation?.status === 'blocked'
    && continuation.id === recovery.continuation_id;
  const resume = canResume && resumeContinuation;
  const common = { run_id: batch.run_id, expected_recovery_version: recovery.version, confirmed: true };
  return <details className="project-iteration-suggestion" open>
    <summary>{abandoned ? (zh ? '批次已结束，证据保留' : 'Batch abandoned; evidence retained')
      : batch.status === 'blocked' ? (zh ? '处理批次阻塞' : 'Resolve batch failure')
        : (zh ? '处理当前批次' : 'Current batch options')} · {batch.task_ids.join(', ')}</summary>
    {recovery.reason ? <p role="status">{recovery.reason}</p> : null}
    {preserved.length > 0 ? <p>{zh ? '保留的成功结果' : 'Preserved successful results'}: {preserved.join(', ')}</p> : null}
    {retryable.length > 0 && !abandoned ? <p>{zh ? '已确认失败的任务' : 'Confirmed failed tasks'}: {retryable.join(', ')}</p> : null}
    {unknown.length > 0 ? <p>{zh ? '结果尚未确认，须先核对执行证据' : 'Outcome unconfirmed; inspect execution evidence first'}: {unknown.join(', ')}</p> : null}
    <a href={`/api/projects/${encodeURIComponent(projectKey)}/iteration-batches/${encodeURIComponent(batch.run_id)}`}
      target="_blank" rel="noreferrer">{zh ? '查看保留的执行证据' : 'Inspect retained execution evidence'}</a>
    {canRetry ? <>
      <p>{zh ? '仅重新执行上方已确认失败的任务；保留成功任务的结果。'
        : 'Retry only the confirmed failed tasks above. Successful task results are retained.'}</p>
      <label><input type="checkbox" checked={retryConfirmed} onChange={event => setRetryConfirmed(event.target.checked)} disabled={disabled} />
        {zh ? '我确认重新执行这些失败任务，并承担已配置模型的调用费用。'
          : 'I confirm retrying these failed tasks and acknowledge configured model charges.'}</label>
      {canResume ? <label><input type="checkbox" checked={resumeContinuation}
        onChange={event => setResumeContinuation(event.target.checked)} disabled={disabled} />
        {zh ? '恢复原授权范围内尚未用完的自动迭代额度。' : 'Resume the remaining iterations within the original approved scope and budget.'}
      </label> : null}
      <button type="button" disabled={disabled || !retryConfirmed || !recovery.version}
        onClick={() => onAction({ action: 'retry_project_batch', ...common,
          idempotency_key: `batch-retry:${batch.run_id}:${recovery.version}:${resume ? recovery.continuation_id : 'manual'}`,
          billing_acknowledged: true, retry_task_ids: retryable, resume_continuation: resume,
          ...(resume ? { continuation_id: recovery.continuation_id } : {}) })}>
        {zh ? '重试已确认失败的任务' : 'Retry confirmed failed tasks'}
      </button>
    </> : null}
    {!abandoned && recovery.can_abandon === true ? <>
      <label><input type="checkbox" checked={abandonConfirmed} onChange={event => setAbandonConfirmed(event.target.checked)} disabled={disabled} />
        {zh ? '我确认结束此批次、取消其中未交付的任务，并保留执行证据。'
          : 'I confirm abandoning this batch and canceling its undelivered tasks while retaining execution evidence.'}</label>
      <button type="button" disabled={disabled || !abandonConfirmed || !recovery.version}
        onClick={() => onAction({ action: 'abandon_project_batch', ...common,
          idempotency_key: `batch-abandon:${batch.run_id}:${recovery.version}` })}>
        {zh ? '结束此批次' : 'Abandon this batch'}
      </button>
    </> : null}
  </details>;
}

function BatchQualityControls({ batch, projectKey, onAction, disabled, zh }) {
  const [acknowledged, setAcknowledged] = useState(false);
  const quality = batch.quality;
  const accepted = qualityAccepted(quality);
  const labels = zh
    ? { prepared: '待检查', awaiting_review: '等待独立审查', awaiting_worker: '等待修复任务完成',
      awaiting_recheck: '等待复检', reviewed: '审查已完成', blocked: '已阻塞', needs_human: '需要人工处理',
      passed: '通过', failed: '失败', unknown: '尚未确认', missing: '尚无记录',
      machine_recorded: '已记录独立审查', operator_recorded: '已记录人工意见', accepted_by_review_policy: '满足验收策略' }
    : { prepared: 'Ready for checks', awaiting_review: 'Awaiting independent review', awaiting_worker: 'Awaiting correction Worker',
      awaiting_recheck: 'Awaiting recheck', reviewed: 'Review completed', blocked: 'Blocked', needs_human: 'Needs attention',
      passed: 'Passed', failed: 'Failed', unknown: 'Not yet verified', missing: 'No record',
      machine_recorded: 'Independent review recorded', operator_recorded: 'Operator review recorded', accepted_by_review_policy: 'Accepted by review policy' };
  const label = value => labels[value] || value || labels.unknown;
  return <details className="project-iteration-suggestion" open>
    <summary>{zh ? '候选检查与独立审查' : 'Candidate checks and independent review'} · {batch.task_ids.join(', ')}</summary>
    <p>{zh ? '进度' : 'Progress'}: {label(quality.status)}</p>
    <ul>
      <li>{zh ? '候选检查' : 'Candidate check'}: {label(quality.check_status)}</li>
      <li>{zh ? '独立审查' : 'Independent review'}: {label(quality.review_status)}</li>
      <li>{zh ? '验收结论' : 'Acceptance'}: {label(quality.acceptance_status)}</li>
    </ul>
    {quality.reason ? <p role="status">{quality.reason}</p> : null}
    {quality.next_action ? <p>{zh ? '下一步' : 'Next step'}: {quality.next_action}</p> : null}
    <a href={`/api/projects/${encodeURIComponent(projectKey)}/iteration-batches/${encodeURIComponent(batch.run_id)}`}
      target="_blank" rel="noreferrer">{zh ? '查看检查、审查与修复证据' : 'Inspect check, review and fix evidence'}</a>
    {quality.execution_id ? <p>{zh ? '检查与审查执行记录' : 'Check and review run'}: <code>{quality.execution_id}</code></p> : null}
    {quality.correction_job_id ? <p>{zh ? '修复任务' : 'Correction job'}: <code>{quality.correction_job_id}</code></p> : null}
    {quality.candidate_commit ? <p>{zh ? '当前候选提交' : 'Current candidate commit'}: <code>{quality.candidate_commit}</code></p> : null}
    {quality.candidate_path ? <p>{zh ? '当前候选目录' : 'Current candidate directory'}: <code>{quality.candidate_path}</code></p> : null}
    {!accepted ? <>
      <p>{zh ? '继续此批次已配置的检查、独立审查及修复。执行器可用时才能推进；交付确认将在检查与审查通过后开放。'
        : 'Continue this batch’s configured checks, independent review and correction. An available configured runtime is required. Delivery confirmation opens after checks and review pass.'}</p>
      <label><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} disabled={disabled} />
        {zh ? '我确认继续上述检查、审查或修复，并承担已配置模型的调用费用。'
          : 'I confirm continuing these checks, review or correction and acknowledge configured model charges.'}</label>
      <button type="button" disabled={disabled || !acknowledged || !quality.input_digest}
        onClick={() => onAction({ action: 'advance_project_batch_quality', run_id: batch.run_id,
          expected_input_digest: quality.input_digest, confirmed: true, billing_acknowledged: true })}>
        {zh ? '继续检查与审查' : 'Continue checks and review'}
      </button>
    </> : null}
  </details>;
}

function DeliveryConfirmation({ batch, projectKey, onAction, disabled, zh }) {
  const [landed, setLanded] = useState('');
  const [reviewed, setReviewed] = useState(false);
  const quality = batch.quality?.required ? batch.quality : null;
  const integration = quality || batch.integration;
  const candidate = integration?.candidate_commit;
  if (!candidate || batch.status !== 'review' || quality && !qualityAccepted(quality)) return null;
  return <details className="project-iteration-suggestion">
    <summary>{zh ? '确认已落地的交付' : 'Confirm landed delivery'} · {batch.task_ids.join(', ')}</summary>
    <p>{zh ? '先审查候选代码和验证证据，并将该候选提交合并到项目。确认后，已授权的自动迭代会继续下一批。'
      : 'Review the candidate and verification evidence, then merge this candidate into the project. Authorized continuation will pick and run the next batch after confirmation.'}</p>
    <a href={`/api/projects/${encodeURIComponent(projectKey)}/iteration-batches/${encodeURIComponent(batch.run_id)}`}
      target="_blank" rel="noreferrer">{zh ? '查看任务执行与验证记录' : 'Inspect execution and verification records'}</a>
    <p>{zh ? '候选提交' : 'Candidate commit'}: <code>{candidate}</code></p>
    <p>{zh ? '本地候选目录' : 'Local candidate directory'}: <code>{integration?.candidate_path}</code></p>
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
  const batches = Object.values(session?.project_task_batches || {}).filter(item =>
    ['review', 'blocked', 'abandoned'].includes(item.status) || item.quality?.required && item.status !== 'delivered');
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
    {batches.map(batch => <Fragment key={batch.run_id}>
      {['blocked', 'abandoned'].includes(batch.status) || batch.recovery?.can_abandon === true ? <BatchRecoveryControls
        key={`${batch.recovery?.version || ''}:${batch.status}:${value?.id || ''}:${value?.status || ''}`}
        batch={batch} continuation={value} projectKey={projectKey} onAction={onAction} disabled={disabled} zh={zh} /> : null}
      {batch.quality?.required && batch.status !== 'abandoned' ? <BatchQualityControls
        key={`${batch.quality.input_digest}:${batch.quality.status}:${batch.quality.correction_job_id || ''}:${batch.quality.candidate_commit || ''}`}
        batch={batch} projectKey={projectKey} onAction={onAction} disabled={disabled} zh={zh} /> : null}
      {batch.status !== 'abandoned' ? <DeliveryConfirmation key={`${batch.quality?.input_digest || ''}:${batch.quality?.candidate_commit || batch.integration?.candidate_commit || ''}`}
        batch={batch} projectKey={projectKey} onAction={onAction} disabled={disabled} zh={zh} /> : null}
    </Fragment>)}
  </section>;
}
