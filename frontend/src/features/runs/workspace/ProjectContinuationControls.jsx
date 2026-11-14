import ContinuationScopeAddition from './ContinuationScopeAddition';
import { continuationActivity } from './continuationActivity';
import { Fragment, useRef, useState } from 'react';

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

function ContinuationStartForm({ tasks, snapshot, value, onAction, disabled, canStart, zh }) {
  const [maximum, setMaximum] = useState('');
  const [discover, setDiscover] = useState(false);
  const [objective, setObjective] = useState('');
  const [paths, setPaths] = useState('');
  const [newTaskLimit, setNewTaskLimit] = useState('');
  const [planningLimit, setPlanningLimit] = useState('');
  const [feedback, setFeedback] = useState(true);
  const [initialFeedback, setInitialFeedback] = useState(true);
  const [observeRunning, setObserveRunning] = useState(false);
  const [followUp, setFollowUp] = useState(false);
  const [followUpLimit, setFollowUpLimit] = useState(1);
  const [confirmedInput, setConfirmedInput] = useState('');
  const [command] = useState(() => globalThis.crypto?.randomUUID?.() || `continuation-${Date.now()}`);
  const lastSubmission = useRef({ input: '', revision: 0 });
  const domains = paths.split(/\r?\n/).map(path => path.trim()).filter(Boolean).map(path => path.replace(/\/$/, ''));
  const validDomains = domains.length >= 1 && domains.length <= 12
    && new Set(domains.map(path => path.toLowerCase())).size === domains.length
    && domains.every(path => path.length <= 160 && !path.split('/').some(part => ['', '.', '..'].includes(part))
      && ![...path].some(character => character.charCodeAt(0) < 32 || '\\:*?[]'.includes(character)));
  const scopedTasks = tasks.filter(task => task.status === 'todo' || discover && task.status === 'blocked');
  const discovery = discover ? { objective: objective.trim(), allowed_write_domains: domains,
    max_new_tasks: newTaskLimit === '' ? null : Number(newTaskLimit),
    max_planning_calls: planningLimit === '' ? null : Number(planningLimit), allow_backlog_judgment: true, allow_team_roles: true,
    ...(feedback ? { allow_undelivered_feedback: true, allow_candidate_handoff: true } : {}),
    ...(feedback && initialFeedback ? { allow_initial_readonly_feedback: true } : {}),
    ...(observeRunning ? { allow_running_observation: true, allow_running_human_question: true } : {}),
    ...(observeRunning && followUp ? { worker_follow_up_limit: followUpLimit } : {}) } : undefined;
  const input = { action: 'start_project_continuation', expected_backlog_version: snapshot.version,
    task_ids: scopedTasks.map(task => task.task_id), max_batches: maximum === '' ? null : Number(maximum), confirmed: true, billing_acknowledged: true,
    ...(discovery ? { discovery } : {}) };
  const inputIdentity = JSON.stringify([value?.id || 'new', input]);
  const validLimits = [maximum, ...(discover ? [newTaskLimit, planningLimit] : [])]
    .every(limit => limit === '' || Number.isInteger(Number(limit)) && Number(limit) >= 1);
  const valid = scopedTasks.length > 0 && validLimits
    && (!discover || objective.trim().length >= 20 && objective.trim().length <= 2000 && validDomains
      && (!observeRunning || !followUp || Number.isInteger(followUpLimit) && followUpLimit >= 1 && followUpLimit <= 10));
  const start = () => {
    if (lastSubmission.current.input !== inputIdentity) {
      lastSubmission.current = { input: inputIdentity, revision: lastSubmission.current.revision + 1 };
    }
    onAction({ ...input, idempotency_key: `${command}:${lastSubmission.current.revision}` });
  };
  return <>
    <p>{discover ? (zh ? '授权下方初始任务，并让 Manager 在指定目标与路径范围内发现后续任务，按需使用项目中已启用的 Team Role。可依据已核实交付或当前已授权任务事实判断、提问及提出后续任务；未交付结果反馈可在下方授权。'
      : 'Authorize the initial tasks below and let Manager discover follow-up tasks within the specified objective and paths, using enabled Team Roles as needed. Judgment and follow-up tasks may use verified delivery or authorized task facts; undelivered feedback can be authorized below.')
      : (zh ? '仅授权下方列出的现有任务自动规划和执行。' : 'Authorize planning and execution of only the existing tasks listed below.')}
      {' '}{zh ? '交付仍需审查并核实已落地的版本；每批最多同时运行 10 个 Worker，按当前配置产生模型费用。'
        : 'Delivery still requires review and a verified landed version. Up to 10 Workers run concurrently per batch and configured model charges apply.'}</p>
    <ul>{scopedTasks.map(task => <li key={task.task_id}>{task.task_id}: {task.title}</li>)}</ul>
    <label><input type="checkbox" checked={discover} onChange={event => setDiscover(event.target.checked)} disabled={disabled} />
      {zh ? '在明确授权范围内发现后续任务' : 'Discover follow-up tasks within an explicit scope'}</label>
    {discover ? <fieldset disabled={disabled} style={{ display: 'grid', gap: '.5rem', minWidth: 0 }}>
      <legend>{zh ? '后续任务的授权范围' : 'Follow-up task authorization'}</legend>
      <label style={{ display: 'grid', gap: '.25rem' }}>{zh ? '项目目标（20–2000 字符）' : 'Project objective (20–2000 characters)'}
        <textarea value={objective} onChange={event => setObjective(event.target.value)} rows={3} maxLength={2000} />
      </label>
      <label style={{ display: 'grid', gap: '.25rem' }}>{zh ? '允许写入的项目路径（每行一个）' : 'Allowed project paths (one per line)'}
        <textarea value={paths} onChange={event => setPaths(event.target.value)} rows={3} spellCheck={false}
          placeholder={'src\ntests'} />
      </label>
      <p>{zh ? '填写 1–12 个项目相对目录或文件路径。Manager 可选择后续任务，独立任务可以并行执行。'
        : 'Enter 1–12 project-relative directories or files. Manager can select follow-up tasks and run independent tasks in parallel.'}</p>
      <p>{zh ? 'Manager 可按需选择项目中已启用且可用的 Team Role；只读任务不获得写权限，写入仍限于上述路径。'
        : 'Manager can select enabled and available Team Roles as needed. Read-only tasks receive no write access; other writes remain within the paths above.'}</p>
      <label><input type="checkbox" checked={feedback} onChange={event => {
        setFeedback(event.target.checked);
        if (!event.target.checked) setInitialFeedback(false);
      }} />
        {zh ? '允许根据未交付任务的已知结果继续判断，并在隔离工作区向后续任务交接已引用的候选'
          : 'Allow decisions from known undelivered outcomes and pass cited candidates to follow-up tasks in isolated workspaces'}</label>
      {feedback ? <>
        <label><input type="checkbox" checked={initialFeedback} onChange={event => setInitialFeedback(event.target.checked)} />
          {zh ? '首次工程交付前，允许根据初始只读任务结果接续' : 'Allow initial read-only task outcomes before the first engineering delivery'}</label>
        <p>{zh ? '仅消费原授权任务的已知结果、失败或部分成果，仍占用 Manager 额度；不重试未知执行，不把报告当作工程交付。'
          : 'Use only known outcomes, failures or partial results of originally authorized tasks, within the Manager allowance. No unknown execution is retried and reports are not engineering delivery.'}</p>
      </> : null}
      <label><input type="checkbox" checked={observeRunning} onChange={event => setObserveRunning(event.target.checked)} />
        {zh ? '允许执行期间的 Manager 观察判断' : 'Allow Manager observations while Workers run'}</label>
      {observeRunning ? <>
        <p>{zh ? '观察可将必要的人类决定保存到本项目问题板，不向外发送消息、不停止在途任务，也不增加执行权限。' : 'Observations may save necessary owner decisions on this project’s question board. No external messages, stopping current work or additional execution authority.'}</p>
        <label><input type="checkbox" checked={followUp} onChange={event => setFollowUp(event.target.checked)} />
          {zh ? '允许 Manager 向当前 Pi 调用提出有界进展追问' : 'Allow bounded Manager progress questions to current Pi invocations'}</label>
        {followUp ? <label>{zh ? 'Worker 追问总次数上限' : 'Total Worker follow-up limit'}
          <input type="number" min="1" max="10" value={followUpLimit} onChange={event => setFollowUpLimit(Number(event.target.value))} />
        </label> : null}
        <p>{zh ? '观察占用 Manager 额度；每次追问可能增加 Worker 模型费用，每个调用最多一次。只限原任务，不授权 steer、abort、扩范围或未知结果重试；旧授权不会自动升级。'
          : 'Observations use the Manager allowance; each follow-up may add Worker model charges, at most once per invocation. Original tasks only: no steer, abort, expanded scope or unknown-result retries. Existing grants are not upgraded.'}</p>
      </> : null}
      <label>{zh ? '新任务总量上限' : 'Maximum new tasks in total'}
        <input type="number" min="1" value={newTaskLimit} placeholder={zh ? '留空不限制' : 'No limit'} onChange={event => setNewTaskLimit(event.target.value)} />
      </label>
      <label>{zh ? 'Manager 调用总量上限（含后续任务发现）' : 'Total Manager calls (including follow-up discovery)'}
        <input type="number" min="1" value={planningLimit} placeholder={zh ? '留空不限制' : 'No limit'} onChange={event => setPlanningLimit(event.target.value)} />
      </label>
    </fieldset> : null}
    <label>{discover ? (zh ? '最多执行批次' : 'Maximum batches')
      : (zh ? '最多执行批次（同时限制 Manager 调用次数）' : 'Maximum batches (also limits Manager calls)')}
      <input type="number" min="1" value={maximum} disabled={disabled} placeholder={zh ? '留空不限制' : 'No limit'}
        onChange={event => setMaximum(event.target.value)} />
    </label>
    <p>{zh ? '上限均可留空。留空时不限制相应次数，仍记录用量，可随时停止后续迭代。'
      : 'Limits are optional. Blank fields leave the corresponding counts uncapped; usage is recorded and you can stop future iterations.'}</p>
    <label><input type="checkbox" checked={confirmedInput === inputIdentity}
      onChange={event => setConfirmedInput(event.target.checked ? inputIdentity : '')} disabled={disabled} />
      {discover ? (zh ? '我确认初始任务、项目目标、允许路径、所选能力与可选上限，并承担已配置模型的调用费用。'
        : 'I confirm the initial tasks, objective, allowed paths, selected capabilities, optional limits and configured model charges.')
        : (zh ? '我确认上述任务范围、可选上限和模型费用。' : 'I confirm this task scope, optional limits and model charges.')}</label>
    <button type="button" disabled={disabled || !canStart || !valid || confirmedInput !== inputIdentity} onClick={start}>
      {zh ? '授权并开始自动迭代' : 'Authorize and start continuation'}
    </button>
  </>;
}

export default function ProjectContinuationControls({ snapshot, session, projectKey, onAction, disabled, canStart, language = 'en' }) {
  const zh = String(language).startsWith('zh');
  const value = session?.project_continuation;
  const tasks = (snapshot?.candidates || []).filter(item => ['todo', 'blocked'].includes(item.status));
  const batches = Object.values(session?.project_task_batches || {}).filter(item =>
    ['review', 'blocked', 'abandoned'].includes(item.status) || item.quality?.required && item.status !== 'delivered');
  if (!onAction || (!value && !tasks.length && !batches.length)) return null;
  const active = value?.status === 'active';
  const activity = continuationActivity(session, zh);
  const canFinish = ['no_followup', 'exhausted'].includes(session?.project_continuation_notice?.phase)
    && !value?.pending_batch;
  const labels = zh ? { active: '自动推进中', stopped: '已停止', blocked: '需要处理阻塞', exhausted: '授权额度已用完', completed: '本次授权已结束' }
    : { active: 'Running automatically', stopped: 'Stopped', blocked: 'Needs attention', exhausted: 'Approved budget used', completed: 'This grant has ended' };
  const reasons = zh ? { discovery_no_followup: '本次未发现授权范围内的后续任务。', discovery_task_budget_used: '已用完授权的新任务额度。',
    approved_iteration_budget_used: '已达到授权批次或规划调用上限。', owner_stopped: '所有者已停止后续迭代。' }
    : { discovery_no_followup: 'No follow-up task was proposed within the approved scope.', discovery_task_budget_used: 'Approved new-task budget used.',
      approved_iteration_budget_used: 'Approved batch or planning-call limit reached.', owner_stopped: 'Future iterations were stopped by the owner.' };
  const statusLabel = value?.discovery && value.reason === 'discovery_no_followup'
    ? (zh ? '本次未发现后续任务' : 'No follow-up proposed') : activity?.label || labels[value?.status] || value?.status;

  return <section className="project-continuation-controls" tabIndex={-1}
    data-continuation-id={value?.id} data-notice-id={session?.project_continuation_notice?.id}
    data-needs-input={Boolean(session?.project_continuation_notice?.kind)}
    aria-label={zh ? '项目自动迭代' : 'Automatic project iteration'}>
    <details className="project-iteration-suggestion" open={Boolean(value)}>
      <summary>{zh ? '项目自动迭代' : 'Automatic project iteration'}{value ? ` · ${statusLabel}` : ''}</summary>
      {value ? <>
        <p>{zh ? '已预留批次' : 'Batch reservations'}: {value.batches_started} / {value.max_batches ?? (zh ? '未设上限' : 'No limit')} · {zh ? '已预留规划次数' : 'Planning reservations'}: {value.manager_calls} / {value.max_manager_calls ?? (zh ? '未设上限' : 'No limit')}</p>
        {value.discovery ? <>
          <p>{zh ? '项目目标' : 'Project objective'}: {value.discovery.objective}</p>
          <p>{zh ? '允许写入的路径' : 'Allowed write paths'}: {value.discovery.allowed_write_domains.join(', ')}</p>
          <p>{zh ? '已创建新任务' : 'New tasks created'}: {value.new_tasks_created ?? 0} / {value.discovery.max_new_tasks ?? (zh ? '未设上限' : 'No limit')}</p>
          <ul aria-label={zh ? '已授权的接续能力' : 'Approved continuation capabilities'}>
            {[
              ...(value.discovery.allow_team_roles === true
                ? [['allow_team_roles', zh ? '按需使用已启用的 Team Role' : 'Enabled Team Roles as needed']]
                : [['allow_investigation', zh ? '只读调查' : 'Read-only investigation'],
                  ['allow_planning', zh ? 'Planner 委派' : 'Planner delegation']]),
              ['allow_running_human_question', zh ? '运行中内部问题板提问' : 'Internal board questions while running'],
              ['allow_backlog_judgment', zh ? '依据当前授权任务事实判断' : 'Judgment from authorized backlog facts'],
              ['allow_undelivered_feedback', zh ? '未交付结果反馈' : 'Undelivered outcome feedback'],
              ['allow_candidate_handoff', zh ? '隔离候选交接' : 'Isolated candidate handoff'],
              ['allow_initial_readonly_feedback', zh ? '首次交付前只读接续' : 'Initial read-only continuation'],
            ].map(([flag, label]) => <li key={flag}>{label}: {value.discovery[flag] === true
              ? (zh ? '已授权' : 'Allowed') : (zh ? '未授权' : 'Not allowed')}</li>)}
          </ul>
          {value.seed_task_ids?.length ? <p>{zh ? '初始任务' : 'Initial tasks'}: {value.seed_task_ids.join(', ')}</p> : null}
          {value.discovered_task_ids?.length ? <p>{zh ? '已发现的任务' : 'Discovered tasks'}: {value.discovered_task_ids.join(', ')}</p> : null}
          {value.discovery_last ? <>
            {value.discovery_last.task_id ? <p>{zh ? '最近创建的任务' : 'Latest created task'}: {value.discovery_last.task_id}</p> : null}
            <p>{zh ? '来源结果凭据' : 'Source outcome receipt'}: <code>{value.discovery_last.outcome_receipt_id}</code></p>
            {value.discovery_last.rationale ? <p>{zh ? '发现依据' : 'Discovery rationale'}: {value.discovery_last.rationale}</p> : null}
          </> : null}
        </> : null}
        {activity ? <div role="status" aria-label={zh ? '接续实际状态' : 'Continuation activity'}>
          <p>{activity.detail}</p>
          {activity.rationale ? <p>{zh ? '上次 Manager 判断依据' : 'Last Manager rationale'}: {activity.rationale}</p> : null}
        </div> : null}
        {value.status !== 'active' && value.reason ? <p role="status">{reasons[value.reason] || value.reason}</p> : null}
        {active || canFinish ? <button type="button" onClick={() => onAction({
          action: 'stop_project_continuation', continuation_id: value.id,
        })}>{active ? (zh ? '停止后续迭代' : 'Stop future iterations')
          : (zh ? '保留结果并结束' : 'Keep results and finish')}</button> : null}
      </> : null}
      {active ? <ContinuationScopeAddition key={value.id} value={value} snapshot={snapshot}
        onAction={onAction} disabled={disabled} zh={zh} /> : null}
      {!active && tasks.length > 0 ? <ContinuationStartForm tasks={tasks} snapshot={snapshot} value={value}
        onAction={onAction} disabled={disabled} canStart={canStart} zh={zh} /> : null}
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
