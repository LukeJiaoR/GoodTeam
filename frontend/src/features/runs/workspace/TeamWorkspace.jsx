import ProjectConversation from './ProjectConversation';
import { TeamAvatar } from './ConversationTimeline';
import { TaskAttention, TaskRunControls } from './TaskAttention';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ChevronRight,
  FileCheck2,
  ExternalLink,
  PanelRightClose,
  PanelRightOpen,
  Wrench,
  X,
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
const MOBILE_TEAM_QUERY = '(max-width: 1199px)';

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

function ExecutionPane({ actors, blocker, controls, context, onInspectAttempt, onClose, onSwitchToOutcomes, outcomesCount, reducedMotion, sectionProps, t }) {
  const [selectedId, setSelectedId] = useState('');
  const selected = actors.find(actor => actor.id === selectedId);
  return <section className="team-pane team-pane--execution" aria-label={t('work.team.workerStatus')} {...sectionProps}>
    <header className="team-pane__header">
      {onSwitchToOutcomes ? (
        <div className="team-right-panel-tabs" role="tablist">
          <button type="button" role="tab" aria-selected="true" className="team-right-panel-tab is-active">
            {t('work.team.workerStatus')} {actors.length ? `(${actors.length})` : ''}
          </button>
          <button type="button" role="tab" aria-selected="false" className="team-right-panel-tab" onClick={onSwitchToOutcomes}>
            {t('work.team.historyDelivery')} {outcomesCount ? `(${outcomesCount})` : ''}
          </button>
        </div>
      ) : (
        <div className="team-execution-heading"><strong id="team-execution-title">{t('work.team.workerStatus')}</strong><span className="team-pane__eyebrow">{t('work.team.workerCount', { count: actors.length })}</span></div>
      )}
      {onClose ? <button type="button" className="team-panel-close" aria-label={t('work.navigation.closePanel')} onClick={onClose}><X size={15} /></button> : null}
    </header>
    {blocker}
    {actors.length ? <ol className="team-actor-list">{actors.map(actor => {
      const role = translated(t, `work.plan.role.${actor.role}`, actor.role);
      const reason = executionStatusReason(actor, t);
      return <li key={actor.id} data-state={actor.state}>
        <button type="button" className="team-worker-summary" aria-expanded={selectedId === actor.id} aria-controls="team-worker-detail" onClick={() => setSelectedId(selectedId === actor.id ? '' : actor.id)}>
          <span className="team-worker-dot" data-state={actor.state} data-motion={reducedMotion ? 'static' : 'ambient'} aria-hidden="true" />
          <span className="team-worker-copy"><span className="team-worker-identity"><strong>{role}</strong><span>{statusText(actor.state, t)}</span></span>
            <span className="team-worker-task" title={actor.responsibility}>{actor.responsibility || t('work.team.noResponsibility')}</span>
            {reason ? <span className="team-worker-reason">{reason}</span> : null}
          </span>
        </button>
      </li>;
    })}</ol> : <p className="team-pane__empty">{t('work.team.executionEmpty')}</p>}
    <section id="team-worker-detail" className="team-worker-detail" hidden={!selected}>
      {selected ? <>
        <div className="team-worker-detail-heading"><strong>{translated(t, `work.plan.role.${selected.role}`, selected.role)}</strong><button type="button" className="team-panel-close" aria-label={t('work.navigation.closeWorker')} onClick={() => setSelectedId('')}><X size={14} /></button></div>
        <p>{selected.responsibility || t('work.team.noResponsibility')}</p>
        <dl><dt>{t('work.navigation.state')}</dt><dd>{statusText(selected.state, t)}</dd>
          <dt>{t('work.navigation.assignment')}</dt><dd>{selected.task_id || selected.id}</dd>
          {selected.dependency_task_title ? <><dt>{t('work.navigation.waitingFor')}</dt><dd>{selected.dependency_task_title}</dd></> : null}
        </dl>
        {executionStatusReason(selected, t) ? <p className="team-worker-reason">{executionStatusReason(selected, t)}</p> : null}
        {onInspectAttempt && selected.current_attempt_id ? <button type="button" className="team-attempt-inspect" onClick={() => onInspectAttempt(selected)}>{t('work.attempt.inspect')}<ChevronRight size={14} aria-hidden="true" /></button> : null}
      </> : null}
    </section>
    {controls}
    {context ? <details className="team-execution-context"><summary>{t('work.navigation.executionContext')}</summary>{context}</details> : null}
  </section>;
}

function OutcomesPane({
  navigation,
  onClose,
  onSwitchToExecution,
  actorsCount,
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
        {onSwitchToExecution ? (
          <div className="team-right-panel-tabs" role="tablist">
            <button type="button" role="tab" aria-selected="false" className="team-right-panel-tab" onClick={onSwitchToExecution}>
              {t('work.team.workerStatus')} {actorsCount ? `(${actorsCount})` : ''}
            </button>
            <button type="button" role="tab" aria-selected="true" className="team-right-panel-tab is-active">
              {t('work.team.historyDelivery')} {outcomes.length ? `(${outcomes.length})` : ''}
            </button>
          </div>
        ) : (
          <div>
            <h2 id="team-outcomes-title">{t('work.team.historyDelivery')}</h2>
            <span className="team-pane__eyebrow">{t('work.team.resultsHelp')}</span>
          </div>
        )}
        {onClose ? <button type="button" className="team-panel-close" aria-label={t('work.navigation.closePanel')} onClick={onClose}><X size={15} /></button> : null}
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
  navigationInSidebar = false,
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
  const [evidenceVisibility, setEvidenceVisibility] = useState(null);
  const [expandedTaskId, setExpandedTaskId] = useState('');
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [selectedReviewTargetId, setSelectedReviewTargetId] = useState('');
  const [activeReviewTaskId, setActiveReviewTaskId] = useState('');
  const mobileTabRefs = useRef(new Map());
  const outcomeRefs = useRef(new Map());
  const returnFocusRef = useRef(null);
  const panelsRef = useRef(null);
  useEffect(() => {
    const panels = panelsRef.current;
    const dock = panels?.querySelector('.team-manager-dock');
    if (!dock || !globalThis.ResizeObserver) return;
    const observer = new ResizeObserver(() => panels.style.setProperty('--composer-height', `${dock.getBoundingClientRect().height + 12}px`));
    observer.observe(dock);
    return () => observer.disconnect();
  }, []);
  const workspace = data.team_workspace;
  const mission = workspace.mission;
  const assistant = projectAssistant || workspace;
  const readOnly = Boolean(mission?.read_only);
  const outcomes = workspace.outcome_tasks || [];
  const selectedTask = outcomes.find((task) => task.id === selectedTaskId) || null;
  const attention = workspace.attention_request;
  const evidenceContext = `${projectKey}:${mission?.run_id || mission?.id || ''}:${attention?.target_id || ''}`;
  const evidenceOpen = evidenceVisibility?.context === evidenceContext
    ? evidenceVisibility.open : attention?.kind === 'approval';
  const evidenceHidden = !mobileWorkspace && !evidenceOpen;
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
    setEvidenceVisibility({ context: evidenceContext, open: true });
    if (!mobileWorkspace) {
      outcomeRefs.current.get(attentionReviewTask.id)?.scrollIntoView?.({
        behavior: reducedMotion ? 'auto' : 'smooth',
        block: 'nearest',
        inline: 'nearest',
      });
    }
    setSelectedReviewTargetId(attention.target_id);
    setSelectedTaskId(attentionReviewTask.id);
  }, [attention, attentionReviewTask, evidenceContext, mobileWorkspace, reducedMotion]);
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
    'data-mobile-active': tab === 'manager' || mobileTab === tab ? 'true' : 'false',
    'data-read-only': readOnly ? 'true' : undefined,
    ...(!mobileWorkspace && tab === 'outcomes' ? { hidden: evidenceHidden } : {}),
    ...(mobileWorkspace ? {
      'aria-labelledby': `team-tab-${tab}`,
      hidden: tab !== 'manager' && mobileTab !== tab,
      role: 'region',
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
          <button type="button" aria-controls="team-panel-outcomes" aria-expanded={!evidenceHidden} onClick={() => setEvidenceVisibility({ context: evidenceContext, open: evidenceHidden })}>{evidenceHidden ? <PanelRightOpen size={16} aria-hidden="true" /> : <PanelRightClose size={16} aria-hidden="true" />}{t(evidenceHidden ? 'product.studio.showEvidence' : 'product.studio.hideEvidence')}</button>
        </div>
      </div>
      <div
        aria-label={t('work.team.navigation')}
        className="team-mobile-nav"
        role="group"
      >
        {mobileTabs.map((tab) => {
          const selected = mobileTab === tab.id;
          const hasAttention = tab.id === (attention?.kind === 'blocker' ? 'execution' : attention?.kind === 'approval' ? 'outcomes' : 'manager') && Boolean(attention);
          return (
            <button
              aria-controls={`team-panel-${tab.id}`}
              aria-label={hasAttention ? `${tab.label}. ${t('work.manager.needsYou')}` : tab.label}
              aria-pressed={selected}
              data-active={selected ? 'true' : 'false'}
              id={`team-tab-${tab.id}`}
              key={tab.id}
              onClick={() => setMobileTab(selected ? 'manager' : tab.id)}
              onKeyDown={(event) => handleMobileTabKeyDown(event, tab.id)}
              ref={(node) => {
                if (node) mobileTabRefs.current.set(tab.id, node);
                else mobileTabRefs.current.delete(tab.id);
              }}
              tabIndex={0}
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
        ref={panelsRef}
        aria-label={t('work.team.workspace')}
        data-evidence-collapsed={evidenceHidden ? 'true' : undefined}
        data-read-only={readOnly ? 'true' : undefined}
      >
        <div className="team-workspace-center">
        <ProjectConversation
          executionRunning={activeJobs.length > 0}
          canManageTasks={true}
          taskManagementOpen={navigationInSidebar ? taskManagementOpen : mobileWorkspace ? taskManagementOpen : !evidenceHidden}
          onManageTasks={() => { if (navigationInSidebar) { onManageTasks?.(); return; } setEvidenceVisibility({ context: evidenceContext, open: true }); if (mobileWorkspace) { setMobileTab('outcomes'); onManageTasks?.(); } }}
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
          iterationChoice={data.iteration_choice || null}
          queuedTasks={data.task_assistant_session?.queued_task_proposals || []}
          iterationSession={data.task_assistant_session}
          canPlanIteration={!readOnly && !data.active_run && (!assistant.proposed_task || assistant.proposed_task.status === 'cancelled')}
          onQueueTask={onQueueTask}
          contextTitle={contextTitle}
          onClearContext={onClearContext}
        />
        </div>
        <ExecutionPane
          onInspectAttempt={onInspectAttempt}
          onClose={mobileWorkspace ? () => setMobileTab('manager') : undefined}
          onSwitchToOutcomes={!mobileWorkspace ? () => setEvidenceVisibility({ context: evidenceContext, open: true }) : undefined}
          outcomesCount={outcomes.length}
          blocker={attention?.kind === 'blocker' ? <TaskAttention attention={attention} activeJobs={activeJobs} error={error} jobs={data.worker_jobs || []} projectKey={projectKey} busy={busy} onRetry={readOnly ? undefined : onRetry} retry={retry} t={t} /> : null}
          context={executionContext}
          controls={!readOnly && (workspace.capabilities || []).some((item) => item.action === 'cancel_mission')
            ? <TaskRunControls key={mission?.run_id || mission?.id} busy={busy} canCancel onTerminate={onTerminate} t={t} /> : null}
          actors={(workspace.execution_actors || []).filter((actor) => actor.role !== 'orchestrator')}
          reducedMotion={reducedMotion}
          sectionProps={sectionProps('execution')}
          t={t}
        />
        <OutcomesPane
          onClose={() => { setEvidenceVisibility({ context: evidenceContext, open: false }); setMobileTab('manager'); }}
          onSwitchToExecution={!mobileWorkspace ? () => setEvidenceVisibility({ context: evidenceContext, open: false }) : undefined}
          actorsCount={(workspace.execution_actors || []).filter((actor) => actor.role !== 'orchestrator').length}
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
