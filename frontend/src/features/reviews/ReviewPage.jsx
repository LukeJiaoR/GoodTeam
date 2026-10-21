import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, FileDiff, FileText, ListChecks, PencilLine } from 'lucide-react';
import { Link, useBlocker, useNavigate, useOutletContext, useParams } from 'react-router-dom';
import MarkdownToc from '../../components/MarkdownToc';
import Button from '../../components/ui/Button';
import WorkspacePanel from '../../components/ui/WorkspacePanel';
import DecisionActions from '../../components/DecisionActions';
import { approvalConfirmationKey, canConfirmApproval, requiresInformedApproval } from '../../lib/reviews/approvalConfirmation';
import EvidenceSummary from '../../components/EvidenceSummary';
import MarkdownView from '../../components/ui/MarkdownView';
import ProductError from '../../components/ui/ProductError';
import PageHeading from '../../components/ui/PageHeading';
import StatusBadge from '../../components/ui/StatusBadge';
import { useToast } from '../../components/ui/ToastProvider';
import { routes } from '../../lib/routing/routes';
import { runApi } from '../runs/runApi';
import useProjectState from '../runs/useProjectState';
import { reviewApi } from './reviewApi';
import useReviewArtifactDraft from './useReviewArtifactDraft';

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

function ArtifactPanel({ baseline, canEdit, onChange, review, t, value }) {
  const isBrief = review.approval === 'takeover';
  const [view, setView] = useState('preview');
  return (
    <article className="review-artifact">
      <header className="review-artifact__header">
        <div>
          <p className="product-eyebrow">{t('review.artifact.eyebrow')}</p>
          <h2>{reviewTitle(review, t)}</h2>
        </div>
        <div className="review-view-switcher" aria-label={t('review.artifact.view')}>
          <button type="button" aria-pressed={view === 'preview'} className={view === 'preview' ? 'is-active' : ''} onClick={() => setView('preview')}><FileText aria-hidden="true" size={15} />{t(isBrief ? 'review.artifact.read' : 'review.artifact.preview')}</button>
          <button type="button" disabled={!canEdit} aria-pressed={view === 'edit'} className={view === 'edit' ? 'is-active' : ''} onClick={() => setView('edit')}><PencilLine aria-hidden="true" size={15} />{t('review.artifact.edit')}</button>
          <button type="button" disabled={!canEdit} aria-pressed={view === 'diff'} className={view === 'diff' ? 'is-active' : ''} onClick={() => setView('diff')}><FileDiff aria-hidden="true" size={15} />{t('review.artifact.diff')}</button>
        </div>
      </header>
      <MarkdownToc content={value} t={t} />
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

function DecisionResult({ decisionNote, projectKey, onReturn, t }) {
  if (!decisionNote) return null;
  const decision = decisionNote.decision || 'approved';
  return (
    <div className="review-decision-result" role="status">
      <strong>{t(`review.decision.result.${decision}`, { gate: String(decisionNote.gate || '').toUpperCase() })}</strong>
      {decisionNote.nextLabel ? <span>{t('review.decision.next', { node: decisionNote.nextLabel })}</span> : null}
      {onReturn ? <Button variant="secondary" onClick={onReturn}>{t('review.decision.viewNext')}</Button> : <Link to={routes.projectWork(projectKey)}>{t('review.decision.viewNext')}</Link>}
    </div>
  );
}

function Inspector({ onReturn, answers, decisionContextKey, approveDisabled, artifactConflict, artifactReady, busy, decisionNote, error, onAccept, onAnswer, onDecision, onDiscard, onDismissDecision, onRefine, onReloadArtifact, onRequestChanges, onRetryError, onSave, projectKey, proposed, questions, review, setAnswers, submittedCount, t }) {
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
      <details><summary>{t('review.evidence.title')}</summary><pre>{JSON.stringify(evidence, null, 2)}</pre></details>
      <details><summary>{t('review.provenance.title')}</summary><pre>{JSON.stringify(provenance, null, 2)}</pre></details>
      <details><summary>{t('review.dissent.title')}</summary>{dissent.length ? dissent.map((reason, index) => <p key={index}>{reason.text || reason}</p>) : <p>{t('review.dissent.none')}</p>}{review.multi_reviewer_projection ? <pre>{JSON.stringify(review.multi_reviewer_projection, null, 2)}</pre> : null}</details>
      {review.committee?.messages?.length ? <details><summary>{t('review.debate.title')}</summary><ol>{review.committee.messages.map((message) => <li key={message.id}><strong>{message.from_role}</strong><MarkdownView content={message.body} /></li>)}</ol></details> : null}
      {questions.length ? <details open><summary>{t('review.questions.title')}</summary><div className="review-question-list">{questions.map((question) => {
        const text = typeof question === 'string' ? question : question.question || question.text;
        return <label key={text}><span>{text}</span><textarea rows="2" value={answers[text] || ''} onChange={(event) => setAnswers((current) => ({ ...current, [text]: event.target.value }))} /></label>;
      })}<Button busy={busy === 'answer'} disabled={!artifactReady || Boolean(busy) || !Object.values(answers).some((value) => value.trim())} onClick={() => onAnswer(Object.entries(answers).filter(([, value]) => value.trim()).map(([question, answer]) => ({ question, answer })))}>{t('review.questions.apply')}</Button>{submittedCount ? <p className="review-questions-submitted" role="status">{t('review.questions.submitted', { count: submittedCount, plural: submittedCount === 1 ? '' : 's' })}</p> : null}</div></details> : null}
      {artifactConflict ? <div className="review-artifact-conflict" role="status"><p>{t('projects.errors.stale_approval')}</p><Button variant="secondary" onClick={onReloadArtifact}>{t('product.actions.reload')}</Button></div> : null}
      {review.artifact_path ? <div className="review-editor-actions"><Button variant="secondary" busy={busy === 'save'} disabled={!artifactReady} onClick={onSave}>{t('review.actions.save')}</Button><Button variant="secondary" busy={busy === 'refine'} disabled={!artifactReady} onClick={onRefine}>{t('review.actions.refine')}</Button></div> : <p className="product-boundary-note">{t('review.artifact.notGenerated')}</p>}
      {proposed ? <section className="review-proposal"><h3>{t('review.refinement.title')}</h3><div className="review-proposal-content"><MarkdownView content={proposed.content} /></div><div className="review-editor-actions"><Button busy={busy === 'accept'} disabled={!artifactReady || Boolean(busy)} onClick={onAccept}>{t('review.refinement.accept')}</Button><Button variant="secondary" busy={busy === 'discard'} disabled={Boolean(busy)} onClick={onDiscard}>{t('review.refinement.discard')}</Button></div></section> : null}
      {error ? <ProductError error={error} onRetry={onRetryError} t={t} /> : null}
      <DecisionResult decisionNote={decisionNote} projectKey={projectKey} onReturn={onReturn} t={t} />
      {review.is_actionable ? (
        <DecisionActions
          approveDisabled={approveDisabled}
          decisionContextKey={decisionContextKey}
          informedApproval={requiresInformedApproval(recommendation)}
          busy={busy}
          onDecision={onDecision}
          onRequestChanges={onRequestChanges}
          t={t}
        />
      ) : <p className="product-boundary-note">{t('review.decision.notActionable')}</p>}
      {decisionNote ? <button type="button" className="review-decision-dismiss" onClick={onDismissDecision}>{t('review.decision.dismiss')}</button> : null}
    </aside>
  );
}

function DirtyDialog({ onLeave, onStay, t }) {
  return (
    <WorkspacePanel compact title={t('review.dirty.title')} closeLabel={t('work.plan.close')} onClose={onStay}>
      <p>{t('review.dirty.description')}</p>
      <div className="product-dialog-actions">
        <Button variant="secondary" onClick={onLeave}>{t('review.dirty.leave')}</Button>
        <Button variant="primary" onClick={onStay}>{t('review.dirty.stay')}</Button>
      </div>
    </WorkspacePanel>
  );
}

function ReviewSurface({ embedded, onClose, busy, t, children }) {
  return embedded
    ? <WorkspacePanel title={t('review.title')} closeLabel={t('work.panel.back')} busy={busy} onClose={onClose}>{children}</WorkspacePanel>
    : children;
}

export default function ReviewPage({ embedded = false, selectedReviewId = '', expectedRunId = '', onClose, onUpdated, onChangeDiscussion }) {
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
  // Remount every local draft, proposal, answer, and mutation lifecycle when its
  // owner changes, including browser back/forward and embedded run switches.
  return <ReviewWorkspace key={JSON.stringify([projectKey, artifactRunId, selected?.artifact, selected?.approval])}
    {...{ embedded, onClose, onUpdated, onChangeDiscussion, projectKey, reviewId,
      data, error, refresh, status, runId, boundRunMatches, reviews, selected, artifactRunId }} />;
}

function ReviewWorkspace({ embedded, onClose, onUpdated, onChangeDiscussion, projectKey, reviewId,
  data, error, refresh, status, runId, boundRunMatches, reviews, selected, artifactRunId }) {
  const { t } = useOutletContext();
  const toast = useToast();
  const navigate = useNavigate();
  const active = useRef(false);
  const operationPending = useRef(false);
  const editRevision = useRef(0);
  useLayoutEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  const artifactDraft = useReviewArtifactDraft(projectKey, artifactRunId, selected);
  const { content, baseline, artifactVersion, full: artifactFull, identity: artifactIdentity } = artifactDraft;
  const [answers, setAnswers] = useState({});
  const [busy, setBusy] = useState('');
  const [mutationError, setMutationError] = useState(null);
  const [proposal, setProposed] = useState(null);
  const proposed = proposal?.proposal_binding?.base_version === artifactVersion
    && proposal?.sourceContent === content ? proposal : null;
  const [questions, setQuestions] = useState([]);
  const [decisionNote, setDecisionNote] = useState(null);
  const [submittedCount, setSubmittedCount] = useState(0);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const approveDisabled = status !== 'ready' || Boolean(busy)
    || !artifactFull
    || !artifactVersion
    || artifactVersion === 'missing'
    || !selected?.artifact
    || !artifactDraft.runId
    || artifactDraft.conflict
    || artifactDraft.pendingReadback
    || content !== baseline;

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
    if (!embedded && !reviewId && selected?.approval) navigate(routes.projectReviewItem(projectKey, selected.approval), { replace: true });
  }, [embedded, navigate, projectKey, reviewId, selected?.approval]);

  useEffect(() => {
    if (selected?.recovery?.available) { queueMicrotask(() => setQuestions([])); return undefined; }
    if (!selected?.artifact || !selected?.artifact_path) { queueMicrotask(() => setQuestions([])); return undefined; }
    const controller = new AbortController();
    reviewApi.getOpenQuestions(projectKey, selected.artifact, { signal: controller.signal })
      .then((payload) => { if (!controller.signal.aborted) setQuestions(payload.questions || []); })
      .catch((caught) => { if (!controller.signal.aborted && caught.name !== 'AbortError') setMutationError(caught); });
    return () => controller.abort();
  }, [projectKey, selected?.artifact, selected?.artifact_path, selected?.recovery?.available]);

  const mutate = async (name, operation) => {
    if (!active.current || operationPending.current) return null;
    operationPending.current = true;
    setBusy(name); setMutationError(null);
    try {
      const result = await operation();
      if (!active.current) return null;
      await refresh();
      if (!active.current) return null;
      await onUpdated?.();
      return active.current ? result : null;
    } catch (caught) {
      if (!active.current) return null;
      setMutationError(caught);
      if (['stale_approval', 'stale_intent_target'].includes(caught?.code)) {
        await refresh().catch(() => {});
      }
      if (!active.current) return null;
      toast.error(t('toast.decisionError', { error: caught.message || String(caught) }));
      return null;
    } finally {
      operationPending.current = false;
      if (active.current) setBusy('');
    }
  };
  const artifactReady = artifactFull && Boolean(artifactVersion && artifactRunId)
    && !artifactDraft.conflict && !artifactDraft.pendingReadback;
  const loadProposal = async (result, revision, sourceContent) => {
    if (!active.current || !result?.proposal_binding) return;
    const binding = result.proposal_binding;
    const fields = ['proposal_id', 'run_id', 'artifact_key', 'base_version', 'proposal_version'];
    if (!fields.every((field) => typeof binding[field] === 'string' && binding[field])
      || binding.run_id !== artifactRunId || binding.artifact_key !== selected.artifact) throw new Error('Refinement proposal identity changed. Please refine again.');
    // Refining edited text persists a new base revision. Read it back before
    // adopting that revision, rather than treating the old editor version as it.
    if (binding.base_version !== artifactVersion) {
      const base = await reviewApi.getArtifact(projectKey, selected.artifact);
      if (!active.current || editRevision.current !== revision) return;
      if (base.run_id !== binding.run_id || base.artifact_version !== binding.base_version
        || base.content !== sourceContent) throw new Error('Refinement base changed while loading. Please reload.');
      artifactDraft.acceptSavedArtifact(base, sourceContent, artifactIdentity, sourceContent);
    }
    const artifact = await reviewApi.getArtifact(projectKey, `${selected.artifact}_proposed`);
    if (!active.current || editRevision.current !== revision) return;
    if (!fields.every((field) => artifact.proposal_binding?.[field] === binding[field])
      || artifact.run_id !== binding.run_id || artifact.artifact_version !== binding.proposal_version) {
      throw new Error('Refinement proposal changed while loading. Please refine again.');
    }
    setProposed({ ...artifact, sourceContent });
  };
  const refine = async () => {
    if (!artifactReady) return;
    const revision = editRevision.current;
    await mutate('refine', async () => {
      setProposed(null);
      const result = await reviewApi.refineArtifact(projectKey, selected.artifact, content,
        { expectedArtifact: { runId: artifactRunId, artifactVersion } });
      await loadProposal(result, revision, content);
      return result;
    });
  };
  const answer = async (answers) => {
    if (!artifactReady) return;
    const revision = editRevision.current;
    const result = await mutate('answer', async () => {
      setProposed(null);
      const result = await reviewApi.answerOpenQuestions(projectKey, selected.artifact, answers,
        { expectedArtifact: { runId: artifactRunId, artifactVersion } });
      await loadProposal(result, revision, content);
      return result;
    });
    if (result) {
      const submitted = new Set(answers.map((entry) => entry.question));
      setAnswers((current) => Object.fromEntries(Object.entries(current).filter(([question]) => !submitted.has(question))));
      setSubmittedCount(answers.length);
    }
  };
  const accept = async () => {
    if (!artifactReady || !proposed) return;
    const identity = artifactIdentity;
    const originalContent = content;
    const acceptedContent = proposed?.content || '';
    const result = await mutate('accept', () => reviewApi.acceptRefinement(projectKey, selected.artifact, { expectedProposal: proposed.proposal_binding }));
    if (result) {
      artifactDraft.beginSavedReadback(acceptedContent, identity, originalContent);
      try {
        const latest = await reviewApi.getArtifact(projectKey, selected.artifact);
        if (!active.current) return;
        artifactDraft.acceptSavedArtifact(latest, acceptedContent, identity, originalContent);
        setProposed(null);
      } catch (caught) {
        artifactDraft.setError(caught);
      }
    }
  };
  const discard = async () => {
    if (!proposed) return;
    const result = await mutate('discard', () => reviewApi.discardRefinement(projectKey, selected.artifact, { expectedProposal: proposed.proposal_binding }));
    if (result) setProposed(null);
  };
  const save = async () => {
    if (!artifactReady) return;
    const identity = artifactIdentity;
    const submittedContent = content;
    const result = await mutate('save', async () => {
      await reviewApi.saveArtifact(
        projectKey,
        selected.artifact,
        submittedContent,
        { expectedArtifact: { runId: artifactRunId, artifactVersion } },
      );
      return true;
    });
    if (!result) return;
    setProposed(null);
    artifactDraft.beginSavedReadback(submittedContent, identity, submittedContent);
    try {
      const latest = await reviewApi.getArtifact(projectKey, selected.artifact);
      if (!active.current) return;
      artifactDraft.acceptSavedArtifact(latest, submittedContent, identity);
    } catch (caught) {
      artifactDraft.setError(caught);
    }
  };
  const decide = async (decision, note) => {
    if (!selected?.is_actionable || status !== 'ready' || busy) return;
    if (decision === 'approved' && !canConfirmApproval({
      disabled: approveDisabled,
      requiresReason: requiresInformedApproval(asObject(selected.recommendation)),
      note,
    })) return;
    const expectedApproval = {
      run_id: artifactDraft.runId || '',
      target_type: 'review',
      target_id: selected.approval || '',
      artifact_key: selected.artifact || '',
      artifact_version: artifactDraft.artifactVersion || '',
    };
    const result = await mutate(decision, () => (
      decision === 'approved'
        ? reviewApi.decideReview(projectKey, selected.approval, decision, note, { expectedApproval })
        : reviewApi.decideReview(projectKey, selected.approval, decision, note)
    ));
    if (!result) return;
    toast.success(t('toast.decisionSubmitted'));
    const payload = await refresh();
    if (!active.current) return;
    const actionItems = payload?.action_items || [];
    const latest = actionItems[actionItems.length - 1];
    setDecisionNote({
      decision,
      gate: selected.approval,
      nextLabel: decisionNodeLabel(latest?.after_node || latest?.node_transition || '', t),
    });
  };
  const requestChanges = async (note = '') => {
    if (embedded && dirty) { setMutationError(new Error(t('work.panel.saveBeforeChange'))); return; }
    const runId = data?.state?.run_id || '';
    const expectedTarget = {
      run_id: runId,
      target_type: 'review',
      target_id: selected.approval || '',
      artifact_key: selected.artifact || '',
      artifact_version: selected.artifact_version || '',
    };
    const result = await mutate('changes_requested', () => (
      runApi.startChangeIntent(projectKey, selected.approval, expectedTarget, { initialText: note })
    ));
    if (!result) return;
    if (embedded) { await onChangeDiscussion?.(); return; }
    navigate(runId ? routes.projectRun(projectKey, runId) : routes.projectWork(projectKey));
  };

  const requestClose = () => {
    if (busy) return;
    if (dirty) setConfirmOpen(true);
    else onClose?.();
  };
  const Page = embedded ? 'section' : 'main';
  if (status === 'loading') return <ReviewSurface embedded={embedded} onClose={requestClose} t={t}><Page id={embedded ? undefined : 'main-content'} className="product-page"><div className="product-workspace-skeleton" role="status"><span /><span /><span /></div></Page></ReviewSurface>;
  if (status === 'error' && !data) return <ReviewSurface embedded={embedded} onClose={requestClose} t={t}><Page id={embedded ? undefined : 'main-content'} className="product-page"><ProductError error={error} onRetry={() => refresh()} t={t} /></Page></ReviewSurface>;
  const displayedError = mutationError || artifactDraft.error;
  const steps = data?.workflow_steps || [];
  const stepIndex = steps.findIndex((step) => step.approval === selected?.approval);
  const actionableCount = reviews.filter((review) => review.is_actionable || review.recovery?.available).length;
  return (
    <ReviewSurface embedded={embedded} onClose={requestClose} busy={Boolean(busy)} t={t}>
    <Page id={embedded ? undefined : 'main-content'} className="product-page product-review-page">
      <PageHeading
        eyebrow={data?.start_context?.project_name || projectKey}
        title={t('review.title')}
        description={t('review.description')}
        summary={(
          <dl className="review-page-summary" aria-label={t('review.workspace.summary')}>
            <div><dt>{t('review.workspace.actionable')}</dt><dd>{actionableCount}</dd></div>
            <div><dt>{t('review.workspace.total')}</dt><dd>{reviews.length}</dd></div>
            <div><dt>{t('review.workspace.stage')}</dt><dd>{stepIndex >= 0 ? `${stepIndex + 1}/${steps.length}` : '—'}</dd></div>
          </dl>
        )}
      />
      {!selected ? <p className="product-empty-copy">{t(boundRunMatches ? 'review.empty' : 'work.panel.taskChanged')}</p> : (
        <div className="review-workspace">
          {!embedded ? <ReviewList projectKey={projectKey} reviews={reviews} selected={selected.approval} t={t} /> : null}
          {selected.recovery?.available ? <TakeoverRecovery key={selected.approval} busy={busy} error={mutationError} review={selected} t={t}
            onRecover={(note) => mutate('recover', () => reviewApi.recoverTakeoverBrief(projectKey, note))}
          /> : <>
            <ArtifactPanel key={`${projectKey}:${runId}:${selected.approval}`} baseline={baseline} canEdit={artifactFull && !busy} review={selected} value={content} onChange={(value) => { editRevision.current += 1; setProposed(null); artifactDraft.setContent(value); }} t={t} />
            <Inspector onReturn={embedded ? requestClose : undefined} answers={answers} approveDisabled={approveDisabled} artifactConflict={artifactDraft.conflict} artifactReady={artifactReady && !busy} busy={busy} decisionNote={decisionNote} error={displayedError} proposed={proposed} questions={questions} review={selected} setAnswers={setAnswers} submittedCount={submittedCount} t={t}
              projectKey={projectKey}
              decisionContextKey={approvalConfirmationKey([
                projectKey, artifactDraft.runId, selected.approval, selected.artifact, artifactVersion,
                asObject(selected.recommendation), asObject(selected.evidence_snapshot),
                asObject(selected.reviewer_provenance), selected.multi_reviewer_projection,
              ])}
              onSave={save}
              onRefine={refine}
              onReloadArtifact={artifactDraft.reload}
              onRetryError={() => (
                artifactDraft.error ? artifactDraft.retryFullRead() : refresh()
              )}
              onAnswer={answer}
              onAccept={accept}
              onDiscard={discard}
              onDismissDecision={() => setDecisionNote(null)}
              onDecision={decide}
              onRequestChanges={requestChanges}
            />
          </>}
        </div>
      )}
      {confirmOpen ? <DirtyDialog t={t} onLeave={() => { if (blocker.state === 'blocked') blocker.proceed(); else onClose?.(); setConfirmOpen(false); }} onStay={() => { if (blocker.state === 'blocked') blocker.reset(); setConfirmOpen(false); }} /> : null}
    </Page>
    </ReviewSurface>
  );
}
