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
    received: 'Report received', prepared: 'Prepared', running: 'Running', stopping: 'Stopping · termination unconfirmed',
    failed: 'Failed', invalid: 'Invalid report', cancelled: 'Cancelled', interrupted: 'Interrupted',
    recovery_required: 'Saved outcome requires recovery', states: 'Observed delivery stages',
    unobserved: 'Not observed', observed: 'Observed by parent', findings: 'Findings', none: 'No findings reported',
    checks: 'Reviewer-reported checks', evidence: 'Source evidence', checked: 'Inspected by Reviewer',
    not_run: 'Not performed', inconclusive: 'Inconclusive', unverified: 'Not verified',
    required: 'Required', on_demand: 'On demand', promoted: 'Explicitly required by this task',
  },
  zh: {
    title: '审查执行详情', refresh: '刷新审查', loading: '正在读取审查依据…',
    empty: '当前任务没有已绑定方法的 Reviewer 执行。', unavailable: '审查依据不可用，不显示成功报告。',
    advisory: '仅供参考。报告被接收不代表判断已获独立证实，也不会自动批准、合并或部署。',
    target: '审查对象', method: '锁定的方法版本', open: '打开审查报告', close: '关闭审查报告',
    fixture: '合同测试夹具 · 不是真实 Runtime 验收', runtime: 'Runtime 验证状态',
    received: '报告已接收', prepared: '已准备', running: '执行中', stopping: '正在停止 · 尚未确认终止',
    failed: '执行失败', invalid: '报告无效', cancelled: '已取消', interrupted: '已中断',
    recovery_required: '已有结果等待恢复', states: '可观察的交付阶段',
    unobserved: '未观察到', observed: '父进程已观察到', findings: '发现的问题', none: '未报告问题',
    checks: 'Reviewer 陈述的检查', evidence: '源码依据', checked: 'Reviewer 已检查',
    not_run: '未执行', inconclusive: '未得出结论', unverified: '未验证',
    required: '必需', on_demand: '按需', promoted: '当前任务明确要求',
  },
};
const stageWords = {
  en: { materialized: 'Fixed resources materialized', catalog_presented: 'Catalog delivered', content_delivered: 'Complete input delivered', resource_read_observed: 'Runtime resource read', script_execution_observed: 'Script execution' },
  zh: { materialized: '固定资源已物化', catalog_presented: '目录已交付', content_delivered: '完整输入已交付', resource_read_observed: 'Runtime 资源读取', script_execution_observed: '脚本执行' },
};
const jobPattern = /^job-skill-[0-9a-f]{64}$/;
const endpoint = (project, run) => `/api/projects/${encodeURIComponent(project)}/runs/${encodeURIComponent(run)}/skill-reviews`;

function matchesScope(payload, projectKey, runId) {
  return payload?.project_key === projectKey && payload?.run_id === runId;
}
function matchesReport(payload, item, projectKey, runId) {
  return matchesScope(payload, projectKey, runId) && payload.schema_version === 'goodteam.review-execution.v1'
    && payload.job_id === item.job_id && payload.status === 'received' && payload.report_available === true
    && payload.snapshot_ref?.digest === item.snapshot_ref?.digest && payload.snapshot_ref?.id === item.snapshot_ref?.id
    && payload.context_ref?.digest === item.context_ref?.digest
    && payload.task_skill_context?.task?.logical_id === runId && payload.task_skill_context?.task?.project_key === projectKey
    && payload.target?.artifact_version === item.target?.artifact_version && payload.report_digest === item.report_digest
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

function ReviewPanel({ projectKey, runId, language }) {
  const locale = String(language || '').startsWith('zh') ? 'zh' : 'en';
  const w = words[locale];
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState(null);
  const [error, setError] = useState(false);
  const controller = useRef(null);
  useEffect(() => () => controller.current?.abort(), []);
  const load = async () => {
    controller.current?.abort();
    setItems(null); // Also unmount/abort every previously displayed report.
    setError(false);
    const request = new AbortController();
    controller.current = request;
    try {
      const payload = await requestJson(endpoint(projectKey, runId), { signal: request.signal, cache: 'no-store' });
      if (request.signal.aborted) return;
      if (!matchesScope(payload, projectKey, runId) || payload.schema_version !== 'goodteam.task-reviews.v1'
        || !Array.isArray(payload.reviews) || payload.reviews.some((item) => !jobPattern.test(item.job_id)
          || item.task_skill_context?.task?.logical_id !== runId || item.task_skill_context?.task?.project_key !== projectKey)) {
        throw new Error('review_scope_mismatch');
      }
      setItems(payload.reviews);
    } catch {
      if (!request.signal.aborted) setError(true);
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
      {error ? <p role="alert">{w.unavailable}</p> : items === null ? <p role="status">{w.loading}</p> : null}
      {items?.length === 0 ? <p>{w.empty}</p> : null}
      {items?.map((item) => <article key={`${item.job_id}:${item.report_digest || item.status}`} aria-label={item.job_id}>
        <h3>{w[item.status] || w.unavailable}</h3>
        {item.error_code ? <p role="status"><code>{item.error_code}</code></p> : null}
        <dl><dt>{w.target}</dt><dd>{item.target?.display_name || item.target?.artifact_key}<code>{item.target?.artifact_version}</code></dd>
          <dt>{w.runtime}</dt><dd>{item.runtime_verification === 'contract_fixture' ? w.fixture : item.runtime_verification || w.unverified}</dd>
        </dl>
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
        {item.report_available === true && item.status === 'received' ? <ReviewReport
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
