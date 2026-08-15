import { useEffect, useState } from 'react';
import { Link, useOutletContext } from 'react-router-dom';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import StatusBadge from '../../components/ui/StatusBadge';
import { getProjectProjections } from '../../lib/api/globalApi';
import { routes } from '../../lib/routing/routes';

function buildItems(projections, t) {
  return projections.flatMap(({ item, state, error }) => {
    const key = item?.project?.project_key || state?.start_context?.project_key;
    if (!key) return [];
    const result = [];
    if (error) result.push({ id: `${key}-error`, key, tone: 'danger', title: t('inbox.items.loadFailed'), detail: error.message, href: routes.projectHome(key) });
    if (item?.readiness?.lifecycle === 'attention_required' || Number(item?.attention_count) > 0) {
      result.push({ id: `${key}-readiness`, key, tone: 'warning', title: t('inbox.items.readiness'), detail: t('inbox.items.readinessDetail'), href: routes.projectHome(key) });
    }
    (state?.approval_reviews || []).filter((review) => review.is_actionable).forEach((review) => result.push({
      id: `${key}-review-${review.approval}`, key, tone: 'warning', title: t('inbox.items.review', { review: review.approval.toUpperCase() }), detail: review.recommendation?.reasons?.[0]?.text || t('inbox.items.reviewDetail'), href: routes.projectReviewItem(key, review.approval),
    }));
    (state?.approval_reviews || []).forEach((review) => {
      const reasons = review.recommendation?.reasons || [];
      const policyState = review.review_evidence_policy?.state || '';
      const missing = reasons.filter((reason) => reason.kind === 'missing_evidence');
      if (missing.length || ['missing_evidence', 'failed_invocation'].includes(policyState)) result.push({
        id: `${key}-evidence-${review.approval}`, key, tone: 'warning', title: t('inbox.items.evidence', { review: review.approval.toUpperCase() }), detail: missing[0]?.text || t('inbox.items.evidenceDetail'), href: routes.projectReviewItem(key, review.approval),
      });
    });
    const blocked = (state?.worker_jobs || []).filter((job) => ['blocked', 'failed'].includes(job.status));
    if (blocked.length) result.push({ id: `${key}-workers`, key, tone: 'danger', title: t('inbox.items.workers', { count: blocked.length }), detail: t('inbox.items.workersDetail'), href: `${routes.projectWork(key)}?view=execution` });
    const preflight = state?.start_context?.project_report?.worker_backends_preflight || {};
    const unavailable = Object.values(preflight).filter((entry) => entry && entry.ready === false);
    if (unavailable.length) result.push({ id: `${key}-worker-config`, key, tone: 'danger', title: t('inbox.items.workerConfig'), detail: unavailable[0]?.hint || t('inbox.items.workerConfigDetail'), href: `${routes.projectSettings(key)}?section=workers` });
    return result;
  });
}

export default function InboxPage() {
  const { language, t } = useOutletContext();
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState('loading');
  const [error, setError] = useState(null);
  const [partialCount, setPartialCount] = useState(0);
  const [updatedAt, setUpdatedAt] = useState(null);
  const load = ({ quiet = false } = {}) => {
    const controller = new AbortController();
    if (!quiet || !items.length) setStatus('loading');
    getProjectProjections({ signal: controller.signal }).then((payload) => { setItems(buildItems(payload, t)); setPartialCount(payload.filter((projection) => projection.error).length); setStatus('ready'); setError(null); setUpdatedAt(new Date()); }).catch((caught) => { if (caught.name !== 'AbortError') { setError(caught); setStatus('error'); } });
    return controller;
  };
  useEffect(() => {
    let controller;
    queueMicrotask(() => { controller = load(); });
    return () => controller?.abort();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const timer = globalThis.setInterval(() => {
      if (!globalThis.document?.hidden) void load({ quiet: true });
    }, 30_000);
    return () => globalThis.clearInterval(timer);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <main id="main-content" className="product-page">
      <header className="product-page-header"><div><h1>{t('inbox.title')}</h1><p>{t('inbox.description')}</p>{updatedAt ? <p className="product-data-age" role="status">{t('inbox.updatedAt', { time: new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short' }).format(updatedAt) })}</p> : null}</div><Button variant="secondary" onClick={() => load()}>{t('product.actions.refresh')}</Button></header>
      {status === 'loading' ? <div className="product-workspace-skeleton" role="status"><span /><span /><span /></div> : null}
      {status === 'error' ? <ProductError error={error} onRetry={load} t={t} /> : null}
      {status === 'ready' && partialCount ? <section className="product-partial-state" role="status"><strong>{t('product.states.partial')}</strong><p>{t('product.states.partialProjects', { count: partialCount })}</p></section> : null}
      {status === 'ready' && !items.length ? <section className="product-empty-state"><h2>{t('inbox.empty.title')}</h2><p>{t('inbox.empty.description')}</p></section> : null}
      {items.length ? <ol className="inbox-list">{items.map((item) => <li key={item.id}><Link to={item.href}><div><span className="product-eyebrow">{item.key}</span><h2>{item.title}</h2><p>{item.detail}</p></div><StatusBadge tone={item.tone}>{t('inbox.open')}</StatusBadge></Link></li>)}</ol> : null}
    </main>
  );
}
