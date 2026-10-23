import { useEffect, useRef, useState } from 'react';
import { FileText, Loader2, X } from 'lucide-react';
import { requestJson } from '../../../lib/api/client';
import MarkdownView from '../../../components/ui/MarkdownView';
import ProductError from '../../../components/ui/ProductError';
import StatusBadge from '../../../components/ui/StatusBadge';
import { documentTitle } from '../../../lib/presentation/filePresentation';
import SkillReviewExecutions from './SkillReviewExecutions';
import LegacyReviewerEvidence from '../../reviews/LegacyReviewerEvidence';
import { deliveryLinkResolver, isBoundReportUrl } from './deliveryLinks';
import './deliverySummary.css';

function ArtifactReport({ artifact, summary, t }) {
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const request = useRef(null);
  const pending = useRef(false);
  useEffect(() => () => request.current?.abort(), []);
  const readable = isBoundReportUrl(artifact, summary);
  const close = () => {
    request.current?.abort();
    pending.current = false;
    setResult(null);
    setBusy(false);
  };
  const open = async () => {
    if (!readable || pending.current) return;
    pending.current = true;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setResult(null);
    setBusy(true);
    try {
      const payload = await requestJson(artifact.href, { signal: controller.signal, cache: 'no-store' });
      if (controller.signal.aborted) return;
      if (payload.project_key !== summary.project_key || payload.run_id !== summary.run_id
        || payload.artifact !== artifact.key || payload.version !== artifact.version
        || (artifact.kind && (payload.kind !== artifact.kind || payload.baseline_commit !== artifact.baseline_commit
          || payload.result_commit !== artifact.result_commit))
        || typeof payload.content !== 'string' || (!artifact.kind && !payload.content.trim())) {
        throw new Error(t('work.deliverySummary.readMismatch'));
      }
      setResult(payload);
    } catch (error) {
      if (!controller.signal.aborted) setResult({ error });
    } finally {
      if (!controller.signal.aborted && request.current === controller) { setBusy(false); pending.current = false; }
    }
  };
  return (
    <li className="delivery-report" aria-busy={busy}>
      <div className="delivery-report-heading">
        <button type="button" disabled={!readable || busy} onClick={open}>
          {busy ? <Loader2 size={15} aria-hidden="true" /> : <FileText size={15} aria-hidden="true" />}
          {artifact.kind ? artifact.label : documentTitle(artifact.key, t)}
        </button>
        {!readable ? <span>{t('work.deliverySummary.unavailable')}</span> : null}
        {result || busy ? <button type="button" aria-label={t('work.plan.close')} onClick={close}><X size={15} aria-hidden="true" /></button> : null}
      </div>
      {artifact.version ? <details className="delivery-evidence"><summary>{t('work.deliverySummary.version')}</summary><code className="delivery-version">{artifact.version}</code></details> : null}
      {artifact.kind ? <p><span>{t('work.deliverySummary.base')}</span><code className="delivery-version">{artifact.baseline_commit}</code>
        <span>{t('work.deliverySummary.result')}</span><code className="delivery-version">{artifact.result_commit}</code></p> : null}
      {artifact.kind === 'qa' ? <p>{t('work.deliverySummary.qaOutcome')}: <strong>{artifact.qa_status || 'unknown'}</strong>
        {' · '}{t('work.deliverySummary.process')}: {artifact.process_status || 'unknown'}<code className="delivery-version">{artifact.job_id}</code></p> : null}
      {busy ? <p role="status">{t('work.deliverySummary.loading')}</p> : null}
      {result?.error ? <ProductError error={result.error} t={t} /> : null}
      {typeof result?.content === 'string' ? <div className="delivery-report-content" tabIndex={0}>
        {artifact.format === 'text' ? <pre>{result.content}</pre>
          : <MarkdownView content={result.content} resolveLink={deliveryLinkResolver(summary)} />}
      </div> : null}
    </li>
  );
}

export function DeliveryArtifacts({ summary, t }) {
  const artifacts = Array.isArray(summary?.artifacts) ? summary.artifacts : [];
  return artifacts.length ? <ul className="delivery-report-list">{artifacts.map((artifact) => (
    <ArtifactReport key={JSON.stringify([summary.project_key, summary.run_id, artifact.key, artifact.version, artifact.href])}
      artifact={artifact} summary={summary} t={t} />
  ))}</ul> : <p>{t('work.deliverySummary.noArtifacts')}</p>;
}

export default function DeliverySummary({ summary, historical = false, language, t }) {
  if (!summary) return null;
  const failed = summary.status === 'blocked';
  const artifacts = Array.isArray(summary.artifacts) ? summary.artifacts : [];
  const attempts = Array.isArray(summary.attempts) ? summary.attempts : [];
  return (
    <section className="delivery-summary" aria-label={t('work.deliverySummary.title')}>
      <p className="delivery-scope">{t(historical ? 'work.deliverySummary.historical' : 'work.deliverySummary.current')}</p>
      <header><h3>{t('work.deliverySummary.title')}</h3><StatusBadge tone={failed ? 'danger' : 'neutral'}>{t(`work.deliverySummary.status.${summary.status}`)}</StatusBadge></header>
      <h4>{t('work.deliverySummary.verification')}</h4>
      <p>{t('work.deliverySummary.unverified')}</p>
      <SkillReviewExecutions mode="delivery" language={language}
        key={JSON.stringify([summary.project_key, summary.run_id, artifacts.map(item => [item.key, item.version])])}
        projectKey={summary.project_key} runId={summary.run_id} />
      <LegacyReviewerEvidence projectKey={summary.project_key} runId={summary.run_id} t={t}
        items={summary.reviewer_reports || []} deliverySummary={summary}
        activeApproval={historical ? '' : summary.next_node?.match(/^await_(.+)_approval$/)?.[1] || ''} />
      {summary.simulated ? <p role="status">{t('work.deliverySummary.simulated')}</p> : null}
      <h4>{t('work.deliverySummary.nextStep')}</h4>
      <p>{t(`work.deliverySummary.next.${summary.status}`)}</p>
      <h4>{t('work.deliverySummary.reports')}</h4>
      <DeliveryArtifacts summary={summary} t={t} />
      <details className="delivery-evidence"><summary>{t('work.deliverySummary.attempts')}</summary><code className="delivery-version">{summary.run_id}</code><ul>{attempts.map((attempt) => <li key={attempt.id}><span>{t(`work.plan.role.${attempt.role}`)} · {attempt.adapter} · {attempt.status}</span><code className="delivery-version">{attempt.id}</code></li>)}</ul></details>
    </section>
  );
}
