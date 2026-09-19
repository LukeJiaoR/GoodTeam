import { useEffect, useRef, useState } from 'react';
import { FileText, Loader2, X } from 'lucide-react';
import { requestJson } from '../../../lib/api/client';
import MarkdownView from '../../../components/ui/MarkdownView';
import ProductError from '../../../components/ui/ProductError';
import StatusBadge from '../../../components/ui/StatusBadge';
import { documentTitle } from '../../../lib/presentation/filePresentation';
import './deliverySummary.css';

function isBoundReportUrl(artifact, summary) {
  if (!artifact.href || !summary.project_key || !summary.run_id || !artifact.version) return false;
  try {
    const url = new URL(artifact.href, 'https://goodteam.invalid');
    const expected = { project: summary.project_key, run: summary.run_id, key: artifact.key, version: artifact.version };
    return artifact.href.startsWith('/api/delivery-artifact?')
      && url.origin === 'https://goodteam.invalid' && !url.hash
      && Object.entries(expected).every(([key, value]) => (
        url.searchParams.getAll(key).length === 1 && url.searchParams.get(key) === value
      ));
  } catch { return false; }
}

function ArtifactReport({ artifact, summary, t }) {
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const request = useRef(null);
  useEffect(() => () => request.current?.abort(), []);
  const readable = isBoundReportUrl(artifact, summary);
  const close = () => {
    request.current?.abort();
    setResult(null);
    setBusy(false);
  };
  const open = async () => {
    if (!readable || busy) return;
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
        || typeof payload.content !== 'string' || !payload.content.trim()) {
        throw new Error(t('work.deliverySummary.readMismatch'));
      }
      setResult(payload);
    } catch (error) {
      if (!controller.signal.aborted) setResult({ error });
    } finally {
      if (!controller.signal.aborted && request.current === controller) setBusy(false);
    }
  };
  return (
    <li className="delivery-report" aria-busy={busy}>
      <div className="delivery-report-heading">
        <button type="button" disabled={!readable || busy} onClick={open}>
          {busy ? <Loader2 size={15} aria-hidden="true" /> : <FileText size={15} aria-hidden="true" />}
          {documentTitle(artifact.key, t)}
        </button>
        {!readable ? <span>{t('work.deliverySummary.unavailable')}</span> : null}
        {result || busy ? <button type="button" aria-label={t('work.plan.close')} onClick={close}><X size={15} aria-hidden="true" /></button> : null}
      </div>
      {artifact.version ? <code className="delivery-version">{artifact.version}</code> : null}
      {busy ? <p role="status">{t('work.deliverySummary.loading')}</p> : null}
      {result?.error ? <ProductError error={result.error} t={t} /> : null}
      {result?.content ? <div className="delivery-report-content" tabIndex={0}><MarkdownView content={result.content} /></div> : null}
    </li>
  );
}

export default function DeliverySummary({ summary, t }) {
  if (!summary) return null;
  const failed = summary.status === 'blocked';
  const artifacts = Array.isArray(summary.artifacts) ? summary.artifacts : [];
  const attempts = Array.isArray(summary.attempts) ? summary.attempts : [];
  return (
    <section className="delivery-summary" aria-label={t('work.deliverySummary.title')}>
      <header><h3>{t('work.deliverySummary.title')}</h3><StatusBadge tone={failed ? 'danger' : 'neutral'}>{t(`work.deliverySummary.status.${summary.status}`)}</StatusBadge></header>
      <p>{t('work.deliverySummary.unverified')}</p>
      {summary.simulated ? <p role="status">{t('work.deliverySummary.simulated')}</p> : null}
      <p>{t(`work.deliverySummary.next.${summary.status}`)}</p>
      {artifacts.length ? <ul className="delivery-report-list">{artifacts.map((artifact) => (
        <ArtifactReport key={JSON.stringify([summary.project_key, summary.run_id, artifact.key, artifact.version, artifact.href])}
          artifact={artifact} summary={summary} t={t} />
      ))}</ul> : <p>{t('work.deliverySummary.noArtifacts')}</p>}
      <details><summary>{t('work.deliverySummary.attempts')}</summary><ul>{attempts.map((attempt) => <li key={attempt.id}><span>{t(`work.plan.role.${attempt.role}`)} · {attempt.adapter} · {attempt.status}</span><code className="delivery-version">{attempt.id}</code></li>)}</ul></details>
    </section>
  );
}
