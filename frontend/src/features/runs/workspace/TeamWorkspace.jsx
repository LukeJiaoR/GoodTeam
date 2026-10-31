import ProjectConversation from './ProjectConversation';
import { TeamAvatar } from './ConversationTimeline';
import { TaskAttention, TaskRunControls } from './TaskAttention';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import {
  ChevronRight,
  FileCheck2,
  ExternalLink,
  PanelRightClose,
  PanelRightOpen,
  Wrench,
} from 'lucide-react';
import StatusBadge from '../../../components/ui/StatusBadge';
import {
  statusText,
  tone,
  translated,
  WORK_VIEWS,
} from './presentationUtils';
import WorkTaskDetailDialog from './WorkTaskDetailDialog';
import ManagerMissionEntry from './ConversationComposer';
import { conversationTimeline } from './conversationFeedback';
import DeliverySummary from './DeliverySummary';
import SkillReviewExecutions from './SkillReviewExecutions';
import { executionEvidenceHref } from './executionEvidence';

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


function ExecutionPane({ actors, blocker, controls, context, task, onInspectAttempt, reducedMotion, sectionProps, t }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <section className="team-pane team-pane--execution" aria-label={t('work.team.workerStatus')} {...sectionProps}>
      <details className="team-execution-disclosure" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
        <summary className="team-pane__header">
          <span className="team-execution-heading">
            <strong id="team-execution-title">{t('work.team.workerStatus')}</strong>
            <span className="team-pane__eyebrow">{t('work.team.workerCount', { count: actors.length })}</span>
          </span>
          <span className="team-execution-heading__status">
            {task?.status ? <StatusBadge tone={tone(task.status)}>{statusText(task.status, t)}</StatusBadge> : null}
            <ChevronRight aria-hidden="true" size={16} />
          </span>
        </summary>
        <div className="team-execution-body">
      {task?.objective && task.objective !== task.title ? <p className="project-task-identity">{task.objective}</p> : null}
      {blocker}
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
                {onInspectAttempt && actor.current_attempt_id ? <button
                  type="button" className="team-attempt-inspect"
                  aria-label={`${t('work.attempt.inspect')}: ${actor.responsibility || role}`}
                  onClick={() => onInspectAttempt(actor)}
                >{t('work.attempt.inspect')}<ChevronRight size={14} aria-hidden="true" /></button> : null}
              </li>
            );
          })}
        </ol>
      ) : <p className="team-pane__empty">{t('work.team.executionEmpty')}</p>}
        </div>
      </details>
      {controls}
      {context}
    </section>
  );
}

function OutcomesPane({
  navigation,
  historyContext,
  approval,
  mission,
  reviews,
  historical,
  deliverySummary,
  language,
  activeReviewTaskId,
  attentionTaskId,
  expandedTaskId,
  onOpenTask,
  onRegisterOutcome,
  onToggleTask,
  outcomes,
  sectionProps,
  t,
}) {
  const evidenceHref = executionEvidenceHref(mission);
  return (
    <section className="team-pane team-pane--outcomes" aria-label={t('work.team.outcomes')} {...sectionProps}>
      <header className="team-pane__header">
        <div>
          <h2 id="team-outcomes-title">{t('work.team.historyDelivery')}</h2>
          <span className="team-pane__eyebrow">{t('work.team.resultsHelp')}</span>
        </div>
        {outcomes.length ? <span className="team-pane__count">{t('work.team.outcomeCount', { count: outcomes.length })}</span> : null}
      </header>
      <div className="team-outcomes-scroll">
      {navigation}
      {historyContext}
      {approval}
      <section className="team-execution-evidence" aria-label={t('work.evidence.title')}>
        <h3>{t('work.evidence.title')}</h3>
        {evidenceHref ? <>
          <a href={evidenceHref}>{t('work.evidence.open')}<ExternalLink size={14} aria-hidden="true" /></a>
          <p>{t('work.evidence.readOnly')}</p>
        </> : <p>{t('work.evidence.unavailable')}</p>}
      </section>
      <DeliverySummary summary={deliverySummary} historical={historical} language={language} t={t} />
      {outcomes.length ? (
        <ol className="team-outcome-list">
          {outcomes.map((task, index) => {
            const isAttentionTarget = task.id === attentionTaskId;
            const isActiveReviewTarget = task.id === activeReviewTaskId;
            const isExpanded = task.id === expandedTaskId;
            const owner = translated(t, `work.plan.role.${task.owner}`, task.owner);
            const detailsId = `team-outcome-details-${index}`;
            return (
              <li
                key={task.id}
                ref={(node) => onRegisterOutcome(task.id, node)}
                data-active-review={isActiveReviewTarget ? 'true' : undefined}
                data-attention={isAttentionTarget ? 'true' : undefined}
                data-expanded={isExpanded ? 'true' : 'false'}
                data-state={task.status}
                data-task-id={task.id}
              >
                <button
                  aria-controls={detailsId}
                  aria-expanded={isExpanded}
                  aria-label={task.objective}
                  className="team-outcome-card__toggle"
                  type="button"
                  aria-pressed={isActiveReviewTarget}
                  onClick={() => onToggleTask(task)}
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
                  <ChevronRight aria-hidden="true" className="team-outcome-card__chevron" size={16} />
                </button>
                <div className="team-outcome-card__details" id={detailsId} hidden={!isExpanded}>
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
                  <button
                    className="team-outcome-card__details-button"
                    onClick={(event) => onOpenTask(task, event.currentTarget)}
                    type="button"
                  >
                    {t('work.plan.taskDetails')}
                  </button>
                </div>
                {isExpanded && task.primary_outcome?.href ? (
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
      ) : !deliverySummary ? <p className="team-pane__empty">{t('work.team.noResults')}</p> : null}
      {reviews}
      </div>
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
  onConfigureRuntime,
  onOpenReview,
  onInspectAttempt,
  conversationDelivery = null,
  projectAssistant,
  assistantBusy,
  assistantError,
  taskNavigation,
  executionContext,
  projectTools,
  historyContext,
  taskManagementOpen,
  onManageTasks,
  contextTitle,
  onClearContext,
  onQueueTask,
  onAssistantAction,
  busy,
  data,
  error,
  interactionsDisabled = false,
  inspectorContent,
  language,
  onCloseInspector,
  onConfirmChangeIntent,
  onDecide,
  onDiscussChangeIntent,
  onInspect,
  onKeepDiscussingChangeIntent,
  onRetry,
  retry,
  onConfirmTask,
  onContinuePreparation,
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
  const [collapsedEvidence, setCollapsedEvidence] = useState(null);
  const [expandedTaskId, setExpandedTaskId] = useState('');
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [selectedReviewTargetId, setSelectedReviewTargetId] = useState('');
  const [activeReviewTaskId, setActiveReviewTaskId] = useState('');
  const mobileTabRefs = useRef(new Map());
  const outcomeRefs = useRef(new Map());
  const returnFocusRef = useRef(null);
  const workspace = data.team_workspace;
  const mission = workspace.mission;
  const assistant = projectAssistant || workspace;
  const readOnly = Boolean(mission?.read_only);
  const outcomes = workspace.outcome_tasks || [];
  const selectedTask = outcomes.find((task) => task.id === selectedTaskId) || null;
  const attention = workspace.attention_request;
  const evidenceContext = `${projectKey}:${mission?.run_id || mission?.id || ''}:${attention?.target_id || ''}`;
  const evidenceHidden = !mobileWorkspace && collapsedEvidence === evidenceContext;
  const activeAttemptIds = new Set((workspace.execution_actors || []).filter(actor => actor.role !== 'orchestrator').map(actor => actor.current_attempt_id));
  const activeJobs = (data.worker_jobs || []).filter(job => activeAttemptIds.has(job.id) && job.status === 'running');
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
  const toggleOutcome = useCallback((task) => {
    setExpandedTaskId((current) => (current === task.id ? '' : task.id));
  }, []);
  const reviewAttentionOutcome = useCallback((trigger) => {
    if (!attentionReviewTask) return;
    returnFocusRef.current = trigger || document.activeElement;
    setActiveReviewTaskId(attentionReviewTask.id);
    setCollapsedEvidence(null);
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
    'data-read-only': readOnly ? 'true' : undefined,
    ...(!mobileWorkspace && tab === 'outcomes' ? { hidden: evidenceHidden } : {}),
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
    { id: 'execution', label: t('work.team.workerStatus') },
    { id: 'manager', label: t('work.team.manager') },
    { id: 'outcomes', label: t('work.team.historyDelivery') },
  ];
  return (
    <>
      <div className="studio-work-toolbar">
        <span>{t('product.studio.focusConversation')}</span>
        <div className="studio-work-toolbar__actions">
          {projectTools ? <details className="studio-project-tools" onKeyDown={event => {
            if (event.key !== 'Escape') return;
            event.stopPropagation();
            event.currentTarget.open = false;
            event.currentTarget.querySelector('summary')?.focus();
          }}>
            <summary><Wrench size={16} aria-hidden="true" />{t('product.studio.projectTools')}</summary>
            <div className="studio-project-tools__panel">{projectTools}</div>
          </details> : null}
          <button type="button" aria-controls="team-panel-outcomes" aria-expanded={!evidenceHidden} onClick={() => setCollapsedEvidence(evidenceHidden ? null : evidenceContext)}>{evidenceHidden ? <PanelRightOpen size={16} aria-hidden="true" /> : <PanelRightClose size={16} aria-hidden="true" />}{t(evidenceHidden ? 'product.studio.showEvidence' : 'product.studio.hideEvidence')}</button>
        </div>
      </div>
      <div
        aria-label={t('work.team.navigation')}
        aria-orientation="horizontal"
        className="team-mobile-nav"
        role="tablist"
      >
        {mobileTabs.map((tab) => {
          const selected = mobileTab === tab.id;
          const hasAttention = tab.id === (attention?.kind === 'blocker' ? 'execution' : attention?.kind === 'approval' ? 'outcomes' : 'manager') && Boolean(attention);
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
      <div
        className="team-workspace"
        aria-label={t('work.team.workspace')}
        data-evidence-collapsed={evidenceHidden ? 'true' : undefined}
        data-read-only={readOnly ? 'true' : undefined}
      >
        <div className="team-workspace-center">
        <ProjectConversation
          executionRunning={activeJobs.length > 0}
          canManageTasks={true}
          taskManagementOpen={!mobileWorkspace || taskManagementOpen}
          onManageTasks={() => { setCollapsedEvidence(null); if (mobileWorkspace) { setMobileTab('outcomes'); onManageTasks?.(); } }}
          onConfigureRuntime={onConfigureRuntime}
          acceptedMessage={conversationDelivery && conversationDelivery.projectKey === projectKey && !conversationDelivery.intentId
            && (conversationDelivery.contextKey || '').startsWith('message:')
            && (assistant.timeline || workspace.manager_timeline || []).some(item => item.actor === 'user' && (item.id === conversationDelivery.id || item.message_id === conversationDelivery.id))
            ? conversationDelivery : null}
          assistantState={assistant}
          onAssistantAction={onAssistantAction}
          attention={workspace.attention_request}
          busy={busy}
          error={assistantError || error}
          assistantBusy={assistantBusy ?? busy}
          language={language}
          mission={mission}
          onConfirmChangeIntent={onConfirmChangeIntent}
          onDecide={onDecide}
          onDiscussChangeIntent={onDiscussChangeIntent}
          onKeepDiscussingChangeIntent={onKeepDiscussingChangeIntent}
          onReview={attentionReviewTask ? reviewAttentionOutcome : onOpenReview && attention?.target_id ? () => onOpenReview(attention.target_id) : undefined}
          onRetry={onRetry}
          retry={retry}
          onConfirmTask={onConfirmTask}
          onContinuePreparation={onContinuePreparation}
          onStart={onStart}
          onStartChangeIntent={onStartChangeIntent}
          projectKey={projectKey}
          sectionProps={sectionProps('manager')}
          timeline={conversationTimeline(assistant.timeline || workspace.manager_timeline || [], conversationDelivery, {
            projectKey, runId: projectAssistant ? '' : data.state?.run_id || '', readOnly: readOnly && !onStart,
          })}
          t={t}
          suggestions={assistant.suggestions || []}
          proposedTask={assistant.proposed_task || null}
          iterationBacklog={data.iteration_backlog || null}
          canPlanIteration={!readOnly && !data.active_run && (!assistant.proposed_task || assistant.proposed_task.status === 'cancelled')}
          onQueueTask={onQueueTask}
          contextTitle={contextTitle}
          onClearContext={onClearContext}
        />
        </div>
        <ExecutionPane
          onInspectAttempt={onInspectAttempt}
          blocker={attention?.kind === 'blocker' ? <TaskAttention attention={attention} activeJobs={activeJobs} error={error} jobs={data.worker_jobs || []} projectKey={projectKey} busy={busy} onRetry={readOnly ? undefined : onRetry} retry={retry} t={t} /> : null}
          task={mission}
          context={executionContext}
          controls={!readOnly && (workspace.capabilities || []).some((item) => item.action === 'cancel_mission')
            ? <TaskRunControls key={mission?.run_id || mission?.id} busy={busy} canCancel onTerminate={onTerminate} t={t} /> : null}
          actors={(workspace.execution_actors || []).filter((actor) => actor.role !== 'orchestrator')}
          reducedMotion={reducedMotion}
          sectionProps={sectionProps('execution')}
          t={t}
        />
        <OutcomesPane
          navigation={taskNavigation}
          historyContext={historyContext}
          mission={mission}
          reviews={<SkillReviewExecutions projectKey={projectKey} runId={mission?.run_id} language={language} />}
          approval={attention?.kind === 'approval' ? <TaskAttention
            attention={attention} busy={busy} onConfirmChangeIntent={onConfirmChangeIntent}
            onDecide={readOnly ? undefined : onDecide} onKeepDiscussingChangeIntent={onKeepDiscussingChangeIntent}
            onReview={attentionReviewTask ? reviewAttentionOutcome : onOpenReview && attention?.target_id ? () => onOpenReview(attention.target_id) : undefined}
            onStartChangeIntent={onStartChangeIntent} projectKey={projectKey} t={t}
          /> : null}
          historical={readOnly}
          deliverySummary={data.delivery_summary}
          language={language}
          activeReviewTaskId={activeReviewTargetId}
          attentionTaskId={attentionReviewTask?.id || ''}
          expandedTaskId={expandedTaskId}
          onOpenTask={openOutcome}
          onRegisterOutcome={registerOutcome}
          onToggleTask={toggleOutcome}
          outcomes={outcomes}
          sectionProps={sectionProps('outcomes')}
          t={t}
        />
      </div>
      {taskForModal && !interactionsDisabled ? (
        <WorkTaskDetailDialog
          language={language}
          onOpenReview={onOpenReview ? (approval) => { closeTaskModal(); onOpenReview(approval); } : undefined}
          task={taskForModal}
          tasks={outcomes}
          reviews={readOnly ? (data.approval_reviews || []).map((review) => ({ ...review, is_actionable: false })) : data.approval_reviews || []}
          projectKey={projectKey}
          onDecide={readOnly ? undefined : onDecide}
          onClose={closeTaskModal}
          onRequestChanges={!readOnly && activeReviewTargetId && attention?.approval_brief ? async (_approval, _target, note) => {
            await onStartChangeIntent?.(
              attention.target_id,
              attention.approval_brief.approval_target,
              note,
            );
            setMobileTab('manager');
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
