import { blockerSummary } from './blockerPresentation';
import { useState } from 'react';
import { AlertCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import Button from '../../../components/ui/Button';
import WorkflowRetryAction from './WorkflowRetryAction';
import StatusBadge from '../../../components/ui/StatusBadge';
import { routes } from '../../../lib/routing/routes';
import { translated } from './presentationUtils';
import ManagerApprovalBrief from './ManagerApprovalBrief';
import PreparationFacts from './PreparationFacts';
import WorkApprovalAction from './WorkApprovalAction';
import { requiresInformedWorkApproval } from './workApprovalContext';

export function TaskAttention({
  attention,
  jobs = [],
  activeJobs = [],
  error,
  busy,
  onConfirmChangeIntent,
  onDecide,
  onKeepDiscussingChangeIntent,
  onReview,
  onRetry,
  retry,
  onStartChangeIntent,
  projectKey,
  t,
}) {
  const intent = attention?.intent_commit || null;
  if (!attention) return null;
  const failureJobs = jobs.filter((job) => (attention.refs || []).includes(job.id) && job.status === 'failed');
  if (attention.kind === 'blocker' && activeJobs.length) return (
    <section className="task-blocker" data-attention-id={attention.id} aria-label={t('work.blocker.executing')}>
      <h3>{t('work.blocker.executing')}</h3>
      <p role="status">{t('work.blocker.workerActive')}</p>
      <ul>{activeJobs.map(job => <li key={job.id}>
        <strong>{translated(t, `work.plan.role.${job.role}`, job.role)}</strong>
        <p>{t('work.blocker.lastHeartbeat')} {new Date(job.heartbeat_at || job.updated_at).toLocaleString()}</p>
      </li>)}</ul>
      <p>{t('work.blocker.heartbeatOnly')}</p>
      <details><summary>{t('work.blocker.previousFailure')}</summary><pre>{attention.reason}</pre></details>
      <Button disabled>{t('work.blocker.executing')}</Button>
    </section>
  );
  if (attention.kind === 'blocker') return (
    <section className="task-blocker" data-attention-id={attention.id} aria-label={t('work.blocker.title')}>
      <h3>{t('work.blocker.title')}</h3>
      <p>{blockerSummary(attention, t)}</p>
      <p>{t(String(attention.reason || '').includes('[rate_limit') ? 'work.blocker.actionHelp' : 'work.blocker.genericHelp')}</p>
      {failureJobs.length ? <p className="task-blocker-record">{t('work.blocker.recorded')} <time dateTime={failureJobs.map(job => job.updated_at).sort().at(-1)}>{new Date(failureJobs.map(job => job.updated_at).sort().at(-1)).toLocaleString()}</time></p> : null}
      {projectKey ? <Link className="product-button product-button--secondary" to={routes.projectTeam(projectKey)}>{t('work.blocker.configure')}</Link> : null}
      <details><summary>{t('work.blocker.details')}</summary><pre>{attention.reason}</pre></details>
      {error ? <p role="alert">{t('work.blocker.retryFailed')} {error.message || String(error)}</p> : null}
      {busy === 'retry' ? <p role="status">{t('work.blocker.retrying')}</p> : null}
      {(attention.actions || []).includes('retry') && onRetry ? <WorkflowRetryAction retry={retry} busy={busy === 'retry'} onRetry={onRetry} t={t} /> : null}
    </section>
  );
  const actions = new Set(attention.actions || []);
  const gate = attention.target_id || '';
  const approvalBrief = attention.approval_brief?.schema_version === 'manager_approval_brief.v1'
    ? attention.approval_brief
    : null;
  const startChangeDiscussion = () => Promise.resolve()
    .then(() => onStartChangeIntent?.(gate, approvalBrief?.approval_target || null))
    .catch(() => {});
  const confirmChangeDiscussion = () => Promise.resolve()
    .then(() => onConfirmChangeIntent?.(intent?.id))
    .catch(() => {});
  const keepDiscussing = () => Promise.resolve()
    .then(() => onKeepDiscussingChangeIntent?.(intent?.id))
    .catch(() => {});
  const interpretationItems = Array.isArray(intent?.interpretation?.items)
    ? intent.interpretation.items.filter((item) => item?.text)
    : [];
  return (
    <section className="team-attention" data-attention-id={attention.id} data-kind={attention.kind} aria-label={t('work.manager.attention')}>
      <p className="team-attention__label">
        <AlertCircle aria-hidden="true" size={15} strokeWidth={2} />
        <span>{t('work.manager.attention')}</span>
      </p>
      <h3 id="team-attention-title">
        {translated(t, `work.manager.attentionKinds.${attention.kind}`, attention.reason)}
      </h3>
      {approvalBrief ? (
        <ManagerApprovalBrief brief={approvalBrief} t={t} compact={!requiresInformedWorkApproval(approvalBrief)} />
      ) : (
        <p>{translated(t, `work.manager.attentionReasons.${attention.reason}`, attention.reason)}</p>
      )}
      <div className="team-attention__actions">
        {!intent && actions.has('approve') ? (
          <WorkApprovalAction
            brief={approvalBrief}
            busy={busy}
            gate={gate}
            onDecide={onDecide}
            projectKey={projectKey}
            t={t}
          />
        ) : null}
        {!intent && actions.has('request_changes') ? (
          <Button busy={busy === 'intent-start'} variant="secondary" onClick={startChangeDiscussion}>
            {t('work.manager.requestChanges')}
          </Button>
        ) : null}
        {actions.has('review') && onReview ? (
          <Button variant="secondary" onClick={(event) => onReview(event.currentTarget)}>
            {t('work.review.viewDetails')}
          </Button>
        ) : actions.has('review') ? (
          <Link className="product-button product-button--secondary" to={routes.projectReviewItem(projectKey, gate)}>
            {t('work.review.viewDetails')}
          </Link>
        ) : null}
        {actions.has('retry') ? (
          <WorkflowRetryAction retry={retry} busy={busy === 'retry'} onRetry={onRetry} t={t} />
        ) : null}
      </div>
      {intent?.status === 'discussing' ? (
        <div className="team-intent-commit" data-status="discussing">
          <div className="team-intent-commit__heading">
            <strong>{t(intent.draft_text ? 'work.manager.intent.savedDraft' : 'work.manager.intent.question')}</strong>
            <StatusBadge tone="pending">{t('work.manager.intent.discussing')}</StatusBadge>
          </div>
          {!intent.draft_text ? <p>{t('work.manager.intent.restate')}</p> : null}
          {intent.draft_text ? <blockquote>{intent.draft_text}</blockquote> : null}
        </div>
      ) : null}
      {intent?.status === 'ready_to_confirm' ? (
        <div className="team-intent-commit" data-status="ready_to_confirm">
          <div className="team-intent-commit__heading">
            <strong>{t('work.manager.intent.understanding')}</strong>
            <StatusBadge tone="warning">{t('work.manager.intent.notCommitted')}</StatusBadge>
          </div>
          {interpretationItems.length ? (
            <ol className="team-intent-commit__interpretation">
              {interpretationItems.map((item, index) => (
                <li key={`${item.kind || 'request'}:${index}`}>{item.text}</li>
              ))}
            </ol>
          ) : (
            <p>{t('work.manager.intent.unsafeInterpretation')}</p>
          )}
          <p className="team-intent-commit__boundary">{t('work.manager.intent.confirmBoundary')}</p>
          <div className="team-intent-commit__actions">
            <Button
              busy={busy === 'intent-confirm'}
              disabled={!interpretationItems.length}
              onClick={confirmChangeDiscussion}
            >
              {t('work.manager.intent.confirm')}
            </Button>
            <Button
              busy={busy === 'intent-discuss'}
              variant="secondary"
              onClick={keepDiscussing}
            >
              {t('work.manager.intent.keepDiscussing')}
            </Button>
          </div>
        </div>
      ) : null}
      {intent?.status === 'committing' ? (
        <div className="team-intent-commit" data-status="committing" role="status">
          <strong>{t('work.manager.intent.committing')}</strong>
          <p>{t('work.manager.intent.commitBoundary')}</p>
          <Button busy={busy === 'intent-confirm'} onClick={confirmChangeDiscussion}>
            {t('work.manager.intent.resumeConfirmation')}
          </Button>
        </div>
      ) : null}
      {intent?.status === 'ambiguous' ? (
        <div className="team-intent-commit" data-status="ambiguous" role="alert">
          <div className="team-intent-commit__heading">
            <strong>{t('work.manager.intent.ambiguousTitle')}</strong>
            <StatusBadge tone="danger">{t('work.manager.intent.ambiguous')}</StatusBadge>
          </div>
          <p>{t('work.manager.intent.ambiguousDescription')}</p>
        </div>
      ) : null}
      {intent?.status === 'stale' ? (
        <div className="team-intent-commit" data-status="stale" role="alert">
          <div className="team-intent-commit__heading">
            <strong>{t('work.manager.intent.staleTitle')}</strong>
            <StatusBadge tone="danger">{t('work.manager.intent.stale')}</StatusBadge>
          </div>
          <p>{t('work.manager.intent.staleDescription')}</p>
          <Button busy={busy === 'intent-start'} onClick={startChangeDiscussion}>
            {t('work.manager.intent.restart')}
          </Button>
        </div>
      ) : null}
    </section>
  );
}

export function ProjectPreparationAttention({ attention, busy, onContinue, t }) {
  const requirements = attention.input_requirements || {};
  const commands = requirements.commands || [];
  const [selectedCommands, setSelectedCommands] = useState(() => commands.map((item) => item.id));
  const [goal, setGoal] = useState('');
  const [brief, setBrief] = useState('');
  const kind = requirements.kind || 'project_preparation';
  const submit = () => {
    let values = { confirmed: true };
    if (kind === 'command_confirmation') values = { confirmed_command_ids: selectedCommands };
    if (kind === 'project_initialization') values = { goal: goal.trim(), brief: brief.trim() };
    onContinue?.(attention.step_id, values);
  };
  const canContinue = kind !== 'project_initialization' || (goal.trim() && brief.trim());
  return (
    <section className="team-attention" data-attention-id={attention.id} data-kind="project_preparation" aria-label={t('work.manager.attention')}>
      <p className="team-attention__label"><AlertCircle aria-hidden="true" size={15} />{t('work.taskAssistant.preparationTitle')}</p>
      <h3>{t(`work.taskAssistant.preparation.${kind}`)}</h3>
      <PreparationFacts requirements={requirements} t={t} />
      {kind === 'command_confirmation' ? (
        <div className="team-preparation-options">
          {commands.map((command) => (
            <label key={command.id}>
              <input
                checked={selectedCommands.includes(command.id)}
                onChange={(event) => setSelectedCommands((current) => (
                  event.target.checked ? [...current, command.id] : current.filter((id) => id !== command.id)
                ))}
                type="checkbox"
              />
              <code>{command.command}</code>
            </label>
          ))}
        </div>
      ) : null}
      {kind === 'project_initialization' ? (
        <div className="team-preparation-fields">
          <label>{t('work.taskAssistant.projectGoal')}<input value={goal} onChange={(event) => setGoal(event.target.value)} /></label>
          <label>{t('work.taskAssistant.projectBrief')}<textarea value={brief} onChange={(event) => setBrief(event.target.value)} /></label>
        </div>
      ) : null}
      <Button busy={busy === 'preparation'} disabled={!canContinue} onClick={submit}>
        {t('work.taskAssistant.continuePreparation')}
      </Button>
    </section>
  );
}

export function TaskRunControls({ busy, canCancel, onTerminate, t }) {
  const [confirmCancel, setConfirmCancel] = useState(false);
  if (!canCancel) return null;
  return (
    <div className="team-manager-controls">
      {!confirmCancel ? (
        <button type="button" onClick={() => setConfirmCancel(true)}>{t('work.manager.archiveTask')}</button>
      ) : (
        <div className="team-attention__confirm" role="group" aria-label={t('work.manager.archiveTask')}>
          <p>{t('work.manager.archiveExplanation')}</p>
          <div>
            <Button busy={busy === 'terminate'} variant="danger" onClick={onTerminate}>{t('work.manager.archiveTask')}</Button>
            <Button variant="secondary" onClick={() => setConfirmCancel(false)}>{t('work.manager.keepWorking')}</Button>
          </div>
        </div>
      )}
    </div>
  );
}
