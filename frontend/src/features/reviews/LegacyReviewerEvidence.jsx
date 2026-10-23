import { useEffect, useRef, useState } from 'react';
import MarkdownView from '../../components/ui/MarkdownView';
import ProductError from '../../components/ui/ProductError';
import { requestJson } from '../../lib/api/client';
import { deliveryLinkResolver } from '../runs/workspace/deliveryLinks';

const sameTarget = (value, item) => value?.project_key === item.project_key && value?.run_id === item.run_id
  && value?.approval === item.approval && value?.artifact_key === item.artifact_key
  && value?.artifact_version === item.artifact_version;

function readableReport(item) {
  try {
    const url = new URL(item.href, 'https://goodteam.invalid');
    const expected = { project: item.project_key, run: item.run_id, job: item.job_id, profile: item.profile_id,
      approval: item.approval, artifact: item.artifact_key, artifact_version: item.artifact_version, version: item.report_version };
    return item.status === 'completed' && item.report_version && item.href.startsWith('/api/reviewer-report?')
      && url.origin === 'https://goodteam.invalid' && !url.hash && [...url.searchParams.keys()].length === 8
      && Object.entries(expected).every(([key, value]) => url.searchParams.getAll(key).length === 1 && url.searchParams.get(key) === value);
  } catch { return false; }
}

function ReviewerJob({ item, activeApproval, onRefresh, deliverySummary, t }) {
  const [busy, setBusy] = useState('');
  const [started, setStarted] = useState(false);
  const [content, setContent] = useState(null);
  const [error, setError] = useState(null);
  const request = useRef(null);
  useEffect(() => () => request.current?.abort(), []);
  const open = async () => {
    if (request.current || !readableReport(item)) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy('read'); setError(null); setContent(null);
    try {
      const payload = await requestJson(item.href, { signal: controller.signal, cache: 'no-store' });
      if (controller.signal.aborted) return;
      if (!sameTarget(payload, item) || payload.job_id !== item.job_id || payload.profile_id !== item.profile_id
        || payload.status !== 'completed' || payload.advisory_only !== true || payload.report_version !== item.report_version
        || typeof payload.content !== 'string' || !payload.content.trim()) throw new Error(t('review.legacy.mismatch'));
      setContent(payload.content);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause);
    } finally {
      if (!controller.signal.aborted) { request.current = null; setBusy(''); }
    }
  };
  const start = async () => {
    if (request.current || started || activeApproval !== item.approval || item.status !== 'queued') return;
    const controller = new AbortController();
    request.current = controller;
    setBusy('start'); setError(null);
    // A network failure may leave the server executing. Refresh saved state
    // before allowing another start for this same projected queued job.
    setStarted(true);
    try {
      const payload = await requestJson('/api/reviewer-jobs/run', {
        method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_key: item.project_key, run_id: item.run_id, approval: item.approval,
          artifact_key: item.artifact_key, artifact_version: item.artifact_version, profile_id: item.profile_id, job_id: item.job_id }),
      });
      if (controller.signal.aborted) return;
      const result = payload.results?.[0];
      if (!sameTarget(payload, item) || payload.advisory_only !== true || payload.approval_unchanged !== true
        || payload.ran_count !== 1 || payload.results?.length !== 1 || payload.errors?.length
        || result?.job_id !== item.job_id || result?.profile_id !== item.profile_id
        || result?.approval !== item.approval || result?.artifact_key !== item.artifact_key
        || result?.artifact_version !== item.artifact_version) throw new Error(t('review.legacy.startUnconfirmed'));
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause);
    } finally {
      if (!controller.signal.aborted) { request.current = null; setBusy(''); onRefresh?.(); }
    }
  };
  return <article className="legacy-reviewer-job" aria-busy={Boolean(busy)}>
    <p><strong>{item.profile_id}</strong> · {item.status}</p>
    <code className="delivery-version">{item.job_id}</code>
    <p>{item.artifact_key}</p><code className="delivery-version">{item.artifact_version}</code>
    {activeApproval === item.approval && item.status === 'queued' ? <button type="button" disabled={Boolean(busy) || started} onClick={start}>
      {busy === 'start' ? t('review.legacy.starting') : t('review.legacy.start')}
    </button> : null}
    {readableReport(item) ? <button type="button" disabled={Boolean(busy)} onClick={open}>{t('review.legacy.open')}</button> : null}
    {item.status === 'completed' && !readableReport(item) ? <p>{t('review.legacy.unavailable')}</p> : null}
    {started && !busy ? <p role="status">{t('review.legacy.refreshHint')}</p> : null}
    {busy === 'read' ? <p role="status">{t('work.deliverySummary.loading')}</p> : null}
    {error ? <ProductError error={error} t={t} /> : null}
    {content !== null ? <div className="delivery-report-content" tabIndex={0}>
      <button type="button" onClick={() => setContent(null)}>{t('work.plan.close')}</button>
      <MarkdownView content={content} resolveLink={deliveryLinkResolver(deliverySummary)} />
    </div> : null}
  </article>;
}

export default function LegacyReviewerEvidence({ projectKey, runId, items, activeApproval = '', onRefresh, deliverySummary, t }) {
  const bound = (items || []).filter(item => item.project_key === projectKey && item.run_id === runId
    && item.advisory_only === true && item.profile_id && item.artifact_key && item.artifact_version
    && /^job-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(item.job_id));
  if (!bound.length) return null;
  return <section className="legacy-reviewer-evidence" aria-label={t('review.legacy.title')}>
    <h3>{t('review.legacy.title')}</h3><p>{t('review.legacy.advisory')}</p>
    {onRefresh ? <button type="button" onClick={onRefresh}>{t('product.actions.reload')}</button> : null}
    {bound.map(item => <ReviewerJob key={JSON.stringify([projectKey, runId, item.job_id, item.profile_id,
      item.approval, item.artifact_key, item.artifact_version, item.report_version, item.status])}
      item={item} activeApproval={activeApproval} onRefresh={onRefresh} deliverySummary={deliverySummary} t={t} />)}
  </section>;
}
