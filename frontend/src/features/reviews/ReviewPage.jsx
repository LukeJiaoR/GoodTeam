import { useEffect, useMemo, useState } from 'react';
import { Link, useBlocker, useNavigate, useOutletContext, useParams } from 'react-router-dom';
import Button from '../../components/ui/Button';
import DecisionActions from '../../components/DecisionActions';
import EvidenceSummary from '../../components/EvidenceSummary';
import MarkdownView from '../../components/ui/MarkdownView';
import ProductError from '../../components/ui/ProductError';
import StatusBadge from '../../components/ui/StatusBadge';
import { routes } from '../../lib/routing/routes';
import useProjectState from '../runs/useProjectState';
import { reviewApi } from './reviewApi';

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

function lineDiff(before, after) {
  const original = String(before || '').split('\n');
  const edited = String(after || '').split('\n');
  const count = Math.max(original.length, edited.length);
  const lines = [];
  for (let index = 0; index < count; index += 1) {
    if (original[index] === edited[index]) lines.push(`  ${original[index] ?? ''}`);
    else {
      if (original[index] !== undefined) lines.push(`- ${original[index]}`);
      if (edited[index] !== undefined) lines.push(`+ ${edited[index]}`);
    }
  }
  return lines.join('\n');
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

const DECISION_NODE_KEYS = {
  prd: 'review.decision.node.prd',
  rfc: 'review.decision.node.rfc',
  security: 'review.decision.node.security',
  staging: 'review.decision.node.staging',
  release: 'review.decision.node.release',
  planner: 'review.decision.node.planner',
  implementation: 'review.decision.node.implementation',
  qa: 'review.decision.node.qa',
  rfc_review_gate: 'review.decision.node.rfc_review_gate',
  security_review_gate: 'review.decision.node.security_review_gate',
  staging_review_gate: 'review.decision.node.staging_review_gate',
  release_review_gate: 'review.decision.node.release_review_gate',
};

function decisionNodeLabel(node, t) {
  const key = DECISION_NODE_KEYS[node];
  if (!key) return '';
  const translated = t(key);
  return translated === key ? '' : translated;
}

function ReviewList({ projectKey, reviews, selected, t }) {
  return (
    <aside className="review-queue" aria-label={t('review.queue.label')}>
      <h2>{t('review.queue.title')}</h2>
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

function TakeoverRecovery({ busy, error, onRecover, review, t }) {
  const [note, setNote] = useState(review.recovery?.note || '');
  return (
    <article className="review-recovery">
      <p className="product-eyebrow">{t('review.recovery.eyebrow')}</p>
      <h2>{t('review.recovery.title')}</h2>
      <p>{t('review.recovery.description')}</p>
      <label><span>{t('review.recovery.note')}</span><textarea rows="4" value={note} onChange={(event) => setNote(event.target.value)} /></label>
      <Button busy={busy === 'recover'} disabled={!note.trim()} onClick={() => onRecover(note)}>{t('review.recovery.action')}</Button>
      <p className="product-boundary-note">{t('review.recovery.hint')}</p>
      {error ? <ProductError error={error} t={t} /> : null}
    </article>
  );
}

function ArtifactPanel({ onChange, review, t, value }) {
  const isBrief = review.approval === 'takeover';
  const [view, setView] = useState(isBrief ? 'preview' : 'edit');
  const baseline = typeof review.content === 'string' ? review.content : '';
  return (
    <article className="review-artifact">
      <header>
        <div>
          <p className="product-eyebrow">{t('review.artifact.eyebrow')}</p>
          <h2>{reviewTitle(review, t)}</h2>
        </div>
        <div className="review-view-switcher">
          <button className={view === 'preview' ? 'is-active' : ''} onClick={() => setView('preview')}>{t(isBrief ? 'review.artifact.read' : 'review.artifact.preview')}</button>
          <button className={view === 'edit' ? 'is-active' : ''} onClick={() => setView('edit')}>{t('review.artifact.edit')}</button>
          <button className={view === 'diff' ? 'is-active' : ''} onClick={() => setView('diff')}>{t('review.artifact.diff')}</button>
        </div>
      </header>
      {view === 'preview' ? (
        isBrief ? (
          <BriefPreview content={value} t={t} />
        ) : (
          <div className="review-rendered-container" tabIndex={0}>
            <MarkdownView content={value} />
          </div>
        )
      ) : view === 'edit' ? (
        <label className="review-editor">
          <span className="sr-only">{t('review.artifact.content')}</span>
          <textarea spellCheck="false" value={value} onChange={(event) => onChange(event.target.value)} />
        </label>
      ) : (
        <pre className="review-diff" aria-label={t('review.artifact.diff')}>
          {lineDiff(baseline, value)}
        </pre>
      )}
    </article>
  );
}

function DecisionResult({ decisionNote, projectKey, t }) {
  if (!decisionNote) return null;
  const decision = decisionNote.decision || 'approved';
  return (
    <div className="review-decision-result" role="status">
      <strong>{t(`review.decision.result.${decision}`, { gate: String(decisionNote.gate || '').toUpperCase() })}</strong>
      {decisionNote.nextLabel ? <span>{t('review.decision.next', { node: decisionNote.nextLabel })}</span> : null}
      <Link to={routes.projectWork(projectKey)}>{t('review.decision.viewNext')}</Link>
    </div>
  );
}

function Inspector({ answers, busy, decisionNote, error, onAccept, onAnswer, onDecision, onDiscard, onDismissDecision, onRefine, onSave, projectKey, proposed, questions, review, setAnswers, submittedCount, t }) {
  const evidence = asObject(review.evidence_snapshot);
  const provenance = asObject(review.reviewer_provenance);
  const recommendation = asObject(review.recommendation);
  const dissent = recommendation.reasons?.filter?.((reason) => ['open_objection', 'dissent'].includes(reason.kind)) || [];
  return (
    <aside className="review-inspector">
      <section><h2>{t('review.recommendation.title')}</h2>
        <p className="review-verdict">{recommendation.verdict ? recommendationVerdict(recommendation.verdict, t) : t('review.recommendation.none')}</p>
        {recommendation.reasons?.map?.((reason, index) => <p key={index}>{recommendationReason(reason, t)}</p>)}
      </section>
      <EvidenceSummary evidence={evidence} t={t} />
      <details><summary>{t('review.evidence.title')}</summary><pre>{JSON.stringify(evidence, null, 2)}</pre></details>
      <details><summary>{t('review.provenance.title')}</summary><pre>{JSON.stringify(provenance, null, 2)}</pre></details>
      <details><summary>{t('review.dissent.title')}</summary>{dissent.length ? dissent.map((reason, index) => <p key={index}>{reason.text || reason}</p>) : <p>{t('review.dissent.none')}</p>}{review.multi_reviewer_projection ? <pre>{JSON.stringify(review.multi_reviewer_projection, null, 2)}</pre> : null}</details>
      {review.committee?.messages?.length ? <details><summary>{t('review.debate.title')}</summary><ol>{review.committee.messages.map((message) => <li key={message.id}><strong>{message.from_role}</strong><MarkdownView content={message.body} /></li>)}</ol></details> : null}
      {questions.length ? <details open><summary>{t('review.questions.title')}</summary><div className="review-question-list">{questions.map((question) => {
        const text = typeof question === 'string' ? question : question.question || question.text;
        return <label key={text}><span>{text}</span><textarea rows="2" value={answers[text] || ''} onChange={(event) => setAnswers((current) => ({ ...current, [text]: event.target.value }))} /></label>;
      })}<Button busy={busy === 'answer'} disabled={!Object.values(answers).some((value) => value.trim())} onClick={() => onAnswer(Object.entries(answers).filter(([, value]) => value.trim()).map(([question, answer]) => ({ question, answer })))}>{t('review.questions.apply')}</Button>{submittedCount ? <p className="review-questions-submitted" role="status">{t('review.questions.submitted', { count: submittedCount, plural: submittedCount === 1 ? '' : 's' })}</p> : null}</div></details> : null}
      {review.artifact_path ? <div className="review-editor-actions"><Button variant="secondary" busy={busy === 'save'} onClick={onSave}>{t('review.actions.save')}</Button><Button variant="secondary" busy={busy === 'refine'} onClick={onRefine}>{t('review.actions.refine')}</Button></div> : <p className="product-boundary-note">{t('review.artifact.notGenerated')}</p>}
      {proposed ? <section className="review-proposal"><h3>{t('review.refinement.title')}</h3><div className="review-proposal-content"><MarkdownView content={proposed.content} /></div><div className="review-editor-actions"><Button busy={busy === 'accept'} onClick={onAccept}>{t('review.refinement.accept')}</Button><Button variant="secondary" busy={busy === 'discard'} onClick={onDiscard}>{t('review.refinement.discard')}</Button></div></section> : null}
      {error ? <ProductError error={error} t={t} /> : null}
      <DecisionResult decisionNote={decisionNote} projectKey={projectKey} t={t} />
      {review.is_actionable ? (
        <DecisionActions busy={busy} onDecision={onDecision} t={t} />
      ) : <p className="product-boundary-note">{t('review.decision.notActionable')}</p>}
      {decisionNote ? <button type="button" className="review-decision-dismiss" onClick={onDismissDecision}>{t('review.decision.dismiss')}</button> : null}
    </aside>
  );
}

function DirtyDialog({ onLeave, onStay, t }) {
  return (
    <div className="product-dialog-backdrop">
      <div className="product-dialog" role="dialog" aria-modal="true" aria-labelledby="review-dirty-title">
        <h2 id="review-dirty-title">{t('review.dirty.title')}</h2>
        <p>{t('review.dirty.description')}</p>
        <div className="product-dialog-actions">
          <Button variant="secondary" onClick={onLeave}>{t('review.dirty.leave')}</Button>
          <Button variant="primary" onClick={onStay}>{t('review.dirty.stay')}</Button>
        </div>
      </div>
    </div>
  );
}

export default function ReviewPage() {
  const { projectKey = '', reviewId = '' } = useParams();
  const { t } = useOutletContext();
  const navigate = useNavigate();
  const api = useMemo(() => ({ getProjectState: reviewApi.getReviewState }), []);
  const { data, error, refresh, status } = useProjectState(projectKey, { api });
  const reviews = (data?.approval_reviews || [])
    .filter((review) => review.is_actionable || review.artifact_path || review.content || !review.is_pending)
    .sort((left, right) => reviewPriority(left) - reviewPriority(right));
  const selected = reviews.find((item) => item.approval === reviewId)
    || reviews.find((item) => item.is_actionable)
    || reviews[0];
  const [content, setContent] = useState('');
  const [baseline, setBaseline] = useState('');
  const [answers, setAnswers] = useState({});
  const [busy, setBusy] = useState('');
  const [mutationError, setMutationError] = useState(null);
  const [proposed, setProposed] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [decisionNote, setDecisionNote] = useState(null);
  const [submittedCount, setSubmittedCount] = useState(0);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const dirty = content !== baseline
    || Object.values(answers).some((value) => String(value || '').trim())
    || Boolean(proposed);
  const blocker = useBlocker(dirty);

  useEffect(() => {
    if (blocker.state === 'blocked') {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- documented react-router useBlocker pattern: the dialog opens only when navigation is blocked.
      setConfirmOpen(true);
    }
  }, [blocker.state]);

  useEffect(() => {
    if (!dirty) return undefined;
    const handler = (event) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  useEffect(() => {
    if (!decisionNote) return undefined;
    const timer = globalThis.setTimeout(() => setDecisionNote(null), 6000);
    return () => globalThis.clearTimeout(timer);
  }, [decisionNote]);

  useEffect(() => {
    if (!submittedCount) return undefined;
    const timer = globalThis.setTimeout(() => setSubmittedCount(0), 4000);
    return () => globalThis.clearTimeout(timer);
  }, [submittedCount]);

  useEffect(() => {
    if (!reviewId && selected?.approval) navigate(routes.projectReviewItem(projectKey, selected.approval), { replace: true });
  }, [navigate, projectKey, reviewId, selected?.approval]);

  useEffect(() => {
    if (!selected) { queueMicrotask(() => { setContent(''); setBaseline(''); }); return undefined; }
    if (selected.recovery?.available) { queueMicrotask(() => { setContent(''); setBaseline(''); }); return undefined; }
    if (typeof selected.content === 'string') {
      queueMicrotask(() => { setContent(selected.content); setBaseline(selected.content); });
      return undefined;
    }
    if (!selected.artifact) return undefined;
    const controller = new AbortController();
    reviewApi.getArtifact(projectKey, selected.artifact, { signal: controller.signal })
      .then((payload) => { setContent(payload.content || ''); setBaseline(payload.content || ''); })
      .catch((caught) => { if (caught.name !== 'AbortError') setMutationError(caught); });
    return () => controller.abort();
  }, [projectKey, selected]);

  useEffect(() => {
    if (selected?.recovery?.available) { queueMicrotask(() => setQuestions([])); return undefined; }
    if (!selected?.artifact || !selected?.artifact_path) { queueMicrotask(() => setQuestions([])); return undefined; }
    const controller = new AbortController();
    reviewApi.getOpenQuestions(projectKey, selected.artifact, { signal: controller.signal })
      .then((payload) => setQuestions(payload.questions || []))
      .catch((caught) => { if (caught.name !== 'AbortError') setMutationError(caught); });
    return () => controller.abort();
  }, [projectKey, selected?.artifact, selected?.artifact_path, selected?.recovery?.available]);

  const mutate = async (name, operation) => {
    setBusy(name); setMutationError(null);
    try { const result = await operation(); await refresh(); return result; }
    catch (caught) { setMutationError(caught); return null; }
    finally { setBusy(''); }
  };
  const refine = async () => {
    const result = await mutate('refine', () => reviewApi.refineArtifact(projectKey, selected.artifact, content));
    if (result?.proposed_path) {
      const artifact = await reviewApi.getArtifact(projectKey, result.proposed_path);
      setProposed({ ...result, content: artifact.content || '' });
    }
  };
  const loadProposal = async (result) => {
    if (!result?.proposed_path) return;
    const artifact = await reviewApi.getArtifact(projectKey, result.proposed_path);
    setProposed({ ...result, content: artifact.content || '' });
  };
  const answer = async (answers) => {
    const result = await mutate('answer', () => reviewApi.answerOpenQuestions(projectKey, selected.artifact, answers));
    if (result) {
      const submitted = new Set(answers.map((entry) => entry.question));
      setAnswers((current) => Object.fromEntries(Object.entries(current).filter(([question]) => !submitted.has(question))));
      setSubmittedCount(answers.length);
    }
    await loadProposal(result);
  };
  const accept = async () => {
    const result = await mutate('accept', () => reviewApi.acceptRefinement(projectKey, selected.artifact));
    if (result) { setContent(proposed.content); setBaseline(proposed.content); setProposed(null); }
  };
  const discard = async () => {
    const result = await mutate('discard', () => reviewApi.discardRefinement(projectKey, selected.artifact));
    if (result) setProposed(null);
  };
  const save = async () => {
    const result = await mutate('save', () => reviewApi.saveArtifact(projectKey, selected.artifact, content));
    if (result) setBaseline(content);
  };
  const decide = async (decision, note) => {
    const result = await mutate(decision, () => reviewApi.decideReview(projectKey, selected.approval, decision, note));
    if (!result) return;
    const payload = await refresh();
    const actionItems = payload?.action_items || [];
    const latest = actionItems[actionItems.length - 1];
    setDecisionNote({
      decision,
      gate: selected.approval,
      nextLabel: decisionNodeLabel(latest?.after_node || latest?.node_transition || '', t),
    });
  };

  if (status === 'loading') return <main id="main-content" className="product-page"><div className="product-workspace-skeleton" role="status"><span /><span /><span /></div></main>;
  if (status === 'error' && !data) return <main id="main-content" className="product-page"><ProductError error={error} onRetry={() => refresh()} t={t} /></main>;
  const steps = data?.workflow_steps || [];
  const stepIndex = steps.findIndex((step) => step.approval === selected?.approval);
  return (
    <main id="main-content" className="product-page product-review-page">
      <header className="product-page-header">
        <div>
          <p className="product-eyebrow">{data?.start_context?.project_name || projectKey}</p>
          <h1>{t('review.title')}</h1>
          <p>{t('review.description')}</p>
          {stepIndex >= 0 ? <p className="review-pipeline-position">{t('work.pipeline.stepOf', { number: stepIndex + 1, total: steps.length })}</p> : null}
        </div>
        <Link to={`${routes.projectWork(projectKey)}?view=plan`}>{t('review.backWork')}</Link>
      </header>
      {!selected ? <p className="product-empty-copy">{t('review.empty')}</p> : (
        <div className="review-workspace">
          <ReviewList projectKey={projectKey} reviews={reviews} selected={selected.approval} t={t} />
          {selected.recovery?.available ? <TakeoverRecovery key={selected.approval} busy={busy} error={mutationError} review={selected} t={t}
            onRecover={(note) => mutate('recover', () => reviewApi.recoverTakeoverBrief(projectKey, note))}
          /> : <>
            <ArtifactPanel key={selected.approval} review={selected} value={content} onChange={setContent} t={t} />
            <Inspector answers={answers} busy={busy} decisionNote={decisionNote} error={mutationError} proposed={proposed} questions={questions} review={selected} setAnswers={setAnswers} submittedCount={submittedCount} t={t}
              projectKey={projectKey}
              onSave={save}
              onRefine={refine}
              onAnswer={answer}
              onAccept={accept}
              onDiscard={discard}
              onDismissDecision={() => setDecisionNote(null)}
              onDecision={decide}
            />
          </>}
        </div>
      )}
      {confirmOpen ? <DirtyDialog t={t} onLeave={() => { blocker.proceed(); setConfirmOpen(false); }} onStay={() => { blocker.reset(); setConfirmOpen(false); }} /> : null}
    </main>
  );
}
