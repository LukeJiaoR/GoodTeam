import { useState } from 'react';
import { FileText, Loader2, X } from 'lucide-react';
import { requestJson } from '../../../lib/api/client';
import MarkdownView from '../../../components/ui/MarkdownView';
import ProductError from '../../../components/ui/ProductError';
import StatusBadge from '../../../components/ui/StatusBadge';
import { documentTitle } from '../../../lib/presentation/filePresentation';
import './deliverySummary.css';

function ArtifactReport({ artifact, t }) {
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const open = async () => {
    setBusy(true);
    try { setResult(await requestJson(artifact.href)); }
    catch (error) { setResult({ error }); }
    finally { setBusy(false); }
  };
  return (
    <li className="delivery-report">
      <div className="delivery-report-heading">
        <button type="button" disabled={!artifact.href || busy} onClick={open}>
          {busy ? <Loader2 size={15} aria-hidden="true" /> : <FileText size={15} aria-hidden="true" />}
          {documentTitle(artifact.key, t)}
        </button>
        {!artifact.href ? <span>{t('work.deliverySummary.unavailable')}</span> : null}
        {result ? <button type="button" aria-label={t('work.plan.close')} onClick={() => setResult(null)}><X size={15} /></button> : null}
      </div>
      {artifact.version ? <code className="delivery-version">{artifact.version}</code> : null}
      {result?.error ? <ProductError error={result.error} t={t} /> : null}
      {result?.content ? <div className="delivery-report-content" tabIndex={0}><MarkdownView content={result.content} /></div> : null}
    </li>
  );
}

export default function DeliverySummary({ summary, t }) {
  if (!summary) return null;
  const failed = summary.status === 'blocked';
  return (
    <section className="delivery-summary" aria-label={t('work.deliverySummary.title')}>
      <header><h3>{t('work.deliverySummary.title')}</h3><StatusBadge tone={failed ? 'danger' : 'neutral'}>{t(`work.deliverySummary.status.${summary.status}`)}</StatusBadge></header>
      <p>{t('work.deliverySummary.unverified')}</p>
      {summary.simulated ? <p role="status">{t('work.deliverySummary.simulated')}</p> : null}
      <p>{t(`work.deliverySummary.next.${summary.status}`)}</p>
      {summary.artifacts.length ? <ul className="delivery-report-list">{summary.artifacts.map((artifact) => <ArtifactReport key={`${summary.run_id}:${artifact.key}:${artifact.version}`} artifact={artifact} t={t} />)}</ul> : <p>{t('work.deliverySummary.noArtifacts')}</p>}
      <details><summary>{t('work.deliverySummary.attempts')}</summary><ul>{summary.attempts.map((attempt) => <li key={attempt.id}><span>{t(`work.plan.role.${attempt.role}`)} · {attempt.adapter} · {attempt.status}</span><code className="delivery-version">{attempt.id}</code></li>)}</ul></details>
    </section>
  );
}
