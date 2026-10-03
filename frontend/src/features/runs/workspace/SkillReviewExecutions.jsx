import { useEffect, useRef, useState } from 'react';
import { requestJson } from '../../../lib/api/client';
import './skillReviewExecutions.css';

const words = {
  en: {
    title: 'Review execution details', refresh: 'Refresh reviews', loading: 'Loading review evidence…',
    empty: 'No locked Reviewer execution in this task.', unavailable: 'Review evidence is unavailable. No successful report is shown.',
    advisory: 'Advisory only. Receiving a report does not independently confirm its judgment or approve, merge, or deploy anything.',
    target: 'Review target', method: 'Locked method version', open: 'Open review report', close: 'Close review report',
    fixture: 'Contract fixture · not live Runtime acceptance', runtime: 'Runtime verification',
    native: 'Existing Codex · trusted self-use review',
    nativePermissions: 'Uses existing Codex permissions. This review grants no additional tools or actions.',
    nativeBudget: 'Authorized sessions: {count}. Maximum per session: {seconds}s. No verified cost ceiling.',
    start: 'Start review', starting: 'Reviewing…', startFailed: 'The review did not complete. Refresh to inspect the saved result.',
    received: 'Report received', prepared: 'Prepared', running: 'Running', stopping: 'Stopping · termination unconfirmed',
    failed: 'Failed', invalid: 'Invalid report', cancelled: 'Cancelled', interrupted: 'Interrupted',
    recovery_required: 'Saved outcome requires recovery', states: 'Observed delivery stages',
    unobserved: 'Not observed', observed: 'Observed by parent', findings: 'Findings', none: 'No findings reported',
    checks: 'Reviewer-reported checks', evidence: 'Source evidence', checked: 'Inspected by Reviewer',
    not_run: 'Not performed', inconclusive: 'Inconclusive', unverified: 'Not verified',
    cancel: 'Cancel review', cancelling: 'Requesting stop…', cancelFailed: 'Stop was not confirmed. Refresh the saved execution; do not assume it has stopped.',
    stopPending: 'Stop requested. Termination is not yet confirmed; no report or retry is available.',
    retry: 'Retry with locked inputs', retrying: 'Retrying…', retryFailed: 'Retry was not confirmed. Refresh to inspect the saved attempt.',
    files: 'Changed files · exact base and head', base: 'Base commit', head: 'Head commit',
    reads: 'Resource read witnesses', readBoundary: 'Exact command-output bytes matched the locked resource. This is not proof of comprehension or method adherence.',
    required: 'Required', on_demand: 'On demand', promoted: 'Explicitly required by this task',
  },
  zh: {
    title: '审查执行详情', refresh: '刷新审查', loading: '正在读取审查依据…',
    empty: '当前任务没有已绑定方法的 Reviewer 执行。', unavailable: '审查依据不可用，不显示成功报告。',
    advisory: '仅供参考。报告被接收不代表判断已获独立证实，也不会自动批准、合并或部署。',
    target: '审查对象', method: '锁定的方法版本', open: '打开审查报告', close: '关闭审查报告',
    fixture: '合同测试夹具 · 不是真实 Runtime 验收', runtime: 'Runtime 验证状态',
    native: '现有 Codex · 自用审查',
    nativePermissions: '沿用本机 Codex 权限。本次审查不授予额外工具或操作权限。',
    nativeBudget: '已授权会话 {count} 次，每次最多 {seconds} 秒；费用上限未确认。',
    start: '开始审查', starting: '正在审查…', startFailed: '审查未完成，请刷新查看已保存的结果。',
    received: '报告已接收', prepared: '已准备', running: '执行中', stopping: '正在停止 · 尚未确认终止',
    failed: '执行失败', invalid: '报告无效', cancelled: '已取消', interrupted: '已中断',
    recovery_required: '已有结果等待恢复', states: '可观察的交付阶段',
    unobserved: '未观察到', observed: '父进程已观察到', findings: '发现的问题', none: '未报告问题',
    checks: 'Reviewer 陈述的检查', evidence: '源码依据', checked: 'Reviewer 已检查',
    not_run: '未执行', inconclusive: '未得出结论', unverified: '未验证',
    cancel: '取消审查', cancelling: '正在请求停止…', cancelFailed: '尚未确认停止，请刷新已保存的执行；不能据此认为进程已终止。',
    stopPending: '已请求停止，尚未确认终止；目前不能查看成功报告或重试。',
    retry: '使用原输入重试', retrying: '正在重试…', retryFailed: '尚未确认重试结果，请刷新查看已保存的执行。',
    files: '变更文件 · 固定前后版本', base: '起始提交', head: '目标提交',
    reads: '资源读取依据', readBoundary: '命令返回的完整字节与锁定资源匹配。这不代表模型已理解或遵循该方法。',
    required: '必需', on_demand: '按需', promoted: '当前任务明确要求',
  },
};
const stageWords = {
  en: { materialized: 'Fixed resources materialized', catalog_presented: 'Catalog delivered', content_delivered: 'Complete input delivered', resource_read_observed: 'Runtime resource read', script_execution_observed: 'Script execution' },
  zh: { materialized: '固定资源已物化', catalog_presented: '目录已交付', content_delivered: '完整输入已交付', resource_read_observed: 'Runtime 资源读取', script_execution_observed: '脚本执行' },
};
const jobPattern = /^job-skill-[0-9a-f]{64}$/;
const endpoint = (project, run) => `/api/projects/${encodeURIComponent(project)}/runs/${encodeURIComponent(run)}/skill-reviews`;

function validReads(item) {
  if (item.resource_reads === undefined) return true;
  if (!Array.isArray(item.resource_reads) || item.resource_reads.length > 1024) return false;
  const ids = new Set();
  return item.resource_reads.every(read => {
    const method = (item.methods || []).find(method => method.skill_id === read?.skill_id);
    if (!read || read.schema_version !== 'goodteam.codex-resource-read.v1' || read.origin !== 'native_command_event'
      || !method || method.version_id !== read.version_id || method.package_digest !== read.package_digest
      || read.context_digest !== item.context_ref?.digest || read.runtime_session_id !== item.runtime?.session_id
      || read.runtime_verification !== item.runtime_verification || typeof read.item_id !== 'string' || !read.item_id
      || ids.has(read.item_id) || !Number.isSafeInteger(read.size) || read.size < 0
      || typeof read.path !== 'string' || read.path.split('/').some(part => ['', '.', '..'].includes(part))
      || ['resource_digest', 'transcript_digest', 'started_digest', 'completed_digest'].some(key => !/^sha256:[0-9a-f]{64}$/.test(read[key]))) return false;
    ids.add(read.item_id);
    return true;
  });
}

function matchesScope(payload, projectKey, runId) {
  return payload?.project_key === projectKey && payload?.run_id === runId;
}
function matchesReport(payload, item, projectKey, runId) {
  return matchesScope(payload, projectKey, runId) && payload.schema_version === 'goodteam.review-execution.v1'
    && payload.job_id === item.job_id && payload.status === 'received' && payload.report_available === true
    && !payload.cancel_requested && !item.cancel_requested
    && payload.snapshot_ref?.digest === item.snapshot_ref?.digest && payload.snapshot_ref?.id === item.snapshot_ref?.id
    && payload.context_ref?.digest === item.context_ref?.digest
    && payload.task_skill_context?.task?.logical_id === runId && payload.task_skill_context?.task?.project_key === projectKey
    && payload.target?.artifact_version === item.target?.artifact_version
    && payload.target?.kind === item.target?.kind && payload.target?.base_sha === item.target?.base_sha
    && payload.target?.head_sha === item.target?.head_sha && payload.report_digest === item.report_digest
    && payload.report?.schema_version === 'goodteam.code-review-report.v1'
    && payload.report.advisory === true && payload.report.judgment_verification === 'not_independently_verified'
    && typeof payload.report.summary === 'string' && Array.isArray(payload.report.findings)
    && Array.isArray(payload.report.checks) && Array.isArray(payload.report.evidence);
}

function ReviewReport({ item, projectKey, runId, w }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(null);
  const [error, setError] = useState(false);
  const controller = useRef(null);
  useEffect(() => () => controller.current?.abort(), []);
  const toggle = async () => {
    controller.current?.abort();
    setValue(null);
    setError(false);
    setOpen(!open);
    if (open) return;
    const request = new AbortController();
    controller.current = request;
    try {
      const result = await requestJson(`${endpoint(projectKey, runId)}/${item.job_id}/report`, { signal: request.signal, cache: 'no-store' });
      if (request.signal.aborted) return;
      if (!matchesReport(result, item, projectKey, runId)) throw new Error('review_scope_mismatch');
      setValue(result.report);
    } catch {
      if (!request.signal.aborted) setError(true);
    }
  };
  return (
    <div className="skill-review-report">
      <button type="button" aria-expanded={open} onClick={toggle}>{open ? w.close : w.open}</button>
      {open && !value && !error ? <p role="status">{w.loading}</p> : null}
      {open && error ? <p role="alert">{w.unavailable}</p> : null}
      {open && value ? <section aria-label={w.open}>
        <p>{value.summary}</p>
        <h4>{w.findings}</h4>
        {value.findings.length ? value.findings.map((finding) => <article key={finding.id}>
          <strong>{finding.severity}: {finding.title}</strong><p>{finding.detail}</p>
          <p>{finding.recommendation}</p><small>{(finding.evidence_ids || []).join(', ')}</small>
        </article>) : <p>{w.none}</p>}
        <h4>{w.checks}</h4>
        <ul>{value.checks.map((check, index) => <li key={`${check.name}:${index}`}>
          <strong>{check.name} · {w[check.status] || w.unverified}</strong><p>{check.detail}</p>
        </li>)}</ul>
        <h4>{w.evidence}</h4>
        {value.evidence.map((part) => <details key={part.id}>
          <summary>{part.id} · {part.path}:{part.line_start}–{part.line_end}</summary>
          <pre>{part.quote}</pre><p>{part.observation}</p><code>{part.source_digest}</code>
        </details>)}
      </section> : null}
    </div>
  );
}

function ReviewControl({ item, projectKey, runId, w, onRefresh, action }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const controller = useRef(null);
  useEffect(() => () => controller.current?.abort(), []);
  const submit = async () => {
    if (busy) return;
    const request = new AbortController();
    controller.current = request;
    setBusy(true); setError(false);
    try {
      const result = await requestJson(`${endpoint(projectKey, runId)}/${item.job_id}/${action}`, {
        method: 'POST', signal: request.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ snapshot_digest: item.snapshot_ref.digest, context_digest: item.context_ref.digest }),
      });
      if (request.signal.aborted) return;
      const exactCancel = result.job_id === item.job_id && result.snapshot_digest === item.snapshot_ref.digest
        && result.context_digest === item.context_ref.digest && result.cancel_requested === true
        && result.advisory === true && result.approval_changed === false
        && ((result.termination_confirmed === false && result.status === 'stopping')
          || (result.termination_confirmed === true && ['cancelled', 'failed', 'invalid', 'interrupted'].includes(result.status)));
      if (!matchesScope(result, projectKey, runId) || !jobPattern.test(result.job_id)
        || (action === 'cancel' ? !exactCancel : result.original_job_id !== item.job_id)) {
        throw new Error('review_scope_mismatch');
      }
      onRefresh();
    } catch {
      if (!request.signal.aborted) setError(true);
    } finally {
      if (!request.signal.aborted) setBusy(false);
    }
  };
  return <div><button type="button" onClick={submit} disabled={busy}>{busy ? (action === 'cancel' ? w.cancelling : w.retrying) : w[action]}</button>
    {error ? <p role="alert">{action === 'cancel' ? w.cancelFailed : w.retryFailed}</p> : null}</div>;
}

function ReviewPanel({ projectKey, runId, language }) {
  const locale = String(language || '').startsWith('zh') ? 'zh' : 'en';
  const w = words[locale];
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState(null);
  const [error, setError] = useState(false);
  const [startAvailable, setStartAvailable] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(false);
  const controller = useRef(null);
  const startController = useRef(null);
  useEffect(() => () => { controller.current?.abort(); startController.current?.abort(); }, []);
  const load = async () => {
    controller.current?.abort();
    setItems(null); // Also unmount/abort every previously displayed report.
    setStartAvailable(false);
    setError(false);
    const request = new AbortController();
    controller.current = request;
    try {
      const payload = await requestJson(endpoint(projectKey, runId), { signal: request.signal, cache: 'no-store' });
      if (request.signal.aborted) return;
      if (!matchesScope(payload, projectKey, runId) || payload.schema_version !== 'goodteam.task-reviews.v1'
        || !Array.isArray(payload.reviews) || payload.reviews.some((item) => !jobPattern.test(item.job_id)
          || item.task_skill_context?.task?.logical_id !== runId || item.task_skill_context?.task?.project_key !== projectKey
          || !validReads(item))) {
        throw new Error('review_scope_mismatch');
      }
      setItems(payload.reviews);
      setStartAvailable(payload.start_available === true);
    } catch {
      if (!request.signal.aborted) setError(true);
    }
  };
  const start = async () => {
    if (starting || !startAvailable) return;
    const request = new AbortController();
    startController.current = request;
    setStarting(true); setStartError(false);
    try {
      const result = await requestJson('/api/reviewer-jobs/run', {
        method: 'POST', signal: request.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_key: projectKey, run_id: runId }),
      });
      if (request.signal.aborted) return;
      if (!matchesScope(result, projectKey, runId) || result.ran_count !== 1
        || result.results?.[0]?.status !== 'review_received') setStartError(true);
      await load();
    } catch {
      if (!request.signal.aborted) setStartError(true);
    } finally {
      if (!request.signal.aborted) setStarting(false);
    }
  };
  const toggle = () => {
    if (open) {
      controller.current?.abort();
      setItems(null);
    } else load();
    setOpen(!open);
  };
  return <div className="skill-review-executions">
    <button type="button" aria-expanded={open} onClick={toggle}>{w.title}</button>
    {open ? <section aria-label={w.title}>
      <p className="skill-review-advisory">{w.advisory}</p>
      <button type="button" onClick={load}>{w.refresh}</button>
      {startAvailable || starting ? <button type="button" onClick={start} disabled={starting}>{starting ? w.starting : w.start}</button> : null}
      {startError ? <p role="alert">{w.startFailed}</p> : null}
      {error ? <p role="alert">{w.unavailable}</p> : items === null ? <p role="status">{w.loading}</p> : null}
      {items?.length === 0 ? <p>{w.empty}</p> : null}
      {items?.map((item) => <article key={`${item.job_id}:${item.report_digest || item.status}`} aria-label={item.job_id}>
        <h3>{w[item.status] || w.unavailable}</h3>
        {item.error_code ? <p role="status"><code>{item.error_code}</code></p> : null}
        <dl><dt>{w.target}</dt><dd>{item.target?.display_name || item.target?.artifact_key}<code>{item.target?.artifact_version}</code></dd>
          <dt>{w.runtime}</dt><dd>{item.runtime_verification === 'contract_fixture' ? w.fixture : item.runtime_verification === 'local_cli' ? w.native : item.runtime_verification || w.unverified}</dd>
        </dl>
        {item.runtime_boundaries?.mode === 'trusted_self_use' ? <div>
          <p>{w.nativePermissions}</p>
          <p>{w.nativeBudget.replace('{count}', item.runtime_boundaries.context_limit).replace('{seconds}', item.runtime_boundaries.deadline_seconds)}</p>
        </div> : null}
        {item.target?.kind === 'git_change' ? <details><summary>{w.files}</summary>
          <dl><dt>{w.base}</dt><dd><code>{item.target.base_sha}</code></dd>
            <dt>{w.head}</dt><dd><code>{item.target.head_sha}</code></dd></dl>
          {(item.target.files || []).map((file) => <div key={file.path}><strong>{file.change} · {file.path}</strong>
            {['base', 'head'].map((side) => file[side] ? <p key={side}>{side}: <code>{file[side].blob_id}</code></p> : null)}
          </div>)}
        </details> : null}
        <details><summary>{w.method}</summary>
          {(item.methods || []).map((method) => <div key={method.skill_id}>
            <code>{method.skill_id}</code><code>{method.version_id}</code><code>{method.package_digest}</code>
            <p>{w[method.activation] || w.unverified}{(item.required_skill_ids || []).includes(method.skill_id) ? ` · ${w.promoted}` : ''}</p>
          </div>)}
          <code>{item.snapshot_ref?.digest}</code>
        </details>
        <details><summary>{w.states}</summary><dl>{Object.entries(item.stages || {}).map(([stage, state]) => <div key={stage}>
          <dt>{stageWords[locale][stage] || stage}</dt><dd>{w[state] || w.unobserved}</dd>
        </div>)}</dl></details>
        {item.status === 'received' && item.report_available && !item.cancel_requested && item.resource_reads?.length ?
          <details><summary>{w.reads}</summary><p>{w.readBoundary}</p>
            {item.resource_reads.map(read => <div key={read.item_id}>
              <strong>{read.path}</strong><code>{read.version_id}</code>
              <code>{read.resource_digest}</code><small>{read.item_id} · {read.size} bytes</small>
              <code>{read.transcript_digest}</code>
            </div>)}
          </details> : null}
        {item.cancel_requested === true && (['prepared', 'running', 'stopping'].includes(item.status) || item.control_status === 'stopping')
          ? <p role="status">{w.stopPending}</p> : null}
        {item.cancel_available === true && !item.cancel_requested && !item.report_available
          && (['prepared', 'running', 'stopping'].includes(item.status)
            || (item.status === 'unavailable' && ['prepared', 'running', 'stopping'].includes(item.control_status)))
          && item.snapshot_ref?.digest && item.context_ref?.digest ? <ReviewControl action="cancel"
          item={item} projectKey={projectKey} runId={runId} w={w} onRefresh={load} /> : null}
        {item.retry_available === true && ['failed', 'invalid', 'cancelled', 'interrupted'].includes(item.status) ? <ReviewControl action="retry"
          item={item} projectKey={projectKey} runId={runId} w={w} onRefresh={load} /> : null}
        {item.report_available === true && item.status === 'received' && !item.cancel_requested ? <ReviewReport
          item={item} projectKey={projectKey} runId={runId} w={w}
        /> : null}
      </article>)}
    </section> : null}
  </div>;
}

export default function SkillReviewExecutions({ projectKey, runId, language }) {
  if (!projectKey || !runId) return null;
  return <ReviewPanel key={`${projectKey}:${runId}`} projectKey={projectKey} runId={runId} language={language} />;
}
