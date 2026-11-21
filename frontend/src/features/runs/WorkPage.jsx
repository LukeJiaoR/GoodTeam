import { useReducedMotion } from '../../hooks/useReducedMotion';
import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal, flushSync } from 'react-dom';
import { focusWorkContext } from './workspace/currentWorkContext.js';
import { Link, useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router-dom';
import { canConfirmApproval, requiresInformedApproval, usableApprovalTarget } from '../../lib/reviews/approvalConfirmation';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import StatusBadge from '../../components/ui/StatusBadge';
import { useToast } from '../../components/ui/ToastProvider';
import { routes } from '../../lib/routing/routes';
import { statusLabel, stepTitle } from '../../utils/workflowHelpers';
import { reviewApi } from '../reviews/reviewApi';
import { runApi } from './runApi';
import useProjectState from './useProjectState';
import ProjectAttentionFocus from '../projects/ProjectAttentionFocus';
import GitRecoveryPanel from '../projects/GitRecoveryPanel';
import { readAttentionSelection } from '../projects/projectAttention';
import CurrentWorkContext from './workspace/CurrentWorkContext.jsx';
import GoalContinuationPanel from './workspace/GoalContinuationPanel.jsx';
import useProjectConversation from './useProjectConversation';
import TaskNavigation from './workspace/TaskNavigation';
import TaskInputContext from './workspace/TaskInputContext.jsx';
import AttemptInspector from './workspace/AttemptInspector';
import { resolveAttemptInspection } from './workspace/attemptInspection';
import { TimelineItem } from './workspace/ConversationTimeline';
import TeamWorkspace from './workspace/TeamWorkspace';
import { statusText, tone } from './workspace/presentationUtils';

const ReviewPage = lazy(() => import('../reviews/ReviewPage'));
const AssistantRuntimePanel = lazy(() => import('../team/AssistantRuntimePanel'));

function shortRunId(runId) {
  return String(runId || '').trim().slice(0, 8);
}

function runTitle(run, t) {
  const title = [run?.title, run?.idea, run?.goal]
    .map((value) => String(value || '').trim())
    .find(Boolean);
  return title || t('work.history.untitled', { runId: shortRunId(run?.run_id) });
}

function runOptionLabel(run, t) {
  const statusKey = run.status === 'active'
    ? 'work.history.activeOption'
    : run.status === 'complete'
      ? 'work.history.completedOption'
      : 'work.history.archivedOption';
  return t(statusKey, { title: runTitle(run, t) });
}

function currentWorkflowStep(steps = []) {
  return steps.find((step) => ['active', 'review-needed', 'blocked'].includes(step.status))
    || steps.find((step) => !['done', 'complete', 'completed'].includes(step.status))
    || null;
}

function formattedRunUpdate(value, language) {
  const timestamp = Date.parse(value || '');
  if (!Number.isFinite(timestamp)) return '';
  return new Intl.DateTimeFormat(language, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(new Date(timestamp));
}

function approvalTargetForReview(review, runId = '') {
  if (!review) return null;
  return {
    run_id: runId,
    target_type: 'review',
    target_id: review.approval || '',
    artifact_key: review.artifact || '',
    artifact_version: review.artifact_version || '',
  };
}

function WorkSkeleton({ t }) {
  return (
    <div className="product-workspace-skeleton" aria-label={t('product.states.loading')} role="status">
      <span /><span /><span />
    </div>
  );
}

function HistoricalRunSummary({ detail, projectKey, summary, language, t }) {
  const state = detail?.state || {};
  return <section className="work-historical-view">
    <h2>{runTitle({ ...state, ...summary }, t)}</h2>
    <details>
    <summary>{t('work.history.readOnly')}</summary>
    <dl className="settings-data-list">
      <div><dt>{t('work.history.goal')}</dt><dd>{summary?.idea || state.idea || '—'}</dd></div>
      <div><dt>{t('work.history.status')}</dt><dd>{summary?.status || '—'}</dd></div>
    </dl>
    <ol className="team-manager-timeline" aria-label={t('assistant.project.taskHistory')}>
      {(detail?.team_workspace?.manager_timeline || []).map((item) => <TimelineItem key={item.id} item={item} language={language} t={t} />)}
    </ol>
    <Link to={routes.projectWork(projectKey)}>{t('work.history.current')}</Link>
    </details>
  </section>;
}

export default function WorkPage() {
  const { projectKey = '', runId = '' } = useParams();
  const { language, t, projectNavigationHost, workQuery, openProjectNavigation, closeProjectNavigation } = useOutletContext();
  const { reducedMotion } = useReducedMotion();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const attentionQuery = search.toString();
  const attentionSelection = useMemo(() => readAttentionSelection(new URLSearchParams(attentionQuery)), [attentionQuery]);
  const { data: latestData, error, refresh, status } = useProjectState(projectKey);
  const retryScope = JSON.stringify([projectKey, runId]);
  const retryRequest = useRef(null);
  const [retrySnapshot, setRetrySnapshot] = useState(null);
  if (retrySnapshot && retrySnapshot.scope !== retryScope) setRetrySnapshot(null);
  const scopedRetry = retrySnapshot?.scope === retryScope ? retrySnapshot : null;
  // Keep the evidence that was actually submitted, including while polling
  // discovers another run/attempt. Only an explicit successful refresh releases
  // failed/uncertain requests; polling never silently rearms Retry.
  const data = scopedRetry?.data || latestData;
  const retrying = scopedRetry?.phase === 'pending';
  const retryConflict = scopedRetry?.phase === 'failed';
  const interactionsPaused = status !== 'ready' || Boolean(scopedRetry);
  useLayoutEffect(() => () => { retryRequest.current = null; }, [retryScope]);
  const [mutationError, setMutationError] = useState(null);
  const [panel, setPanel] = useState(null);
  const [attemptSelection, setAttemptSelection] = useState(null);
  if (attemptSelection && attemptSelection.scope !== retryScope) setAttemptSelection(null);
  const workspaceRef = useRef(null);
  const [workToolbarHost, setWorkToolbarHost] = useState(null);
  const [taskManagementProject, setTaskManagementProject] = useState('');
  const taskManagementOpen = taskManagementProject === projectKey;
  const [mutationBusy, setBusy] = useState('');
  const busy = retrying ? 'retry' : mutationBusy;
  const [historyState, setHistoryState] = useState({ projectKey: '', runs: [], status: 'loading', error: null });
  const runHistory = historyState.projectKey === projectKey ? historyState.runs : [];
  const historyStatus = historyState.projectKey === projectKey ? historyState.status : 'loading';
  const historyError = historyState.projectKey === projectKey ? historyState.error : null;
  const historyRequest = useRef(null);
  const historyScopeRef = useRef(null);
  const [historicalSnapshot, setHistoricalSnapshot] = useState(null);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const historicalDetail = historicalSnapshot?.projectKey === projectKey && historicalSnapshot?.runId === runId
    ? historicalSnapshot.detail : null;
  const projectedWorkspace = data?.team_workspace?.schema_version === 'team_workspace.v1'
    ? data.team_workspace
    : null;
  const projectedMission = projectedWorkspace?.mission || null;
  const hasProjectedMission = Boolean(projectedMission);
  const isTaskAssistantConversation = projectedMission?.source === 'task_assistant';
  const currentRunId = data?.active_run
    ? (projectedMission?.source === 'workflow' ? projectedMission.run_id : data?.state?.run_id) || ''
    : '';
  // The project snapshot can move on while this route still names the old run.
  // Re-read history for that transition without navigating or dispatching work.
  const historyScope = JSON.stringify([projectKey, currentRunId]);
  useLayoutEffect(() => {
    historyScopeRef.current = historyScope;
    historyRequest.current = null;
    return () => { historyScopeRef.current = null; historyRequest.current = null; };
  }, [historyScope]);
  const currentWorkflowBlocked = Boolean(currentRunId && projectedMission?.source === 'workflow'
    && !projectedMission.read_only && projectedWorkspace?.attention_request?.kind === 'blocker'
    && !(projectedWorkspace.execution_actors || []).some(actor => actor.role !== 'orchestrator' && actor.state === 'working'));
  const currentRunTitle = projectedMission?.title || runTitle(data?.state, t);
  const selectedSummary = runHistory.find((item) => item.run_id === runId);
  const isHistorical = Boolean(runId && selectedSummary && selectedSummary.status !== 'active');
  const selectedKnown = !runId || historyStatus !== 'ready' || Boolean(selectedSummary);
  const workflowSteps = (isHistorical ? historicalDetail?.workflow_steps : data?.workflow_steps) || [];
  const contextMission = isHistorical ? historicalDetail?.team_workspace?.mission : projectedMission;
  const contextTitle = isHistorical ? runTitle(selectedSummary, t) : currentRunTitle;
  const contextIsLoading = (isHistorical && (!historicalDetail || historyStatus !== 'ready'))
    || Boolean(runId && runId !== currentRunId && !isHistorical);
  // Never infer an Operator/Assistant task's input scope from stale workflow state.
  const inputContextRunId = !contextIsLoading && contextMission?.source === 'workflow'
    && (!contextMission.project_key || contextMission.project_key === projectKey)
    && contextMission.run_id && (!runId || contextMission.run_id === runId)
    ? contextMission.run_id : '';
  const contextFreshness = retryConflict ? 'stale' : status !== 'ready' ? status : contextIsLoading ? 'loading' : 'ready';
  const inspection = attemptSelection?.scope === retryScope ? resolveAttemptInspection({
    data: contextIsLoading ? null : isHistorical ? historicalDetail : data,
    projectKey,
    runId: attemptSelection.runId,
    actorId: attemptSelection.actorId,
    attemptId: attemptSelection.attemptId,
    freshness: contextFreshness,
  }) : null;
  const visibleTaskTitle = isHistorical ? contextTitle
    : projectedWorkspace?.proposed_task?.run_id ? currentRunTitle
      : projectedWorkspace?.proposed_task?.exact_request || (hasProjectedMission && !isTaskAssistantConversation ? currentRunTitle : '');
  const candidateRetryTarget = data?.retry_target;
  const retryTarget = candidateRetryTarget?.schema_version === 'workflow-retry-target.v1'
    && candidateRetryTarget.project_key === projectKey
    && candidateRetryTarget.run_id && candidateRetryTarget.run_id === currentRunId
    && candidateRetryTarget.run_id === data?.state?.run_id
    && /^sha256:[a-f0-9]{64}$/.test(candidateRetryTarget.version || '')
    && ['resume_transition', 'retry_revision', 'retry_fix', 'retry_staging', 'retry_implementation', 'unavailable'].includes(candidateRetryTarget.mode)
    && Array.isArray(candidateRetryTarget.attempts) && Array.isArray(candidateRetryTarget.active_attempt_ids)
    && projectedMission?.source === 'workflow' && !projectedMission.read_only
    && (!projectedMission.project_key || projectedMission.project_key === projectKey)
    && !isHistorical && !contextIsLoading
    ? candidateRetryTarget : null;
  const retry = {
    target: retryTarget,
    disabled: interactionsPaused || retryTarget?.available !== true || retryTarget.mode === 'unavailable'
      || Boolean(retryTarget.reason) || retryTarget.active_attempt_ids.length > 0,
    reason: retryConflict ? 'refreshRequired' : !retryTarget ? 'bindingMissing'
      : retryTarget.reason === 'active_attempt' || retryTarget.active_attempt_ids.length ? 'activeAttempt'
        : retryTarget.reason === 'attempt_unknown' ? 'attemptUnknown'
          : retryTarget.available !== true || retryTarget.mode === 'unavailable' ? 'unavailable' : '',
  };
  const contextStep = currentWorkflowStep(workflowSteps);
  const completedStepCount = workflowSteps.filter((step) => (
    ['done', 'complete', 'completed'].includes(step.status)
  )).length;
  const currentRunSummary = runHistory.find((item) => item.run_id === currentRunId);
  const contextUpdatedAt = contextMission?.updated_at || (isHistorical ? selectedSummary?.updated_at : currentRunSummary?.updated_at) || '';
  const contextUpdatedLabel = formattedRunUpdate(contextUpdatedAt, language);
  const hasRunContext = Boolean(isHistorical || data?.active_run || (hasProjectedMission && !isTaskAssistantConversation));
  const showRunSelector = runHistory.length > 0 && (
    !hasRunContext || runHistory.some((item) => item.run_id !== currentRunId)
  );

  const loadRuns = useCallback(async ({ signal } = {}) => {
    if (signal?.aborted || historyScopeRef.current !== historyScope) return null;
    const request = {};
    historyRequest.current = request;
    const isCurrent = () => !signal?.aborted && historyRequest.current === request
      && historyScopeRef.current === historyScope;
    setHistoryState((current) => current.projectKey === projectKey
      ? { ...current, status: current.runs.length ? current.status : 'loading' }
      : { projectKey, runs: [], status: 'loading', error: null });
    try {
      const payload = await runApi.getProjectRuns(projectKey, { signal });
      if (!isCurrent()) return null;
      if (!Array.isArray(payload?.runs) || (payload.project_key && payload.project_key !== projectKey)) {
        throw Object.assign(new Error('The requested project task history could not be verified.'), { code: 'invalid_response' });
      }
      setHistoryState({ projectKey, runs: payload.runs, status: 'ready', error: null });
      return payload;
    } catch (caught) {
      if (!isCurrent() || caught.name === 'AbortError') return null;
      setHistoryState((current) => ({ ...current, error: caught, status: current.runs.length ? 'stale' : 'error' }));
      return null;
    }
  }, [projectKey, historyScope]);

  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => void loadRuns({ signal: controller.signal }));
    return () => controller.abort();
  }, [loadRuns]);

  useEffect(() => {
    if (!isHistorical || !runId) return undefined;
    const controller = new AbortController();
    runApi.getProjectRun(projectKey, runId, { signal: controller.signal })
      .then((detail) => {
        if (controller.signal.aborted) return;
        const workspace = detail?.team_workspace;
        const mission = workspace?.mission;
        if (workspace?.schema_version !== 'team_workspace.v1' || mission?.read_only !== true
            || mission.run_id !== runId || (mission.project_key && mission.project_key !== projectKey)
            || detail?.state?.run_id !== runId || (detail.state.project_key && detail.state.project_key !== projectKey)) {
          throw { code: 'invalid_response', message: 'The requested historical run could not be verified.' };
        }
        setHistoricalSnapshot({ projectKey, runId, detail });
      })
      .catch((caught) => {
        if (!controller.signal.aborted && caught.name !== 'AbortError') {
          setHistoryState((current) => ({ ...current, status: 'stale', error: caught }));
        }
      });
    return () => controller.abort();
  }, [isHistorical, projectKey, runId, historyRefresh]);


  const projectName = data?.start_context?.project_name || projectKey;
  const toast = useToast();

  const mutate = async (name, operation) => {
    setBusy(name); setMutationError(null);
    try {
      if (interactionsPaused) throw error || new Error(t('work.context.staleActions'));
      const result = await operation();
      await refresh();
      await loadRuns();
      return result;
    } catch (caught) {
      setMutationError(caught);
      if (['message', 'retry_message', 'check_runtime', 'confirm_team_decision', 'dismiss_team_decision', 'edit_task', 'resolve_draft', 'confirm_project_mode', 'task-confirm', 'terminate'].includes(name)) {
        await refresh().catch(() => {});
        if (name === 'terminate') await loadRuns();
      }
      throw caught;
    } finally {
      setBusy('');
    }
  };

  const handleRetry = async () => {
    if (retry.disabled || busy || retryRequest.current) return;
    const request = { scope: retryScope, data, phase: 'pending', error: null };
    retryRequest.current = request;
    setRetrySnapshot(request);
    setMutationError(null);
    try {
      await runApi.retryRun(projectKey, retryTarget);
      if (retryRequest.current !== request) return;
      const refreshed = await refresh();
      if (retryRequest.current !== request) return;
      if (!refreshed) throw { code: 'retry_refresh_required' };
      await loadRuns();
      if (retryRequest.current !== request) return;
      retryRequest.current = null;
      setRetrySnapshot(null);
    } catch (caught) {
      if (retryRequest.current !== request) return;
      const failed = { ...request, phase: 'failed', error: caught };
      retryRequest.current = failed;
      setRetrySnapshot(failed);
    }
  };

  const handleRefreshContext = async () => {
    if (retrying) return;
    const snapshot = scopedRetry;
    const payload = await refresh();
    if (payload && snapshot && retryRequest.current === snapshot) {
      retryRequest.current = null;
      setRetrySnapshot(null);
    }
    await loadRuns();
    setHistoryRefresh((value) => value + 1);
  };

  const handleDecide = async (approval, decision, note = '', approvalTarget = null) => {
    const currentReview = (data?.approval_reviews || []).find(
      (review) => review.approval === approval && review.is_actionable,
    );
    const expectedApproval = approvalTarget
      || approvalTargetForReview(currentReview, data?.state?.run_id || '');
    if (decision === 'approved' && !canConfirmApproval({
      disabled: !currentReview || !usableApprovalTarget(expectedApproval),
      requiresReason: requiresInformedApproval(currentReview?.recommendation),
      note,
    })) {
      navigate(routes.projectReviewItem(projectKey, approval));
      return false;
    }
    try {
      const result = await mutate('decision', () => (
        decision === 'approved'
          ? reviewApi.decideReview(projectKey, approval, decision, note, { expectedApproval })
          : reviewApi.decideReview(projectKey, approval, decision, note)
      ));
      toast.success(t('toast.decisionSubmitted'));
      return result;
    } catch (caught) {
      if (['stale_approval', 'approval_reason_required'].includes(caught?.code)) {
        await Promise.allSettled([refresh(), loadRuns()]);
      }
      toast.error(t('toast.decisionError', { error: caught.message || String(caught) }));
      throw caught;
    }
  };

  const refreshStaleIntent = async (caught) => {
    if (caught?.code === 'stale_intent_target') {
      await Promise.allSettled([refresh(), loadRuns()]);
    }
    toast.error(t('toast.decisionError', { error: caught.message || String(caught) }));
    throw caught;
  };

  const handleStartChangeIntent = async (approval, approvalTarget = null, initialText) => {
    const currentReview = (data?.approval_reviews || []).find(
      (review) => review.approval === approval && review.is_actionable,
    );
    const expectedTarget = approvalTarget
      || approvalTargetForReview(currentReview, data?.state?.run_id || '');
    try {
      return await mutate('intent-start', () => (
        initialText === undefined
          ? runApi.startChangeIntent(projectKey, approval, expectedTarget)
          : runApi.startChangeIntent(projectKey, approval, expectedTarget, { initialText })
      ));
    } catch (caught) {
      return refreshStaleIntent(caught);
    }
  };

  const conversation = useProjectConversation({ projectKey, contextRunId: runId,
    refresh: async () => { await refresh(); await loadRuns(); },
  });
  const deliverConversation = conversation.deliver;

  const handleDiscussChangeIntent = (intentId, text) => deliverConversation(
    'intent-discuss', text,
    () => runApi.discussChangeIntent(projectKey, intentId, text), intentId,
  );

  const handleKeepDiscussingChangeIntent = async (intentId) => {
    try {
      return await mutate('intent-discuss', () => (
        runApi.keepDiscussingChangeIntent(projectKey, intentId)
      ));
    } catch (caught) {
      return refreshStaleIntent(caught);
    }
  };

  const handleConfirmChangeIntent = async (intentId) => {
    try {
      const result = await mutate('intent-confirm', () => (
        runApi.confirmChangeIntent(projectKey, intentId)
      ));
      toast.success(t('toast.decisionSubmitted'));
      return result;
    } catch (caught) {
      return refreshStaleIntent(caught);
    }
  };

  const sendTaskAssistantMessage = conversation.send;
  const handleAssistantAction = async (request) => {
    const result = await conversation.act(request);
    if (result !== false && request.action === 'select_queued_task') {
      setTaskManagementProject('');
      navigate(routes.projectWork(projectKey));
    }
    return result;
  };

  const confirmTaskAssistantProposal = (proposalId, revision) => mutate('task-confirm', () => (
    runApi.taskAssistantAction(projectKey, { action: 'confirm_task', proposal_id: proposalId, expected_revision: revision })
  )).catch(() => {});

  const continueProjectPreparation = (stepId, values) => mutate('preparation', () => (
    runApi.taskAssistantAction(projectKey, { action: 'continue_preparation', step_id: stepId, values })
  )).catch(() => {});

  const pendingContext = contextIsLoading && !historicalDetail;
  const surfaceReady = Boolean(projectedWorkspace && selectedKnown && !pendingContext);
  const retryContext = () => { void handleRefreshContext(); };
  const continuation = latestData?.task_assistant_session?.project_continuation;
  const stopContinuation = (interactionsPaused || !surfaceReady) && continuation?.status === 'active' ? (
    <Button variant="secondary" onClick={() => handleAssistantAction({
      action: 'stop_project_continuation', continuation_id: continuation.id,
    })}>{String(language).startsWith('zh') ? '停止后续迭代' : 'Stop future iterations'}</Button>
  ) : null;

  const attentionFocus = attentionSelection ? <ProjectAttentionFocus
    key={JSON.stringify([projectKey, runId, attentionQuery])}
    selection={attentionSelection} projectKey={projectKey} runId={runId} data={latestData}
    freshness={status} refresh={refresh} reducedMotion={reducedMotion}
    surfaceReady={status === 'ready' && surfaceReady} t={t} /> : null;

  if (status === 'loading') return <main id="main-content" className="product-page">{attentionFocus}<WorkSkeleton t={t} /></main>;
  if (status === 'error' && !data) return <main id="main-content" className="product-page">{attentionFocus}<ProductError error={error} onRetry={retryContext} t={t} /></main>;
  if (!selectedKnown || !projectedWorkspace || pendingContext) {
    const unavailable = !selectedKnown ? { code: 'run_not_found', message: t('work.errors.runNotFound', { runId }) }
      : !projectedWorkspace ? { code: 'invalid_response', message: t('projects.errors.invalid_response') } : historyError;
    return <main id="main-content" className="product-page">
      {attentionFocus}
      {unavailable ? <ProductError error={unavailable} onRetry={selectedKnown ? retryContext : undefined} t={t} /> : <WorkSkeleton t={t} />}
      {runId ? <Link to={routes.projectWork(projectKey)}>{t('work.history.current')}</Link> : null}
      {stopContinuation}
    </main>;
  }

  const runSelector = showRunSelector ? (
    <label className="work-run-selector"><span>{t('work.history.selector')}</span>
      <select value={runId || currentRunId || ''} onChange={(event) => selectTask(event.target.value)}>
        {!currentRunId && !runId ? (
          <option disabled value="">
            {hasProjectedMission
              ? t('work.history.currentMissionOption', { title: currentRunTitle })
              : t('work.history.selectPrevious')}
          </option>
        ) : null}
        {runHistory.map((item) => <option key={item.run_id} value={item.run_id}>{runOptionLabel(item, t)}</option>)}
      </select>
    </label>
  ) : null;

  const selectTask = (selected) => {
    setTaskManagementProject('');
    closeProjectNavigation?.();
    navigate(selected ? routes.projectRun(projectKey, selected) : routes.projectWork(projectKey));
  };
  const taskNavigation = <TaskNavigation currentRunId={currentRunId} currentTitle={currentRunTitle} currentStatus={projectedMission?.status} query={workQuery || ''} currentBlocked={currentWorkflowBlocked} runs={runHistory} selectedRunId={runId} onSelect={selectTask}
      assistant={data.project_assistant} busy={conversation.busy} onAction={handleAssistantAction} t={t}
      onClose={() => {
        setTaskManagementProject('');
        closeProjectNavigation?.();
        focusWorkContext(workspaceRef.current, 'composer', { reducedMotion });
      }} />;
  const conversationProps = {
    projectAssistant: data.project_assistant || { ...data.team_workspace, timeline: data.team_workspace?.manager_timeline || [] },
    conversationDelivery: conversation.delivery,
    assistantBusy: conversation.busy,
    assistantError: conversation.error,
    onStart: sendTaskAssistantMessage,
    onQueueTask: conversation.queue,
    onAssistantAction: handleAssistantAction,
    onAnswerQuestion: conversation.answer,
    questionBoard: latestData?.task_assistant_session?.question_board,
    contextTitle: runId ? contextTitle : '',
    onClearContext: () => selectTask(''),
    taskManagementOpen,
    onManageTasks: () => { setTaskManagementProject(projectKey); openProjectNavigation?.(); },
    taskNavigation: projectNavigationHost ? null : taskNavigation,
    navigationInSidebar: Boolean(projectNavigationHost),
  };

  const currentContext = (
      <CurrentWorkContext
        projectKey={projectKey}
        projectName={projectName}
        title={visibleTaskTitle}
        workspace={isHistorical ? historicalDetail?.team_workspace : projectedWorkspace}
        freshness={contextFreshness}
        historical={isHistorical}
        onRefresh={() => { void handleRefreshContext(); }}
        reducedMotion={reducedMotion}
        t={t}
      />
  );

  const executionContext = (<>
      {inputContextRunId ? <TaskInputContext projectKey={projectKey} runId={inputContextRunId} language={language} t={t} /> : null}
  </>);
  const historyContext = (<>
      <details className="work-context-disclosure">
        <summary>{t('work.context.taskAndHistory')}</summary>
        <header className="product-page-header work-context-drawer">
          <div className="product-work-page__identity"><p className="product-eyebrow">{projectName}</p><h1>{t('work.title')}</h1><p>{t('work.description')}</p></div>
          <div className="product-page-header__meta">
            {hasRunContext ? (
              <section className={`work-run-context${showRunSelector ? ' work-run-context--selectable' : ''}`} aria-label={t('work.manager.currentMission')}>
                <div className="work-run-context__mission">
                  <span>{t('work.manager.currentMission')}</span>
                  <strong className="work-run-title" title={contextTitle}>{contextTitle}</strong>
                </div>
                <div className="work-run-context__facts">
                  <StatusBadge tone={tone(contextStep?.status || contextMission?.status || 'active')}>
                    {contextStep
                      ? statusLabel(contextStep, t)
                      : contextMission
                        ? statusText(contextMission.status, t)
                        : t('work.active')}
                  </StatusBadge>
                  {contextStep ? (
                    <span className="work-run-context__fact">
                      <small>{t('work.context.currentStage')}</small>
                      <strong>{stepTitle(contextStep, t)}</strong>
                    </span>
                  ) : null}
                  {workflowSteps.length ? (
                    <span className="work-run-context__fact">
                      <small>{t('work.context.progressLabel')}</small>
                      <strong>{t('work.context.progress', { complete: completedStepCount, total: workflowSteps.length })}</strong>
                    </span>
                  ) : null}
                  {contextUpdatedLabel ? (
                    <time className="work-run-context__updated" dateTime={contextUpdatedAt}>
                      {t('work.context.updated', { time: contextUpdatedLabel })}
                    </time>
                  ) : null}
                </div>
                {runSelector}
              </section>
            ) : <StatusBadge>{t('work.noRun')}</StatusBadge>}
            <div className="work-run-context__actions">
              {!hasRunContext ? runSelector : null}
            </div>
          </div>
        </header>
      </details>
      <GoalContinuationPanel
        key={projectKey}
        goals={data.task_assistant_session?.goals || []}
        projectKey={projectKey}
        historyCursor={data.task_assistant_session?.goal_page?.next_before || ''}
        busy={busy || conversation.busy}
        readOnly={isHistorical || contextIsLoading}
        activeRun={Boolean(data.active_run)}
        currentRunId={!interactionsPaused && !isHistorical && !contextIsLoading
          && projectedMission?.source === 'workflow' && !projectedMission.read_only
          && (!projectedMission.project_key || projectedMission.project_key === projectKey)
          && currentRunId === data?.state?.run_id ? currentRunId : ''}
        approvalReviews={data.approval_reviews || []}
        onOpenReview={(approval, boundRunId) => setPanel({ type: 'review', approval, runId: boundRunId })}
        error={conversation.error?.code === 'goal_action_unavailable' ? conversation.error : null}
        onAction={handleAssistantAction}
        t={t}
      />
      {isHistorical ? <HistoricalRunSummary language={language} detail={historicalDetail} summary={selectedSummary} projectKey={projectKey} t={t} /> : null}
  </>);

  return (
    <main ref={workspaceRef} id="main-content" className="product-page product-work-page" data-context-scope={`${projectKey}:${runId}`}>
      {attentionFocus}
      {projectNavigationHost ? createPortal(<fieldset className="studio-navigation-scope" disabled={interactionsPaused}>{taskNavigation}</fieldset>, projectNavigationHost) : null}
      <div className="work-header-rail">
        {currentContext}
        <div className="work-header-rail__toolbar" ref={setWorkToolbarHost} />
      </div>
      {status === 'stale' ? <ProductError error={error} onRetry={retryContext} t={t} /> : null}
      {status === 'stale' ? <p role="status">{t('work.context.staleActions')}</p> : null}
      {historyStatus === 'error' || historyStatus === 'stale' ? <ProductError error={historyError} onRetry={retryContext} t={t} /> : null}
      {retryConflict ? <section className="task-blocker" role="alert">
        <p>{t(['retry_target_changed', 'run_binding_mismatch', 'retry_attempt_active', 'retry_attempt_unknown', 'retry_not_available', 'retry_refresh_required'].includes(scopedRetry.error?.code)
          ? `work.retry.conflict.${scopedRetry.error.code}` : 'work.retry.uncertain')}</p>
        <p>{t('work.retry.refreshRequired')}</p>
        <Button variant="secondary" onClick={() => { void handleRefreshContext(); }}>{t('work.retry.refresh')}</Button>
      </section> : null}
      {stopContinuation}
      <fieldset className="work-interaction-scope" disabled={interactionsPaused}>
        <TeamWorkspace
          toolbarHost={workToolbarHost}
          projectTools={<GitRecoveryPanel projectKey={projectKey} language={language} />}
          executionContext={executionContext}
          historyContext={historyContext}
          onInspectAttempt={inputContextRunId ? actor => setAttemptSelection({
            scope: retryScope, runId: inputContextRunId, actorId: actor.id, attemptId: actor.current_attempt_id,
          }) : undefined}
          onConfigureRuntime={() => setPanel({ type: 'runtime' })}
          onOpenReview={!isHistorical ? (approval) => setPanel({ type: 'review', approval, runId: currentRunId }) : undefined}
          {...conversationProps}
          busy={busy}
          data={isHistorical ? historicalDetail : data}
          error={mutationError}
          interactionsDisabled={interactionsPaused}
          language={language}
          onConfirmChangeIntent={handleConfirmChangeIntent}
          onDecide={handleDecide}
          onDiscussChangeIntent={handleDiscussChangeIntent}
          onKeepDiscussingChangeIntent={handleKeepDiscussingChangeIntent}
          onRetry={handleRetry}
          retry={retry}
          onConfirmTask={confirmTaskAssistantProposal}
          onContinuePreparation={continueProjectPreparation}
          onStartChangeIntent={handleStartChangeIntent}
          onTerminate={() => mutate('terminate', () => runApi.terminateRun(projectKey, currentRunId)).catch(() => {})}
          projectKey={projectKey}
          reducedMotion={reducedMotion}
          t={t}
        />
        <Suspense fallback={<p role="status">{t('product.states.loading')}</p>}>
          {status === 'ready' && panel?.type === 'runtime' ? <AssistantRuntimePanel
            data={data} projectKey={projectKey} onClose={() => setPanel(null)} refresh={refresh} t={t} /> : null}
          {status === 'ready' && panel?.type === 'review' && !isHistorical ? <ReviewPage embedded
            selectedReviewId={panel.approval} expectedRunId={panel.runId}
            onClose={() => setPanel(null)} onUpdated={refresh}
            onChangeDiscussion={async () => {
              const workspace = workspaceRef.current;
              await refresh();
              if (!workspace?.isConnected || workspace !== workspaceRef.current
                  || workspace.dataset.contextScope !== `${projectKey}:${runId}`) return;
              // Finish native dialog teardown and its focus restoration before handing focus to the composer.
              flushSync(() => setPanel(null));
              focusWorkContext(workspace, 'composer', { reducedMotion });
            }} /> : null}
        </Suspense>
      </fieldset>
      {inspection ? <AttemptInspector
        key={JSON.stringify([attemptSelection.scope, attemptSelection.runId, attemptSelection.actorId, attemptSelection.attemptId])}
        inspection={inspection} projectKey={projectKey} runId={attemptSelection.runId}
        historical={isHistorical || Boolean(contextMission?.read_only)} language={language} t={t} onClose={() => setAttemptSelection(null)}
      /> : null}
    </main>
  );
}
