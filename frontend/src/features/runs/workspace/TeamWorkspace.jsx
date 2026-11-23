/**
 * [INPUT]: 接收项目对话、Worker 状态、交付记录与顶栏挂载点。
 * [OUTPUT]: 提供项目工作台、可折叠的执行与交付侧栏和任务详情入口。
 * [POS]: 组织工作台视图，将顶部工具区接入流式顶栏，并把 Worker 详情绑定到对应成员行。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import ProjectConversation from './ProjectConversation';
import { Fragment, useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  PanelRightClose,
  PanelRightOpen,
  Wrench,
  X,
} from 'lucide-react';
import {
  statusText,
  translated,
} from './presentationUtils';
import { conversationTimeline } from './conversationFeedback';

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

function ExecutionPane({ actors, onClose, onSwitchToOutcomes, reducedMotion, sectionProps, t }) {
  const [selectedId, setSelectedId] = useState('');
  const workerListId = useId();
  return <section className="team-pane team-pane--execution" aria-label={t('work.team.workerStatus')} {...sectionProps}>
    <header className="team-pane__header">
      {onSwitchToOutcomes ? (
        <div className="team-right-panel-tabs" role="tablist">
          <button type="button" role="tab" aria-selected="true" className="team-right-panel-tab is-active">
            {t('work.team.workerStatus')} {actors.length ? `(${actors.length})` : ''}
          </button>
          <button type="button" role="tab" aria-selected="false" className="team-right-panel-tab" onClick={onSwitchToOutcomes}>
            {t('work.team.historyDelivery')}
          </button>
        </div>
      ) : (
        <div className="team-execution-heading"><strong id="team-execution-title">{t('work.team.workerStatus')}</strong><span className="team-pane__eyebrow">{t('work.team.workerCount', { count: actors.length })}</span></div>
      )}
      {onClose ? <button type="button" className="team-panel-close" aria-label={t('work.navigation.closePanel')} title={t('work.navigation.closePanel')} onClick={onClose}><X size={15} /></button> : null}
    </header>
    {actors.length ? <ol className="team-actor-list">{actors.map((actor, index) => {
      const role = translated(t, `work.plan.role.${actor.role}`, actor.role);
      const reason = executionStatusReason(actor, t);
      const status = statusText(actor.state, t);
      const expanded = selectedId === actor.id;
      const detailId = `${workerListId}-worker-${index}`;
      const methods = actor.method_details;
      return <li key={actor.id} data-state={actor.state}>
        <button type="button" className="team-worker-summary" aria-expanded={expanded} aria-controls={detailId}
          onClick={() => setSelectedId(expanded ? '' : actor.id)}>
          <span className="team-worker-dot" data-state={actor.state} data-motion={reducedMotion ? 'static' : 'ambient'} aria-hidden="true" />
          <span className="team-worker-copy">
            <span className="team-worker-identity"><strong>{role}</strong><span>{status}</span></span>
            <span className="team-worker-task" title={actor.responsibility}>{actor.responsibility || t('work.team.noResponsibility')}</span>
            {reason ? <span className="team-worker-reason">{reason}</span> : null}
          </span>
        </button>
        <div id={detailId} className="team-worker-detail" hidden={!expanded}>
          {expanded ? <>
            <dl>
              <dt>{t('work.navigation.assignment')}</dt><dd>{actor.task_id || actor.id}</dd>
              {methods?.adapter ? <><dt>{t('work.team.methodAgent')}</dt><dd>{methods.adapter}</dd></> : null}
              {methods?.provider_connection ? <><dt>Provider</dt><dd title={methods.provider_connection.id}>
                {methods.provider_connection.id.slice(0, 17)} · r{methods.provider_connection.revision}
              </dd></> : null}
              {methods?.method_items?.map((item) => <Fragment key={item.skill_id}>
                <dt title={item.skill_id}>{item.name || item.skill_id.slice(0, 18)}</dt>
                <dd title={item.version_id}>{item.version_id.slice(0, 20)}</dd>
              </Fragment>)}
              {methods?.method_packages_status ? <><dt>{t('work.team.methodResources')}</dt>
                <dd>{t(`work.team.methodStage.${methods.method_packages_status}`)}</dd></> : null}
              {methods?.method_input_status ? <><dt>{t('work.team.methodInput')}</dt>
                <dd>{t(`work.team.methodStage.${methods.method_input_status}`)}</dd></> : null}
              {actor.dependency_task_title ? <><dt>{t('work.navigation.waitingFor')}</dt><dd>{actor.dependency_task_title}</dd></> : null}
            </dl>
          </> : null}
        </div>
      </li>;
    })}</ol> : <p className="team-pane__empty">{t('work.team.executionEmpty')}</p>}
  </section>;
}

function OutcomesPane({ navigation, onClose, onSwitchToExecution, actorsCount, historyContext, sectionProps, t }) {
  return (
    <section className="team-pane team-pane--outcomes" aria-label={t('work.team.outcomes')} {...sectionProps}>
      <header className="team-pane__header">
        {onSwitchToExecution ? (
          <div className="team-right-panel-tabs" role="tablist">
            <button type="button" role="tab" aria-selected="false" className="team-right-panel-tab" onClick={onSwitchToExecution}>
              {t('work.team.workerStatus')} {actorsCount ? `(${actorsCount})` : ''}
            </button>
            <button type="button" role="tab" aria-selected="true" className="team-right-panel-tab is-active">
              {t('work.team.historyDelivery')}
            </button>
          </div>
        ) : (
          <div>
            <h2 id="team-outcomes-title">{t('work.team.historyDelivery')}</h2>
            <span className="team-pane__eyebrow">{t('work.team.resultsHelp')}</span>
          </div>
        )}
        {onClose ? <button type="button" className="team-panel-close" aria-label={t('work.navigation.closePanel')} title={t('work.navigation.closePanel')} onClick={onClose}><X size={15} /></button> : null}
      </header>
      <div className="team-outcomes-scroll">
      {navigation}
      {historyContext}
      </div>
    </section>
  );
}

function TeamWorkspace({
  toolbarHost = null,
  onConfigureRuntime,
  conversationDelivery = null,
  projectAssistant,
  assistantBusy,
  assistantError,
  taskNavigation,
  navigationInSidebar = false,
  projectTools,
  historyContext,
  taskManagementOpen,
  onManageTasks,
  onQueueTask,
  onAssistantAction,
  onAnswerQuestion,
  questionBoard,
  busy,
  data,
  error,
  interactionsDisabled = false,
  language,
  onConfirmTask,
  onContinuePreparation,
  onStart,
  projectKey,
  reducedMotion,
  t,
}) {
  const mobileWorkspace = useMobileTeamWorkspace();
  const [mobileTab, setMobileTab] = useState('manager');
  const [asideCollapsed, setAsideCollapsed] = useState(false);
  const [evidenceVisibility, setEvidenceVisibility] = useState(null);
  const mobileTabRefs = useRef(new Map());
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
  const attention = workspace.attention_request;
  const evidenceContext = `${projectKey}:${mission?.run_id || mission?.id || ''}:${attention?.target_id || ''}`;
  const evidenceOpen = evidenceVisibility?.context === evidenceContext && evidenceVisibility.open;
  const evidenceHidden = !mobileWorkspace && (asideCollapsed || !evidenceOpen);
  const closeMobileDrawer = useCallback((restoreFocus = true) => {
    const activeTab = mobileTab;
    setMobileTab('manager');
    if (restoreFocus && activeTab !== 'manager') {
      mobileTabRefs.current.get(activeTab)?.focus();
    }
  }, [mobileTab]);
  useEffect(() => {
    if (!mobileWorkspace || mobileTab === 'manager') return undefined;
    const handleKeyDown = (event) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      if (event.target?.closest?.('.studio-project-tools[open], dialog, [role="dialog"]')) return;
      if (document.querySelector('dialog[open], [role="dialog"]:not([hidden])')) return;
      event.preventDefault();
      closeMobileDrawer();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [closeMobileDrawer, mobileTab, mobileWorkspace]);
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
    ...(!mobileWorkspace && tab === 'execution' ? { hidden: asideCollapsed } : {}),
    ...(!mobileWorkspace && tab === 'outcomes' ? { hidden: evidenceHidden } : {}),
    ...(mobileWorkspace ? {
      'aria-labelledby': `team-tab-${tab}`,
      hidden: tab !== 'manager' && mobileTab !== tab,
      role: 'region',
    } : {}),
  });
  const mobileTabs = [
    { id: 'execution', label: t('work.team.workerStatus') },
    { id: 'manager', label: t('work.team.manager') },
    { id: 'outcomes', label: t('work.team.historyDelivery') },
  ];
  const workToolbar = (
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
        <button
          type="button"
          aria-controls="team-panel-outcomes"
          aria-expanded={!evidenceHidden}
          data-active={!evidenceHidden ? 'true' : undefined}
          onClick={() => {
            setAsideCollapsed(false);
            setEvidenceVisibility({ context: evidenceContext, open: evidenceHidden });
          }}
        >
          {evidenceHidden ? <PanelRightOpen size={16} aria-hidden="true" /> : <PanelRightClose size={16} aria-hidden="true" />}
          {t(evidenceHidden ? 'product.studio.showEvidence' : 'product.studio.hideEvidence')}
        </button>
        {!mobileWorkspace && asideCollapsed ? (
          <button
            type="button"
            className="studio-work-toolbar__reopen"
            aria-controls="team-panel-execution"
            aria-expanded={false}
            aria-label={t('work.team.workerStatus')}
            title={t('work.team.workerStatus')}
            onClick={() => {
              setAsideCollapsed(false);
              setEvidenceVisibility({ context: evidenceContext, open: false });
            }}
          >
            <PanelRightOpen size={16} aria-hidden="true" />
            <span>{t('work.team.workerStatus')}</span>
          </button>
        ) : null}
      </div>
    </div>
  );
  return (
    <>
      {toolbarHost
        ? createPortal(<fieldset className="studio-work-toolbar-scope" disabled={interactionsDisabled}>{workToolbar}</fieldset>, toolbarHost)
        : workToolbar}
      <div
        aria-label={t('work.team.navigation')}
        className="team-mobile-nav"
        role="group"
      >
        {mobileTabs.map((tab) => {
          const selected = mobileTab === tab.id;
          const hasAttention = tab.id === 'manager' && Boolean(attention);
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
        data-aside-collapsed={!mobileWorkspace && asideCollapsed ? 'true' : undefined}
        data-evidence-collapsed={evidenceHidden ? 'true' : undefined}
      >
        <div className="team-workspace-center">
        <ProjectConversation
          canManageTasks={true}
          taskManagementOpen={navigationInSidebar ? taskManagementOpen : mobileWorkspace ? taskManagementOpen : !evidenceHidden}
          onManageTasks={() => { if (navigationInSidebar) { onManageTasks?.(); return; } setAsideCollapsed(false); setEvidenceVisibility({ context: evidenceContext, open: true }); if (mobileWorkspace) { setMobileTab('outcomes'); onManageTasks?.(); } }}
          onConfigureRuntime={onConfigureRuntime}
          acceptedMessage={conversationDelivery && conversationDelivery.projectKey === projectKey && !conversationDelivery.intentId
            && (conversationDelivery.contextKey || '').startsWith('message:')
            && (assistant.timeline || workspace.manager_timeline || []).some(item => item.actor === 'user' && (item.id === conversationDelivery.id || item.message_id === conversationDelivery.id))
            ? conversationDelivery : null}
          assistantState={assistant}
          onAssistantAction={onAssistantAction}
          onAnswerQuestion={onAnswerQuestion}
          questionBoard={questionBoard ?? data.task_assistant_session?.question_board}
          attention={workspace.attention_request}
          busy={busy}
          error={assistantError || error}
          assistantBusy={assistantBusy ?? busy}
          language={language}
          mission={mission}
          onConfirmTask={onConfirmTask}
          onContinuePreparation={onContinuePreparation}
          onStart={onStart}
          projectKey={projectKey}
          sectionProps={sectionProps('manager')}
          timeline={conversationTimeline(assistant.timeline || workspace.manager_timeline || [], conversationDelivery, {
            projectKey,
          })}
          t={t}
          suggestions={assistant.suggestions || []}
          proposedTask={assistant.proposed_task || null}
          iterationBacklog={data.iteration_backlog || null}
          iterationChoice={data.iteration_choice || null}
          queuedTasks={data.task_assistant_session?.queued_task_proposals || []}
          iterationSession={data.task_assistant_session}
          canPlanIteration={assistant.can_select_task}
          onQueueTask={onQueueTask}
        />
        </div>
        {mobileWorkspace && mobileTab !== 'manager' ? (
          <button
            type="button"
            className="team-pane-backdrop"
            aria-label={t('work.navigation.closePanel')}
            onClick={() => closeMobileDrawer()}
            tabIndex={-1}
          />
        ) : null}
        <ExecutionPane
          onClose={() => (mobileWorkspace ? closeMobileDrawer() : setAsideCollapsed(true))}
          onSwitchToOutcomes={!mobileWorkspace ? () => { setAsideCollapsed(false); setEvidenceVisibility({ context: evidenceContext, open: true }); } : undefined}
          actors={(workspace.execution_actors || []).filter((actor) => actor.role !== 'orchestrator')}
          reducedMotion={reducedMotion}
          sectionProps={sectionProps('execution')}
          t={t}
        />
        <OutcomesPane
          onClose={() => { setEvidenceVisibility({ context: evidenceContext, open: false }); if (mobileWorkspace) closeMobileDrawer(); }}
          onSwitchToExecution={!mobileWorkspace ? () => { setAsideCollapsed(false); setEvidenceVisibility({ context: evidenceContext, open: false }); } : undefined}
          actorsCount={(workspace.execution_actors || []).filter((actor) => actor.role !== 'orchestrator').length}
          navigation={taskNavigation}
          historyContext={historyContext}
          sectionProps={sectionProps('outcomes')}
          t={t}
        />
      </div>
    </>
  );
}
export default TeamWorkspace;
