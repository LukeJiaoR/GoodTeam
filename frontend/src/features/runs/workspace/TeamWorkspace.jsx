import { useCallback, useEffect, useId, useRef, useState } from 'react';
import {
  AlertCircle,
  Bot,
  ChevronRight,
  FileCheck2,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import Button from '../../../components/ui/Button';
import ProductError from '../../../components/ui/ProductError';
import StatusBadge from '../../../components/ui/StatusBadge';
import { routes } from '../../../lib/routing/routes';
import ManagerApprovalBrief from './ManagerApprovalBrief';
import {
  reviewGateName,
  statusText,
  tone,
  translated,
  WORK_VIEWS,
} from './presentationUtils';
import WorkTaskDetailDialog from './WorkTaskDetailDialog';

const MOBILE_TEAM_TABS = ['execution', 'manager', 'outcomes'];
const MOBILE_TEAM_QUERY = '(max-width: 767px)';

function useMobileTeamWorkspace() {
  const [mobile, setMobile] = useState(() => (
    globalThis.matchMedia?.(MOBILE_TEAM_QUERY)?.matches || false
  ));
  useEffect(() => {
    const mq = globalThis.matchMedia?.(MOBILE_TEAM_QUERY);
    if (!mq) return undefined;
    const handler = (event) => setMobile(event.matches);
    mq.addEventListener?.('change', handler);
    return () => mq.removeEventListener?.('change', handler);
  }, []);
  return mobile;
}

function executionStatusReason(actor, t) {
  if (actor.state === 'waiting' && actor.dependency_task_title) {
    return translated(
      t,
      'work.team.reasons.waiting_for_dependency',
      `Waiting for “${actor.dependency_task_title}” to complete.`,
      { dependency: actor.dependency_task_title },
    );
  }
  const reason = actor.status_reason || actor.dependency_reason;
  return reason ? translated(t, `work.team.reasons.${reason}`, reason) : '';
}

function TeamAvatar({ label, manager = false }) {
  const initial = String(label || '').trim().slice(0, 1).toUpperCase() || 'G';
  return (
    <span aria-hidden="true" className={`team-avatar${manager ? ' team-avatar--manager' : ''}`}>
      {manager ? <Bot size={18} strokeWidth={1.9} /> : initial}
    </span>
  );
}

function ExecutionStatusIndicator({ actor, reducedMotion, t }) {
  const [tooltipOpen, setTooltipOpen] = useState(false);
  const tooltipId = useId();
  const status = statusText(actor.state, t);
  const reason = executionStatusReason(actor, t);
  const label = reason
    ? translated(
      t,
      'work.team.executionStatusLabel',
      `${status}. ${reason}`,
      { status, reason },
    )
    : status;
  const ambient = ['working', 'reviewing', 'fixing'].includes(actor.state) && !reducedMotion;

  return (
    <span
      aria-describedby={tooltipOpen ? tooltipId : undefined}
      aria-label={label}
      className="team-status-led-trigger"
      data-motion={ambient ? 'ambient' : 'static'}
      data-state={actor.state || 'up_next'}
      onBlur={() => setTooltipOpen(false)}
      onFocus={() => setTooltipOpen(true)}
      onMouseEnter={() => setTooltipOpen(true)}
      onMouseLeave={() => setTooltipOpen(false)}
      role="img"
      tabIndex={0}
    >
      <span aria-hidden="true" className="team-status-led" />
      <span
        aria-hidden={!tooltipOpen}
        className="team-status-led-tooltip"
        data-open={tooltipOpen ? 'true' : 'false'}
        id={tooltipId}
        role="tooltip"
      >
        <strong>{status}</strong>
        {reason ? <span>{reason}</span> : null}
      </span>
    </span>
  );
}


function ManagerMissionEntry({ busy, data, embedded = true, error, onStart, t }) {
  const [goal, setGoal] = useState('');
  const submit = (event) => {
    event.preventDefault();
    if (goal.trim()) onStart(goal.trim());
  };
  return (
    <form className={embedded ? 'team-manager-entry' : 'work-start-panel'} onSubmit={submit}>
      <div>
        <p className="product-eyebrow">{t('work.start.eyebrow')}</p>
        {embedded ? <h3>{t('work.start.title')}</h3> : <h2>{t('work.start.title')}</h2>}
        <p>{t('work.start.description')}</p>
      </div>
      <label>
        <span>{t('work.start.goal')}</span>
        <textarea rows="4" required value={goal} onChange={(event) => setGoal(event.target.value)} />
      </label>
      {error ? <ProductError error={error} t={t} /> : null}
      <Button busy={busy} disabled={!goal.trim()} type="submit">{t('work.start.action')}</Button>
      <p className="product-boundary-note">
        {t('work.start.boundary', { profile: data?.start_context?.model_profile || 'balanced' })}
      </p>
    </form>
  );
}


function timelineCopy(item, t) {
  if (item.actor === 'user' && item.event === 'mission_requested') return item.content;
  const details = item.details || {};
  return translated(
    t,
    `work.manager.events.${item.event}`,
    item.content || translated(t, 'work.manager.events.progress', 'The team state changed.'),
    {
      artifact: details.artifact || t('work.manager.deliverable'),
      decision: details.decision ? statusText(details.decision, t) : '',
      gate: details.gate ? reviewGateName(details.gate, t) : '',
      taskCount: details.task_count || 0,
    },
  );
}

function TimelineItem({ item, language, t }) {
  const timestamp = Date.parse(item.timestamp);
  const formatted = Number.isFinite(timestamp)
    ? new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(timestamp))
    : '';
  const actorLabel = item.actor === 'user' ? t('work.manager.you') : t('work.manager.name');
  const copy = timelineCopy(item, t);
  return (
    <li className="team-manager-event" data-actor={item.actor} data-kind={item.kind}>
      <TeamAvatar label={actorLabel} manager={item.actor !== 'user'} />
      <div className="team-manager-event__bubble">
        <div className="team-manager-event__meta">
          <strong>{actorLabel}</strong>
          {formatted ? <time dateTime={item.timestamp}>{formatted}</time> : null}
        </div>
        <p>{copy}</p>
        {item.content && item.event !== 'mission_requested' && copy !== item.content ? (
          <blockquote>{item.content}</blockquote>
        ) : null}
      </div>
    </li>
  );
}

function ManagerAttention({
  attention,
  busy,
  onConfirmChangeIntent,
  onDecide,
  onDiscussChangeIntent,
  onKeepDiscussingChangeIntent,
  onReview,
  onRetry,
  onStartChangeIntent,
  projectKey,
  t,
}) {
  const intent = attention?.intent_commit || null;
  const [note, setNote] = useState(intent?.draft_text || '');
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- durable intent state replaces any draft from the prior authoritative projection.
    setNote(intent?.draft_text || '');
  }, [intent?.draft_text, intent?.id]);
  if (!attention) return null;
  const actions = new Set(attention.actions || []);
  const gate = attention.target_id || '';
  const approvalBrief = attention.approval_brief?.schema_version === 'manager_approval_brief.v1'
    ? attention.approval_brief
    : null;
  const decide = (decision, decisionNote = '') => {
    Promise.resolve()
      .then(() => onDecide?.(
        gate,
        decision,
        decisionNote,
        decision === 'approved' ? approvalBrief?.approval_target : null,
      ))
      .catch(() => {});
  };
  const submitChanges = (event) => {
    event.preventDefault();
    if (!intent?.id || !note.trim()) return;
    Promise.resolve()
      .then(() => onDiscussChangeIntent?.(intent.id, note.trim()))
      .catch(() => {});
  };
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
    <section className="team-attention" data-kind={attention.kind} aria-label={t('work.manager.attention')}>
      <p className="team-attention__label">
        <AlertCircle aria-hidden="true" size={15} strokeWidth={2} />
        <span>{t('work.manager.attention')}</span>
      </p>
      <h3 id="team-attention-title">
        {translated(t, `work.manager.attentionKinds.${attention.kind}`, attention.reason)}
      </h3>
      {approvalBrief ? (
        <ManagerApprovalBrief brief={approvalBrief} t={t} />
      ) : (
        <p>{translated(t, `work.manager.attentionReasons.${attention.reason}`, attention.reason)}</p>
      )}
      <div className="team-attention__actions">
        {!intent && actions.has('approve') ? (
          <Button busy={busy === 'decision'} onClick={() => decide('approved')}>
            {approvalBrief
              ? t('work.manager.approvalBrief.approve')
              : t('work.review.quickApprove')}
          </Button>
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
          <Button busy={busy === 'retry'} onClick={onRetry}>{t('work.actions.retryBlocked')}</Button>
        ) : null}
      </div>
      {intent?.status === 'discussing' ? (
        <div className="team-intent-commit" data-status="discussing">
          <div className="team-intent-commit__heading">
            <strong>{t('work.manager.intent.question')}</strong>
            <StatusBadge tone="pending">{t('work.manager.intent.discussing')}</StatusBadge>
          </div>
          <p>{t('work.manager.intent.restate')}</p>
          <form className="team-attention__form" onSubmit={submitChanges}>
            <label>
              <span>{t('work.manager.changeReason')}</span>
              <textarea required rows="4" value={note} onChange={(event) => setNote(event.target.value)} />
            </label>
            <div>
              <Button
                busy={busy === 'intent-discuss'}
                disabled={!note.trim()}
                type="submit"
              >
                {t('work.manager.intent.reviewInterpretation')}
              </Button>
            </div>
          </form>
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

function ManagerMissionControls({ busy, canCancel, onTerminate, t }) {
  const [confirmCancel, setConfirmCancel] = useState(false);
  if (!canCancel) return null;
  return (
    <div className="team-manager-controls">
      {!confirmCancel ? (
        <button type="button" onClick={() => setConfirmCancel(true)}>{t('work.manager.cancelMission')}</button>
      ) : (
        <div className="team-attention__confirm" role="group" aria-label={t('work.manager.cancelMission')}>
          <p>{t('work.manager.cancelConfirm')}</p>
          <div>
            <Button busy={busy === 'terminate'} variant="danger" onClick={onTerminate}>{t('work.actions.terminate')}</Button>
            <Button variant="secondary" onClick={() => setConfirmCancel(false)}>{t('work.manager.keepWorking')}</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function ManagerPane({
  attention,
  busy,
  capabilities,
  data,
  error,
  language,
  mission,
  onConfirmChangeIntent,
  onDecide,
  onDiscussChangeIntent,
  onKeepDiscussingChangeIntent,
  onReview,
  onRetry,
  onStart,
  onStartChangeIntent,
  onTerminate,
  projectKey,
  sectionProps,
  timeline,
  t,
}) {
  const managerTone = attention?.kind === 'blocker'
    ? 'danger'
    : attention
      ? 'active'
      : mission
        ? tone(mission.status)
        : 'neutral';
  const managerStatus = attention
    ? t('work.manager.needsYou')
    : mission
      ? statusText(mission.status, t)
      : t('work.manager.ready');
  const timelineHasMissionRequest = (timeline || []).some((item) => item.event === 'mission_requested');
  return (
    <section className="team-pane team-pane--manager" aria-label={t('work.manager.name')} {...sectionProps}>
      <header className="team-pane__header">
        <div className="team-manager-heading">
          <TeamAvatar label={t('work.manager.name')} manager />
          <div>
            <div className="team-manager-heading__title">
              <h2 id="team-manager-title">{t('work.manager.name')}</h2>
              <StatusBadge tone={managerTone}>{managerStatus}</StatusBadge>
            </div>
            <span className="team-pane__eyebrow">{t('work.manager.subtitle')}</span>
          </div>
        </div>
      </header>
      <div className="team-manager-conversation">
        {mission ? (
          <>
            {!timelineHasMissionRequest ? (
              <p className="team-manager-objective">
                <span>{t('work.manager.currentMission')}</span>
                <strong>{mission.objective}</strong>
              </p>
            ) : null}
            {error ? <ProductError error={error} t={t} /> : null}
            <ol className="team-manager-timeline" aria-label={t('work.manager.history')}>
              {(timeline || []).map((item) => <TimelineItem item={item} key={item.id} language={language} t={t} />)}
            </ol>
            <ManagerAttention
              attention={attention}
              busy={busy}
              key={`${attention?.id || 'none'}:${attention?.intent_commit?.id || 'direct'}:${attention?.intent_commit?.status || 'idle'}`}
              onConfirmChangeIntent={onConfirmChangeIntent}
              onDecide={onDecide}
              onDiscussChangeIntent={onDiscussChangeIntent}
              onKeepDiscussingChangeIntent={onKeepDiscussingChangeIntent}
              onReview={onReview}
              onRetry={onRetry}
              onStartChangeIntent={onStartChangeIntent}
              projectKey={projectKey}
              t={t}
            />
          </>
        ) : (
          <ManagerMissionEntry
            busy={busy === 'start'}
            data={data}
            error={error}
            onStart={onStart}
            t={t}
          />
        )}
      </div>
      {mission && (capabilities || []).some((item) => item.action === 'cancel_mission') ? (
        <footer className="team-manager-dock">
          <ManagerMissionControls
            busy={busy}
            canCancel
            onTerminate={onTerminate}
            t={t}
          />
        </footer>
      ) : null}
    </section>
  );
}

function ExecutionPane({ actors, reducedMotion, sectionProps, t }) {
  return (
    <section className="team-pane team-pane--execution" aria-label={t('work.team.execution')} {...sectionProps}>
      <header className="team-pane__header">
        <div>
          <h2 id="team-execution-title">{t('work.team.execution')}</h2>
          <span className="team-pane__eyebrow">{t('work.team.liveExecution')}</span>
        </div>
        <span className="team-pane__count">{actors.length}</span>
      </header>
      {actors.length ? (
        <ol className="team-actor-list">
          {actors.map((actor) => {
            const role = translated(t, `work.plan.role.${actor.role}`, actor.role);
            const reason = executionStatusReason(actor, t);
            return (
              <li key={actor.id} data-state={actor.state}>
                <div className="team-actor-list__summary">
                  <TeamAvatar label={role} />
                  <div className="team-actor-list__copy">
                    <div className="team-actor-list__identity">
                      <strong>{role}</strong>
                      <span className="team-actor-list__state">
                        <ExecutionStatusIndicator actor={actor} reducedMotion={reducedMotion} t={t} />
                        <span>{statusText(actor.state, t)}</span>
                      </span>
                    </div>
                    <p>{actor.responsibility || t('work.team.noResponsibility')}</p>
                  </div>
                </div>
                {reason ? <p className="team-actor-list__reason">{reason}</p> : null}
              </li>
            );
          })}
        </ol>
      ) : <p className="team-pane__empty">{t('work.team.executionEmpty')}</p>}
    </section>
  );
}

function OutcomesPane({
  activeReviewTaskId,
  attentionTaskId,
  onRegisterOutcome,
  onSelectTask,
  outcomes,
  sectionProps,
  t,
}) {
  return (
    <section className="team-pane team-pane--outcomes" aria-label={t('work.team.outcomes')} {...sectionProps}>
      <header className="team-pane__header">
        <div>
          <h2 id="team-outcomes-title">{t('work.team.deliveryBoard')}</h2>
          <span className="team-pane__eyebrow">{t('work.team.convergence')}</span>
        </div>
        <span className="team-pane__count">{outcomes.length}</span>
      </header>
      {outcomes.length ? (
        <ol className="team-outcome-list">
          {outcomes.map((task, index) => {
            const isAttentionTarget = task.id === attentionTaskId;
            const isActiveReviewTarget = task.id === activeReviewTaskId;
            const owner = translated(t, `work.plan.role.${task.owner}`, task.owner);
            return (
              <li
                key={task.id}
                ref={(node) => onRegisterOutcome(task.id, node)}
                data-active-review={isActiveReviewTarget ? 'true' : undefined}
                data-attention={isAttentionTarget ? 'true' : undefined}
                data-state={task.status}
                data-task-id={task.id}
              >
                <button
                  type="button"
                  aria-pressed={isActiveReviewTarget}
                  onClick={(event) => onSelectTask(task, event.currentTarget)}
                >
                  <span className="team-outcome-card__heading">
                    <span className="team-outcome-card__index">{index + 1}</span>
                    <span className="team-outcome-list__title">
                      <strong>{task.objective}</strong>
                      <small>{task.id}</small>
                    </span>
                    <StatusBadge tone={tone(task.status)}>{statusText(task.status, t)}</StatusBadge>
                  </span>
                  <span className="team-outcome-card__owner">
                    <TeamAvatar label={owner} />
                    <span>
                      <small>{t('work.team.owner')}</small>
                      <strong>{owner}</strong>
                    </span>
                  </span>
                  {isAttentionTarget ? (
                    <span className="team-outcome-list__attention">{t('work.team.needsReview')}</span>
                  ) : null}
                  <span className="team-outcome-list__reason">{translated(t, `work.team.reasons.${task.status_reason}`, task.status_reason)}</span>
                  <span className="team-outcome-card__facts">
                    {task.dependencies?.length ? (
                      <span>{t('work.team.dependenciesCount', { count: task.dependencies.length })}</span>
                    ) : <span>{t('work.team.noDependencies')}</span>}
                    {task.acceptance_criteria?.length ? (
                      <span>{t('work.team.acceptanceCount', { count: task.acceptance_criteria.length })}</span>
                    ) : null}
                  </span>
                  {task.primary_outcome ? (
                    <span className="team-outcome-list__deliverable">
                      <FileCheck2 aria-hidden="true" size={14} />
                      <span>{t('work.team.primaryOutcome')}: {task.primary_outcome.label}</span>
                    </span>
                  ) : null}
                  <ChevronRight aria-hidden="true" className="team-outcome-card__chevron" size={16} />
                </button>
                {task.primary_outcome?.href ? (
                  <a
                    className="team-outcome-list__link"
                    href={task.primary_outcome.href}
                    rel="noreferrer"
                    target="_blank"
                  >
                    <span>{t('work.team.openOutcome', { outcome: task.primary_outcome.label })} ↗</span>
                  </a>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : <p className="team-pane__empty">{t('work.team.outcomesEmpty')}</p>}
    </section>
  );
}

function AdvancedInspector({
  children,
  onClose,
  onSelectView,
  reducedMotion,
  t,
  view,
}) {
  const inspectorRef = useRef(null);

  useEffect(() => {
    if (!view || !inspectorRef.current) return;
    inspectorRef.current.scrollIntoView?.({
      behavior: reducedMotion ? 'auto' : 'smooth',
      block: 'start',
    });
    inspectorRef.current.focus({ preventScroll: true });
  }, [reducedMotion, view]);

  if (!view) return null;
  return (
    <section
      className="team-inspector"
      aria-labelledby="team-inspector-title"
      id="team-inspector"
      ref={inspectorRef}
      tabIndex={-1}
    >
      <header>
        <div><span>{t('work.team.inspection')}</span><h2 id="team-inspector-title">{t(`work.views.${view}`)}</h2></div>
        <button type="button" onClick={onClose}>{t('work.team.closeInspection')}</button>
      </header>
      <nav className="team-inspector__tabs" aria-label={t('work.team.detailViews')}>
        {WORK_VIEWS.map((item) => (
          <button
            aria-pressed={view === item}
            data-active={view === item ? 'true' : 'false'}
            key={item}
            onClick={() => onSelectView(item)}
            type="button"
          >
            {t(`work.views.${item}`)}
          </button>
        ))}
      </nav>
      {children}
    </section>
  );
}

function TeamWorkspace({
  busy,
  data,
  error,
  inspectorContent,
  language,
  onCloseInspector,
  onConfirmChangeIntent,
  onDecide,
  onDiscussChangeIntent,
  onInspect,
  onKeepDiscussingChangeIntent,
  onRetry,
  onStart,
  onStartChangeIntent,
  onTerminate,
  projectKey,
  reducedMotion,
  t,
  view,
}) {
  const mobileWorkspace = useMobileTeamWorkspace();
  const [mobileTab, setMobileTab] = useState('manager');
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [selectedReviewTargetId, setSelectedReviewTargetId] = useState('');
  const [activeReviewTaskId, setActiveReviewTaskId] = useState('');
  const mobileTabRefs = useRef(new Map());
  const outcomeRefs = useRef(new Map());
  const returnFocusRef = useRef(null);
  const workspace = data.team_workspace;
  const mission = workspace.mission;
  const outcomes = workspace.outcome_tasks || [];
  const selectedTask = outcomes.find((task) => task.id === selectedTaskId) || null;
  const attention = workspace.attention_request;
  const attentionReviewTask = attention?.target_type === 'review' && attention?.target_id
    ? outcomes.find((task) => (
      task.id === attention.task_id
      && task.review_target_type === 'review'
      && task.review_target_id === attention.target_id
    )) || null
    : null;
  const activeReviewTargetId = activeReviewTaskId === attentionReviewTask?.id
    ? activeReviewTaskId
    : '';
  const taskForModal = selectedTask ? {
    ...selectedTask,
    title: selectedTask.objective,
    role: selectedTask.owner,
    status: selectedTask.status,
    review_target_id: selectedReviewTargetId || selectedTask.review_target_id || '',
    artifact_refs: [
      selectedTask.primary_outcome?.path,
      ...(selectedTask.supporting_refs || []),
    ].filter(Boolean),
  } : null;
  const registerOutcome = useCallback((taskId, node) => {
    if (node) outcomeRefs.current.set(taskId, node);
    else outcomeRefs.current.delete(taskId);
  }, []);
  const openOutcome = useCallback((task, trigger) => {
    returnFocusRef.current = trigger || document.activeElement;
    setActiveReviewTaskId(task.id === attentionReviewTask?.id ? task.id : '');
    setSelectedReviewTargetId(task.review_target_id || '');
    setSelectedTaskId(task.id);
  }, [attentionReviewTask]);
  const reviewAttentionOutcome = useCallback((trigger) => {
    if (!attentionReviewTask) return;
    returnFocusRef.current = trigger || document.activeElement;
    setActiveReviewTaskId(attentionReviewTask.id);
    if (!mobileWorkspace) {
      outcomeRefs.current.get(attentionReviewTask.id)?.scrollIntoView?.({
        behavior: reducedMotion ? 'auto' : 'smooth',
        block: 'nearest',
        inline: 'nearest',
      });
    }
    setSelectedReviewTargetId(attention.target_id);
    setSelectedTaskId(attentionReviewTask.id);
  }, [attention, attentionReviewTask, mobileWorkspace, reducedMotion]);
  const closeTaskModal = useCallback(() => {
    const returnFocus = returnFocusRef.current;
    returnFocusRef.current = null;
    setSelectedTaskId('');
    setSelectedReviewTargetId('');
    returnFocus?.focus?.();
  }, []);
  const handleMobileTabKeyDown = useCallback((event, currentTab) => {
    const currentIndex = MOBILE_TEAM_TABS.indexOf(currentTab);
    let nextIndex;
    if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % MOBILE_TEAM_TABS.length;
    else if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + MOBILE_TEAM_TABS.length) % MOBILE_TEAM_TABS.length;
    else if (event.key === 'Home') nextIndex = 0;
    else if (event.key === 'End') nextIndex = MOBILE_TEAM_TABS.length - 1;
    else return;
    event.preventDefault();
    const nextTab = MOBILE_TEAM_TABS[nextIndex];
    setMobileTab(nextTab);
    mobileTabRefs.current.get(nextTab)?.focus();
  }, []);
  const sectionProps = (tab) => ({
    id: `team-panel-${tab}`,
    'data-mobile-active': mobileTab === tab ? 'true' : 'false',
    ...(mobileWorkspace ? {
      'aria-labelledby': `team-tab-${tab}`,
      hidden: mobileTab !== tab,
      role: 'tabpanel',
    } : {}),
  });
  useEffect(() => {
    if (!mobileWorkspace || mobileTab !== 'outcomes' || !activeReviewTargetId) return;
    outcomeRefs.current.get(activeReviewTargetId)?.scrollIntoView?.({
      behavior: reducedMotion ? 'auto' : 'smooth',
      block: 'nearest',
      inline: 'nearest',
    });
  }, [activeReviewTargetId, mobileTab, mobileWorkspace, reducedMotion]);
  const mobileTabs = [
    { id: 'execution', label: t('work.team.execution') },
    { id: 'manager', label: t('work.team.manager') },
    { id: 'outcomes', label: t('work.team.outcomes') },
  ];
  return (
    <>
      <div
        aria-label={t('work.team.navigation')}
        aria-orientation="horizontal"
        className="team-mobile-nav"
        role="tablist"
      >
        {mobileTabs.map((tab) => {
          const selected = mobileTab === tab.id;
          const hasAttention = tab.id === 'manager' && Boolean(attention);
          return (
            <button
              aria-controls={`team-panel-${tab.id}`}
              aria-label={hasAttention ? `${tab.label}. ${t('work.manager.needsYou')}` : tab.label}
              aria-selected={selected}
              data-active={selected ? 'true' : 'false'}
              id={`team-tab-${tab.id}`}
              key={tab.id}
              onClick={() => setMobileTab(tab.id)}
              onKeyDown={(event) => handleMobileTabKeyDown(event, tab.id)}
              ref={(node) => {
                if (node) mobileTabRefs.current.set(tab.id, node);
                else mobileTabRefs.current.delete(tab.id);
              }}
              role="tab"
              tabIndex={selected ? 0 : -1}
              type="button"
            >
              <span>{tab.label}</span>
              {hasAttention ? (
                <span className="team-mobile-nav__attention">
                  <span className="sr-only">{t('work.manager.needsYou')}</span>
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      <div className="team-workspace" aria-label={t('work.team.workspace')}>
        <ManagerPane
          attention={workspace.attention_request}
          busy={busy}
          capabilities={workspace.capabilities || []}
          data={data}
          error={error}
          language={language}
          mission={mission}
          onConfirmChangeIntent={onConfirmChangeIntent}
          onDecide={onDecide}
          onDiscussChangeIntent={onDiscussChangeIntent}
          onKeepDiscussingChangeIntent={onKeepDiscussingChangeIntent}
          onReview={attentionReviewTask ? reviewAttentionOutcome : undefined}
          onRetry={onRetry}
          onStart={onStart}
          onStartChangeIntent={onStartChangeIntent}
          onTerminate={onTerminate}
          projectKey={projectKey}
          sectionProps={sectionProps('manager')}
          timeline={workspace.manager_timeline || []}
          t={t}
        />
        <ExecutionPane
          actors={workspace.execution_actors || []}
          reducedMotion={reducedMotion}
          sectionProps={sectionProps('execution')}
          t={t}
        />
        <OutcomesPane
          activeReviewTaskId={activeReviewTargetId}
          attentionTaskId={attentionReviewTask?.id || ''}
          onRegisterOutcome={registerOutcome}
          onSelectTask={openOutcome}
          outcomes={outcomes}
          sectionProps={sectionProps('outcomes')}
          t={t}
        />
      </div>
      {taskForModal ? (
        <WorkTaskDetailDialog
          language={language}
          task={taskForModal}
          tasks={outcomes}
          reviews={data.approval_reviews || []}
          projectKey={projectKey}
          onDecide={onDecide}
          onClose={closeTaskModal}
          onRequestChanges={activeReviewTargetId && attention?.approval_brief ? async () => {
            await onStartChangeIntent?.(
              attention.target_id,
              attention.approval_brief.approval_target,
            );
            setMobileTab('manager');
            closeTaskModal();
          } : undefined}
          t={t}
        />
      ) : null}
      <AdvancedInspector
        onClose={onCloseInspector}
        onSelectView={onInspect}
        reducedMotion={reducedMotion}
        t={t}
        view={mission ? view : ''}
      >
        {inspectorContent}
      </AdvancedInspector>
    </>
  );
}


export { ManagerMissionEntry };
export default TeamWorkspace;
