import { useReducedMotion } from '../../hooks/useReducedMotion';
import { lazy, Suspense, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { focusWorkContext } from './workspace/currentWorkContext.js';
import { useOutletContext, useParams, useSearchParams } from 'react-router-dom';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import { runApi } from './runApi';
import useProjectState from './useProjectState';
import ProjectAttentionFocus from '../projects/ProjectAttentionFocus';
import GitRecoveryPanel from '../projects/GitRecoveryPanel';
import { readAttentionSelection } from '../projects/projectAttention';
import CurrentWorkContext from './workspace/CurrentWorkContext.jsx';
import GoalContinuationPanel from './workspace/GoalContinuationPanel.jsx';
import useProjectConversation from './useProjectConversation';
import TaskNavigation from './workspace/TaskNavigation';
import TeamWorkspace from './workspace/TeamWorkspace';

const AssistantRuntimePanel = lazy(() => import('../team/AssistantRuntimePanel'));

function WorkSkeleton({ t }) {
  return (
    <div className="product-workspace-skeleton" aria-label={t('product.states.loading')} role="status">
      <span /><span /><span />
    </div>
  );
}

export default function WorkPage() {
  const { projectKey = '' } = useParams();
  const { language, t, projectNavigationHost, workQuery, openProjectNavigation, closeProjectNavigation } = useOutletContext();
  const { reducedMotion } = useReducedMotion();
  const [search] = useSearchParams();
  const attentionQuery = search.toString();
  const attentionSelection = useMemo(() => readAttentionSelection(new URLSearchParams(attentionQuery)), [attentionQuery]);
  const { data, error, refresh, status } = useProjectState(projectKey);
  const interactionsPaused = status !== 'ready';
  const [mutationError, setMutationError] = useState(null);
  const [panel, setPanel] = useState(null);
  const workspaceRef = useRef(null);
  const [workToolbarHost, setWorkToolbarHost] = useState(null);
  const [taskManagementProject, setTaskManagementProject] = useState('');
  const taskManagementOpen = taskManagementProject === projectKey;
  const [busy, setBusy] = useState('');
  const projectedWorkspace = data?.team_workspace?.schema_version === 'team_workspace.v1'
    ? data.team_workspace
    : null;
  const visibleTaskTitle = projectedWorkspace?.proposed_task?.exact_request || projectedWorkspace?.mission?.title || '';
  const projectName = data?.start_context?.project_name || projectKey;

  const mutate = async (name, operation) => {
    setBusy(name); setMutationError(null);
    try {
      if (interactionsPaused) throw error || new Error(t('work.context.staleActions'));
      const result = await operation();
      await refresh();
      return result;
    } catch (caught) {
      setMutationError(caught);
      if (['message', 'retry_message', 'check_runtime', 'confirm_team_decision', 'dismiss_team_decision', 'edit_task', 'resolve_draft', 'confirm_project_mode', 'task-confirm'].includes(name)) {
        await refresh().catch(() => {});
      }
      throw caught;
    } finally {
      setBusy('');
    }
  };

  const conversation = useProjectConversation({ projectKey, refresh });

  const sendTaskAssistantMessage = conversation.send;
  const handleAssistantAction = async (request) => {
    const result = await conversation.act(request);
    if (result !== false && request.action === 'select_queued_task') {
      setTaskManagementProject('');
    }
    return result;
  };

  const confirmTaskAssistantProposal = (proposalId, revision) => mutate('task-confirm', () => (
    runApi.taskAssistantAction(projectKey, { action: 'confirm_task', proposal_id: proposalId, expected_revision: revision })
  )).catch(() => {});

  const continueProjectPreparation = (stepId, values) => mutate('preparation', () => (
    runApi.taskAssistantAction(projectKey, { action: 'continue_preparation', step_id: stepId, values })
  )).catch(() => {});

  const surfaceReady = Boolean(projectedWorkspace);
  const retryContext = () => { void refresh(); };
  const continuation = data?.task_assistant_session?.project_continuation;
  const stopContinuation = (interactionsPaused || !surfaceReady) && continuation?.status === 'active' ? (
    <Button variant="secondary" onClick={() => handleAssistantAction({
      action: 'stop_project_continuation', continuation_id: continuation.id,
    })}>{String(language).startsWith('zh') ? '停止后续迭代' : 'Stop future iterations'}</Button>
  ) : null;

  const attentionFocus = attentionSelection ? <ProjectAttentionFocus
    key={JSON.stringify([projectKey, attentionQuery])}
    selection={attentionSelection} projectKey={projectKey} data={data}
    freshness={status} refresh={refresh} reducedMotion={reducedMotion}
    surfaceReady={status === 'ready' && surfaceReady} t={t} /> : null;

  if (status === 'loading') return <main id="main-content" className="product-page">{attentionFocus}<WorkSkeleton t={t} /></main>;
  if (status === 'error' && !data) return <main id="main-content" className="product-page">{attentionFocus}<ProductError error={error} onRetry={retryContext} t={t} /></main>;
  if (!surfaceReady) return <main id="main-content" className="product-page">
    {attentionFocus}
    <ProductError error={{ code: 'invalid_response', message: t('projects.errors.invalid_response') }} onRetry={retryContext} t={t} />
    {stopContinuation}
  </main>;

  const taskNavigation = <TaskNavigation
    query={workQuery || ''} assistant={data.project_assistant} busy={conversation.busy}
    onAction={handleAssistantAction} t={t} onClose={() => {
      setTaskManagementProject('');
      closeProjectNavigation?.();
      focusWorkContext(workspaceRef.current, 'composer', { reducedMotion });
    }}
  />;
  const conversationProps = {
    projectAssistant: data.project_assistant || { ...data.team_workspace, timeline: data.team_workspace?.manager_timeline || [] },
    conversationDelivery: conversation.delivery,
    assistantBusy: conversation.busy,
    assistantError: conversation.error,
    onStart: sendTaskAssistantMessage,
    onQueueTask: conversation.queue,
    onAssistantAction: handleAssistantAction,
    onAnswerQuestion: conversation.answer,
    questionBoard: data?.task_assistant_session?.question_board,
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
        workspace={projectedWorkspace}
        freshness={status}
        onRefresh={retryContext}
        reducedMotion={reducedMotion}
        t={t}
      />
  );

  const goalContext = (
      <GoalContinuationPanel
        key={projectKey}
        goals={data.task_assistant_session?.goals || []}
        projectKey={projectKey}
        historyCursor={data.task_assistant_session?.goal_page?.next_before || ''}
        busy={busy || conversation.busy}
        error={conversation.error?.code === 'goal_action_unavailable' ? conversation.error : null}
        onAction={handleAssistantAction}
        t={t}
      />
  );

  return (
    <main ref={workspaceRef} id="main-content" className="product-page product-work-page" data-context-scope={projectKey}>
      {attentionFocus}
      {projectNavigationHost ? createPortal(<fieldset className="studio-navigation-scope" disabled={interactionsPaused}>{taskNavigation}</fieldset>, projectNavigationHost) : null}
      <div className="work-header-rail">
        {currentContext}
        <div className="work-header-rail__toolbar" ref={setWorkToolbarHost} />
      </div>
      {status === 'stale' ? <ProductError error={error} onRetry={retryContext} t={t} /> : null}
      {status === 'stale' ? <p role="status">{t('work.context.staleActions')}</p> : null}
      {stopContinuation}
      <fieldset className="work-interaction-scope" disabled={interactionsPaused}>
        <TeamWorkspace
          toolbarHost={workToolbarHost}
          projectTools={<GitRecoveryPanel projectKey={projectKey} language={language} />}
          historyContext={goalContext}
          onConfigureRuntime={() => setPanel({ type: 'runtime' })}
          {...conversationProps}
          busy={busy}
          data={data}
          error={mutationError}
          interactionsDisabled={interactionsPaused}
          language={language}
          onConfirmTask={confirmTaskAssistantProposal}
          onContinuePreparation={continueProjectPreparation}
          projectKey={projectKey}
          reducedMotion={reducedMotion}
          t={t}
        />
        <Suspense fallback={<p role="status">{t('product.states.loading')}</p>}>
          {status === 'ready' && panel?.type === 'runtime' ? <AssistantRuntimePanel
            data={data} projectKey={projectKey} onClose={() => setPanel(null)} refresh={refresh} t={t} /> : null}
        </Suspense>
      </fieldset>
    </main>
  );
}
