import { useMemo } from 'react';
import { CheckCircle2, ListChecks } from 'lucide-react';
import { Link, useOutletContext, useParams } from 'react-router-dom';
import MarkdownToc from '../../components/MarkdownToc';
import WorkspacePanel from '../../components/ui/WorkspacePanel';
import EvidenceSummary from '../../components/EvidenceSummary';
import MarkdownView from '../../components/ui/MarkdownView';
import ProductError from '../../components/ui/ProductError';
import PageHeading from '../../components/ui/PageHeading';
import StatusBadge from '../../components/ui/StatusBadge';
import { routes } from '../../lib/routing/routes';
import useProjectState from '../runs/useProjectState';
import { reviewApi } from './reviewApi';
import useReviewArtifactDraft from './useReviewArtifactDraft';
import './reviewCompact.css';
import LegacyReviewerEvidence from './LegacyReviewerEvidence';
import { DeliveryArtifacts } from '../runs/workspace/DeliverySummary';
import { deliveryLinkResolver } from '../runs/workspace/deliveryLinks';

function reviewTone(review) {
  if (review.is_actionable) return 'warning';
  if (review.decision === 'approved' || review.status === 'approved') return 'success';
  if (review.decision === 'rejected') return 'danger';
  return 'pending';
}

function reviewTitle(review, t) {
  if (review.approval === 'takeover') return t('review.item.takeover');
  return review.title || review.approval.toUpperCase();
}

function reviewStatus(review, t) {
  if (review.recovery?.available) return t('review.queue.recoveryAction');
  if (review.is_actionable) return t('review.queue.action');
  const labels = {
    approved: 'review.status.approved',
    changes_requested: 'review.status.changesRequested',
    rejected: 'review.status.rejected',
    pending: 'review.queue.pending',
  };
  return labels[review.decision || review.status] ? t(labels[review.decision || review.status]) : (review.decision || review.status || t('review.queue.pending'));
}

function reviewPriority(review) {
  return review.is_current || review.is_actionable || review.recovery?.available ? 0 : 1;
}

function asObject(value) {
  if (!value) return {};
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { return { value }; }
}

function briefHeading(value, t) {
  const headings = {
    'What I looked at': 'review.brief.heading.lookedAt',
    'Key findings': 'review.brief.heading.findings',
    'My current assessment': 'review.brief.heading.assessment',
    'Current assessment': 'review.brief.heading.assessment',
    'Risks and unknowns': 'review.brief.heading.risks',
    'Questions for you': 'review.brief.heading.questions',
    'Requested changes': 'review.brief.heading.requestedChanges',
    'What happens next': 'review.brief.heading.next',
  };
  return headings[value] ? t(headings[value]) : value;
}

function BriefPreview({ content, t }) {
  return (
    <section className="review-brief-preview">
      {String(content || '').split('\n').map((rawLine, index) => {
        const line = rawLine.trim();
        if (!line) return null;
        const heading = line.match(/^#{1,6}\s+(.+)$/);
        if (heading) return line.startsWith('# ') ? null : <h3 key={index}>{briefHeading(heading[1], t)}</h3>;
        const item = line.match(/^[-*]\s+(.+)$/) || line.match(/^\d+[.)]\s+(.+)$/);
        if (item) return <p className="review-brief-preview__item" key={index}>{item[1]}</p>;
        return <p key={index}>{line}</p>;
      })}
    </section>
  );
}

function recommendationVerdict(value, t) {
  const key = `review.recommendation.verdict.${value}`;
  const translated = t(key);
  return translated === key ? value : translated;
}

function recommendationReason(reason, t) {
  if (reason?.kind === 'clean') return t('review.recommendation.reason.clean');
  return reason?.text || reason;
}

function ReviewList({ projectKey, reviews, selected, t }) {
  return (
    <aside className="review-queue" aria-label={t('review.queue.label')}>
      <header className="review-queue__header">
        <span aria-hidden="true"><ListChecks size={17} /></span>
        <div><h2>{t('review.queue.title')}</h2><small>{reviews.length}</small></div>
      </header>
      {reviews.length ? <ol>{reviews.map((review) => (
        <li key={review.approval}>
          <Link aria-current={selected === review.approval ? 'true' : undefined} className={selected === review.approval ? 'is-active' : ''} to={routes.projectReviewItem(projectKey, review.approval)}>
            <span><strong>{reviewTitle(review, t)}</strong><small>{review.recovery?.available ? t('review.queue.recoveryNeeded') : (review.artifact_path ? t('review.queue.artifactReady') : t('review.queue.noArtifact'))}</small></span>
            <StatusBadge tone={reviewTone(review)}>{reviewStatus(review, t)}</StatusBadge>
          </Link>
        </li>
      ))}</ol> : <p className="product-empty-copy">{t('review.queue.empty')}</p>}
    </aside>
  );
}

function ArtifactPanel({ review, t, value, deliverySummary }) {
  const isBrief = review.approval === 'takeover';
  return (
    <article className="review-artifact">
      <header className="review-artifact__header">
        <div>
          <p className="product-eyebrow">{t('review.artifact.eyebrow')}</p>
          <h2>{reviewTitle(review, t)}</h2>
        </div>
      </header>
      <MarkdownToc content={value} t={t} />
      {isBrief ? (
          <BriefPreview content={value} t={t} />
        ) : (
          <div className="review-rendered-container" tabIndex={0}>
            <MarkdownView content={value} resolveLink={deliveryLinkResolver(deliverySummary)} />
          </div>
      )}
      {deliverySummary && ['security', 'staging', 'release', 'qa'].includes(review.approval) ? <section>
        <h3>{t('work.deliverySummary.retained')}</h3>
        <DeliveryArtifacts summary={{ ...deliverySummary, artifacts: (deliverySummary.artifacts || []).filter(item => item.kind) }} t={t} />
      </section> : null}
    </article>
  );
}

function Inspector({ projectKey, runId, deliverySummary, review, t }) {
  const evidence = asObject(review.evidence_snapshot);
  const provenance = asObject(review.reviewer_provenance);
  const recommendation = asObject(review.recommendation);
  const dissent = recommendation.reasons?.filter?.((reason) => ['open_objection', 'dissent'].includes(reason.kind)) || [];
  return (
    <aside className="review-inspector">
      <header className="review-inspector__header"><span aria-hidden="true"><CheckCircle2 size={17} /></span><div><p>{t('review.workspace.decision')}</p><h2>{reviewStatus(review, t)}</h2></div></header>
      <section className="review-inspector__recommendation"><h2>{t('review.recommendation.title')}</h2>
        <p className="review-verdict">{recommendation.verdict ? recommendationVerdict(recommendation.verdict, t) : t('review.recommendation.none')}</p>
        {recommendation.reasons?.map?.((reason, index) => <p key={index}>{recommendationReason(reason, t)}</p>)}
      </section>
      <EvidenceSummary evidence={evidence} t={t} />
      <LegacyReviewerEvidence projectKey={projectKey} runId={runId} items={review.legacy_reviewer_jobs || []}
        deliverySummary={deliverySummary} t={t} />
      <details><summary>{t('review.evidence.title')}</summary><pre>{JSON.stringify(evidence, null, 2)}</pre></details>
      <details><summary>{t('review.provenance.title')}</summary><pre>{JSON.stringify(provenance, null, 2)}</pre></details>
      <details><summary>{t('review.dissent.title')}</summary>{dissent.length ? dissent.map((reason, index) => <p key={index}>{reason.text || reason}</p>) : <p>{t('review.dissent.none')}</p>}{review.multi_reviewer_projection ? <pre>{JSON.stringify(review.multi_reviewer_projection, null, 2)}</pre> : null}</details>
      {review.committee?.messages?.length ? <details><summary>{t('review.debate.title')}</summary><ol>{review.committee.messages.map((message) => <li key={message.id}><strong>{message.from_role}</strong><MarkdownView content={message.body} /></li>)}</ol></details> : null}
    </aside>
  );
}


function ReviewSurface({ embedded, onClose, busy, t, children }) {
  return embedded
    ? <WorkspacePanel title={t('review.title')} closeLabel={t('work.panel.back')} busy={busy} onClose={onClose}>{children}</WorkspacePanel>
    : children;
}

export default function ReviewPage({ embedded = false, selectedReviewId = '', expectedRunId = '', onClose }) {
  const params = useParams();
  const projectKey = params.projectKey || '';
  const reviewId = embedded ? selectedReviewId : params.reviewId || '';
  const api = useMemo(() => ({ getProjectState: reviewApi.getReviewState }), []);
  const { data, error, refresh, status } = useProjectState(projectKey, { api });
  const runId = data?.state?.run_id || data?.active_run?.run_id || data?.team_workspace?.mission?.run_id || '';
  const boundRunMatches = !embedded || Boolean(expectedRunId && expectedRunId === runId);
  const reviews = (boundRunMatches ? data?.approval_reviews || [] : [])
    .filter((review) => review.is_actionable || review.artifact_path || review.content || !review.is_pending)
    .sort((left, right) => reviewPriority(left) - reviewPriority(right));
  const selected = reviews.find((item) => item.approval === reviewId)
    || (!embedded ? reviews.find((item) => item.is_actionable) || reviews[0] : null);
  const artifactRunId = runId || selected?.run_id || '';
  const { t } = useOutletContext();
  const artifactDraft = useReviewArtifactDraft(projectKey, artifactRunId, selected);
  const Page = embedded ? 'section' : 'main';
  if (status === 'loading') return <ReviewSurface embedded={embedded} onClose={onClose} t={t}><Page id={embedded ? undefined : 'main-content'} className="product-page"><div className="product-workspace-skeleton" role="status"><span /><span /><span /></div></Page></ReviewSurface>;
  if (status === 'error' && !data) return <ReviewSurface embedded={embedded} onClose={onClose} t={t}><Page id={embedded ? undefined : 'main-content'} className="product-page"><ProductError error={error} onRetry={() => refresh()} t={t} /></Page></ReviewSurface>;
  const steps = data?.workflow_steps || [];
  const stepIndex = steps.findIndex((step) => step.approval === selected?.approval);
  const reviewSummary = (
    <dl className="review-page-summary" aria-label={t('review.workspace.summary')}>
      <div><dt>{t('review.workspace.total')}</dt><dd>{reviews.length}</dd></div>
      <div><dt>{t('review.workspace.stage')}</dt><dd>{stepIndex >= 0 ? `${stepIndex + 1}/${steps.length}` : '—'}</dd></div>
    </dl>
  );
  return (
    <ReviewSurface embedded={embedded} onClose={onClose} t={t}>
    <Page id={embedded ? undefined : 'main-content'} className="product-page product-review-page">
      {embedded ? <div className="review-embedded-summary">{reviewSummary}</div> : <PageHeading
        eyebrow={data?.start_context?.project_name || projectKey}
        title={t('review.title')}
        summary={reviewSummary}
      />}
      {!selected ? <p className="product-empty-copy">{t(boundRunMatches ? 'review.empty' : 'work.panel.taskChanged')}</p> : (
        <div className="review-workspace">
          {!embedded ? <ReviewList projectKey={projectKey} reviews={reviews} selected={selected.approval} t={t} /> : null}
          <ArtifactPanel review={selected} value={artifactDraft.content} t={t} deliverySummary={data?.delivery_summary} />
          <Inspector projectKey={projectKey} runId={runId} review={selected} t={t} deliverySummary={data?.delivery_summary} />
          {artifactDraft.error ? <ProductError error={artifactDraft.error} onRetry={artifactDraft.retryFullRead} t={t} /> : null}
        </div>
      )}
    </Page>
    </ReviewSurface>
  );
}
